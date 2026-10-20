# Test-only credentials below are deliberately fake. Every provider call is mocked.
import os,sys,json,importlib.util
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

root=Path(__file__).resolve().parents[1];sys.path[:0]=[str(root)]
import tempfile
(root/'data').mkdir(exist_ok=True)
_scratch=tempfile.TemporaryDirectory(prefix='brassline-discovery-test-',dir=root/'data');folder=Path(_scratch.name);db_path=folder/'setup.db'
for key in ['TWILIO_ENABLED','TWILIO_ACCOUNT_SID','TWILIO_AUTH_TOKEN','TWILIO_API_KEY_SID','TWILIO_API_KEY_SECRET','TWILIO_TWIML_APP_SID','TWILIO_NUMBERS_JSON','PUBLIC_BASE_URL','RENDER_EXTERNAL_URL','INTEGRATION_ENCRYPTION_KEY']:os.environ[key]=''
os.environ.update(PRACTICE='0',ONLINE='0',DATABASE_PATH=str(db_path))
spec=importlib.util.spec_from_file_location('setup_app',root/'app.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

# Invoke routes directly: the sandbox blocks Windows loopback socketpair creation.
import inspect
from urllib.parse import urlsplit,urlencode
from starlette.requests import Request
from starlette.responses import Response,JSONResponse
from starlette.routing import Match
from fastapi import HTTPException
from pydantic import BaseModel,ValidationError
class TestClient:
 def __init__(self,app):self.app=app;self.cookie=''
 def get(self,path):return self.request('GET',path)
 def post(self,path,json=None,data=None,headers=None):return self.request('POST',path,json,data,headers)
 def request(self,method,path,json_data=None,data=None,headers=None):
  parsed=urlsplit(path);raw=(urlencode(data).encode() if data else b'');head={'cookie':self.cookie,**(headers or {})}
  if data:head['content-type']='application/x-www-form-urlencoded'
  scope={'type':'http','method':method,'path':parsed.path,'root_path':'','query_string':parsed.query.encode(),'headers':[(k.encode(),v.encode()) for k,v in head.items()],'scheme':'https','server':('testserver',443)}
  async def receive():return {'type':'http.request','body':raw,'more_body':False}
  request=Request(scope,receive)
  for route in self.app.routes:
   match,child=route.matches(scope)
   if match!=Match.FULL:continue
   kwargs=dict(child.get('path_params',{}))
   try:
    for key,param in inspect.signature(route.endpoint).parameters.items():
     if key=='request':kwargs[key]=request
     elif inspect.isclass(param.annotation) and issubclass(param.annotation,BaseModel):kwargs[key]=param.annotation(**(json_data or {}))
    result=route.endpoint(**kwargs)
    if inspect.isawaitable(result):
     try:result.send(None)
     except StopIteration as done:result=done.value
     else:raise AssertionError('Unexpected suspended async route')
    response=result if isinstance(result,Response) else JSONResponse(result)
   except HTTPException as error:response=JSONResponse({'detail':error.detail},status_code=error.status_code)
   except ValidationError:response=JSONResponse({'detail':'Invalid input'},status_code=422)
   if response.headers.get('set-cookie'):self.cookie=response.headers['set-cookie'].split(';')[0]
   return SimpleNamespace(status_code=response.status_code,text=response.body.decode(),json=lambda:__import__('json').loads(response.body))
  raise AssertionError('Route not found '+path)

c=TestClient(m.app);other=TestClient(m.app)
pw='long-owner-password-123';assert c.post('/api/register',json={'email':'owner@example.test','password':pw}).status_code==200
invite=c.post('/api/invites',json={}).json()['path'].split('=')[1];assert other.post('/api/register',json={'email':'other@example.test','password':pw,'token':invite}).status_code==200
payload={'current_password':pw,'account_sid':'AC'+'a'*32,'auth_token':'private-test-auth-token','api_key_sid':'SK'+'b'*32,'api_key_secret':'private-test-key-secret','app_sid':'AP'+'c'*32,'public_url':'https://brassline.example.lat','numbers':[{'number':'+19515550101','users':['owner@example.test']}]}
assert not other.get('/api/integrations/twilio').json()['credentials_present']['TWILIO_AUTH_TOKEN']
assert c.post('/api/integrations/twilio/save',json={**payload,'current_password':'wrong'}).status_code==403
assert c.post('/api/integrations/twilio/save',json={**payload,'public_url':'http://127.0.0.1:8000'}).status_code==400
assert c.post('/api/integrations/twilio/save',json={**payload,'public_url':'https://localhost'}).status_code==400
invalid=c.post('/api/integrations/twilio/save',json={**payload,'auth_token':'never-echo-this-secret'*30});assert invalid.status_code==422;assert 'never-echo-this-secret' not in invalid.text
saved=c.post('/api/integrations/twilio/save',json=payload);assert saved.status_code==200,saved.text
status=c.get('/api/integrations/twilio').json();assert not status['enabled'] and status['credentials_present']['TWILIO_AUTH_TOKEN'];assert 'private-test-auth-token' not in str(status)
assert not other.get('/api/integrations/twilio').json()['numbers']
with m.connect() as db:
 encrypted=db.execute('SELECT payload FROM integration_configs').fetchone()[0];assert b'private-test-auth-token' not in encrypted
assert (folder/'integration.key').exists()
assert c.post('/api/integrations/twilio/activate',json={'current_password':pw,'enabled':True,'acknowledge_charges':True}).status_code==409
number=SimpleNamespace(phone_number='+19515550101',capabilities={'voice':True})
app=SimpleNamespace(voice_url='https://brassline.example.lat/api/twilio/voice',voice_method='POST')
class Fake:
 incoming_phone_numbers=SimpleNamespace(list=lambda **kwargs:[number])
 def applications(self,sid):return SimpleNamespace(fetch=lambda:app)
with patch('twilio.rest.Client',return_value=Fake()):
 verified=c.post('/api/integrations/twilio/verify',json={'current_password':pw});assert verified.status_code==200,verified.text
assert c.post('/api/integrations/twilio/activate',json={'current_password':pw,'enabled':True}).status_code==409
assert c.post('/api/integrations/twilio/activate',json={'current_password':pw,'enabled':True,'acknowledge_charges':True}).status_code==200
assert c.get('/api/dialer').json()['mode']=='twilio';assert other.get('/api/dialer').json()['mode']=='list'

# A member can manage an independent account without inheriting the server account.
payload2={**payload,'account_sid':'AC'+'d'*32,'auth_token':'member-private-token','api_key_sid':'SK'+'e'*32,'api_key_secret':'member-key-secret','app_sid':'AP'+'f'*32,'numbers':[{'number':'+19045550102','users':['owner@example.test']}]}
r=other.post('/api/integrations/twilio/save',json=payload2);assert r.status_code==200,r.text
number2=SimpleNamespace(phone_number='+19045550102',capabilities={'voice':True})
class FakeMember(Fake):incoming_phone_numbers=SimpleNamespace(list=lambda **kwargs:[number2])
with patch('twilio.rest.Client',return_value=FakeMember()):
 r=other.post('/api/integrations/twilio/verify',json={'current_password':pw});assert r.status_code==200,r.text
assert other.post('/api/integrations/twilio/activate',json={'current_password':pw,'enabled':True,'acknowledge_charges':True}).status_code==200
assert other.get('/api/dialer').json()['mode']=='twilio'
from integration_store import effective_config
from dialer_service import twilio_config
assert effective_config(1)['TWILIO_ACCOUNT_SID']==payload['account_sid']
assert effective_config(2)['TWILIO_ACCOUNT_SID']==payload2['account_sid']
assert twilio_config(2)['numbers'][0]['users']==['other@example.test']
import jwt
assert jwt.decode(other.post('/api/dialer/token').json()['token'],options={'verify_signature':False})['sub']==payload2['account_sid']
assert jwt.decode(c.post('/api/dialer/token').json()['token'],options={'verify_signature':False})['sub']==payload['account_sid']
# Existing global environment credentials never become a member's fallback.
with patch.dict(os.environ,{'TWILIO_ACCOUNT_SID':'AC'+'0'*32,'TWILIO_AUTH_TOKEN':'server-token','TWILIO_ENABLED':'1'}):
 assert effective_config(2)['TWILIO_AUTH_TOKEN']=='member-private-token'
 assert not other.get('/api/integrations/twilio').json()['environment_managed']
# Seed independent call attempts to test signed webhook routing and hangup account selection.
with m.connect() as db:
 for uid in [1,2]:
  lead=db.execute("INSERT INTO leads(user_id,name,phone) VALUES(?,?,'9515550103')",(uid,'Test business')).lastrowid
  db.execute("INSERT INTO dial_attempts(id,user_id,lead_id,started_at,status,mode,phone,caller_number) VALUES(?,?,?,'2026-10-14T12:00:00+00:00','preparing','twilio','+19515550103',?)",('attempt'+str(uid),uid,lead,'+19515550101' if uid==1 else '+19045550102'))
from twilio.request_validator import RequestValidator
voice_url=payload['public_url']+'/api/twilio/voice'
with m.connect() as db:lid=db.execute("SELECT lead_id FROM dial_attempts WHERE id='attempt2'").fetchone()[0]
form={'AttemptId':'attempt2','From':'client:user_2','LeadId':str(lid),'AccountSid':payload2['account_sid'],'CallSid':'CA'+'2'*32}
def post_signed(path,url,form,secret):return c.post(path,data=form,headers={'x-twilio-signature':RequestValidator(secret).compute_signature(url,form)})
assert post_signed('/api/twilio/voice',voice_url,form,payload['auth_token']).status_code==403
r=post_signed('/api/twilio/voice',voice_url,form,payload2['auth_token']);assert r.status_code==200,r.text
assert '+19045550102' in r.text
status_form={'AccountSid':payload2['account_sid'],'CallSid':'CA'+'3'*32,'ParentCallSid':form['CallSid'],'CallStatus':'in-progress'}
status_url=payload['public_url']+'/api/twilio/status?attempt=attempt2'
assert post_signed('/api/twilio/status?attempt=attempt2',status_url,status_form,payload['auth_token']).status_code==403
assert post_signed('/api/twilio/status?attempt=attempt2',status_url,status_form,payload2['auth_token']).status_code==204
# Each connection is locked only while that person's call is active.
assert other.post('/api/integrations/twilio/disconnect',json={'current_password':pw}).status_code==409
with m.connect() as db:db.execute("UPDATE dial_attempts SET result_recorded=1 WHERE id='attempt1'")
assert c.post('/api/integrations/twilio/activate',json={'current_password':pw,'enabled':False}).status_code==200
assert other.get('/api/dialer').json()['mode']=='twilio'
with patch('twilio.rest.Client') as client:
 assert other.post('/api/dialer/attempts/attempt2/hangup').status_code==200
 client.assert_called_once_with(payload2['account_sid'],payload2['auth_token'])
with m.connect() as db:db.execute("UPDATE dial_attempts SET result_recorded=1 WHERE id='attempt2'")
assert other.post('/api/integrations/twilio/disconnect',json={'current_password':pw}).status_code==200
assert effective_config(1)['TWILIO_AUTH_TOKEN']==payload['auth_token']
assert not effective_config(2)['TWILIO_AUTH_TOKEN']
print('PASS: separate member settings, encrypted credentials, password checks, account-specific tokens, cross-account webhook rejection, correct hangup billing account, environment isolation, independent activation and disconnect. No real provider requests.')



