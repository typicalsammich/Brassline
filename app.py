import hashlib, hmac, json, os, secrets, sqlite3, tempfile, time
from pathlib import Path
from urllib.parse import urlsplit
from contextlib import contextmanager,closing
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel

ROOT = Path(__file__).parent
# Local secrets stay in the ignored .env file; host environment values win.
if (ROOT/'.env').is_file():
    for line in (ROOT/'.env').read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.lstrip().startswith('#') or '=' not in line:continue
        key,value=line.split('=',1);key=key.strip();value=value.strip()
        if value[:1] in ('\"', "'") and value[-1:]==value[:1]:value=value[1:-1]
        if key.replace('_','').isalnum():os.environ.setdefault(key,value)
if not os.getenv('PUBLIC_BASE_URL') and os.getenv('RENDER_EXTERNAL_URL'):os.environ['PUBLIC_BASE_URL']=os.getenv('RENDER_EXTERNAL_URL')
PRACTICE = os.getenv('PRACTICE') == '1'
DB = Path(tempfile.mkdtemp(prefix='calling-practice-')) / 'practice.db' if PRACTICE else Path(os.getenv('DATABASE_PATH', str(ROOT / 'data' / 'dashboard.db')))
DB.parent.mkdir(parents=True, exist_ok=True)
ONLINE = os.getenv('ONLINE') == '1'
app = FastAPI()

@app.exception_handler(RequestValidationError)
async def safe_validation(request,error):
    return JSONResponse({'detail':[{'loc':item['loc'],'msg':item['msg'],'type':item['type']} for item in error.errors()]},status_code=422)


@contextmanager
def connect():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

