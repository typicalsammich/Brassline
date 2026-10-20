import json,math,os,re,secrets
from datetime import datetime,timezone
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from urllib.parse import parse_qsl,urlsplit
from fastapi import HTTPException,Request
from fastapi.responses import Response
from starlette.datastructures import FormData
from pydantic import BaseModel,Field
from scoring import settings,phone_key
from leads_service import list_for
from dispositions import schedule

def timestamp():return datetime.now(timezone.utc).isoformat()
def as_date(value,tz):
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(tz).date()

def twilio_config(uid=None):
    names=['TWILIO_ACCOUNT_SID','TWILIO_AUTH_TOKEN','TWILIO_API_KEY_SID','TWILIO_API_KEY_SECRET','TWILIO_TWIML_APP_SID','PUBLIC_BASE_URL']
    from integration_store import effective_config
    try:values=effective_config(uid)
    except ValueError:values={};values['TWILIO_ENABLED']='0'
    config={key:str(values.get(key) or '').strip() for key in names}
    try:numbers=json.loads(values.get('TWILIO_NUMBERS_JSON') or '[]')
    except ValueError:numbers=[]
    if not isinstance(numbers,list):numbers=[]
    config['numbers']=[n for n in numbers if isinstance(n,dict) and re.fullmatch(r'\+1\d{10}',str(n.get('number','')))]
    base=urlsplit(config['PUBLIC_BASE_URL'])
    config['enabled']=values.get('TWILIO_ENABLED')=='1' and all(config[key] for key in names) and base.scheme=='https' and bool(base.hostname) and not base.username and not base.password and bool(config['numbers'])
    return config

def number_pool(config,email):return [n for n in config['numbers'] if not n.get('users') or email.lower() in [str(u).lower() for u in n['users']]]

def choose_number(pool,phone,counters,centres=None):
    area=phone_key(phone)[:3];exact=[n for n in pool if phone_key(n['number'])[:3]==area]
    eligible=exact or pool;reason='Matching area code' if exact else 'Area location unavailable; rotating available numbers'
    if not exact and centres and area in centres:
        target=centres[area];located=[n for n in pool if n.get('latitude') is not None and n.get('longitude') is not None]
        if located:
            def distance(n):
                a,b,c,d=map(math.radians,[target['latitude'],target['longitude'],n['latitude'],n['longitude']]);return math.sin((c-a)/2)**2+math.cos(a)*math.cos(c)*math.sin((d-b)/2)**2
            nearest=min(distance(n) for n in located);eligible=[n for n in located if abs(distance(n)-nearest)<1e-9];reason='Nearest configured area-code centre'
    chosen=min(eligible,key=lambda n:(counters.get(n['number'],0),n['number']))
    return chosen['number'],reason

def group_saved_list_days(db, uid, zone):
    for row in db.execute('SELECT id,name,folder,created_at FROM calling_lists WHERE user_id=? AND day_grouped=0',(uid,)).fetchall():
        folder=row['folder'];basis=''
        if not folder and row['name'] not in ('Manual numbers','No Answer follow-ups','Scheduled callbacks'):
            if row['created_at']:
                try:folder=str(as_date(row['created_at'],zone));basis='List creation date'
                except (ValueError,TypeError):pass
            else:
                # Older lists did not record creation time. Use a common original
                # import day only when every member has that evidence.
                members=db.execute('SELECT l.id,b.created_at FROM calling_list_members m JOIN leads l ON l.id=m.lead_id LEFT JOIN batches b ON b.id=l.batch_id AND b.user_id=? WHERE m.list_id=? AND l.user_id=?',(uid,row['id'],uid)).fetchall()
                days=set()
                try:
                    if members and all(r['created_at'] for r in members):
                        days={str(as_date(r['created_at'],zone)) for r in members}
                except (ValueError,TypeError):days=set()
                if len(days)==1:folder=days.pop();basis='Original import date (list creation date unavailable)'
        db.execute('UPDATE calling_lists SET folder=?,day_basis=?,day_grouped=1 WHERE id=? AND user_id=?',(folder,basis,row['id'],uid))


class Preferences(BaseModel):
    daily_goal:int=Field(ge=1,le=2000)
    timezone:str=Field(max_length=100)
    auto_no_answer:bool=True
class Begin(BaseModel):
    lead_id:int
    list_id:int=0
    retry_failed:bool=False
    manual_repeat:bool=False
class CallingList(BaseModel):
    name:str=Field(min_length=1,max_length=100)
    lead_ids:list[int]=Field(default_factory=list,max_length=5000)
class ListDay(BaseModel):
    list_ids:list[int]=Field(min_length=1,max_length=500)
    day:str=Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
class ListOrganization(BaseModel):
    name:str=Field(min_length=1,max_length=100)
    folder:str=Field(default='',max_length=60)
    favorite:bool=False
    archived:bool=False
class ManualNumber(BaseModel):
    phone:str=Field(max_length=80)
    name:str=Field(default='',max_length=300)
class Outcome(BaseModel):
    result:str=Field(max_length=100)
    note:str=Field(default='',max_length=5000)

