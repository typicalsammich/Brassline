import hashlib,json,os,re,ipaddress
from datetime import datetime,timezone
from urllib.parse import urlsplit
from fastapi import Request,HTTPException
from pydantic import BaseModel,Field
from integration_store import saved_config,effective_config,install_store

FIELDS=['TWILIO_ACCOUNT_SID','TWILIO_AUTH_TOKEN','TWILIO_API_KEY_SID','TWILIO_API_KEY_SECRET','TWILIO_TWIML_APP_SID','PUBLIC_BASE_URL']
class Number(BaseModel):
    number:str=Field(max_length=20)
    users:list[str]=Field(default_factory=list,max_length=100)
    latitude:float|None=Field(default=None,ge=-90,le=90)
    longitude:float|None=Field(default=None,ge=-180,le=180)
class Draft(BaseModel):
    current_password:str=Field(max_length=1024)
    account_sid:str=Field(default='',max_length=100)
    auth_token:str=Field(default='',max_length=256)
    api_key_sid:str=Field(default='',max_length=100)
    api_key_secret:str=Field(default='',max_length=256)
    app_sid:str=Field(default='',max_length=100)
    public_url:str=Field(default='',max_length=2000)
    numbers:list[Number]=Field(default_factory=list,max_length=100)
class Confirm(BaseModel):current_password:str=Field(max_length=1024)
class Activate(Confirm):enabled:bool;acknowledge_charges:bool=False

def fingerprint(config):return hashlib.sha256(json.dumps({k:config.get(k,'') for k in FIELDS+['TWILIO_NUMBERS_JSON']},sort_keys=True).encode()).hexdigest()
def valid_url(value):
    try:
        base=urlsplit(value);port=base.port;host=base.hostname or ''
        if host.lower()=='localhost' or host.lower().endswith(('.localhost','.local')):return False
        try:
            if not ipaddress.ip_address(host).is_global:return False
        except ValueError:
            if '.' not in host:return False
        return base.scheme=='https' and bool(host) and not base.username and not base.password and base.path in ('','/') and not base.query and not base.fragment
    except ValueError:return False

