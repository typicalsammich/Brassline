import json
from datetime import datetime,timezone,timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal
from fastapi import Request,HTTPException
from pydantic import BaseModel,Field
from scoring import phone_key

from dispositions import STAGES,pipeline_stage
def now():return datetime.now(timezone.utc).isoformat()
def contact_keys(lead):
    from lead_identity import phones
    return ['phone:'+p for p in sorted(phones(lead['phone']))]+(['place:'+lead['place_id']] if lead['place_id'] else [])
def blocked(db,uid,lead):return any(db.execute('SELECT 1 FROM contact_blocks WHERE user_id=? AND contact_key=?',(uid,key)).fetchone() for key in contact_keys(lead))
def owned(db,uid,identifier):
    row=db.execute('SELECT * FROM leads WHERE id=? AND user_id=?',(identifier,uid)).fetchone()
    if not row:raise HTTPException(404,'Lead not found')
    return row
class Sales(BaseModel):
    stage:str='New'
    quoted:Decimal|None=Field(default=None,ge=0,le=10000000,max_digits=12,decimal_places=2)
    agreed:Decimal|None=Field(default=None,ge=0,le=10000000,max_digits=12,decimal_places=2)
class Note(BaseModel):text:str=Field(min_length=1,max_length=5000)
class Followup(BaseModel):
    due_at:str=Field(default='',max_length=60)
    local_time:str=Field(default='',max_length=40)
    time_zone:str=Field(default='America/New_York',max_length=80)
    note:str=Field(default='',max_length=2000)
class Block(BaseModel):blocked:bool;reason:str=Field(default='',max_length=500)
class Assign(BaseModel):user_id:int

def callback_due(data):
    try:
        if data.local_time:
            if data.time_zone!='America/New_York':raise ValueError()
            local=datetime.fromisoformat(data.local_time)
            if local.tzinfo is not None:raise ValueError()
            zone=ZoneInfo('America/New_York');due=local.replace(tzinfo=zone)
            if due.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)!=local:raise ValueError()
            if due.utcoffset()!=local.replace(tzinfo=zone,fold=1).utcoffset():raise ValueError()
        else:
            due=datetime.fromisoformat(data.due_at.replace('Z','+00:00'))
            if due.tzinfo is None:raise ValueError()
        return due.astimezone(timezone.utc).isoformat()
    except ValueError:raise HTTPException(400,'Choose a valid Eastern date/time. During daylight-saving clock changes, choose an unambiguous time.')

