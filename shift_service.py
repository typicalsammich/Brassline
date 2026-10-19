"""Manual shift clock with private caller records and administrator reporting."""
import time
from datetime import datetime,date,time as day_time,timedelta,timezone
from zoneinfo import ZoneInfo
from fastapi import Request,HTTPException
from pydantic import BaseModel,Field
from typing import Literal

ZONE=ZoneInfo('America/New_York')
STATUSES=['Dialing','Break','Lunch','Technical Issues']
class ShiftRef(BaseModel):shift_id:int
class ShiftStatus(ShiftRef):
    status:Literal['Dialing','Break','Lunch','Technical Issues']
    note:str=Field(default='',max_length=1000)

def split_days(start,end,zone=ZONE):
    while start<end:
        local=datetime.fromtimestamp(start,zone);tomorrow=datetime.combine(local.date()+timedelta(days=1),day_time.min,tzinfo=zone).timestamp()
        finish=min(end,tomorrow)
        yield local.date().isoformat(),finish-start
        start=finish

def install(app,connect,user):
    with connect() as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS work_shifts(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),started REAL NOT NULL,ended REAL,last_seen REAL NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS one_open_work_shift ON work_shifts(user_id) WHERE ended IS NULL;
        CREATE TABLE IF NOT EXISTS work_shift_events(id INTEGER PRIMARY KEY,shift_id INTEGER NOT NULL REFERENCES work_shifts(id),at REAL NOT NULL,status TEXT NOT NULL,note TEXT NOT NULL DEFAULT '');
        CREATE INDEX IF NOT EXISTS shift_events_order ON work_shift_events(shift_id,at,id);
        CREATE TRIGGER IF NOT EXISTS immutable_shift_event BEFORE UPDATE ON work_shift_events BEGIN SELECT RAISE(ABORT,'Shift status history cannot be overwritten'); END;''')
    def active(db,uid):return db.execute('SELECT * FROM work_shifts WHERE user_id=? AND ended IS NULL',(uid,)).fetchone()
    def view(db,row,now):
        value=dict(row);events=[dict(e) for e in db.execute('SELECT at,status,note FROM work_shift_events WHERE shift_id=? ORDER BY at,id',(row['id'],))]
        value.update(status=events[-1]['status'] if events else 'Dialing',events=events,elapsed_seconds=max(0,int((row['ended'] or now)-row['started'])),connection_stale=row['ended'] is None and now-row['last_seen']>180,review_needed=row['ended'] is None and now-row['started']>16*3600)
        return value
    def owned_open(db,uid,sid):
        row=active(db,uid)
        if not row or row['id']!=sid:raise HTTPException(409,'This shift is no longer active. Refresh the shift tracker.')
        return row
    def report(db,uid,start,end,now):
        args=[end,start];where='started<? AND (ended IS NULL OR ended>?)'
        if uid:where+=' AND user_id=?';args.append(uid)
        rows=db.execute('SELECT * FROM work_shifts WHERE '+where+' ORDER BY started DESC',args).fetchall();days={};shifts=[]
        for row in rows:
            item=view(db,row,now);shifts.append(item);events=item['events'];finish=min(row['ended'] or now,end)
            for i,event in enumerate(events):
                a=max(start,event['at']);b=min(finish,events[i+1]['at'] if i+1<len(events) else finish)
                if b<=a:continue
                for day,seconds in split_days(a,b):
                    key=(row['user_id'],day)
                    if key not in days:days[key]={'user_id':key[0],'day':day,'seconds':{s:0 for s in STATUSES},'total_seconds':0}
                    days[key]['seconds'][event['status']]+=seconds;days[key]['total_seconds']+=seconds
        for day in days.values():
            day['seconds']={k:int(v) for k,v in day['seconds'].items()};day['total_seconds']=sum(day['seconds'].values())
        members=[dict(r) for r in db.execute('SELECT id,email,display_name FROM users'+(' WHERE id=?' if uid else '')+' ORDER BY id',(uid,) if uid else ())]
        return {'timezone':'America/New_York','days':sorted(days.values(),key=lambda d:(d['day'],d['user_id']),reverse=True),'shifts':shifts,'members':members,'server_now':now,'statuses':STATUSES}

    @app.get('/api/shifts')
    def current(request:Request):
        uid=user(request)['id'];now=time.time();today=datetime.fromtimestamp(now,ZONE).date();start=datetime.combine(today-timedelta(days=6),day_time.min,tzinfo=ZONE).timestamp()
        with connect() as db:
            data=report(db,uid,start,now,now);row=active(db,uid);data['active']=view(db,row,now) if row else None
        return data

    @app.post('/api/shifts/start')
    def start(request:Request):
        uid=user(request)['id'];now=time.time()
        with connect() as db:
            db.execute('BEGIN IMMEDIATE');row=active(db,uid)
            if row:return {'id':row['id'],'already_started':True}
            sid=db.execute('INSERT INTO work_shifts(user_id,started,last_seen) VALUES(?,?,?)',(uid,now,now)).lastrowid
            db.execute("INSERT INTO work_shift_events(shift_id,at,status) VALUES(?,?,'Dialing')",(sid,now))
        return {'id':sid}

    @app.post('/api/shifts/status')
    def status(data:ShiftStatus,request:Request):
        uid=user(request)['id'];now=time.time()
        with connect() as db:
            db.execute('BEGIN IMMEDIATE');owned_open(db,uid,data.shift_id)
            previous=db.execute('SELECT status,note FROM work_shift_events WHERE shift_id=? ORDER BY at DESC,id DESC LIMIT 1',(data.shift_id,)).fetchone()
            if previous['status']!=data.status or (data.note.strip() and data.note.strip()!=previous['note']):db.execute('INSERT INTO work_shift_events(shift_id,at,status,note) VALUES(?,?,?,?)',(data.shift_id,now,data.status,data.note.strip()))
            db.execute('UPDATE work_shifts SET last_seen=? WHERE id=?',(now,data.shift_id))
        return {'ok':True}

    @app.post('/api/shifts/heartbeat')
    def heartbeat(data:ShiftRef,request:Request):
        with connect() as db:
            owned_open(db,user(request)['id'],data.shift_id);db.execute('UPDATE work_shifts SET last_seen=? WHERE id=?',(time.time(),data.shift_id))
        return {'ok':True}

    @app.post('/api/shifts/end')
    def end(data:ShiftRef,request:Request):
        uid=user(request)['id'];now=time.time()
        with connect() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM work_shifts WHERE id=? AND user_id=?',(data.shift_id,uid)).fetchone()
            if not row:raise HTTPException(404,'Shift not found')
            if row['ended'] is not None:return {'ok':True,'already_ended':True}
            db.execute('UPDATE work_shifts SET ended=?,last_seen=? WHERE id=?',(now,now,data.shift_id))
        return {'ok':True}

    @app.get('/api/shifts/report')
    def history(request:Request):
        u=user(request);now=time.time();today=datetime.fromtimestamp(now,ZONE).date()
        try:
            first=date.fromisoformat(request.query_params.get('from',str(today-timedelta(days=6))));last=date.fromisoformat(request.query_params.get('to',str(today)));uid=int(request.query_params.get('user_id','0' if u['admin'] else str(u['id'])))
            if uid<0 or not 0<=(last-first).days<=61:raise ValueError()
        except ValueError:raise HTTPException(400,'Choose a valid date range of at most 62 days')
        if not u['admin'] and uid!=u['id']:raise HTTPException(403,'Only administrators can review another account’s shifts')
        start=datetime.combine(first,day_time.min,tzinfo=ZONE).timestamp();end=datetime.combine(last+timedelta(days=1),day_time.min,tzinfo=ZONE).timestamp()
        with connect() as db:return report(db,uid,start,end,now)