with connect() as db:
    db.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, password TEXT NOT NULL, admin INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id), expires REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS invites(token TEXT PRIMARY KEY, expires REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS batches(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),name TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP,deleted_at TEXT);
    CREATE TABLE IF NOT EXISTS leads(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),batch_id INTEGER REFERENCES batches(id),name TEXT NOT NULL,phone TEXT,place_id TEXT,review_facts TEXT DEFAULT '{}',score INTEGER,grade TEXT);
    CREATE TABLE IF NOT EXISTS call_events(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),lead_id INTEGER NOT NULL REFERENCES leads(id),result TEXT NOT NULL,source TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP,ignored INTEGER NOT NULL DEFAULT 0,script_id INTEGER,caller_number TEXT);
    CREATE TRIGGER IF NOT EXISTS preserve_call_events BEFORE DELETE ON call_events BEGIN SELECT RAISE(ABORT,'Call history cannot be deleted'); END;
    CREATE TRIGGER IF NOT EXISTS preserve_call_results BEFORE UPDATE ON call_events WHEN NEW.user_id != OLD.user_id OR NEW.lead_id != OLD.lead_id OR NEW.result != OLD.result OR NEW.source != OLD.source OR NEW.created_at != OLD.created_at OR NEW.script_id IS NOT OLD.script_id OR NEW.caller_number IS NOT OLD.caller_number BEGIN SELECT RAISE(ABORT,'Call history cannot be overwritten'); END;
    CREATE TABLE IF NOT EXISTS login_attempts(key TEXT PRIMARY KEY,count INTEGER,last REAL);
    ''')

def password_hash(password):
    salt = secrets.token_hex(16)
    return salt + ':' + hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()

def verify(password, stored):
    salt, digest = stored.split(':')
    return hmac.compare_digest(digest, hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex())

def user(request):
    with connect() as db:
        row = db.execute('SELECT users.* FROM sessions JOIN users ON users.id=sessions.user_id WHERE token=? AND expires>?', (request.cookies.get('session',''),time.time())).fetchone()
        if row: return dict(row)
        if PRACTICE:
            return dict(db.execute('SELECT * FROM users LIMIT 1').fetchone())
    raise HTTPException(401,'Please sign in')

def session_response(uid):
    token=secrets.token_urlsafe(32)
    with connect() as db:
        db.execute('INSERT INTO sessions VALUES(?,?,?)',(token,uid,time.time()+86400))
    response=JSONResponse({'ok':True})
    response.set_cookie('session',token,httponly=True,secure=ONLINE,samesite='strict',max_age=86400)
    return response

@app.middleware('http')
async def guard(request, call_next):
    if request.method not in ('GET','HEAD','OPTIONS') and request.url.path not in ('/api/twilio/voice','/api/twilio/status','/api/twilio/transcription','/api/twilio/stream-status'):
        origin=request.headers.get('origin')
        expected=os.getenv('PUBLIC_BASE_URL','') if ONLINE and os.getenv('PUBLIC_BASE_URL') else str(request.base_url).rstrip('/')
        def origin_key(value):
            try:
                parsed=urlsplit(value);return (parsed.scheme,parsed.hostname,parsed.port or (443 if parsed.scheme=='https' else 80)) if parsed.scheme in ('http','https') and parsed.hostname and not parsed.username and not parsed.password else None
            except ValueError:return None
        if origin and (not origin_key(origin) or origin_key(origin)!=origin_key(expected)):
            return JSONResponse({'detail':'Request origin does not match'},status_code=403)
        if request.headers.get('content-type','').split(';')[0] != 'application/json':
            return JSONResponse({'detail':'JSON required'},status_code=415)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['Cache-Control']='no-store'
    return response

class Credentials(BaseModel):
    email: str
    password: str
    token: str = ''

@app.get('/api/status')
def status():
    with connect() as db: initialized=db.execute('SELECT COUNT(*) FROM users').fetchone()[0]>0
    return {'initialized':initialized,'practice':PRACTICE,'online':ONLINE}

@app.post('/api/register')
def register(data:Credentials):
    if len(data.password)<12 or '@' not in data.email: raise HTTPException(400,'Use an email address and a password of at least 12 characters')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        first=db.execute('SELECT COUNT(*) FROM users').fetchone()[0]==0
        if first and ONLINE and not hmac.compare_digest(data.token,os.getenv('SETUP_TOKEN','')):
            raise HTTPException(403,'Enter the setup token from your host settings')
        if first and ONLINE and not os.getenv('SETUP_TOKEN'): raise HTTPException(503,'Set SETUP_TOKEN in host settings first')
        if not first:
            invite=db.execute('SELECT * FROM invites WHERE token=? AND used=0 AND revoked=0 AND expires>?',(hashlib.sha256(data.token.encode()).hexdigest(),time.time())).fetchone()
            if not invite: raise HTTPException(403,'Invitation is invalid, expired, or already used')
        try: uid=db.execute('INSERT INTO users(email,password,admin) VALUES(?,?,?)',(data.email.strip().lower(),password_hash(data.password),int(first))).lastrowid
        except sqlite3.IntegrityError: raise HTTPException(400,'This email already has an account')
        if not first: db.execute('UPDATE invites SET used=1,used_by=? WHERE token=?',(uid,invite['token']))
    return session_response(uid)

@app.post('/api/login')
def login(data:Credentials,request:Request):
    key=(request.client.host if request.client else '')+':'+data.email.lower()
    with connect() as db:
        attempt=db.execute('SELECT * FROM login_attempts WHERE key=?',(key,)).fetchone()
        if attempt and attempt['count']>=10 and time.time()-attempt['last']<900: raise HTTPException(429,'Wait 15 minutes before trying again')
        row=db.execute('SELECT * FROM users WHERE email=?',(data.email.strip().lower(),)).fetchone()
        if not row or not verify(data.password,row['password']):
            count=attempt['count']+1 if attempt and time.time()-attempt['last']<900 else 1
            db.execute('INSERT OR REPLACE INTO login_attempts VALUES(?,?,?)',(key,count,time.time()))
            db.commit()
            raise HTTPException(401,'Email or password is incorrect')
        db.execute('DELETE FROM login_attempts WHERE key=?',(key,))
    return session_response(row['id'])

@app.post('/api/logout')
def logout(request:Request):
    with connect() as db: db.execute('DELETE FROM sessions WHERE token=?',(request.cookies.get('session',''),))
    response=JSONResponse({'ok':True});response.delete_cookie('session');return response

@app.get('/api/me')
def me(request:Request):
    u=user(request);return {'email':u['email'],'display_name':u['display_name'],'avatar':u['avatar'],'admin':bool(u['admin']),'onboarding_done':bool(u['onboarding_done']),'practice':PRACTICE}

@app.post('/api/invites')
def invite(request:Request):
    if not user(request)['admin']: raise HTTPException(403,'Only the administrator can invite people')
    if PRACTICE: raise HTTPException(400,'Invitations are disabled in throwaway practice mode')
    token=secrets.token_urlsafe(32)
    with connect() as db: db.execute('INSERT INTO invites(token,expires,used,created_by,created_at) VALUES(?,?,0,?,?)',(hashlib.sha256(token.encode()).hexdigest(),time.time()+7*86400,user(request)['id'],time.time()))
    return {'path':'/#invite='+token,'expires':'7 days'}

@app.get('/api/leads')
def leads(request:Request):
    from leads_service import list_for
    return list_for(connect,user(request)['id'])

@app.get('/api/events')
def events(request:Request):
    u=user(request)
    with connect() as db: return [dict(row) for row in db.execute('SELECT * FROM call_events WHERE user_id=? ORDER BY id DESC',(u['id'],))]

@app.post('/api/events/{event_id}/ignore')
def ignore(event_id:int,request:Request):
    u=user(request)
    with connect() as db:
        if db.execute('UPDATE call_events SET ignored=1 WHERE id=? AND user_id=?',(event_id,u['id'])).rowcount!=1:raise HTTPException(404,'Result not found')
    return {'ok':True}

@app.get('/api/backup')
def backup(request:Request):
    if not user(request)['admin']:raise HTTPException(403,'Whole-database backup is administrator-only because it contains every account')
    from starlette.background import BackgroundTask
    handle,path=tempfile.mkstemp(suffix='.sqlite3');os.close(handle)
    with connect() as src,closing(sqlite3.connect(path)) as dst:src.backup(dst)
    return FileResponse(path,filename='dashboard-backup.sqlite3',background=BackgroundTask(os.unlink,path))

from leads_service import install
install(app,connect,user,PRACTICE)
from scraper_service import install as install_scraper
install_scraper(app,connect,user,PRACTICE)
from dialer_service import install as install_dialer
install_dialer(app,connect,user,PRACTICE)
from analytics_service import install as install_analytics
install_analytics(app,connect,user)
from scripts_service import install as install_scripts
install_scripts(app,connect,user)
from account_service import install as install_account
install_account(app,connect,user,PRACTICE,password_hash,verify)
from call_assistant_service import install as install_assistant
install_assistant(app,connect,user,PRACTICE,app.state.verified_twilio_form)
from crm_service import install as install_crm
install_crm(app,connect,user)
from email_service import install as install_email
install_email(app,connect,user,PRACTICE)
from operations_service import install as install_operations
install_operations(app,connect,user,PRACTICE,DB)
from integrations_service import install as install_integrations
install_integrations(app,connect,user,PRACTICE,verify,DB)
from shared_services import install as install_shared
install_shared(app,connect,user,PRACTICE,verify,DB)
from research_service import install as install_research
install_research(app,connect,user,PRACTICE)
from coaching_service import install as install_coaching
install_coaching(app,connect,user,PRACTICE)
from deepgram_service import install as install_deepgram
install_deepgram(app,connect,user,PRACTICE,app.state.verified_twilio_form)


if PRACTICE:
    with connect() as db:
        uid=db.execute('INSERT INTO users(email,password,admin) VALUES(?,?,1)',('practice@example.test',password_hash(secrets.token_urlsafe(32)))).lastrowid
        bid=db.execute('INSERT INTO batches(user_id,name) VALUES(?,?)',(uid,'Made-up practice businesses')).lastrowid
        from datetime import datetime, timezone, timedelta
        practice_names=['Maple Street Plumbing','Harbor Electric','Oak & Pine Landscaping','Summit Roofing']
        categories=['plumber','electrician','landscaper','roofer']
        for index,name in enumerate(practice_names):
            source='https://example.test/business/'+str(index+1)
            facts={'website_need':['no_website','unknown','usability','strong'][index], 'website_source':source,'website_checked_at':datetime.now(timezone.utc).isoformat(),'website_note':['Fictional example: dedicated website absence confirmed across business profiles.','Website opportunity needs checking.','Fictional example: mobile navigation covers the contact button.','Fictional example: functional website with no identified improvement opportunity.'][index],'independent':True,'independence_source':source,'activity':[{'date':(datetime.now(timezone.utc)-timedelta(days=7+index*10)).isoformat(),'kind':'project post','source':source}], 'owner_name':'Alex Rivera' if index==0 else '', 'owner_access':'identified' if index==0 else 'unknown', 'owner_source':source}
            lead_id=db.execute('INSERT INTO leads(user_id,batch_id,name,phone,category,city,source_url,website_url,facts) VALUES(?,?,?,?,?,?,?,?,?)',(uid,bid,name,'(951) 555-01'+str(index).zfill(2),categories[index],['Menifee','Jacksonville','Colorado Springs','Menifee'][index],source,'' if index==0 else source,json.dumps(facts))).lastrowid
            db.execute('INSERT INTO lead_batches VALUES(?,?)',(lead_id,bid))

app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
@app.get('/')
def home():return FileResponse(ROOT/'static'/'index.html')
