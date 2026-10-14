import hmac,hashlib,json,os,secrets,time,urllib.request,urllib.error
from urllib.parse import urlsplit
from fastapi import Request,HTTPException
from pydantic import BaseModel,Field

def email_settings():
    from shared_services import config,email_fingerprint
    cfg=config()
    verified=cfg.get('_email_verified')==email_fingerprint(cfg)
    return {'enabled':os.getenv('EMAIL_ENABLED')=='1' if os.getenv('EMAIL_ENABLED') is not None else bool(cfg.get('email_enabled') and verified),
            'key':os.getenv('RESEND_API_KEY') or cfg.get('resend_key',''),'from':os.getenv('EMAIL_FROM') or cfg.get('email_from','')}
def email_ready():
    cfg=email_settings();base=urlsplit(os.getenv('PUBLIC_BASE_URL',''));return cfg['enabled'] and bool(cfg['key']) and '@' in cfg['from'] and base.scheme=='https' and bool(base.hostname)
def send(to,subject,text):
    # Reserve before sending; failures also count conservatively against free-tier limits.
    from shared_services import CONNECT
    if CONNECT is not None:
        from datetime import datetime,timezone
        day=datetime.now(timezone.utc).strftime('%Y-%m-%d');period=day[:7]
        with CONNECT() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS email_delivery_usage(id TEXT PRIMARY KEY,day TEXT,period TEXT)')
            if db.execute('SELECT COUNT(*) FROM email_delivery_usage WHERE day=?',(day,)).fetchone()[0]>=100 or db.execute('SELECT COUNT(*) FROM email_delivery_usage WHERE period=?',(period,)).fetchone()[0]>=3000:raise HTTPException(429,'Account email allowance reached. Ask your administrator for a private recovery link.')
            db.execute('INSERT INTO email_delivery_usage VALUES(?,?,?)',(secrets.token_hex(16),day,period))
    cfg=email_settings();body=json.dumps({'from':cfg['from'],'to':[to],'subject':subject,'text':text}).encode()
    request=urllib.request.Request('https://api.resend.com/emails',data=body,headers={'Authorization':'Bearer '+cfg['key'],'Content-Type':'application/json','User-Agent':'Brassline/1.0'},method='POST')
    try:
        from research_service import NoRedirect
        with urllib.request.build_opener(NoRedirect).open(request,timeout=15) as response:return json.loads(response.read(10000))
    except (OSError,ValueError,urllib.error.HTTPError):raise HTTPException(502,'Email could not be sent. Try again or ask your administrator for a recovery link.')
class Email(BaseModel):email:str=Field(max_length=254)
class Verify(BaseModel):token:str=Field(max_length=200)
class ResetCode(BaseModel):
    challenge:str=Field(min_length=20,max_length=100)
    code:str=Field(pattern=r'^\d{6}$',max_length=6)

