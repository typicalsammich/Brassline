import json,html
from html.parser import HTMLParser
from datetime import datetime,timezone
from fastapi import Request,HTTPException
from pydantic import BaseModel,Field

class CleanHTML(HTMLParser):
    allowed={'p','div','h2','h3','strong','b','em','i','br','ul','ol','li','mark','hr'}
    blocked={'script','style','iframe','object','svg','math','template'}
    def __init__(self):super().__init__(convert_charrefs=True);self.parts=[];self.depth=0;self.stack=[]
    def handle_starttag(self,tag,attrs):
        if tag in self.blocked:self.depth+=1;return
        if self.depth or tag not in self.allowed:return
        extra=''
        if tag=='mark':
            color=dict(attrs).get('data-color','yellow');extra=' data-color="'+(color if color in ('yellow','green','pink') else 'yellow')+'"'
        if tag=='hr':extra=' data-split="true"'
        self.parts.append('<'+tag+extra+'>')
        if tag not in ('br','hr'):self.stack.append(tag)
    def handle_endtag(self,tag):
        if tag in self.blocked:self.depth=max(0,self.depth-1);return
        if self.depth or tag not in self.stack:return
        while self.stack:
            previous=self.stack.pop();self.parts.append('</'+previous+'>')
            if previous==tag:break
    def handle_data(self,data):
        if not self.depth:self.parts.append(html.escape(data))
    def result(self):return ''.join(self.parts)+''.join('</'+t+'>' for t in reversed(self.stack))

def clean(value):p=CleanHTML();p.feed(value);return p.result()
class Script(BaseModel):
    title:str=Field(min_length=1,max_length=120)
    kind:str
    body:str=Field(max_length=50000)
class ReaderSettings(BaseModel):
    active_script:int|None=None
    objections:list[int]=Field(default_factory=list,max_length=12)
    font_size:int=Field(default=18,ge=14,le=28)
    theme:str='charcoal'

