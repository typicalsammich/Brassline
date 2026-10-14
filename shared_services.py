"""Administrator-funded services, durable UTC-month quotas and encrypted settings."""
import hashlib,json,math,os,secrets,time
from datetime import datetime,timezone
from fastapi import HTTPException,Request
from pydantic import BaseModel,Field
from typing import Literal
from integration_store import saved_config,install_store

CONNECT=None
KINDS=('research','calling','transcription','coaching')
KEYS=('serpapi_key','firecrawl_key','apify_key','openai_key','resend_key','deepgram_key')
DEFAULTS={'research_enabled':False,'calling_enabled':False,'transcription_enabled':False,'coaching_enabled':False,
          'research':0,'calling':0,'transcription':0,'coaching':0,'max_call_minutes':10,
          'max_suggestions_per_call':20,'max_research_results':10,'email_enabled':False,'email_from':'',
          'transcription_provider':'twilio','max_transcription_minutes':10,
          'offer':'We design and develop custom websites for independent local service businesses. Ask about needs before offering a quote. Prices and delivery dates require a confirmed quote.'}

def month():return datetime.now(timezone.utc).strftime('%Y-%m')
def admin_id():
    with CONNECT() as db:row=db.execute('SELECT id FROM users WHERE admin=1 ORDER BY id LIMIT 1').fetchone()
    return row['id'] if row else None
def config():
    if CONNECT is None:return dict(DEFAULTS)
    uid=admin_id()
    return {**DEFAULTS,**(saved_config(uid,'shared') if uid else {})}
def allowance(db,uid):
    row=db.execute('SELECT * FROM service_allowances WHERE user_id=?',(uid,)).fetchone()
    return dict(row) if row else {'user_id':uid,'paused':1,'use_admin_calls':0,**{k:0 for k in KINDS}}
def used(db,uid,kind):
    sql='SELECT COALESCE(SUM(units),0) FROM service_usage WHERE period=? AND kind=?';args=[month(),kind]
    if uid is not None:sql+=' AND user_id=?';args.append(uid)
    return db.execute(sql,args).fetchone()[0]
def reserve_in(db,uid,kind,units,key,cfg=None):
    # Re-read under the caller's write transaction so disabled services/new caps win.
    cfg=config()
    if kind not in KINDS or units<1:raise HTTPException(400,'Invalid usage reservation')
    previous=db.execute('SELECT * FROM service_usage WHERE request_key=?',(key,)).fetchone()
    if previous:
        if previous['user_id']!=uid or previous['kind']!=kind:raise HTTPException(409,'Usage reservation does not match')
        return previous['units']
    limit=allowance(db,uid)
    if not cfg.get(kind+'_enabled') or limit['paused']:raise HTTPException(403,'Administrator-funded '+kind+' is disabled for this account')
    if used(db,uid,kind)+units>limit[kind] or used(db,None,kind)+units>cfg[kind]:raise HTTPException(429,'Monthly '+kind+' allowance reached. Ask the administrator to review usage.')
    db.execute('INSERT INTO service_usage(request_key,user_id,period,kind,units,created_at) VALUES(?,?,?,?,?,?)',(key,uid,month(),kind,units,time.time()))
    return units
def reserve(uid,kind,units,key,cfg=None):
    with CONNECT() as db:
        db.execute('BEGIN IMMEDIATE');return reserve_in(db,uid,kind,units,key,cfg)
def funded_owner(uid):
    if CONNECT is None:return None
    cfg=config()
    with CONNECT() as db:limit=allowance(db,uid)
    return admin_id() if cfg['calling_enabled'] and not limit['paused'] and limit['use_admin_calls'] else None
def calling_config(uid):
    from dialer_service import twilio_config
    owner=funded_owner(uid)
    cfg=twilio_config(owner if owner is not None else uid)
    if owner is not None:
        cfg['numbers']=[{**n,'users':[]} for n in cfg['numbers']]
    cfg['provider_owner']=owner if owner is not None else uid
    cfg['sponsored']=owner is not None
    with CONNECT() as db:preference=allowance(db,uid)['use_admin_calls']
    if preference and owner is None:cfg['enabled']=False
    return cfg