def install(app,connect,user):
    with connect() as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS lead_sales(user_id INTEGER NOT NULL REFERENCES users(id),lead_id INTEGER NOT NULL REFERENCES leads(id),stage TEXT NOT NULL DEFAULT 'New',quoted_cents INTEGER,agreed_cents INTEGER,do_not_contact INTEGER NOT NULL DEFAULT 0,assigned_out INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(user_id,lead_id));
        CREATE TABLE IF NOT EXISTS lead_notes(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),lead_id INTEGER NOT NULL REFERENCES leads(id),text TEXT NOT NULL,created_at TEXT NOT NULL,kind TEXT NOT NULL DEFAULT 'note');
        CREATE TABLE IF NOT EXISTS followups(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),lead_id INTEGER NOT NULL REFERENCES leads(id),due_at TEXT NOT NULL,note TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'open',created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS contact_blocks(user_id INTEGER NOT NULL REFERENCES users(id),contact_key TEXT NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(user_id,contact_key));
        CREATE TABLE IF NOT EXISTS lead_assignments(id INTEGER PRIMARY KEY,from_user INTEGER NOT NULL REFERENCES users(id),to_user INTEGER NOT NULL REFERENCES users(id),source_lead INTEGER NOT NULL REFERENCES leads(id),target_lead INTEGER NOT NULL REFERENCES leads(id),created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS followup_due_idx ON followups(user_id,state,due_at,lead_id);
        CREATE INDEX IF NOT EXISTS lead_note_idx ON lead_notes(user_id,lead_id,id);
        CREATE TRIGGER IF NOT EXISTS keep_lead_notes BEFORE UPDATE ON lead_notes BEGIN SELECT RAISE(ABORT,'Timeline entries cannot be overwritten'); END;''')
    with connect() as db:
        if 'stage_manual' not in {r[1] for r in db.execute('PRAGMA table_info(lead_sales)')}:db.execute('ALTER TABLE lead_sales ADD COLUMN stage_manual INTEGER NOT NULL DEFAULT 0')
    with connect() as db:db.execute('CREATE TABLE IF NOT EXISTS callback_reminder_claims(user_id INTEGER NOT NULL,followup_id INTEGER NOT NULL,due_at TEXT NOT NULL,PRIMARY KEY(user_id,followup_id,due_at))')
    def log(db,uid,lead,text,kind='note'):db.execute('INSERT INTO lead_notes(user_id,lead_id,text,created_at,kind) VALUES(?,?,?,?,?)',(uid,lead,text,now(),kind))
    def initialize(db,uid,lead):db.execute('INSERT OR IGNORE INTO lead_sales(user_id,lead_id) VALUES(?,?)',(uid,lead))

    @app.get('/api/crm')
    def dashboard(request:Request):
        from leads_service import list_for
        uid=user(request)['id']
        with connect() as db:
            rows=[dict(r) for r in db.execute("SELECT f.*,l.name FROM followups f JOIN leads l ON l.id=f.lead_id AND l.user_id=f.user_id WHERE f.user_id=? ORDER BY f.due_at",(uid,))]
        return {'leads':list_for(connect,uid),'followups':rows,'stages':STAGES}

    @app.get('/api/crm/leads/{identifier}')
    def detail(identifier:int,request:Request):
        uid=user(request)['id']
        with connect() as db:
            lead=owned(db,uid,identifier);initialize(db,uid,identifier);sales=dict(db.execute('SELECT * FROM lead_sales WHERE user_id=? AND lead_id=?',(uid,identifier)).fetchone());sales['do_not_contact']=bool(sales['do_not_contact'] or blocked(db,uid,lead))
            timeline=[dict(r) for r in db.execute('SELECT * FROM lead_notes WHERE user_id=? AND lead_id=? ORDER BY id DESC',(uid,identifier))]
            calls=[{'id':r['id'],'kind':'call','text':r['result']+(' (ignored)' if r['ignored'] else '')+' · '+r['source'],'created_at':r['created_at']} for r in db.execute('SELECT * FROM call_events WHERE user_id=? AND lead_id=?',(uid,identifier))]
            followups=[dict(r) for r in db.execute('SELECT * FROM followups WHERE user_id=? AND lead_id=? ORDER BY due_at',(uid,identifier))]
            attempts=[{'id':r['id'],'started_at':r['started_at'],'mode':r['mode'],'transcription_enabled':r['transcription_enabled']} for r in db.execute('SELECT * FROM dial_attempts WHERE user_id=? AND lead_id=? ORDER BY started_at DESC',(uid,identifier))]
        with connect() as db:
            event=db.execute('SELECT result FROM call_events WHERE user_id=? AND lead_id=? AND ignored=0 ORDER BY id DESC LIMIT 1',(uid,identifier)).fetchone()
            sales['stage']=pipeline_stage(sales,event['result'] if event else '')
        return {'stages':STAGES,'name':lead['name'],'sales':sales,'timeline':sorted(timeline+calls,key=lambda r:r['created_at'].replace(' ','T'),reverse=True),'followups':followups,'attempts':attempts}

    @app.post('/api/crm/leads/{identifier}/sales')
    def sales(identifier:int,data:Sales,request:Request):
        uid=user(request)['id']
        if data.stage not in STAGES:raise HTTPException(400,'Choose a sales stage')
        with connect() as db:
            owned(db,uid,identifier);initialize(db,uid,identifier)
            db.execute('UPDATE lead_sales SET stage_manual=1,stage=?,quoted_cents=?,agreed_cents=? WHERE user_id=? AND lead_id=?',(data.stage,int(data.quoted*100) if data.quoted is not None else None,int(data.agreed*100) if data.agreed is not None else None,uid,identifier));log(db,uid,identifier,'Sales stage: '+data.stage,'sales')
        return {'ok':True}

    @app.post('/api/crm/leads/{identifier}/notes')
    def note(identifier:int,data:Note,request:Request):
        uid=user(request)['id']
        if not data.text.strip():raise HTTPException(400,'Write a note first')
        with connect() as db:owned(db,uid,identifier);log(db,uid,identifier,data.text.strip())
        return {'ok':True}

    @app.post('/api/crm/leads/{identifier}/followups')
    def schedule(identifier:int,data:Followup,request:Request):
        uid=user(request)['id']
        due=callback_due(data)
        with connect() as db:
            lead=owned(db,uid,identifier);initialize(db,uid,identifier)
            meta=db.execute('SELECT * FROM lead_sales WHERE user_id=? AND lead_id=?',(uid,identifier)).fetchone()
            if meta['do_not_contact'] or meta['assigned_out'] or blocked(db,uid,lead):raise HTTPException(409,'This lead is blocked or assigned to another person')
            db.execute('INSERT INTO followups(user_id,lead_id,due_at,note,created_at) VALUES(?,?,?,?,?)',(uid,identifier,due,data.note,now()));log(db,uid,identifier,'Follow-up scheduled: '+due+(' · '+data.note if data.note else ''),'followup')
        return {'ok':True}

    @app.post('/api/crm/followups/{identifier}/reschedule')
    def reschedule(identifier:int,data:Followup,request:Request):
        uid=user(request)['id'];due=callback_due(data)
        with connect() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute("SELECT * FROM followups WHERE id=? AND user_id=? AND state='open'",(identifier,uid)).fetchone()
            if not row:raise HTTPException(404,'Open follow-up not found')
            lead=owned(db,uid,row['lead_id']);initialize(db,uid,row['lead_id'])
            meta=db.execute('SELECT * FROM lead_sales WHERE user_id=? AND lead_id=?',(uid,row['lead_id'])).fetchone()
            if meta['do_not_contact'] or meta['assigned_out'] or lead['deleted_at'] or blocked(db,uid,lead):raise HTTPException(409,'This contact is unavailable')
            db.execute('UPDATE followups SET due_at=?,note=? WHERE id=?',(due,data.note,identifier));log(db,uid,row['lead_id'],'Callback rescheduled from '+row['due_at']+' to '+due,'followup')
        return {'ok':True,'due_at':due}

    def reminder_allowed(db,uid,lead_id,lead):
        from dispositions import schedule as contact_schedule
        timing=contact_schedule(db,uid,lead_id)
        return not blocked(db,uid,lead) and timing.get('schedule_reason')!='Contact blocked or completed' and timing.get('effective_result') not in ('DNC','Wrong Number')

    @app.get('/api/callback-reminders')
    def reminders(request:Request):
        uid=user(request)['id'];current=datetime.now(timezone.utc);end=(current+timedelta(minutes=10)).isoformat()
        with connect() as db:
            rows=db.execute("SELECT f.*,l.name,l.phone,l.place_id,l.deleted_at FROM followups f JOIN leads l ON l.id=f.lead_id AND l.user_id=f.user_id WHERE f.user_id=? AND f.state='open' AND f.due_at>? AND f.due_at<=? AND l.deleted_at IS NULL",(uid,current.isoformat(),end)).fetchall()
            return {'followups':[dict(r) for r in rows if reminder_allowed(db,uid,r['lead_id'],r)]}

    @app.post('/api/crm/followups/{identifier}/reminder')
    def claim_reminder(identifier:int,request:Request):
        uid=user(request)['id'];current=datetime.now(timezone.utc)
        with connect() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute("SELECT * FROM followups WHERE id=? AND user_id=? AND state='open'",(identifier,uid)).fetchone()
            if not row:raise HTTPException(404,'Follow-up not found')
            due=datetime.fromisoformat(row['due_at'])
            if not 0<(due-current).total_seconds()<=600:raise HTTPException(409,'Reminder is not due')
            lead=owned(db,uid,row['lead_id'])
            if lead['deleted_at'] or not reminder_allowed(db,uid,row['lead_id'],lead):raise HTTPException(409,'Contact unavailable')
            inserted=db.execute('INSERT OR IGNORE INTO callback_reminder_claims(user_id,followup_id,due_at) VALUES(?,?,?)',(uid,identifier,row['due_at'])).rowcount
        return {'notify':bool(inserted)}

    @app.get('/callback-reminder-sw.js')
    def reminder_worker():
        from fastapi.responses import FileResponse
        from pathlib import Path
        return FileResponse(Path(__file__).parent/'static'/'callback-reminder-sw.js',media_type='application/javascript',headers={'Cache-Control':'no-cache'})

    @app.post('/api/crm/followups/{identifier}/{state}')
    def finish(identifier:int,state:str,request:Request):
        uid=user(request)['id']
        if state not in ('completed','cancelled'):raise HTTPException(400,'Choose completed or cancelled')
        with connect() as db:
            row=db.execute('SELECT * FROM followups WHERE id=? AND user_id=?',(identifier,uid)).fetchone()
            if not row:raise HTTPException(404,'Follow-up not found')
            db.execute('UPDATE followups SET state=? WHERE id=?',(state,identifier));log(db,uid,row['lead_id'],'Follow-up '+state,'followup')
        return {'ok':True}

    @app.post('/api/crm/leads/{identifier}/contact-block')
    def block(identifier:int,data:Block,request:Request):
        uid=user(request)['id']
        with connect() as db:
            lead=owned(db,uid,identifier);initialize(db,uid,identifier)
            db.execute('UPDATE lead_sales SET do_not_contact=? WHERE user_id=? AND lead_id=?',(int(data.blocked),uid,identifier))
            for key in contact_keys(lead):
                if data.blocked:db.execute('INSERT OR REPLACE INTO contact_blocks VALUES(?,?,?,?)',(uid,key,data.reason,now()))
                else:db.execute('DELETE FROM contact_blocks WHERE user_id=? AND contact_key=?',(uid,key))
            if data.blocked:db.execute("UPDATE followups SET state='cancelled' WHERE user_id=? AND lead_id=? AND state='open'",(uid,identifier))
            log(db,uid,identifier,('Do not contact enabled' if data.blocked else 'Contact block removed')+(' · '+data.reason if data.reason else ''),'contact')
        return {'ok':True}

    @app.post('/api/crm/leads/{identifier}/assign')
    def assign(identifier:int,data:Assign,request:Request):
        u=user(request);uid=u['id']
        if not u['admin']:raise HTTPException(403,'Only the administrator can assign leads')
        if data.user_id==uid:raise HTTPException(400,'Choose another teammate')
        with connect() as db:
            db.execute('BEGIN IMMEDIATE');lead=owned(db,uid,identifier);initialize(db,uid,identifier)
            meta=db.execute('SELECT * FROM lead_sales WHERE user_id=? AND lead_id=?',(uid,identifier)).fetchone()
            if meta['do_not_contact'] or meta['assigned_out'] or blocked(db,uid,lead):raise HTTPException(409,'Lead is blocked or already handed off')
            if db.execute('SELECT 1 FROM dial_attempts WHERE user_id=? AND lead_id=? AND result_recorded=0',(uid,identifier)).fetchone():raise HTTPException(409,'Finish the current call before assigning this lead')
            member=db.execute('SELECT email FROM users WHERE id=?',(data.user_id,)).fetchone()
            if not member:raise HTTPException(404,'Teammate not found')
            if blocked(db,data.user_id,lead):raise HTTPException(409,'The recipient has blocked this contact')
            candidates=db.execute('SELECT * FROM leads WHERE user_id=?',(data.user_id,)).fetchall()
            duplicate=next((r for r in candidates if (lead['place_id'] and r['place_id']==lead['place_id']) or (phone_key(lead['phone']) and phone_key(r['phone'])==phone_key(lead['phone'])) or r['name'].casefold()==lead['name'].casefold()),None)
            if duplicate:raise HTTPException(409,'That teammate already has this business; no duplicate was created')
            columns=['name','phone','place_id','review_facts','score','grade','email','category','city','website_url','social_url','source_url','facts','website_check'];target=db.execute('INSERT INTO leads(user_id,'+','.join(columns)+') VALUES('+','.join('?' for _ in range(len(columns)+1))+')',[data.user_id]+[lead[key] for key in columns]).lastrowid
            initialize(db,data.user_id,target);db.execute('UPDATE lead_sales SET assigned_out=1 WHERE user_id=? AND lead_id=?',(uid,identifier));db.execute("UPDATE followups SET state='cancelled' WHERE user_id=? AND lead_id=? AND state='open'",(uid,identifier));db.execute('INSERT INTO lead_assignments(from_user,to_user,source_lead,target_lead,created_at) VALUES(?,?,?,?,?)',(uid,data.user_id,identifier,target,now()));log(db,uid,identifier,'Lead assigned to '+member['email'],'assignment');log(db,data.user_id,target,'Business details assigned by '+u['email']+'; private notes and call history were not copied.','assignment')
        return {'ok':True,'target_lead_id':target}
