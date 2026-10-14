from datetime import datetime,timezone,timedelta,date
from zoneinfo import ZoneInfo
from fastapi import Request,HTTPException
from pydantic import BaseModel
from scoring import settings

class Milestone(BaseModel):
    call_event_id:int
    stage:str

def install(app,connect,user):
    with connect() as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS conversion_events(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),call_event_id INTEGER NOT NULL REFERENCES call_events(id),stage TEXT NOT NULL,created_at TEXT NOT NULL,ignored INTEGER NOT NULL DEFAULT 0);
        CREATE TRIGGER IF NOT EXISTS preserve_conversions BEFORE UPDATE ON conversion_events WHEN NEW.user_id != OLD.user_id OR NEW.call_event_id != OLD.call_event_id OR NEW.stage != OLD.stage OR NEW.created_at != OLD.created_at BEGIN SELECT RAISE(ABORT,'Conversion history cannot be overwritten'); END;
        CREATE TRIGGER IF NOT EXISTS keep_conversions BEFORE DELETE ON conversion_events BEGIN SELECT RAISE(ABORT,'Conversion history cannot be deleted'); END;''')

    @app.post('/api/analytics/milestones')
    def milestone(data:Milestone,request:Request):
        uid=user(request)['id']
        if data.stage not in ('Showed','Signed'):raise HTTPException(400,'Choose Showed or Signed')
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            parent=db.execute("SELECT id FROM call_events WHERE id=? AND user_id=? AND source='dialer' AND result='Booked' AND ignored=0",(data.call_event_id,uid)).fetchone()
            if not parent:raise HTTPException(404,'An active booked Dialer result is required')
            previous=db.execute('SELECT id FROM conversion_events WHERE user_id=? AND call_event_id=? AND stage=? AND ignored=0',(uid,data.call_event_id,data.stage)).fetchone()
            if previous:return {'ok':True,'id':previous['id']}
            if data.stage=='Signed' and not db.execute("SELECT id FROM conversion_events WHERE call_event_id=? AND stage='Showed' AND ignored=0",(data.call_event_id,)).fetchone():raise HTTPException(409,'Record the held meeting first')
            identifier=db.execute('INSERT INTO conversion_events(user_id,call_event_id,stage,created_at) VALUES(?,?,?,?)',(uid,data.call_event_id,data.stage,datetime.now(timezone.utc).isoformat())).lastrowid
        return {'ok':True,'id':identifier}

    @app.post('/api/analytics/milestones/{identifier}/ignore')
    def ignore(identifier:int,request:Request):
        with connect() as db:
            row=db.execute('SELECT * FROM conversion_events WHERE id=? AND user_id=?',(identifier,user(request)['id'])).fetchone()
            if not row:raise HTTPException(404,'Milestone not found')
            if row['stage']=='Showed' and db.execute("SELECT id FROM conversion_events WHERE call_event_id=? AND stage='Signed' AND ignored=0",(row['call_event_id'],)).fetchone():raise HTTPException(409,'Ignore the signed milestone first')
            db.execute('UPDATE conversion_events SET ignored=1 WHERE id=?',(identifier,))
        return {'ok':True}

    @app.get('/api/analytics')
    def analytics(request:Request,start:str|None=None,end:str|None=None):
        uid=user(request)['id']
        with connect() as db:
            pref=db.execute('SELECT timezone FROM dialer_preferences WHERE user_id=?',(uid,)).fetchone()
            tz=ZoneInfo(pref['timezone'] if pref else settings()['dialer']['default_timezone'])
            today=datetime.now(timezone.utc).astimezone(tz).date()
            try:last=date.fromisoformat(end) if end else today;first=date.fromisoformat(start) if start else last-timedelta(days=29)
            except ValueError:raise HTTPException(400,'Use valid dates')
            if last<first or (last-first).days>365:raise HTTPException(400,'Choose a date range of up to 366 days')
            rows=[dict(r) for r in db.execute("SELECT e.*,l.name,l.category,s.title AS script_title FROM call_events e JOIN leads l ON l.id=e.lead_id LEFT JOIN scripts s ON s.id=e.script_id AND s.user_id=e.user_id WHERE e.user_id=? AND e.source='dialer' AND e.ignored=0 ORDER BY e.created_at",(uid,))]
            conversions=[dict(r) for r in db.execute('SELECT * FROM conversion_events WHERE user_id=? AND ignored=0',(uid,))]
        def local(value):
            d=datetime.fromisoformat(value.replace('Z','+00:00'));return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(tz)
        rows=[r for r in rows if first<=local(r['created_at']).date()<=last]
        outcomes={r['label']:r for r in settings()['dialer']['results']}
        for r in rows:r['answered']=bool(outcomes.get(r['result'],{}).get('answered'));r['booked']=bool(outcomes.get(r['result'],{}).get('booked'))
        milestones={}
        for c in conversions:milestones.setdefault(c['call_event_id'],{})[c['stage']]=c['id']
        def metrics(items):
            n=len(items);a=sum(r['answered'] for r in items);b=sum(r['booked'] for r in items)
            return {'dials':n,'answered':a,'booked':b,'answer_rate':round(a/n*100,1) if n else None,'booking_rate':round(b/a*100,1) if a else None,'small_sample':0<n<20}
        totals=metrics(rows);bookings=[dict(r,milestones=milestones.get(r['id'],{})) for r in rows if r['booked']]
        totals['showed']=sum('Showed' in r['milestones'] for r in bookings);totals['signed']=sum('Showed' in r['milestones'] and 'Signed' in r['milestones'] for r in bookings)
        def groups(key,display=None):
            result=[]
            for label in sorted({str(r[key] or 'Unassigned') for r in rows}):
                items=[r for r in rows if str(r[key] or 'Unassigned')==label];result.append(dict(label=(items[0].get(display) or label) if display else label,**metrics(items)))
            return result
        trades=[]
        for label in sorted({r['category'] or 'Unknown industry' for r in rows}):
            businesses={}
            for r in rows:
                if (r['category'] or 'Unknown industry')==label:businesses.setdefault(r['lead_id'],[]).append(r)
            n=len(businesses);a=sum(any(r['answered'] for r in v) for v in businesses.values());b=sum(any(r['booked'] for r in v) for v in businesses.values())
            trades.append({'label':label,'businesses':n,'answered':a,'booked':b,'answer_rate':round(a/n*100,1) if n else None,'small_sample':n<20})
        days=[]
        for i in range((last-first).days+1):
            day=first+timedelta(days=i);days.append(dict(label=str(day),**metrics([r for r in rows if local(r['created_at']).date()==day])))
        return {'start':str(first),'end':str(last),'timezone':str(tz),'totals':totals,'days':days,'hours':[dict(label=f'{i:02}:00',**metrics([r for r in rows if local(r['created_at']).hour==i])) for i in range(24)],'weekdays':[dict(label=label,**metrics([r for r in rows if local(r['created_at']).weekday()==i])) for i,label in enumerate(['Mon','Tue','Wed','Thu','Fri','Sat','Sun'])],'trades':trades,'numbers':groups('caller_number'),'scripts':groups('script_id','script_title') if len({r['script_id'] for r in rows if r['script_id'] is not None})>=2 else [],'bookings':bookings}