def attempt_config(row):
    from dialer_service import twilio_config
    return twilio_config(row['provider_owner'] if row['provider_owner'] is not None else row['user_id'])
def calling_reservation(db,uid,attempt,cfg):
    if not cfg.get('sponsored'):return 0
    shared=config();limit=allowance(db,uid)
    remaining=min(limit['calling']-used(db,uid,'calling'),shared['calling']-used(db,None,'calling'))
    # Two minute-equivalents reserve both browser and phone legs, including ringing/rounding.
    minutes=min(shared['max_call_minutes'],remaining//2-1)
    if minutes<1:raise HTTPException(429,'Not enough administrator-funded calling allowance for another call')
    reserve_in(db,uid,'calling',2*(minutes+1),'call:'+attempt,shared)
    return minutes*60
def transcription_available(row):
    cfg=config();owner=admin_id()
    if cfg.get('transcription_provider')=='deepgram':return bool(cfg['transcription_enabled'] and cfg.get('deepgram_key'))
    return bool(cfg['transcription_enabled'] and row['provider_owner']==owner and row['sponsored'] and row['call_limit_seconds'])

class Settings(BaseModel):
    current_password:str=Field(max_length=1024)
    acknowledge_charges:bool=False
    research_enabled:bool=False
    calling_enabled:bool=False
    transcription_enabled:bool=False
    coaching_enabled:bool=False
    research:int=Field(default=0,ge=0,le=1000000)
    calling:int=Field(default=0,ge=0,le=1000000)
    transcription:int=Field(default=0,ge=0,le=1000000)
    coaching:int=Field(default=0,ge=0,le=1000000)
    max_call_minutes:int=Field(default=10,ge=1,le=30)
    max_suggestions_per_call:int=Field(default=20,ge=1,le=100)
    max_research_results:int=Field(default=10,ge=1,le=20)
    serpapi_key:str=Field(default='',max_length=512)
    firecrawl_key:str=Field(default='',max_length=512)
    apify_key:str=Field(default='',max_length=512)
    openai_key:str=Field(default='',max_length=512)
    resend_key:str=Field(default='',max_length=512)
    deepgram_key:str=Field(default='',max_length=512)
    transcription_provider:Literal['twilio','deepgram']='twilio'
    max_transcription_minutes:int=Field(default=10,ge=1,le=30)
    clear_keys:list[str]=Field(default_factory=list,max_length=6)
    offer:str=Field(default=DEFAULTS['offer'],max_length=4000)
    email_enabled:bool=False
    email_from:str=Field(default='',max_length=254)
class Grant(BaseModel):
    current_password:str=Field(max_length=1024)
    user_id:int
    paused:bool=True
    research:int=Field(default=0,ge=0,le=1000000)
    calling:int=Field(default=0,ge=0,le=1000000)
    transcription:int=Field(default=0,ge=0,le=1000000)
    coaching:int=Field(default=0,ge=0,le=1000000)
class Funding(BaseModel):use_admin_calls:bool=False
class Password(BaseModel):current_password:str=Field(max_length=1024)
def email_fingerprint(cfg):return hashlib.sha256((cfg.get('resend_key','')+'\n'+cfg.get('email_from','')).encode()).hexdigest()

def install(app,connect,user,practice,verify,db_path):
    global CONNECT
    CONNECT=connect;writer=install_store(connect,db_path)
    with connect() as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS service_allowances(user_id INTEGER PRIMARY KEY REFERENCES users(id),paused INTEGER NOT NULL DEFAULT 1,research INTEGER NOT NULL DEFAULT 0,calling INTEGER NOT NULL DEFAULT 0,transcription INTEGER NOT NULL DEFAULT 0,coaching INTEGER NOT NULL DEFAULT 0,use_admin_calls INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS service_usage(request_key TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),period TEXT NOT NULL,kind TEXT NOT NULL,units INTEGER NOT NULL,created_at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS service_usage_period ON service_usage(period,kind,user_id);
        CREATE TABLE IF NOT EXISTS service_audit(id INTEGER PRIMARY KEY,admin_id INTEGER,action TEXT,user_id INTEGER,created_at REAL);
        CREATE TABLE IF NOT EXISTS coaching_jobs(attempt_id TEXT PRIMARY KEY,revision INTEGER NOT NULL DEFAULT 0,state TEXT NOT NULL DEFAULT 'off',text TEXT NOT NULL DEFAULT '',error TEXT NOT NULL DEFAULT '',last_started REAL NOT NULL DEFAULT 0,request_key TEXT,latency_ms INTEGER,input_tokens INTEGER,output_tokens INTEGER);
        CREATE TABLE IF NOT EXISTS transcript_partials(attempt_id TEXT PRIMARY KEY,speaker TEXT,text TEXT,sequence INTEGER,updated_at REAL);''')
        columns={r[1] for r in db.execute('PRAGMA table_info(dial_attempts)')}
        for name,definition in [('provider_owner','INTEGER REFERENCES users(id)'),('sponsored','INTEGER NOT NULL DEFAULT 0'),('call_limit_seconds','INTEGER NOT NULL DEFAULT 0'),('coaching_enabled','INTEGER NOT NULL DEFAULT 0')]:
            if name not in columns:db.execute(f'ALTER TABLE dial_attempts ADD COLUMN {name} {definition}')
        db.execute("UPDATE coaching_jobs SET state='interrupted',error='Server restarted; the next prospect utterance can request a new suggestion.' WHERE state='generating'")
    def admin(request,password):
        u=user(request)
        if not u['admin']:raise HTTPException(403,'Administrator access required')
        if practice:raise HTTPException(400,'Shared service changes are disabled in practice')
        if not verify(password,u['password']):raise HTTPException(403,'Current password is incorrect')
        return u
    def view(uid,cfg):
        with connect() as db:
            limit=allowance(db,uid)
            return {**limit,'usage':{k:used(db,uid,k) for k in KINDS},'remaining':{k:max(0,min(limit[k]-used(db,uid,k),cfg[k]-used(db,None,k))) for k in KINDS}}
    @app.get('/api/services')
    def status(request:Request):
        u=user(request);cfg=config();result={'admin':bool(u['admin']),'practice':practice,'period':month(),'features':{k:bool(cfg[k+'_enabled']) for k in KINDS},'allowance':view(u['id'],cfg),'max_call_minutes':cfg['max_call_minutes'],'max_suggestions_per_call':cfg['max_suggestions_per_call'],'max_research_results':cfg['max_research_results'],'transcription_provider':cfg['transcription_provider'],'max_transcription_minutes':cfg['max_transcription_minutes'],'email_enabled':cfg['email_enabled'],'email_from':cfg['email_from']}
        if u['admin']:
            with connect() as db:members=[dict(r) for r in db.execute('SELECT id,email,display_name FROM users ORDER BY id')];audit=[dict(r) for r in db.execute('SELECT * FROM service_audit ORDER BY id DESC LIMIT 30')]
            result.update(email_verified=bool(cfg.get('resend_key') and cfg.get('_email_verified')==email_fingerprint(cfg)),settings={k:cfg[k] for k in DEFAULTS},credentials_present={k:bool(cfg.get(k)) for k in KEYS},members=[{**m,**view(m['id'],cfg)} for m in members],total_usage={k:used_total(k) for k in KINDS},audit=audit)
        return result
    def used_total(kind):
        with connect() as db:return used(db,None,kind)
    @app.post('/api/services/settings')
    def save(data:Settings,request:Request):
        u=admin(request,data.current_password);cfg=config();incoming=data.model_dump()
        if any(incoming[k+'_enabled'] for k in KINDS) and not data.acknowledge_charges:raise HTTPException(409,'Acknowledge administrator provider charges before enabling funded services')
        for key in data.clear_keys:
            if key not in KEYS:raise HTTPException(400,'Unknown provider key')
            cfg.pop(key,None)
        for key in KEYS:
            if incoming[key].strip():cfg[key]=incoming[key].strip()
        cfg.update({k:incoming[k] for k in DEFAULTS})
        if cfg['research_enabled'] and not (cfg.get('serpapi_key') or cfg.get('firecrawl_key')):raise HTTPException(400,'Connect SerpAPI or Firecrawl for web discovery first')
        if cfg['coaching_enabled'] and not cfg.get('openai_key'):raise HTTPException(400,'Connect the administrator OpenAI API key first')
        if cfg['transcription_enabled'] and cfg['transcription_provider']=='deepgram' and not cfg.get('deepgram_key'):raise HTTPException(400,'Connect the administrator Deepgram API key first')
        if cfg['calling_enabled'] or (cfg['transcription_enabled'] and cfg['transcription_provider']=='twilio'):
            from dialer_service import twilio_config
            if not twilio_config(u['id'])['enabled']:raise HTTPException(400,'Verify and enable the administrator Twilio connection first')
        if cfg['email_enabled'] and (not cfg.get('resend_key') or '@' not in cfg['email_from']):raise HTTPException(400,'Add a Resend key and an address on your verified sending domain')
        if cfg['email_enabled'] and cfg.get('_email_verified')!=email_fingerprint(cfg):raise HTTPException(409,'Save email settings disabled, then verify your sending domain before enabling delivery')
        writer(cfg,u['id'],'shared')
        with connect() as db:db.execute('INSERT INTO service_audit(admin_id,action,created_at) VALUES(?,?,?)',(u['id'],'Shared settings updated',time.time()))
        return {'ok':True,'message':'Shared settings saved. Members still need individual allowances; personal services are not charged to this pool.'}
    @app.post('/api/services/email/verify')
    def verify_email(data:Password,request:Request):
        u=admin(request,data.current_password);cfg=config()
        if not cfg.get('resend_key') or '@' not in cfg.get('email_from',''):raise HTTPException(400,'Save your Resend key and sending address with email disabled first')
        from research_service import provider_json
        result=provider_json('https://api.resend.com/domains',headers={'Authorization':'Bearer '+cfg['resend_key']})
        domain=cfg['email_from'].rsplit('@',1)[-1].strip().strip('>').lower()
        if not any(r.get('name','').lower()==domain and r.get('status')=='verified' for r in result.get('data',[])):raise HTTPException(409,'Your sending domain is not verified in Resend. Add its DNS records at your domain registrar, then retry.')
        latest=config()
        if email_fingerprint(latest)!=email_fingerprint(cfg):raise HTTPException(409,'Email settings changed; verify again')
        latest['_email_verified']=email_fingerprint(cfg);writer(latest,u['id'],'shared')
        return {'ok':True,'message':'Resend reports the sending domain verified. Save settings with email delivery enabled when ready.'}
    @app.post('/api/services/allowance')
    def grant(data:Grant,request:Request):
        u=admin(request,data.current_password)
        with connect() as db:
            if not db.execute('SELECT id FROM users WHERE id=?',(data.user_id,)).fetchone():raise HTTPException(404,'Account not found')
            db.execute('INSERT INTO service_allowances(user_id,paused,research,calling,transcription,coaching) VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET paused=excluded.paused,research=excluded.research,calling=excluded.calling,transcription=excluded.transcription,coaching=excluded.coaching',(data.user_id,int(data.paused),data.research,data.calling,data.transcription,data.coaching))
            db.execute('INSERT INTO service_audit(admin_id,action,user_id,created_at) VALUES(?,?,?,?)',(u['id'],'Monthly allowances/paused state updated',data.user_id,time.time()))
        return {'ok':True}
    @app.post('/api/services/funding')
    def funding(data:Funding,request:Request):
        uid=user(request)['id']
        if practice:raise HTTPException(400,'Funding preferences are unavailable in practice')
        with connect() as db:
            if db.execute('SELECT 1 FROM dial_attempts WHERE user_id=? AND result_recorded=0',(uid,)).fetchone():raise HTTPException(409,'Finish your active call before switching billing accounts')
            db.execute('INSERT INTO service_allowances(user_id,use_admin_calls) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET use_admin_calls=excluded.use_admin_calls',(uid,int(data.use_admin_calls)))
        return {'ok':True}