def install(app,connect,user):
    with connect() as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS scripts(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),title TEXT NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0,deleted INTEGER NOT NULL DEFAULT 0,version INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS script_preferences(user_id INTEGER PRIMARY KEY REFERENCES users(id),active_script INTEGER,objections TEXT NOT NULL DEFAULT '[]',font_size INTEGER NOT NULL DEFAULT 18,theme TEXT NOT NULL DEFAULT 'charcoal');''')
        cols={r[1] for r in db.execute('PRAGMA table_info(dial_attempts)')}
        if 'script_snapshot' not in cols:db.execute("ALTER TABLE dial_attempts ADD COLUMN script_snapshot TEXT NOT NULL DEFAULT '{}'")
        db.executescript("CREATE TRIGGER IF NOT EXISTS preserve_attempt_script BEFORE UPDATE ON dial_attempts WHEN NEW.script_id IS NOT OLD.script_id OR NEW.script_snapshot IS NOT OLD.script_snapshot BEGIN SELECT RAISE(ABORT,'The script used for a call cannot be changed'); END;")
    def owned(db,uid,identifier):
        row=db.execute('SELECT * FROM scripts WHERE id=? AND user_id=? AND deleted=0',(identifier,uid)).fetchone()
        if not row:raise HTTPException(404,'Script not found')
        return row
    def preferences(uid):
        with connect() as db:row=db.execute('SELECT * FROM script_preferences WHERE user_id=?',(uid,)).fetchone()
        return {'active_script':row['active_script'],'objections':json.loads(row['objections']),'font_size':row['font_size'],'theme':row['theme']} if row else {'active_script':None,'objections':[],'font_size':18,'theme':'charcoal'}
    def save(data,uid,identifier=None):
        if data.kind not in ('call','objection','text'):raise HTTPException(400,'Choose call, objection, or text')
        if not data.title.strip():raise HTTPException(400,'Give your script a title')
        body=clean(data.body)
        if not body.strip():raise HTTPException(400,'Add script content')
        with connect() as db:
            if identifier:
                row=owned(db,uid,identifier)
                if row['kind']!=data.kind and db.execute('SELECT active_script FROM script_preferences WHERE active_script=?',(identifier,)).fetchone():raise HTTPException(409,'Clear the in-use selection before changing script type')
                db.execute('UPDATE scripts SET title=?,kind=?,body=?,version=version+1,updated_at=? WHERE id=?',(data.title.strip(),data.kind,body,datetime.now(timezone.utc).isoformat(),identifier))
            else:identifier=db.execute('INSERT INTO scripts(user_id,title,kind,body,updated_at) VALUES(?,?,?,?,?)',(uid,data.title.strip(),data.kind,body,datetime.now(timezone.utc).isoformat())).lastrowid
        return {'ok':True,'id':identifier}

    @app.get('/api/scripts')
    def scripts(request:Request):
        uid=user(request)['id']
        with connect() as db:rows=[dict(r) for r in db.execute('SELECT * FROM scripts WHERE user_id=? AND deleted=0 ORDER BY updated_at DESC,id DESC',(uid,))]
        return {'scripts':rows,'preferences':preferences(uid)}
    @app.post('/api/scripts')
    def create(data:Script,request:Request):return save(data,user(request)['id'])
    @app.post('/api/scripts/preferences')
    def reader(data:ReaderSettings,request:Request):
        uid=user(request)['id']
        if data.theme not in ('charcoal','paper','contrast'):raise HTTPException(400,'Choose a reader theme')
        with connect() as db:
            if data.active_script:
                row=owned(db,uid,data.active_script)
                if row['kind']!='call' or row['archived']:raise HTTPException(400,'Select an active call script')
            for identifier in data.objections:
                row=owned(db,uid,identifier)
                if row['kind']!='objection' or row['archived']:raise HTTPException(400,'Select active objection answers')
            db.execute('INSERT OR REPLACE INTO script_preferences VALUES(?,?,?,?,?)',(uid,data.active_script,json.dumps(list(dict.fromkeys(data.objections))),data.font_size,data.theme))
        return {'ok':True}
    @app.post('/api/scripts/{identifier}/save')
    def update(identifier:int,data:Script,request:Request):return save(data,user(request)['id'],identifier)
    def set_state(identifier,request,deleted=False,restore=False):
        uid=user(request)['id']
        with connect() as db:
            owned(db,uid,identifier);db.execute('UPDATE scripts SET archived=?,deleted=? WHERE id=?',(int(not restore),int(deleted),identifier))
            if not restore:
                row=db.execute('SELECT * FROM script_preferences WHERE user_id=?',(uid,)).fetchone()
                if row:db.execute('UPDATE script_preferences SET active_script=?,objections=? WHERE user_id=?',(None if row['active_script']==identifier else row['active_script'],json.dumps([x for x in json.loads(row['objections']) if x!=identifier]),uid))
        return {'ok':True}
    @app.post('/api/scripts/{identifier}/archive')
    def archive(identifier:int,request:Request):return set_state(identifier,request)
    @app.post('/api/scripts/{identifier}/restore')
    def restore(identifier:int,request:Request):return set_state(identifier,request,restore=True)
    @app.post('/api/scripts/{identifier}/delete')
    def delete(identifier:int,request:Request):return set_state(identifier,request,deleted=True)

    @app.post('/api/scripts/starters')
    def starters(request:Request):
        uid=user(request)['id']
        examples=[('Permission-first website pitch','call','<h2>Open the conversation</h2><p>Hi, is this the owner of [Business]? I design websites for local businesses. Do you have thirty seconds for a quick question?</p><p>Before calling, replace this sentence with a specific website issue you verified. Avoid assuming that the business is losing customers.</p><h3>Understand the need</h3><p>How do most new customers find you today? Are you happy with how your website shows your work and makes it easy to contact you?</p><hr><h2>Offer a next step</h2><p>I build mobile-friendly websites that explain your services, show your projects and reviews, and make calls or quote requests easy.</p><p>Would a short conversation about what a useful website for [Business] could include be worthwhile?</p><p>If yes, agree on a time and confirm their preferred contact method. If no, thank them and respect their answer.</p>'),('I already have a website','objection','<p>That makes sense. Is there anything about the current site you wish worked better? If it already does everything you need, there may not be a reason to change it.</p>'),('Not a good time','objection','<p>No problem. Would you prefer a different time, or should I leave it here?</p>'),('What does it cost?','objection','<p>It depends on the pages and features you need. I would first understand the scope and give you a clear quote, so you can decide without pressure.</p>'),('Requested follow-up text','text','<p>Hi, this is [Your name]. Thanks for speaking with me about [Business]. As requested, here is my contact information: [Your contact]. Our agreed next step is [Next step].</p>')]
        ids=[]
        for title,kind,body in examples:
            with connect() as db:exists=db.execute('SELECT id FROM scripts WHERE user_id=? AND title=? AND deleted=0',(uid,title)).fetchone()
            ids.append(exists['id'] if exists else save(Script(title=title,kind=kind,body=body),uid)['id'])
        current=preferences(uid)
        if not current['active_script']:reader(ReaderSettings(active_script=ids[0],objections=ids[1:4],font_size=current['font_size'],theme=current['theme']),request)
        return {'ok':True}