def install(app,connect,user,practice,verify,db_path):
    writer=install_store(connect,db_path)
    def write(value,uid):
        try:writer(value,uid)
        except Exception:raise HTTPException(503,'Could not securely save the connection. Check the integration encryption key and database permissions.')
    def authenticated(request,password=None):
        u=user(request)
        if password is not None:
            if practice:raise HTTPException(400,'Connection changes are disabled in practice')
            if not verify(password,u['password']):raise HTTPException(403,'Current password is incorrect')
            with connect() as db:
                if db.execute('SELECT 1 FROM dial_attempts WHERE (user_id=? OR provider_owner=?) AND result_recorded=0',(u['id'],u['id'])).fetchone():raise HTTPException(409,'Finish your active call before changing the calling connection')
        return u
    def safe_read(uid):
        try:return saved_config(uid),effective_config(uid)
        except ValueError as error:raise HTTPException(503,str(error))
    @app.get('/api/integrations/twilio')
    def status(request:Request):
        u=user(request);stored,config=safe_read(u['id'])
        from dialer_service import twilio_config,number_pool
        cfg=twilio_config(u['id']);numbers=number_pool(cfg,u['email'])
        present={key:bool(config.get(key)) for key in FIELDS};missing=[key for key in FIELDS if not present[key]]
        if not numbers:missing.append('A voice-capable phone number')
        if config.get('PUBLIC_BASE_URL') and not valid_url(config['PUBLIC_BASE_URL']):missing.append('A public root HTTPS app address')
        base=config.get('PUBLIC_BASE_URL','').rstrip('/')
        return {'admin':bool(u['admin']),'practice':practice,'enabled':cfg['enabled'],'switch_enabled':config.get('TWILIO_ENABLED')=='1','credentials_present':present,'managed_fields':[k for k in FIELDS+['TWILIO_ENABLED','TWILIO_NUMBERS_JSON'] if os.getenv(k) and (u['admin'] or k=='PUBLIC_BASE_URL')],'public_url':config.get('PUBLIC_BASE_URL',''),'numbers':numbers,'missing':missing,'verified':bool(stored.get('_verified_fingerprint')==fingerprint(config)),'verified_at':stored.get('_verified_at'),'voice_webhook':base+'/api/twilio/voice' if base else '', 'billing':'Your own Twilio account pays for your calls','storage':'encrypted database; separate encryption key','environment_managed':bool(u['admin'] and os.getenv('TWILIO_ENABLED'))}
    @app.post('/api/integrations/twilio/save')
    def save(data:Draft,request:Request):
        u=authenticated(request,data.current_password)
        if u['admin'] and os.getenv('TWILIO_ENABLED')=='1':raise HTTPException(409,'Disable calling in your server environment before editing this managed connection')
        stored,config=safe_read(u['id']);mapping=dict(zip(FIELDS,[data.account_sid,data.auth_token,data.api_key_sid,data.api_key_secret,data.app_sid,data.public_url]))
        for key,value in mapping.items():
            value=value.strip()
            if not value:continue
            if (u['admin'] or key=='PUBLIC_BASE_URL') and os.getenv(key) and value!=os.getenv(key):raise HTTPException(409,'This setting is managed in the server environment: '+key)
            if key in ('TWILIO_ACCOUNT_SID','TWILIO_API_KEY_SID','TWILIO_TWIML_APP_SID'):
                prefix={'TWILIO_ACCOUNT_SID':'AC','TWILIO_API_KEY_SID':'SK','TWILIO_TWIML_APP_SID':'AP'}[key]
                if not re.fullmatch(prefix+r'[0-9a-fA-F]{32}',value):raise HTTPException(400,'Enter a valid '+key)
            if key=='PUBLIC_BASE_URL' and not valid_url(value):raise HTTPException(400,'Use your public root HTTPS app URL, without a path, query or credentials')
            stored[key]=value
        with connect() as db:members={u['email']}
        normalized=[]
        for number in data.numbers:
            if not re.fullmatch(r'\+1\d{10}',number.number):raise HTTPException(400,'Use a US number in +1 format')
            users=[u['email']]
            if any(email not in members for email in users):raise HTTPException(400,'Assign numbers only to existing members')
            if (number.latitude is None)!=(number.longitude is None):raise HTTPException(400,'Provide both region coordinates or neither')
            normalized.append({'number':number.number,'users':users,'latitude':number.latitude,'longitude':number.longitude})
        if len({n['number'] for n in normalized})!=len(normalized):raise HTTPException(400,'Remove duplicate phone numbers')
        numbers=json.dumps(normalized)
        if u['admin'] and os.getenv('TWILIO_NUMBERS_JSON'):
            try:managed=[Number(**n).model_dump() for n in json.loads(os.getenv('TWILIO_NUMBERS_JSON'))]
            except Exception:raise HTTPException(409,'Correct the phone-number configuration in the server environment')
            if managed!=normalized:raise HTTPException(409,'Phone numbers are managed in the server environment')
        stored['TWILIO_NUMBERS_JSON']=numbers;stored['TWILIO_ENABLED']='0';stored.pop('_verified_fingerprint',None);stored.pop('_verified_at',None)
        write(stored,u['id']);return {'ok':True,'message':'Connection saved with calling disabled. Verify it before enabling calling.'}
    @app.post('/api/integrations/twilio/verify')
    def check(data:Confirm,request:Request):
        u=authenticated(request,data.current_password);stored,config=safe_read(u['id'])
        if not all(config.get(k) for k in FIELDS):raise HTTPException(400,'Complete the credentials and public HTTPS URL first')
        if not valid_url(config['PUBLIC_BASE_URL']):raise HTTPException(400,'A public HTTPS address is required')
        from twilio.rest import Client
        from twilio.http.http_client import TwilioHttpClient
        try:
            client=Client(config['TWILIO_ACCOUNT_SID'],config['TWILIO_AUTH_TOKEN'],http_client=TwilioHttpClient(timeout=10))
            available=client.incoming_phone_numbers.list(limit=1000)
            owned={n.phone_number for n in available if n.capabilities.get('voice')}
            numbers=json.loads(config.get('TWILIO_NUMBERS_JSON') or '[]')
            if not numbers or any(n['number'] not in owned for n in numbers):raise HTTPException(400,'Every selected number must belong to this Twilio account and support Voice')
            key_client=Client(config['TWILIO_API_KEY_SID'],config['TWILIO_API_KEY_SECRET'],account_sid=config['TWILIO_ACCOUNT_SID'],http_client=TwilioHttpClient(timeout=10))
            application=key_client.applications(config['TWILIO_TWIML_APP_SID']).fetch()
            if application.voice_url!=config['PUBLIC_BASE_URL'].rstrip('/')+'/api/twilio/voice' or str(application.voice_method).upper()!='POST':raise HTTPException(400,'Set the TwiML app Voice request URL to the shown webhook and choose POST, then verify again')
        except HTTPException:raise
        except Exception:raise HTTPException(502,'Twilio verification failed. Check the credentials, API key permissions, and connection. No call was placed.')
        latest,_=safe_read(u['id'])
        if fingerprint(effective_config(u['id']))!=fingerprint(config):raise HTTPException(409,'Connection settings changed during verification; verify again')
        latest['_verified_fingerprint']=fingerprint(config);latest['_verified_at']=datetime.now(timezone.utc).isoformat();write(latest,u['id'])
        return {'ok':True,'message':'Credentials, number ownership, Voice capability, and TwiML app webhook verified. Calling remains off until enabled.'}
    @app.post('/api/integrations/twilio/activate')
    def activate(data:Activate,request:Request):
        u=authenticated(request,data.current_password);stored,config=safe_read(u['id'])
        if u['admin'] and os.getenv('TWILIO_ENABLED'):raise HTTPException(409,'Calling is managed by TWILIO_ENABLED in the server environment')
        if data.enabled and (not data.acknowledge_charges or stored.get('_verified_fingerprint')!=fingerprint(config)):raise HTTPException(409,'Verify the connection and acknowledge provider charges before enabling calls')
        stored['TWILIO_ENABLED']='1' if data.enabled else '0';write(stored,u['id']);return {'ok':True}
    @app.post('/api/integrations/twilio/disconnect')
    def disconnect(data:Confirm,request:Request):
        u=authenticated(request,data.current_password)
        if u['admin'] and any(os.getenv(key) for key in FIELDS+['TWILIO_ENABLED','TWILIO_NUMBERS_JSON'] if key!='PUBLIC_BASE_URL'):raise HTTPException(409,'Remove Twilio settings from the server environment to disconnect this managed connection')
        write({},u['id']);return {'ok':True}

