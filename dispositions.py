"""One disposition policy for queue display and transactional call checks."""
from datetime import datetime, timezone, timedelta

DELAYS={'No Answer':1,'Voicemail':1,'Not Interested':30,'Hung Up':7}
RESULTS=['No Answer','Voicemail','Not Interested','Hung Up','DNC','Callback','Booked','Wrong Number']

def parsed(value):
    value=datetime.fromisoformat(value.replace('Z','+00:00'))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

def schedule(db,uid,lead_id,at=None):
    at=at or datetime.now(timezone.utc)
    event=db.execute('SELECT * FROM call_events WHERE user_id=? AND lead_id=? AND ignored=0 ORDER BY id DESC LIMIT 1',(uid,lead_id)).fetchone()
    followups=db.execute("SELECT due_at FROM followups WHERE user_id=? AND lead_id=? AND state='open' ORDER BY due_at",(uid,lead_id)).fetchall()
    result=event['result'] if event else ''
    ready=True;due=None;reason='Never called'
    if result in ('DNC','Wrong Number','Booked'):
        ready=False;reason={'DNC':'Do not call','Wrong Number':'Wrong number — excluded','Booked':'Meeting booked — excluded'}[result]
    elif result in ('Callback','Call Later'):
        due=min((parsed(r['due_at']) for r in followups),default=None)
        ready=due is not None and due<=at;reason='Callback due' if ready else 'Callback scheduled' if due else 'Choose a callback date and time'
    elif result in DELAYS:
        # Use the time the disposition was saved, including older records without recorded_at.
        due=parsed(event['recorded_at'] or event['created_at'])+timedelta(days=DELAYS[result])
        ready=at>=due;reason='Ready for follow-up' if ready else result+' — waiting '+str(DELAYS[result])+' day'+('s' if DELAYS[result]!=1 else '')
    elif event:
        ready=False;reason='Review previous result'
    elif followups:
        due=min(parsed(r['due_at']) for r in followups);ready=due<=at;reason='Callback due' if ready else 'Callback scheduled'
    return {'call_ready':ready,'next_call_at':due.isoformat() if due else None,'schedule_reason':reason,'manual_retry_allowed':result not in ('DNC','Wrong Number','Booked','Callback','Call Later') and (result!='Not Interested' or ready)}

def apply_disposition(db,uid,lead_id,result):
    if result in ('Callback','Call Later'):
        db.execute("UPDATE followups SET state='cancelled' WHERE user_id=? AND lead_id=? AND state='open'",(uid,lead_id))
        return
    if result!='DNC':return
    from crm_service import contact_keys,now
    lead=db.execute('SELECT * FROM leads WHERE id=? AND user_id=?',(lead_id,uid)).fetchone()
    db.execute('INSERT OR IGNORE INTO lead_sales(user_id,lead_id) VALUES(?,?)',(uid,lead_id))
    db.execute('UPDATE lead_sales SET do_not_contact=1 WHERE user_id=? AND lead_id=?',(uid,lead_id))
    for key in contact_keys(lead):db.execute('INSERT OR REPLACE INTO contact_blocks VALUES(?,?,?,?)',(uid,key,'DNC disposition',now()))
    db.execute("UPDATE followups SET state='cancelled' WHERE user_id=? AND lead_id=? AND state='open'",(uid,lead_id))
