import base64,binascii,hashlib,hmac,io,json,secrets,time,sqlite3
from PIL import Image,ImageOps,UnidentifiedImageError
from fastapi import Request,HTTPException
from pydantic import BaseModel,Field

class Profile(BaseModel):display_name:str=Field(max_length=80)
class Photo(BaseModel):data:str=Field(max_length=1500000)
class Security(BaseModel):current_password:str=Field(max_length=1024);email:str='';new_password:str=Field(default='',max_length=1024)
class Recovery(BaseModel):user_id:int;current_password:str=Field(max_length=1024)
class Reset(BaseModel):token:str=Field(max_length=200);password:str=Field(min_length=12,max_length=1024)

def install(app,connect,user,practice,password_hash,verify):
    with connect() as db:
        cols={r[1] for r in db.execute('PRAGMA table_info(users)')}
        for name,definition in [('display_name',"TEXT NOT NULL DEFAULT ''"),('avatar',"TEXT NOT NULL DEFAULT ''"),('onboarding_done','INTEGER NOT NULL DEFAULT 0')]:
            if name not in cols:db.execute(f'ALTER TABLE users ADD COLUMN {name} {definition}')
        cols={r[1] for r in db.execute('PRAGMA table_info(invites)')}
        for name,definition in [('created_by','INTEGER'),('created_at','REAL'),('used_by','INTEGER'),('revoked','INTEGER NOT NULL DEFAULT 0')]:
            if name not in cols:db.execute(f'ALTER TABLE invites ADD COLUMN {name} {definition}')
        db.executescript('CREATE TABLE IF NOT EXISTS password_resets(token TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),expires REAL NOT NULL,used INTEGER NOT NULL DEFAULT 0);')

    def secure(u,password):
        if practice:raise HTTPException(400,'Security changes are disabled in temporary practice mode')
        if not verify(password,u['password']):raise HTTPException(403,'Current password is incorrect')
    def admin(request):
        u=user(request)
        if not u['admin']:raise HTTPException(403,'Administrator access required')
        if practice:raise HTTPException(400,'Team management is disabled in practice mode')
        return u

    @app.get('/api/account')
    def account(request:Request):
        u=user(request)
        with connect() as db:
            sessions=[{'id':hashlib.sha256(r['token'].encode()).hexdigest(),'expires':r['expires'],'current':r['token']==request.cookies.get('session')} for r in db.execute('SELECT * FROM sessions WHERE user_id=? AND expires>?',(u['id'],time.time()))]
        return {'display_name':u['display_name'],'email':u['email'],'avatar':u['avatar'],'admin':bool(u['admin']),'practice':practice,'sessions':sessions}

    @app.post('/api/account/onboarding/complete')
    def complete_onboarding(request:Request):
        with connect() as db:db.execute('UPDATE users SET onboarding_done=1 WHERE id=?',(user(request)['id'],))
        return {'ok':True}

    @app.post('/api/account/profile')
    def profile(data:Profile,request:Request):
        with connect() as db:db.execute('UPDATE users SET display_name=? WHERE id=?',(data.display_name.strip(),user(request)['id']))
        return {'ok':True}

    @app.post('/api/account/photo')
    def photo(data:Photo,request:Request):
        uid=user(request)['id']
        if not data.data:
            with connect() as db:db.execute("UPDATE users SET avatar='' WHERE id=?",(uid,))
            return {'ok':True}
        if not data.data.startswith(('data:image/png;base64,','data:image/jpeg;base64,','data:image/webp;base64,')):raise HTTPException(400,'Use a PNG, JPEG, or WebP image')
        try:
            raw=base64.b64decode(data.data.split(',',1)[1],validate=True)
            if len(raw)>1024*1024:raise ValueError()
            with Image.open(io.BytesIO(raw)) as img:
                if img.format not in ('PNG','JPEG','WEBP') or img.width*img.height>16000000:raise ValueError()
                img.load();normal=ImageOps.fit(ImageOps.exif_transpose(img).convert('RGB'),(256,256));out=io.BytesIO();normal.save(out,format='JPEG',quality=85)
            avatar='data:image/jpeg;base64,'+base64.b64encode(out.getvalue()).decode()
        except (ValueError,binascii.Error,UnidentifiedImageError,OSError,Image.DecompressionBombError):raise HTTPException(400,'Upload a valid image under 1 MB and 16 megapixels')
        with connect() as db:db.execute('UPDATE users SET avatar=? WHERE id=?',(avatar,uid))
        return {'ok':True}

    @app.post('/api/account/security')
    def security(data:Security,request:Request):
        u=user(request);secure(u,data.current_password);email=data.email.strip().lower() or u['email']
        if len(email)>254 or '@' not in email or any(c.isspace() for c in email):raise HTTPException(400,'Enter a valid email address')
        if data.new_password and len(data.new_password)<12:raise HTTPException(400,'Use a password of at least 12 characters')
        with connect() as db:
            try:db.execute('UPDATE users SET email=?,password=? WHERE id=?',(email,password_hash(data.new_password) if data.new_password else u['password'],u['id']))
            except sqlite3.IntegrityError:raise HTTPException(409,'This email already has an account')
            if email!=u['email']:
                db.execute('UPDATE users SET email_verified=0 WHERE id=?',(u['id'],));db.execute('UPDATE email_verifications SET used=1 WHERE user_id=?',(u['id'],))
            db.execute('DELETE FROM sessions WHERE user_id=? AND token!=?',(u['id'],request.cookies.get('session','')))
            db.execute('UPDATE password_resets SET used=1 WHERE user_id=?',(u['id'],));db.execute('UPDATE recovery_codes SET used=1 WHERE user_id=?',(u['id'],))
        return {'ok':True}

    @app.post('/api/account/sessions/revoke-others')
    def revoke_others(request:Request):
        u=user(request)
        if practice:raise HTTPException(400,'Practice has no persistent sessions')
        with connect() as db:db.execute('DELETE FROM sessions WHERE user_id=? AND token!=?',(u['id'],request.cookies.get('session','')))
        return {'ok':True}

    @app.get('/api/team')
    def team(request:Request):
        admin(request)
        with connect() as db:
            invites=[{'id':r['token'],'created_at':r['created_at'],'expires':r['expires'],'status':'revoked' if r['revoked'] else 'used' if r['used'] else 'expired' if r['expires']<=time.time() else 'active'} for r in db.execute('SELECT * FROM invites ORDER BY expires DESC')]
            members=[dict(r) for r in db.execute('SELECT id,email,display_name,admin FROM users ORDER BY id')]
        return {'invites':invites,'members':members}

    @app.post('/api/team/invites/{identifier}/revoke')
    def revoke_invite(identifier:str,request:Request):
        admin(request)
        with connect() as db:
            if db.execute('UPDATE invites SET revoked=1 WHERE token=? AND used=0',(identifier,)).rowcount!=1:raise HTTPException(404,'Unused invitation not found')
        return {'ok':True}

    @app.post('/api/team/unlock')
    def unlock(data:Recovery,request:Request):
        u=admin(request);secure(u,data.current_password)
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            target=db.execute('SELECT email FROM users WHERE id=?',(data.user_id,)).fetchone()
            if not target:raise HTTPException(404,'Account not found')
            suffix=':'+target['email'].strip().lower()
            keys=[r['key'] for r in db.execute('SELECT key FROM login_attempts') if r['key'].endswith(suffix)]
            db.executemany('DELETE FROM login_attempts WHERE key=?',[(key,) for key in keys])
        return {'ok':True,'cleared':len(keys),'email':target['email'],'message':'Sign-in lockout cleared. The account can try its current password again.'}

    @app.post('/api/team/recovery')
    def recovery(data:Recovery,request:Request):
        u=admin(request);secure(u,data.current_password);token=secrets.token_urlsafe(32)
        with connect() as db:
            if not db.execute('SELECT id FROM users WHERE id=?',(data.user_id,)).fetchone():raise HTTPException(404,'Account not found')
            db.execute('UPDATE password_resets SET used=1 WHERE user_id=?',(data.user_id,))
            db.execute('INSERT INTO password_resets VALUES(?,?,?,0)',(hashlib.sha256(token.encode()).hexdigest(),data.user_id,time.time()+1800))
        return {'path':'/#reset='+token,'expires':'30 minutes'}

    @app.post('/api/account/reset')
    def reset(data:Reset):
        if practice:raise HTTPException(400,'Recovery is disabled in practice mode')
        with connect() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM password_resets WHERE token=? AND used=0 AND expires>?',(hashlib.sha256(data.token.encode()).hexdigest(),time.time())).fetchone()
            if not row:raise HTTPException(403,'Recovery link is invalid, expired, or already used')
            db.execute('UPDATE users SET password=? WHERE id=?',(password_hash(data.password),row['user_id']));db.execute('DELETE FROM sessions WHERE user_id=?',(row['user_id'],));db.execute('UPDATE password_resets SET used=1 WHERE user_id=?',(row['user_id'],));db.execute('UPDATE recovery_codes SET used=1 WHERE user_id=?',(row['user_id'],))
        return {'ok':True}

