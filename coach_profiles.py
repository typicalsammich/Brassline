"""Private, reusable coach profiles and immutable per-call snapshots."""
import json,re
from fastapi import HTTPException,Request
from pydantic import BaseModel,Field
class Profile(BaseModel):
    id:int=0
    name:str=Field(min_length=1,max_length=80)
    instructions:str=Field(default='',max_length=3000)
    script_id:int|None=None
class Selection(BaseModel):id:int=0

def snapshot(db,uid):
    row=db.execute('SELECT p.* FROM coach_profiles p JOIN coach_profile_preferences s ON s.profile_id=p.id WHERE s.user_id=? AND p.user_id=?',(uid,uid)).fetchone()
    result=dict(row) if row else {'name':'Default coach','instructions':'','script_id':None}
    if not result['script_id']:
        selected=db.execute('SELECT active_script FROM script_preferences WHERE user_id=?',(uid,)).fetchone();result['script_id']=selected['active_script'] if selected else None
    script=db.execute("SELECT id,title,body,version FROM scripts WHERE id=? AND user_id=? AND kind='call' AND archived=0 AND deleted=0",(result['script_id'],uid)).fetchone()
    result['script']=dict(script) if script else {};return result

def context(profile):
    if not profile:return ''
    script=profile.get('script') or {}
    return '\nSelected coach profile: '+profile['name']+'\nUser coaching preferences (subject to safety and factual accuracy): '+profile['instructions']+'\nCall script — follow its stages naturally, never read it all at once: '+re.sub('<[^>]*>',' ',script.get('body',''))[:6000]

def install(app,connect,user):
    with connect() as db:
        db.executescript('CREATE TABLE IF NOT EXISTS coach_profiles(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,name TEXT NOT NULL,instructions TEXT NOT NULL,script_id INTEGER);CREATE TABLE IF NOT EXISTS coach_profile_preferences(user_id INTEGER PRIMARY KEY,profile_id INTEGER);')
        if 'coach_profile_snapshot' not in {r[1] for r in db.execute('PRAGMA table_info(dial_attempts)')}:db.execute("ALTER TABLE dial_attempts ADD COLUMN coach_profile_snapshot TEXT NOT NULL DEFAULT '{}'")
    def owned(db,uid,pid):
        row=db.execute('SELECT * FROM coach_profiles WHERE id=? AND user_id=?',(pid,uid)).fetchone()
        if not row:raise HTTPException(404,'Coach profile not found')
        return row
    @app.get('/api/coach-profiles')
    def profiles(request:Request):
        uid=user(request)['id']
        with connect() as db:
            current=snapshot(db,uid)
            return {'profiles':[dict(r) for r in db.execute('SELECT * FROM coach_profiles WHERE user_id=? ORDER BY id',(uid,))],'active_id':current.get('id',0)}
    @app.post('/api/coach-profiles/save')
    def save(data:Profile,request:Request):
        uid=user(request)['id']
        if not data.name.strip():raise HTTPException(400,'Name the coach profile')
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if data.script_id and not db.execute("SELECT 1 FROM scripts WHERE id=? AND user_id=? AND kind='call' AND deleted=0 AND archived=0",(data.script_id,uid)).fetchone():raise HTTPException(400,'Choose one of your active call scripts')
            if data.id:
                owned(db,uid,data.id);pid=data.id
                db.execute('UPDATE coach_profiles SET name=?,instructions=?,script_id=? WHERE id=?',(data.name.strip(),data.instructions.strip(),data.script_id or None,pid))
            else:
                if db.execute('SELECT COUNT(*) FROM coach_profiles WHERE user_id=?',(uid,)).fetchone()[0]>=20:raise HTTPException(400,'You can save up to 20 coach profiles')
                pid=db.execute('INSERT INTO coach_profiles(user_id,name,instructions,script_id) VALUES(?,?,?,?)',(uid,data.name.strip(),data.instructions.strip(),data.script_id or None)).lastrowid
            db.execute('INSERT INTO coach_profile_preferences VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET profile_id=excluded.profile_id',(uid,pid))
        return {'id':pid}
    @app.post('/api/coach-profiles/select')
    def select(data:Selection,request:Request):
        uid=user(request)['id']
        with connect() as db:
            if data.id:owned(db,uid,data.id)
            db.execute('INSERT INTO coach_profile_preferences VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET profile_id=excluded.profile_id',(uid,data.id or None))
        return {'ok':True}
    @app.post('/api/coach-profiles/remove')
    def remove(data:Selection,request:Request):
        uid=user(request)['id']
        with connect() as db:
            owned(db,uid,data.id);db.execute('DELETE FROM coach_profiles WHERE id=?',(data.id,));db.execute('DELETE FROM coach_profile_preferences WHERE user_id=? AND profile_id=?',(uid,data.id))
        return {'ok':True}

