import json,os,threading,secrets
from pathlib import Path
_load=lambda uid=None,namespace='twilio':{}
_is_admin=lambda uid:False
_lock=threading.RLock()

def saved_config(uid=None,namespace='twilio'):
    with _lock:return _load(uid,namespace)

def effective_config(uid=None):
    stored=saved_config(uid);keys=['TWILIO_ACCOUNT_SID','TWILIO_AUTH_TOKEN','TWILIO_API_KEY_SID','TWILIO_API_KEY_SECRET','TWILIO_TWIML_APP_SID','PUBLIC_BASE_URL','TWILIO_NUMBERS_JSON','TWILIO_ENABLED']
    return {key:(os.getenv(key) if uid is None or _is_admin(uid) or key=='PUBLIC_BASE_URL' else '') or stored.get(key,'') for key in keys}

def install_store(connect,db_path):
    global _load,_is_admin
    key_path=Path(db_path).parent/'integration.key'
    with connect() as db:db.execute('CREATE TABLE IF NOT EXISTS integration_configs(name TEXT PRIMARY KEY,payload BLOB NOT NULL)')
    def cipher(create=False):
        from cryptography.fernet import Fernet
        configured=os.getenv('INTEGRATION_ENCRYPTION_KEY','')
        if configured:return Fernet(configured.encode())
        if not key_path.exists():
            if not create:raise ValueError('Encryption key is missing')
            key_path.parent.mkdir(parents=True,exist_ok=True)
            try:
                with key_path.open('xb') as target:target.write(Fernet.generate_key())
                try:key_path.chmod(0o600)
                except OSError:pass
            except FileExistsError:pass
        return Fernet(key_path.read_bytes())
    def is_admin(uid):
        with connect() as db:
            row=db.execute('SELECT admin FROM users WHERE id=?',(uid,)).fetchone()
        return bool(row and row['admin'])
    _is_admin=is_admin
    def name(uid,namespace):return namespace if uid is None or is_admin(uid) else namespace+':user:'+str(int(uid))
    def read(uid=None,namespace='twilio'):
        with connect() as db:row=db.execute("SELECT payload FROM integration_configs WHERE name=?",(name(uid,namespace),)).fetchone()
        if not row:return {}
        try:return json.loads(cipher().decrypt(row['payload']))
        except Exception:raise ValueError('Saved integration cannot be decrypted. Restore its encryption key or configure a new connection.')
    _load=read
    def write(value,uid=None,namespace='twilio'):
        with _lock:
            encrypted=cipher(True).encrypt(json.dumps(value).encode())
            with connect() as db:db.execute("INSERT OR REPLACE INTO integration_configs VALUES(?,?)",(name(uid,namespace),encrypted))
    return write