def install(app,connect,user,practice):
    with connect() as db:
        columns={r[1] for r in db.execute('PRAGMA table_info(users)')}
        if 'email_verified' not in columns:db.execute('ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0')
        db.executescript('CREATE TABLE IF NOT EXISTS email_verifications(token TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),email TEXT NOT NULL,expires REAL NOT NULL,used INTEGER NOT NULL DEFAULT 0);CREATE TABLE IF NOT EXISTS email_requests(request_key TEXT PRIMARY KEY,last REAL NOT NULL);')
    with connect() as db:
        db.executescript('CREATE TABLE IF NOT EXISTS recovery_codes(challenge TEXT PRIMARY KEY,user_id INTEGER REFERENCES users(id),email_hash TEXT NOT NULL,code_hash TEXT NOT NULL,expires REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,used INTEGER NOT NULL DEFAULT 0);CREATE TABLE IF NOT EXISTS recovery_guesses(ip_hash TEXT PRIMARY KEY,started REAL NOT NULL,count INTEGER NOT NULL);')
    def throttle(db,key):
        row=db.execute('SELECT last FROM email_requests WHERE request_key=?',(key,)).fetchone()
        if row and time.time()-row['last']<60:raise HTTPException(429,'Wait one minute before requesting another email')
        db.execute('INSERT OR REPLACE INTO email_requests VALUES(?,?)',(key,time.time()))
    @app.get('/api/account/email-status')
    def status(request:Request):u=user(request);return {'available':email_ready() and not practice,'verified':bool(u['email_verified'])}
    @app.post('/api/account/request-reset')
    def reset(data:Email,request:Request):
        if not email_ready() or practice:raise HTTPException(503,'Email delivery is not connected. Ask your administrator for a recovery link.')
        email=data.email.strip().lower();challenge=secrets.token_urlsafe(32);code=f'{secrets.randbelow(1000000):06d}';email_hash=hashlib.sha256(email.encode()).hexdigest()
        code_hash=hashlib.scrypt(code.encode(),salt=challenge.encode(),n=16384,r=8,p=1).hex()
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            throttle(db,'reset-ip:'+(request.client.host if request.client else 'unknown'));throttle(db,'reset-email:'+email_hash)
            row=db.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone()
            db.execute('UPDATE recovery_codes SET used=1 WHERE email_hash=?',(email_hash,))
            db.execute('DELETE FROM recovery_codes WHERE expires<?',(time.time()-86400,))
            db.execute('INSERT INTO recovery_codes(challenge,user_id,email_hash,code_hash,expires) VALUES(?,?,?,?,?)',(challenge,row['id'] if row else None,email_hash,code_hash,time.time()+600))
        if row:
            try:send(email,'Your Brassline password reset code','Your six-digit code is: '+code+'\nEnter it in Brassline within 10 minutes. This code works once.\nIf you did not request this, ignore this email.')
            except HTTPException:
                with connect() as db:db.execute('UPDATE recovery_codes SET used=1 WHERE challenge=?',(challenge,))
        return {'ok':True,'challenge':challenge,'expires_in':600,'message':'If that address has an account, a six-digit code will be emailed. Check your inbox and spam folder.'}
    @app.post('/api/account/verify-reset-code')
    def verify_reset_code(data:ResetCode,request:Request):
        if practice:raise HTTPException(400,'Recovery is disabled in practice mode')
        failure=None;token=None;now=time.time();ip_hash=hashlib.sha256((request.client.host if request.client else 'unknown').encode()).hexdigest()
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            guess=db.execute('SELECT * FROM recovery_guesses WHERE ip_hash=?',(ip_hash,)).fetchone()
            if guess and now-guess['started']<60 and guess['count']>=30:failure=(429,'Too many code checks. Wait one minute before trying again.')
            else:
                if guess and now-guess['started']<60:db.execute('UPDATE recovery_guesses SET count=count+1 WHERE ip_hash=?',(ip_hash,))
                else:db.execute('INSERT OR REPLACE INTO recovery_guesses VALUES(?,?,1)',(ip_hash,now))
                row=db.execute('SELECT * FROM recovery_codes WHERE challenge=?',(data.challenge,)).fetchone()
                if not row or row['used'] or row['expires']<=now or row['attempts']>=5:failure=(403,'Code is invalid, expired, or already used. Request a new code.')
                else:
                    actual=hashlib.scrypt(data.code.encode(),salt=data.challenge.encode(),n=16384,r=8,p=1).hex()
                    if not row['user_id'] or not hmac.compare_digest(actual,row['code_hash']):
                        db.execute('UPDATE recovery_codes SET attempts=attempts+1,used=CASE WHEN attempts+1>=5 THEN 1 ELSE used END WHERE challenge=?',(data.challenge,))
                        failure=(403,'Code is incorrect. After five incorrect attempts, request a new code.')
                    else:
                        db.execute('UPDATE recovery_codes SET used=1 WHERE challenge=?',(data.challenge,))
                        token=secrets.token_urlsafe(32)
                        db.execute('INSERT INTO password_resets VALUES(?,?,?,0)',(hashlib.sha256(token.encode()).hexdigest(),row['user_id'],now+300))
        if failure:raise HTTPException(*failure)
        return {'ok':True,'token':token,'expires_in':300}
    @app.post('/api/account/request-verification')
    def request_verification(request:Request):
        u=user(request)
        if not email_ready() or practice:raise HTTPException(503,'Email delivery is not connected yet')
        token=secrets.token_urlsafe(32);hashed=hashlib.sha256(token.encode()).hexdigest()
        with connect() as db:
            throttle(db,'verify:'+str(u['id']));db.execute('INSERT INTO email_verifications VALUES(?,?,?,?,0)',(hashed,u['id'],u['email'],time.time()+86400))
        try:send(u['email'],'Verify your Brassline email','Verify your email within 24 hours: '+os.getenv('PUBLIC_BASE_URL').rstrip('/')+'/#verify='+token)
        except HTTPException:
            with connect() as db:db.execute('UPDATE email_verifications SET used=1 WHERE token=?',(hashed,))
            raise
        return {'ok':True}
    @app.post('/api/account/verify-email')
    def verify(data:Verify):
        with connect() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM email_verifications WHERE token=? AND used=0 AND expires>?',(hashlib.sha256(data.token.encode()).hexdigest(),time.time())).fetchone()
            if not row:raise HTTPException(403,'Verification link is invalid, expired, or already used')
            if not db.execute('SELECT id FROM users WHERE id=? AND email=?',(row['user_id'],row['email'])).fetchone():raise HTTPException(409,'The account email has changed; request a new verification link')
            db.execute('UPDATE users SET email_verified=1 WHERE id=?',(row['user_id'],));db.execute('UPDATE email_verifications SET used=1 WHERE token=?',(row['token'],))
        return {'ok':True}