def install(app,connect,user,practice):
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS dialer_preferences(user_id INTEGER PRIMARY KEY REFERENCES users(id),daily_goal INTEGER NOT NULL,timezone TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS dial_attempts(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),lead_id INTEGER NOT NULL REFERENCES leads(id),started_at TEXT NOT NULL,status TEXT NOT NULL,mode TEXT NOT NULL,dial_dispatched INTEGER NOT NULL DEFAULT 0,phone TEXT NOT NULL,caller_number TEXT,route_reason TEXT,twilio_sid TEXT,result_recorded INTEGER NOT NULL DEFAULT 0,script_id INTEGER);
        CREATE TABLE IF NOT EXISTS calling_lists(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS calling_list_members(list_id INTEGER REFERENCES calling_lists(id) ON DELETE CASCADE,lead_id INTEGER REFERENCES leads(id),PRIMARY KEY(list_id,lead_id));
        CREATE TABLE IF NOT EXISTS number_rotation(user_id INTEGER REFERENCES users(id),number TEXT,uses INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(user_id,number));
        ''')
        list_columns={r[1] for r in db.execute('PRAGMA table_info(calling_lists)')}
        for key,definition in [('folder',"TEXT NOT NULL DEFAULT ''"),('favorite','INTEGER NOT NULL DEFAULT 0'),('archived','INTEGER NOT NULL DEFAULT 0'),('created_at',"TEXT NOT NULL DEFAULT ''"),('day_grouped','INTEGER NOT NULL DEFAULT 0'),('day_basis',"TEXT NOT NULL DEFAULT ''")]:
            if key not in list_columns:db.execute('ALTER TABLE calling_lists ADD COLUMN '+key+' '+definition)
        db.execute("CREATE TRIGGER IF NOT EXISTS calling_list_created AFTER INSERT ON calling_lists WHEN NEW.created_at='' BEGIN UPDATE calling_lists SET created_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE id=NEW.id; END")
        columns={r[1] for r in db.execute('PRAGMA table_info(call_events)')}
        if 'attempt_id' not in columns:db.execute('ALTER TABLE call_events ADD COLUMN attempt_id TEXT')
        if 'recorded_at' not in columns:db.execute('ALTER TABLE call_events ADD COLUMN recorded_at TEXT')
        if 'recording_method' not in columns:db.execute("ALTER TABLE call_events ADD COLUMN recording_method TEXT NOT NULL DEFAULT 'manual'")
        db.execute('CREATE INDEX IF NOT EXISTS event_attempt ON call_events(attempt_id)')
        db.execute('DROP TRIGGER IF EXISTS preserve_call_results')
        db.executescript('''CREATE TRIGGER preserve_call_results BEFORE UPDATE ON call_events WHEN NEW.user_id != OLD.user_id OR NEW.lead_id != OLD.lead_id OR NEW.result != OLD.result OR NEW.source != OLD.source OR NEW.created_at != OLD.created_at OR NEW.script_id IS NOT OLD.script_id OR NEW.caller_number IS NOT OLD.caller_number OR NEW.attempt_id IS NOT OLD.attempt_id OR NEW.recorded_at IS NOT OLD.recorded_at OR NEW.recording_method IS NOT OLD.recording_method BEGIN SELECT RAISE(ABORT,'Call history cannot be overwritten'); END;''')

    with connect() as db:
        columns={r[1] for r in db.execute('PRAGMA table_info(dialer_preferences)')}
        if 'auto_no_answer' not in columns:db.execute('ALTER TABLE dialer_preferences ADD COLUMN auto_no_answer INTEGER NOT NULL DEFAULT 1')
        columns={r[1] for r in db.execute('PRAGMA table_info(dial_attempts)')}
        for name,definition in [('manual_stop','INTEGER NOT NULL DEFAULT 0'),('answered_seen','INTEGER NOT NULL DEFAULT 0'),('auto_result','INTEGER NOT NULL DEFAULT 0'),('child_sid','TEXT'),('call_note',"TEXT NOT NULL DEFAULT ''")]:
            if name not in columns:db.execute(f'ALTER TABLE dial_attempts ADD COLUMN {name} {definition}')

    def auto_no_answer(db,row):
        pref=db.execute('SELECT auto_no_answer FROM dialer_preferences WHERE user_id=?',(row['user_id'],)).fetchone()
        if not row['dial_dispatched'] or row['result_recorded'] or row['manual_stop'] or row['answered_seen'] or (pref and not pref['auto_no_answer']):return
        if db.execute('SELECT 1 FROM call_events WHERE attempt_id=? AND ignored=0',(row['id'],)).fetchone():return
        db.execute("INSERT INTO call_events(user_id,lead_id,result,source,created_at,caller_number,script_id,attempt_id,recorded_at,recording_method) VALUES(?,?,'No Answer','dialer',?,?,?,?,?,'provider_no_answer')",(row['user_id'],row['lead_id'],row['started_at'],row['caller_number'],row['script_id'],row['id'],timestamp()))
        db.execute("UPDATE dial_attempts SET ended_at=COALESCE(ended_at,CURRENT_TIMESTAMP),status='ended',result_recorded=1,auto_result=1 WHERE id=?",(row['id'],))
        db.execute("UPDATE followups SET state='completed' WHERE user_id=? AND lead_id=? AND state='open' AND due_at<=?",(row['user_id'],row['lead_id'],row['started_at']))
        db.execute("INSERT INTO lead_sales(user_id,lead_id,stage) VALUES(?,?,'Contacted') ON CONFLICT(user_id,lead_id) DO UPDATE SET stage=CASE WHEN lead_sales.stage='New' THEN 'Contacted' ELSE lead_sales.stage END",(row['user_id'],row['lead_id']))

    @app.get('/api/dialer/attempts/{attempt_id}/state')
    def attempt_state(attempt_id:str,request:Request):
        with connect() as db:
            row=owned_attempt(db,user(request)['id'],attempt_id)
            result={key:row[key] for key in ['id','status','result_recorded','auto_result','answered_seen','manual_stop','transcription_enabled','transcript_error']}
            recording=db.execute("SELECT status FROM call_recordings WHERE attempt_id=? AND user_id=? AND status!='deleted' ORDER BY created_at DESC LIMIT 1",(attempt_id,row['user_id'])).fetchone()
            result['recording_status']=recording['status'] if recording else 'off'
            return result

    @app.post('/api/dialer/attempts/{attempt_id}/simulate-no-answer')
    def simulate(attempt_id:str,request:Request):
        with connect() as db:
            db.execute('BEGIN IMMEDIATE');row=owned_attempt(db,user(request)['id'],attempt_id)
            if not practice or row['mode']!='practice':raise HTTPException(403,'Simulation is practice-only')
            auto_no_answer(db,row)
        return {'ok':True}

    def prefs(uid):
        config=settings()['dialer']
        with connect() as db:
            row=db.execute('SELECT * FROM dialer_preferences WHERE user_id=?',(uid,)).fetchone()
        return dict(row) if row else {'daily_goal':config['default_goal'],'timezone':config['default_timezone'],'auto_no_answer':True}

    def view(uid,email,list_id=0):
        preferences=prefs(uid);tz=ZoneInfo(preferences['timezone']);today=datetime.now(timezone.utc).astimezone(tz).date();rules=settings()['dialer']
        with connect() as db:
            dispositions=[dict(row) for row in db.execute("SELECT * FROM call_events WHERE user_id=? AND ignored=0 ORDER BY id",(uid,))]
            events=[event for event in dispositions if event['source']=='dialer']
            pending=db.execute('SELECT * FROM dial_attempts WHERE user_id=? AND result_recorded=0 ORDER BY started_at LIMIT 1',(uid,)).fetchone()
        with connect() as db:
            group_saved_list_days(db,uid,tz)
            lists=[dict(r) for r in db.execute('SELECT l.id,l.name,l.folder,l.favorite,l.archived,l.created_at,l.day_basis,COUNT(b.id) AS count FROM calling_lists l LEFT JOIN calling_list_members m ON m.list_id=l.id LEFT JOIN leads b ON b.id=m.lead_id AND b.user_id=l.user_id AND b.deleted_at IS NULL WHERE l.user_id=? GROUP BY l.id ORDER BY l.favorite DESC,l.folder COLLATE NOCASE,l.name COLLATE NOCASE,l.id',(uid,))]
            # Count attempted contacts across this account, including duplicate lead rows.
            from lead_identity import contact_groups
            identity=contact_groups(db.execute('SELECT id,name,phone,place_id,city FROM leads WHERE user_id=?',(uid,)).fetchall())
            attempted={identity.get(r[0],r[0]) for r in db.execute('SELECT lead_id FROM dial_attempts WHERE user_id=? UNION SELECT lead_id FROM call_events WHERE user_id=? AND ignored=0',(uid,uid))}
            memberships={}
            for r in db.execute('SELECT m.list_id,l.id FROM calling_list_members m JOIN leads l ON l.id=m.lead_id JOIN calling_lists c ON c.id=m.list_id WHERE c.user_id=? AND l.user_id=? AND l.deleted_at IS NULL',(uid,uid)):
                memberships.setdefault(r[0],set()).add(identity.get(r[1],r[1]))
            folder_stats=[]
            for item in lists:
                contacts=memberships.get(item['id'],set())
                item['uncalled']=len(contacts-attempted)
                item['contact_count']=len(contacts)
            for state in ('active','archived','all'):
                grouped={}
                for item in lists:
                    if state!='all' and bool(item['archived'])!=(state=='archived'):continue
                    entry=grouped.setdefault(item['folder'],{'contacts':set(),'lists':0})
                    entry['contacts'].update(memberships.get(item['id'],set()));entry['lists']+=1
                for folder,entry in grouped.items():
                    folder_stats.append({'folder':folder,'state':state,'lists':entry['lists'],'count':len(entry['contacts']),'uncalled':len(entry['contacts']-attempted)})
            if list_id and not any(l['id']==list_id and not l['archived'] for l in lists):raise HTTPException(404,'Calling list unavailable; restore archived lists in Manage calling lists')
            members={r[0] for r in db.execute('SELECT lead_id FROM calling_list_members WHERE list_id=?',(list_id,))} if list_id else set()
        today_events=[e for e in events if as_date(e['created_at'],tz)==today]
        outcomes={item['label']:item for item in rules['results']};outcomes['Call Later']=outcomes.get('Callback',{});answered=sum(bool(outcomes.get(e['result'],{}).get('answered')) for e in today_events);booked=sum(bool(outcomes.get(e['result'],{}).get('booked')) for e in today_events)
        from shared_services import calling_config
        config=calling_config(uid);mode='practice' if practice else 'twilio' if config['enabled'] and number_pool(config,email) else 'list'
        queue=[];histories={}
        for event in dispositions:histories.setdefault(event['lead_id'],[]).append(event)
        from lead_identity import groups
        visible=list_for(connect,uid);selected=[l for l in visible if not list_id or l['id'] in members];identity=groups(selected)
        for lead in selected:
            if identity[lead['id']]!=lead['id']:continue
            if list_id and lead['id'] not in members:continue
            lead['last_result']=lead.get('effective_result',lead['last_result'])
            history=histories.get(lead['id'],[]);called_today=any(as_date(e['created_at'],tz)==today for e in history)
            calls=[e for e in history if e['source']=='dialer']
            retry=bool(history and lead.get('call_ready') and lead['last_result'] in ('No Answer','Voicemail','Not Interested','Hung Up'))
            due=bool(lead.get('followup_due'));blocked=lead.get('do_not_contact') or lead.get('assigned_out') or lead.get('sales_stage') in ('Won','Lost')
            eligible=bool(not blocked and phone_key(lead['phone']) and not any(lead['facts'].get(k) for k in ('permanently_closed','illegitimate')) and (lead['state']=='ready' or bool(list_id)) and lead['call_ready'])
            if eligible or called_today or (list_id and lead.get('effective_result')):
                lead.update(manual_repeat_available=bool(not blocked and phone_key(lead['phone']) and lead['manual_retry_allowed'] and not any(lead['facts'].get(k) for k in ('permanently_closed','illegitimate'))),eligible=eligible,can_call=eligible and mode!='list',attempt_count=len(calls),done_today=called_today and not eligible,second_try=retry,latest_attempt_id=calls[-1]['attempt_id'] if calls else None)
                queue.append(lead)
        active=None
        if pending:
            active={key:pending[key] for key in ['id','lead_id','status','mode','started_at','caller_number','route_reason','script_id','call_limit_seconds','sponsored']}
        if active:
            active['script_snapshot']=json.loads(pending['script_snapshot'])
            active['coach_profile_name']=json.loads(pending['coach_profile_snapshot'] or '{}').get('name','Default coach')
            active['funding']='Administrator-funded' if pending['sponsored'] else 'Your Twilio account'
        return {'lists':lists,'folders':folder_stats,'list_id':list_id,'mode':mode,'funding':'Administrator-funded' if config.get('sponsored') else 'Your Twilio account','preferences':preferences,'results':rules['results'],'queue':queue,'active_attempt':active,'summary':{'dials':len(today_events),'answered':answered,'booked':booked,'answer_rate':round(answered/len(today_events)*100,1) if today_events else None,'booking_rate':round(booked/answered*100,1) if answered else None},'today':str(today)}

    def owned_attempt(db,uid,attempt_id):
        row=db.execute('SELECT * FROM dial_attempts WHERE id=? AND user_id=?',(attempt_id,uid)).fetchone()
        if not row:raise HTTPException(404,'Call attempt not found')
        return row

    def hangup(row):
        if row['mode']=='twilio' and row['twilio_sid'] and row['status'] not in ('ended','failed'):
            from twilio.rest import Client
            from shared_services import attempt_config
            cfg=attempt_config(row)
            try:Client(cfg['TWILIO_ACCOUNT_SID'],cfg['TWILIO_AUTH_TOKEN']).calls(row['twilio_sid']).update(status='completed')
            except Exception:raise HTTPException(502,'Could not confirm hang-up with Twilio. Auto-dial is paused; retry hang-up before continuing.')

    @app.get('/api/dialer')
    def dashboard(request:Request):
        u=user(request)
        try:list_id=int(request.query_params.get('list_id','0'))
        except ValueError:raise HTTPException(400,'Invalid calling list')
        return view(u['id'],u['email'],list_id)

    def unanswered(uid):
        from lead_identity import contact_groups
        rows=[l for l in list_for(connect,uid) if l.get('effective_result')=='No Answer' and not l.get('do_not_contact') and not l.get('assigned_out') and l.get('sales_stage') not in ('Won','Lost') and not any(l['facts'].get(k) for k in ('permanently_closed','illegitimate'))]
        identity=contact_groups(rows)
        return [l for l in rows if identity[l['id']]==l['id']]

    @app.post('/api/followups/{followup_id}/prepare-call')
    def prepare_callback(followup_id:int,request:Request):
        uid=user(request)['id']
        with connect() as db:
            db.execute('BEGIN IMMEDIATE');followup=db.execute("SELECT * FROM followups WHERE id=? AND user_id=? AND state='open'",(followup_id,uid)).fetchone()
            if not followup:raise HTTPException(404,'Open callback not found')
            if datetime.fromisoformat(followup['due_at'])>datetime.now(timezone.utc):raise HTTPException(409,'This callback is scheduled for later. Reschedule it first if the requested time has changed.')
            timing=schedule(db,uid,followup['lead_id'])
            if not timing['call_ready']:raise HTTPException(409,timing['schedule_reason'])
            lead=db.execute('SELECT * FROM leads WHERE id=? AND user_id=?',(followup['lead_id'],uid)).fetchone()
            from crm_service import blocked
            if not lead or lead['deleted_at'] or not phone_key(lead['phone']) or blocked(db,uid,lead):raise HTTPException(409,'Contact is unavailable or has no callable phone')
            if db.execute('SELECT 1 FROM dial_attempts WHERE user_id=? AND result_recorded=0',(uid,)).fetchone():raise HTTPException(409,'Finish the current call first')
            name='Scheduled callbacks';row=db.execute('SELECT id FROM calling_lists WHERE user_id=? AND name=?',(uid,name)).fetchone()
            lid=row['id'] if row else db.execute('INSERT INTO calling_lists(user_id,name) VALUES(?,?)',(uid,name)).lastrowid
            db.execute('DELETE FROM calling_list_members WHERE list_id=?',(lid,))
            db.execute('INSERT OR IGNORE INTO calling_list_members VALUES(?,?)',(lid,lead['id']))
        return {'lead_id':lead['id'],'list_id':lid,'name':lead['name']}

    @app.get('/api/followups/no-answer')
    def no_answer_followups(request:Request):
        rows=unanswered(user(request)['id'])
        return {'leads':rows,'ready':sum(bool(l['call_ready']) for l in rows)}

    @app.post('/api/followups/no-answer/load')
    def load_unanswered(request:Request):
        uid=user(request)['id'];rows=[l for l in unanswered(uid) if l['call_ready'] and phone_key(l['phone'])]
        if not rows:raise HTTPException(409,'No unanswered leads are due yet. The 24-hour wait and contact blocks still apply.')
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM dial_attempts WHERE user_id=? AND result_recorded=0',(uid,)).fetchone():raise HTTPException(409,'Finish the current call first')
            name='No Answer follow-ups';row=db.execute('SELECT id FROM calling_lists WHERE user_id=? AND name=?',(uid,name)).fetchone()
            lid=row['id'] if row else db.execute('INSERT INTO calling_lists(user_id,name) VALUES(?,?)',(uid,name)).lastrowid
            db.execute('DELETE FROM calling_list_members WHERE list_id=?',(lid,))
            db.executemany('INSERT INTO calling_list_members VALUES(?,?)',[(lid,l['id']) for l in rows])
        return {'id':lid,'name':name,'count':len(rows)}

    @app.post('/api/dialer/lists')
    def save_list(data:CallingList,request:Request):
        uid=user(request)['id'];name=data.name.strip()
        if not name:raise HTTPException(400,'Name your calling list')
        visible={l['id'] for l in list_for(connect,uid)}
        if not set(data.lead_ids)<=visible:raise HTTPException(404,'One or more leads are unavailable')
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT id FROM calling_lists WHERE user_id=? AND name=?',(uid,name)).fetchone()
            lid=row['id'] if row else db.execute('INSERT INTO calling_lists(user_id,name) VALUES(?,?)',(uid,name)).lastrowid
            db.execute('UPDATE calling_lists SET archived=0 WHERE id=? AND user_id=?',(lid,uid))
            db.executemany('INSERT OR IGNORE INTO calling_list_members VALUES(?,?)',[(lid,i) for i in set(data.lead_ids)])
        return {'id':lid,'name':name}

    @app.post('/api/dialer/lists/{list_id}/deduplicate')
    def deduplicate_list(list_id:int,request:Request):
        uid=user(request)['id']
        from lead_identity import groups
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM calling_lists WHERE id=? AND user_id=?',(list_id,uid)).fetchone():raise HTTPException(404,'Calling list not found')
            if db.execute('SELECT 1 FROM dial_attempts WHERE user_id=? AND result_recorded=0',(uid,)).fetchone():return {'removed':0,'pending_call':True}
            rows=db.execute('SELECT l.* FROM leads l JOIN calling_list_members m ON m.lead_id=l.id WHERE m.list_id=? AND l.user_id=? AND l.deleted_at IS NULL ORDER BY l.id',(list_id,uid)).fetchall()
            mapping=groups(rows);duplicates=[i for i,root in mapping.items() if i!=root]
            db.executemany('DELETE FROM calling_list_members WHERE list_id=? AND lead_id=?',[(list_id,i) for i in duplicates])
        return {'removed':len(duplicates),'remaining':len(rows)-len(duplicates),'note':'Only duplicate list entries removed. Business records, import rows and call history retained.'}

    @app.post('/api/dialer/lists/group-day')
    def group_lists_by_day(data:ListDay,request:Request):
        uid=user(request)['id']
        try:datetime.strptime(data.day,'%Y-%m-%d')
        except ValueError:raise HTTPException(400,'Choose a valid date')
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            ids=set(data.list_ids)
            owned={r[0] for r in db.execute('SELECT id FROM calling_lists WHERE user_id=?',(uid,))}
            if not ids<=owned:raise HTTPException(404,'Calling list not found')
            if db.execute('SELECT 1 FROM dial_attempts WHERE user_id=? AND result_recorded=0',(uid,)).fetchone():raise HTTPException(409,'Finish the current call before organizing lists')
            db.executemany("UPDATE calling_lists SET folder=?,day_grouped=1,day_basis='Chosen day' WHERE id=? AND user_id=?",[(data.day,lid,uid) for lid in ids])
        return {'ok':True,'updated':len(ids)}

    @app.post('/api/dialer/lists/{list_id}/organization')
    def organize_list(list_id:int,data:ListOrganization,request:Request):
        uid=user(request)['id'];name=data.name.strip();folder=data.folder.strip()
        if not name:raise HTTPException(400,'Name your calling list')
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM calling_lists WHERE id=? AND user_id=?',(list_id,uid)).fetchone():raise HTTPException(404,'Calling list not found')
            if db.execute('SELECT 1 FROM dial_attempts WHERE user_id=? AND result_recorded=0',(uid,)).fetchone():raise HTTPException(409,'Finish the current call before organizing lists')
            if db.execute('SELECT 1 FROM calling_lists WHERE user_id=? AND id!=? AND lower(name)=lower(?)',(uid,list_id,name)).fetchone():raise HTTPException(409,'Another calling list already uses that name')
            db.execute("UPDATE calling_lists SET day_grouped=1,day_basis='Chosen folder',name=?,folder=?,favorite=?,archived=? WHERE id=? AND user_id=?",(name,folder,int(data.favorite),int(data.archived),list_id,uid))
        return {'ok':True}

    @app.post('/api/dialer/lists/{list_id}/delete')
    def delete_list(list_id:int,request:Request):
        uid=user(request)['id']
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT name FROM calling_lists WHERE id=? AND user_id=?',(list_id,uid)).fetchone()
            if not row:raise HTTPException(404,'Calling list not found')
            if db.execute('SELECT 1 FROM dial_attempts WHERE user_id=? AND result_recorded=0',(uid,)).fetchone():
                raise HTTPException(409,'Finish the current call and save its result before removing a calling list')
            db.execute('DELETE FROM calling_list_members WHERE list_id=?',(list_id,))
            db.execute('DELETE FROM calling_lists WHERE id=? AND user_id=?',(list_id,uid))
        return {'ok':True,'name':row['name'],'message':'Calling list removed. Leads, imported batches, recordings and call history kept.'}

    @app.post('/api/dialer/lists/{list_id}/remove')
    def remove_member(list_id:int,data:Begin,request:Request):
        with connect() as db:
            if not db.execute('SELECT 1 FROM calling_lists WHERE id=? AND user_id=?',(list_id,user(request)['id'])).fetchone():raise HTTPException(404,'Calling list not found')
            db.execute('DELETE FROM calling_list_members WHERE list_id=? AND lead_id=?',(list_id,data.lead_id))
        return {'ok':True}

    @app.post('/api/dialer/manual')
    def manual(data:ManualNumber,request:Request):
        uid=user(request)['id'];phone=phone_key(data.phone)
        if not phone or not re.fullmatch(r'\+?[\d\s().-]+',data.phone):raise HTTPException(400,'Enter a US or Canadian number with 10 digits, optionally prefixed with +1')
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            matches=[r for r in db.execute('SELECT * FROM leads WHERE user_id=?',(uid,)) if phone_key(r['phone'])==phone]
            from crm_service import blocked
            for r in matches:
                sales=db.execute('SELECT * FROM lead_sales WHERE lead_id=? AND user_id=?',(r['id'],uid)).fetchone()
                last=db.execute('SELECT result FROM call_events WHERE lead_id=? AND user_id=? AND ignored=0 ORDER BY id DESC LIMIT 1',(r['id'],uid)).fetchone()
                if blocked(db,uid,r) or (sales and (sales['do_not_contact'] or sales['assigned_out'] or sales['stage'] in ('Won','Lost'))) or (last and last['result'] in ('Wrong Number','DNC','Booked')) or not schedule(db,uid,r['id'])['manual_retry_allowed']:raise HTTPException(409,'This number belongs to a blocked or completed lead. Review its contact status first.')
            lid=matches[0]['id'] if matches else db.execute("INSERT INTO leads(user_id,name,phone,facts) VALUES(?,?,?,'{}')",(uid,data.name.strip() or 'Manual call '+phone,'+1'+phone)).lastrowid
        saved=save_list(CallingList(name='Manual numbers',lead_ids=[lid]),request)
        with connect() as db:
            previous=db.execute('SELECT * FROM dial_attempts WHERE user_id=? AND lead_id=? ORDER BY started_at DESC,id DESC LIMIT 1',(uid,lid)).fetchone()
        # Older versions replaced a provider failure with 'ended' when saving a result.
        # Confirm those historical failures with a read-only provider lookup on an explicit manual retry.
        if previous and previous['mode']=='twilio' and previous['status']=='ended' and not previous['answered_seen'] and previous['child_sid']:
            from shared_services import attempt_config
            from twilio.rest import Client
            cfg=attempt_config(previous)
            try:provider_status=Client(cfg['TWILIO_ACCOUNT_SID'],cfg['TWILIO_AUTH_TOKEN']).calls(previous['child_sid']).fetch().status
            except Exception:provider_status=None
            if provider_status=='failed':
                with connect() as db:
                    db.execute("UPDATE dial_attempts SET status='failed' WHERE id=? AND status='ended' AND answered_seen=0",(previous['id'],))
                    previous=db.execute('SELECT * FROM dial_attempts WHERE id=?',(previous['id'],)).fetchone()
        return {'lead_id':lid,'list_id':saved['id'],'retry_failed':bool(previous and previous['status']=='failed' and not previous['answered_seen'])}

    @app.post('/api/dialer/preferences')
    def save_preferences(data:Preferences,request:Request):
        try:ZoneInfo(data.timezone)
        except (ZoneInfoNotFoundError,ValueError):raise HTTPException(400,'Choose a valid time zone')
        with connect() as db:db.execute('INSERT OR REPLACE INTO dialer_preferences(user_id,daily_goal,timezone,auto_no_answer) VALUES(?,?,?,?)',(user(request)['id'],data.daily_goal,data.timezone,int(data.auto_no_answer)))
        return {'ok':True}

    @app.post('/api/dialer/attempts')
    def begin(data:Begin,request:Request):
        u=user(request);snapshot=view(u['id'],u['email'],data.list_id)
        if snapshot['mode']=='list':raise HTTPException(503,'Twilio is not configured. Practice mode works without an account.')
        lead=next((l for l in snapshot['queue'] if l['id']==data.lead_id and l['eligible']),None)
        if data.retry_failed or data.manual_repeat:lead=next((l for l in list_for(connect,u['id']) if l['id']==data.lead_id),None)
        if not lead:raise HTTPException(409,'This lead is not ready for a call today. Refresh the calling list.')
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            failed=db.execute('SELECT * FROM dial_attempts WHERE user_id=? AND lead_id=? ORDER BY started_at DESC,id DESC LIMIT 1',(u['id'],lead['id'])).fetchone()
            retry_failure=bool(data.retry_failed and failed and failed['status']=='failed' and not failed['answered_seen'])
            if data.retry_failed and not retry_failure:raise HTTPException(409,'Only a confirmed failed, unanswered connection can be retried this way')
            if retry_failure:db.execute('UPDATE dial_attempts SET ended_at=COALESCE(ended_at,CURRENT_TIMESTAMP),result_recorded=1 WHERE id=?',(failed['id'],))
            if db.execute('SELECT id FROM dial_attempts WHERE user_id=? AND result_recorded=0',(u['id'],)).fetchone():raise HTTPException(409,'Finish the current call result before starting another')
            if data.list_id and not db.execute('SELECT 1 FROM calling_list_members m JOIN calling_lists l ON l.id=m.list_id WHERE l.id=? AND l.user_id=? AND m.lead_id=?',(data.list_id,u['id'],lead['id'])).fetchone():raise HTTPException(409,'Lead was removed from this calling list')
            from leads_service import row_dict
            fresh=db.execute('SELECT * FROM leads WHERE id=? AND user_id=?',(lead['id'],u['id'])).fetchone()
            current=row_dict(fresh) if fresh else None
            history=db.execute("SELECT result,created_at FROM call_events WHERE user_id=? AND lead_id=? AND ignored=0 ORDER BY id",(u['id'],lead['id'])).fetchall()
            tz=ZoneInfo(snapshot['preferences']['timezone']);today=datetime.now(timezone.utc).astimezone(tz).date()
            last=db.execute('SELECT result FROM call_events WHERE user_id=? AND lead_id=? AND ignored=0 ORDER BY id DESC LIMIT 1',(u['id'],lead['id'])).fetchone()
            from dispositions import schedule
            timing=schedule(db,u['id'],lead['id'])
            from crm_service import blocked as contact_blocked
            sales=db.execute('SELECT * FROM lead_sales WHERE user_id=? AND lead_id=?',(u['id'],lead['id'])).fetchone()
            excluded=sales and (sales['do_not_contact'] or sales['assigned_out'] or sales['stage'] in ('Won','Lost'))
            allowed=timing['call_ready'] or ((data.manual_repeat or retry_failure) and timing['manual_retry_allowed'])
            if not current or fresh['deleted_at'] or (current['state']!='ready' and not data.list_id and not data.manual_repeat) or (current and (not phone_key(current['phone']) or any(current['facts'].get(k) for k in ('permanently_closed','illegitimate')))) or excluded or contact_blocked(db,u['id'],fresh) or not allowed:raise HTTPException(409,'Lead is not ready: '+timing['schedule_reason']+'. Refresh the calling list.')
            lead['phone']=current['phone']
            if snapshot['mode']=='practice':number='Practice line';reason='Simulated call — no real connection'
            else:
                from shared_services import calling_config
                cfg=calling_config(u['id']);pool=number_pool(cfg,u['email']);counters={r['number']:r['uses'] for r in db.execute('SELECT * FROM number_rotation WHERE user_id=?',(u['id'],))}
                centres=settings()['dialer'].get('area_code_centres',{})
                number,reason=choose_number(pool,lead['phone'],counters,centres)
                db.execute('INSERT INTO number_rotation VALUES(?,?,1) ON CONFLICT(user_id,number) DO UPDATE SET uses=uses+1',(u['id'],number))
            script=db.execute("SELECT s.* FROM scripts s JOIN script_preferences p ON p.active_script=s.id WHERE p.user_id=? AND s.user_id=? AND s.kind='call' AND s.archived=0 AND s.deleted=0",(u['id'],u['id'])).fetchone()
            from coach_profiles import snapshot as profile_snapshot
            coach_profile=profile_snapshot(db,u['id'])
            if coach_profile.get('script'):script=coach_profile['script']
            script_snapshot=dict(script) if script else {}
            attempt=secrets.token_urlsafe(24)
            from shared_services import calling_reservation
            call_limit=calling_reservation(db,u['id'],attempt,cfg) if snapshot['mode']!='practice' else 0
            db.execute('INSERT INTO dial_attempts(id,user_id,lead_id,started_at,status,mode,dial_dispatched,phone,caller_number,route_reason,script_id,script_snapshot) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(attempt,u['id'],lead['id'],timestamp(),'ringing' if practice else 'preparing',snapshot['mode'],int(practice),'+1'+phone_key(lead['phone']),number,reason,script['id'] if script else None,json.dumps(script_snapshot)))
            db.execute('UPDATE dial_attempts SET coach_profile_snapshot=? WHERE id=?',(json.dumps(coach_profile),attempt))
            if snapshot['mode']!='practice':db.execute('UPDATE dial_attempts SET provider_owner=?,sponsored=?,call_limit_seconds=? WHERE id=?',(cfg['provider_owner'],int(cfg['sponsored']),call_limit,attempt))
        return {'coach_profile_name':coach_profile.get('name','Default coach'),'id':attempt,'funding':snapshot['funding'],'call_limit_seconds':call_limit,'lead_id':lead['id'],'mode':snapshot['mode'],'caller_number':number,'route_reason':reason,'script_id':script['id'] if script else None,'script_snapshot':script_snapshot}

    @app.post('/api/dialer/attempts/{attempt_id}/finish-without-result')
    def finish_without_result(attempt_id:str,request:Request):
        with connect() as db:
            db.execute('BEGIN IMMEDIATE');row=owned_attempt(db,user(request)['id'],attempt_id)
            if row['status'] not in ('ended','failed'):raise HTTPException(409,'Hang up before finishing this call')
            db.execute('UPDATE dial_attempts SET ended_at=COALESCE(ended_at,CURRENT_TIMESTAMP),result_recorded=1 WHERE id=?',(attempt_id,))
        return {'ok':True}

    @app.post('/api/dialer/attempts/{attempt_id}/hangup')
    def end_call(attempt_id:str,request:Request):
        uid=user(request)['id']
        with connect() as db:
            row=owned_attempt(db,uid,attempt_id);db.execute('UPDATE dial_attempts SET manual_stop=1 WHERE id=?',(attempt_id,))
        hangup(row)
        with connect() as db:db.execute("UPDATE dial_attempts SET ended_at=COALESCE(ended_at,CURRENT_TIMESTAMP),status=CASE WHEN status='failed' THEN 'failed' ELSE 'ended' END WHERE id=?",(attempt_id,))
        return {'ok':True,'note':'Choose a result to finish this lead. No result was recorded automatically.'}

    @app.post('/api/dialer/attempts/{attempt_id}/result')
    def record(attempt_id:str,data:Outcome,request:Request):
        uid=user(request)['id'];labels={r['label'] for r in settings()['dialer']['results']}|{'Call Later'}
        if data.result not in labels:raise HTTPException(400,'Unknown call result')
        with connect() as db:row=owned_attempt(db,uid,attempt_id)
        hangup(row)
        with connect() as db:
            db.execute('BEGIN IMMEDIATE');row=owned_attempt(db,uid,attempt_id)
            previous=db.execute('SELECT id,result FROM call_events WHERE attempt_id=? AND ignored=0 ORDER BY id DESC LIMIT 1',(attempt_id,)).fetchone()
            if previous:
                if previous['result']==data.result:return {'ok':True,'event_id':previous['id'],'already_saved':True}
                raise HTTPException(409,'Mark the previous result ignored before recording a correction')
            if data.note.strip():
                db.execute("INSERT INTO lead_notes(user_id,lead_id,text,created_at,kind) VALUES(?,?,?,?,'call_note')",(uid,row['lead_id'],data.note.strip(),timestamp()))
                db.execute('UPDATE dial_attempts SET call_note=? WHERE id=?',(data.note.strip(),attempt_id))
            from dispositions import apply_disposition
            apply_disposition(db,uid,row['lead_id'],data.result)
            source='dialer' if row['dial_dispatched'] else 'leads'
            event=db.execute('INSERT INTO call_events(user_id,lead_id,result,source,created_at,caller_number,script_id,attempt_id,recorded_at) VALUES(?,?,?,?,?,?,?,?,?)',(uid,row['lead_id'],data.result,source,row['started_at'],row['caller_number'],row['script_id'],attempt_id,timestamp())).lastrowid
            db.execute("UPDATE dial_attempts SET ended_at=COALESCE(ended_at,CURRENT_TIMESTAMP),status=CASE WHEN status='failed' THEN 'failed' ELSE 'ended' END,result_recorded=1 WHERE id=?",(attempt_id,))
            db.execute("UPDATE followups SET state='completed' WHERE user_id=? AND lead_id=? AND state='open' AND due_at<=?",(uid,row['lead_id'],row['started_at']))
            db.execute("INSERT INTO lead_sales(user_id,lead_id,stage) VALUES(?,?,?) ON CONFLICT(user_id,lead_id) DO UPDATE SET stage=CASE WHEN lead_sales.stage IN ('New','Contacted') THEN excluded.stage ELSE lead_sales.stage END",(uid,row['lead_id'],'Meeting booked' if data.result=='Booked' else 'Contacted'))
        return {'ok':True,'event_id':event,'source':source}

    @app.post('/api/dialer/token')
    def token(request:Request):
        u=user(request)
        from shared_services import calling_config
        cfg=calling_config(u['id'])
        if practice or not cfg['enabled'] or not number_pool(cfg,u['email']):raise HTTPException(503,'Real calling is not enabled')
        from twilio.jwt.access_token import AccessToken
        from twilio.jwt.access_token.grants import VoiceGrant
        lifetime=settings()['dialer']['token_ttl_seconds']
        grant=VoiceGrant(outgoing_application_sid=cfg['TWILIO_TWIML_APP_SID'],incoming_allow=False)
        token=AccessToken(cfg['TWILIO_ACCOUNT_SID'],cfg['TWILIO_API_KEY_SID'],cfg['TWILIO_API_KEY_SECRET'],identity=f"user_{u['id']}",ttl=lifetime);token.add_grant(grant)
        return {'token':token.to_jwt(),'expires_in':lifetime}

    async def verified_form(request):
        body=await request.body()
        if len(body)>32000:raise HTTPException(413,'Webhook too large')
        if not request.headers.get('content-type','').startswith('application/x-www-form-urlencoded'):raise HTTPException(415,'Twilio form request required')
        try:form=FormData(parse_qsl(body.decode(),keep_blank_values=True))
        except UnicodeError:raise HTTPException(400,'Invalid form')
        attempt=request.query_params.get('attempt') or form.get('AttemptId','')
        with connect() as db:row=db.execute('SELECT * FROM dial_attempts WHERE id=?',(attempt,)).fetchone()
        if not row:raise HTTPException(403,'Call is not authorized')
        from shared_services import attempt_config
        cfg=attempt_config(row)
        if practice or not cfg['enabled']:raise HTTPException(503,'Real calling is not enabled')
        from twilio.request_validator import RequestValidator
        url=cfg['PUBLIC_BASE_URL'].rstrip('/')+request.url.path+('?' + request.url.query if request.url.query else '')
        if not RequestValidator(cfg['TWILIO_AUTH_TOKEN']).validate(url,form,request.headers.get('x-twilio-signature','')):raise HTTPException(403,'Invalid Twilio signature')
        if form.get('AccountSid')!=cfg['TWILIO_ACCOUNT_SID']:raise HTTPException(403,'Twilio account does not match')
        return cfg,form

    @app.post('/api/twilio/voice')
    async def voice(request:Request):
        cfg,form=await verified_form(request)
        attempt=form.get('AttemptId','');identity=form.get('From','');lead=form.get('LeadId','')
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM dial_attempts WHERE id=?',(attempt,)).fetchone()
            if not row or identity!=f"client:user_{row['user_id']}" or lead!=str(row['lead_id']) or row['result_recorded'] or row['status'] not in ('preparing','ringing'):raise HTTPException(403,'Call is not authorized')
            if row['twilio_sid'] and row['twilio_sid']!=form.get('CallSid'):raise HTTPException(403,'Call attempt is already bound')
            db.execute("UPDATE dial_attempts SET twilio_sid=?,dial_dispatched=1,status='ringing' WHERE id=?",(form.get('CallSid'),attempt))
        from twilio.twiml.voice_response import VoiceResponse
        response=VoiceResponse();dial=response.dial(caller_id=row['caller_number'],answer_on_bridge=True,timeout=25,**({'time_limit':row['call_limit_seconds']} if row['call_limit_seconds'] else {}))
        dial.number(row['phone'],status_callback=cfg['PUBLIC_BASE_URL'].rstrip('/')+'/api/twilio/status?attempt='+attempt,status_callback_method='POST',status_callback_event='initiated ringing answered completed')
        return Response(str(response),media_type='application/xml')

    @app.post('/api/twilio/status')
    async def status(request:Request):
        cfg,form=await verified_form(request);attempt=request.query_params.get('attempt','')
        mapping={'initiated':'ringing','ringing':'ringing','in-progress':'connected','completed':'ended','busy':'ended','no-answer':'ended','failed':'failed','canceled':'ended'}
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM dial_attempts WHERE id=?',(attempt,)).fetchone()
            if not row or not row['twilio_sid'] or not form.get('CallSid') or (form.get('ParentCallSid') or form.get('CallSid'))!=row['twilio_sid']:raise HTTPException(403,'Call callback does not match')
            if row['child_sid'] and row['child_sid']!=form.get('CallSid'):raise HTTPException(403,'Call leg does not match')
            db.execute('UPDATE dial_attempts SET child_sid=COALESCE(child_sid,?) WHERE id=?',(form.get('CallSid'),attempt))
            if form.get('CallStatus')=='in-progress':db.execute('UPDATE dial_attempts SET answered_seen=1,answered_at=COALESCE(answered_at,?) WHERE id=?',(timestamp(),attempt))
            if form.get('CallStatus')=='no-answer':auto_no_answer(db,row)
            if form.get('CallStatus') in ('completed','busy','no-answer','failed','canceled'):
                duration=form.get('CallDuration','')
                seconds=min(int(duration),86400) if str(duration).isdigit() else None
                db.execute('UPDATE dial_attempts SET ended_at=COALESCE(ended_at,?),duration_seconds=COALESCE(?,duration_seconds) WHERE id=?',(timestamp(),seconds,attempt))
            state=mapping.get(form.get('CallStatus'))
            if state and not row['result_recorded'] and row['status'] not in ('ended','failed'):db.execute('UPDATE dial_attempts SET status=? WHERE id=?',(state,attempt))
        return Response(status_code=204)

    app.state.verified_twilio_form=verified_form
    from call_history_service import install as install_history
    install_history(app,connect,user,practice,verified_form)

    from shift_service import install as install_shifts
    install_shifts(app,connect,user)

