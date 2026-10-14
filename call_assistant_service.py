import json,os,re
from datetime import datetime,timezone,timedelta
from fastapi import Request,HTTPException
from pydantic import BaseModel,Field
from fastapi.responses import Response

class Permission(BaseModel):consent:bool;acknowledge_streaming:bool=False
class PracticeText(BaseModel):text:str=Field(min_length=1,max_length=4000);speaker:str='prospect'
def transcription_ready():return os.getenv('TWILIO_TRANSCRIPTION_ENABLED')=='1'
def suggestions(text,scripts,business):
    text=text.casefold().replace('’',"'");intent='discovery';fallback='How do most new customers find '+business+' today, and what would you like your website to do better?'
    patterns=[('stop',["do not call","don't call","stop calling","remove me","take me off"],'Respect the request. Say: “Understood. I will mark your business do not contact. Thank you.”'),('decline',['not interested','no thank you',"don't need",'do not need'],'Understood. Thank them for their time and end the conversation without pressure.'),('busy',['busy','bad time','not a good time','call back'],'No problem. Is there a better time, or would you prefer that I leave it here?'),('cost',['cost','price','expensive','budget','how much'],'The quote depends on the pages and features you need. Could I first understand what you want the website to accomplish?'),('website',['already have a website','have a site','already got a website'],'Is there anything about the current website you wish worked better? If it meets your needs, there may not be a reason to change it.'),('send',['send me','email me','text me'],'Of course. What would be most useful to send, and which business contact should I use?'),('interest',['interested','sounds good','meeting','appointment'],'Would a short conversation about the scope be useful? Agree on a time and confirm the preferred business contact.')]
    for name,words,response in patterns:
        if any(word in text for word in words):intent=name;fallback=response;break
    matches=[]
    if intent!='stop':
        tokens={'busy':['time','busy'],'cost':['cost','price','budget'],'website':['website','site'],'send':['send','email','text'],'interest':['meeting','interest']}.get(intent,[])
        for s in scripts:
            if any(token in s['title'].casefold() for token in tokens):matches.append({'title':s['title'],'body':s['body']})
    return {'intent':intent,'prompt':fallback,'objections':matches[:3],'basis':'Script-based suggestions. Review before speaking.','do_not_contact_suggested':intent=='stop'}

def install(app,connect,user,practice,verified_form):
    with connect() as db:
        columns={r[1] for r in db.execute('PRAGMA table_info(dial_attempts)')}
        for name,definition in [('transcription_enabled','INTEGER NOT NULL DEFAULT 0'),('transcription_sid','TEXT'),('transcript_error',"TEXT NOT NULL DEFAULT ''"),('transcript_pending','INTEGER NOT NULL DEFAULT 0')]:
            if name not in columns:db.execute(f'ALTER TABLE dial_attempts ADD COLUMN {name} {definition}')
        db.executescript('''CREATE TABLE IF NOT EXISTS transcript_segments(id INTEGER PRIMARY KEY,attempt_id TEXT NOT NULL REFERENCES dial_attempts(id),user_id INTEGER NOT NULL REFERENCES users(id),provider_key TEXT NOT NULL,speaker TEXT NOT NULL,text TEXT NOT NULL,created_at TEXT NOT NULL,source TEXT NOT NULL,UNIQUE(attempt_id,provider_key));
        CREATE TABLE IF NOT EXISTS transcript_deletions(attempt_id TEXT PRIMARY KEY REFERENCES dial_attempts(id),created_at TEXT NOT NULL);''')
    def owned(db,uid,attempt):
        row=db.execute('SELECT a.*,l.name FROM dial_attempts a JOIN leads l ON l.id=a.lead_id WHERE a.id=? AND a.user_id=?',(attempt,uid)).fetchone()
        if not row:raise HTTPException(404,'Call not found')
        return row
    def purge(db):
        cutoff=(datetime.now(timezone.utc)-timedelta(days=30)).isoformat()
        db.execute('DELETE FROM transcript_segments WHERE created_at<?',(cutoff,))
        db.execute('DELETE FROM coaching_jobs WHERE attempt_id IN (SELECT id FROM dial_attempts WHERE started_at<?)',(cutoff,))
        db.execute('DELETE FROM transcript_partials WHERE updated_at<?',(__import__('time').time()-3600,))

    @app.get('/api/dialer/attempts/{attempt}/assistant')
    def assistant(attempt:str,request:Request):
        uid=user(request)['id']
        with connect() as db:
            row=owned(db,uid,attempt);purge(db);segments=[dict(r) for r in db.execute('SELECT * FROM transcript_segments WHERE attempt_id=? AND user_id=? ORDER BY id',(attempt,uid))]
            pref=db.execute('SELECT objections FROM script_preferences WHERE user_id=?',(uid,)).fetchone();selected=json.loads(pref['objections']) if pref else []
            scripts=[dict(r) for r in db.execute("SELECT title,body,id FROM scripts WHERE user_id=? AND kind='objection' AND archived=0 AND deleted=0",(uid,)) if r['id'] in selected]
        prospect=next((r['text'] for r in reversed(segments) if r['speaker']=='prospect'),'')
        return {'enabled':bool(row['transcription_enabled']),'available':practice or bool(row['transcription_enabled']) or ((__import__('shared_services').transcription_available(row) or transcription_ready()) and __import__('shared_services').attempt_config(row)['enabled']),'mode':'practice' if row['mode']=='practice' else 'live','segments':segments,'error':row['transcript_error'],'suggestion':suggestions(prospect,scripts,row['name']),'retention_days':30,'coaching':__import__('coaching_service').state(attempt),'coaching_enabled':bool(row['coaching_enabled']),'funding':'Administrator-funded' if row['sponsored'] else 'Your Twilio account','transcription_provider':row['transcription_provider'] if row['transcription_enabled'] or row['transcription_sid'] else __import__('shared_services').config()['transcription_provider'],'deepgram':__import__('deepgram_service').status(attempt)}

    @app.post('/api/dialer/attempts/{attempt}/assistant/permission')
    def permission(attempt:str,data:Permission,request:Request):
        uid=user(request)['id']
        with connect() as db:existing=owned(db,uid,attempt)
        chosen=existing['transcription_provider'] if existing['transcription_enabled'] or existing['transcription_sid'] else __import__('shared_services').config()['transcription_provider']
        if not practice and chosen=='deepgram':
            from deepgram_service import start,stop
            return start(uid,attempt,data.acknowledge_streaming) if data.consent else stop(uid,attempt)
        from dialer_service import twilio_config
        with connect() as db:
            db.execute('BEGIN IMMEDIATE');row=owned(db,uid,attempt)
            from shared_services import attempt_config,transcription_available,reserve_in
            cfg=attempt_config(row)
            if row['result_recorded'] or row['status']=='ended':raise HTTPException(409,'This call has ended')
            if row['transcript_pending']:raise HTTPException(409,'A transcription request is in progress; wait a moment')
            if practice:
                db.execute('UPDATE dial_attempts SET transcription_enabled=? WHERE id=?',(int(data.consent),attempt))
                return {'ok':True}
            if data.consent and (not cfg['enabled'] or not (transcription_ready() or transcription_available(row))):raise HTTPException(503,'Live transcription is not connected')
            if data.consent and (row['status']!='connected' or not row['twilio_sid']):raise HTTPException(409,'Wait until the call is connected and permission is confirmed')
            if data.consent and row['transcription_enabled']:return {'ok':True}
            if data.consent and row['transcription_sid']:raise HTTPException(409,'Transcription was stopped for this call; it cannot be restarted')
            if data.consent and row['sponsored']:
                if not transcription_available(row):raise HTTPException(403,'Administrator-funded transcription is unavailable')
                reserve_in(db,uid,'transcription',max(1,(row['call_limit_seconds']+59)//60),'transcript:'+attempt)
            if not data.consent:
                db.execute('UPDATE dial_attempts SET coaching_enabled=0 WHERE id=?',(attempt,))
                db.execute("DELETE FROM transcript_partials WHERE attempt_id=?",(attempt,))
                db.execute("UPDATE coaching_jobs SET state='off',text='',revision=revision+1 WHERE attempt_id=?",(attempt,))
            db.execute('UPDATE dial_attempts SET transcription_enabled=?,transcript_pending=1 WHERE id=?',(int(data.consent),attempt))
        from twilio.rest import Client
        from twilio.http.http_client import TwilioHttpClient
        client=Client(cfg['TWILIO_ACCOUNT_SID'],cfg['TWILIO_AUTH_TOKEN'],http_client=TwilioHttpClient(timeout=10))
        try:
            if data.consent:
                session=client.calls(row['twilio_sid']).transcriptions.create(name='Brassline',track='both_tracks',status_callback_url=cfg['PUBLIC_BASE_URL'].rstrip('/')+'/api/twilio/transcription?attempt='+attempt,status_callback_method='POST',inbound_track_label='Agent',outbound_track_label='Prospect',partial_results=True,language_code='en-US')
                with connect() as db:db.execute('UPDATE dial_attempts SET transcription_sid=?,transcript_pending=0 WHERE id=?',(session.sid,attempt))
            elif row['transcription_sid']:
                client.calls(row['twilio_sid']).transcriptions(row['transcription_sid']).update(status='stopped')
                with connect() as db:db.execute('UPDATE dial_attempts SET transcript_pending=0 WHERE id=?',(attempt,))
            else:
                with connect() as db:db.execute('UPDATE dial_attempts SET transcript_pending=0 WHERE id=?',(attempt,))
        except Exception:
            with connect() as db:db.execute('UPDATE dial_attempts SET transcription_enabled=?,transcript_error=?,transcript_pending=0 WHERE id=?',(int(not data.consent),'Could not confirm transcription start/stop with the provider.',attempt))
            raise HTTPException(502,'Could not confirm transcription start/stop. Retry before continuing.')
        return {'ok':True}

    @app.post('/api/dialer/attempts/{attempt}/assistant/practice')
    def sample(attempt:str,data:PracticeText,request:Request):
        uid=user(request)['id']
        if data.speaker not in ('agent','prospect'):raise HTTPException(400,'Choose agent or prospect')
        with connect() as db:
            row=owned(db,uid,attempt)
            if not practice or row['mode']!='practice':raise HTTPException(403,'Simulated transcript input is practice-only')
            if not row['transcription_enabled'] or row['result_recorded']:raise HTTPException(409,'Enable the practice assistant on the current call first')
            if db.execute('SELECT 1 FROM transcript_deletions WHERE attempt_id=?',(attempt,)).fetchone():raise HTTPException(409,'Transcript was deleted')
            import secrets
            db.execute('INSERT INTO transcript_segments(attempt_id,user_id,provider_key,speaker,text,created_at,source) VALUES(?,?,?,?,?,?,?)',(attempt,uid,secrets.token_hex(12),data.speaker,data.text,datetime.now(timezone.utc).isoformat(),'practice'))
        return {'ok':True}

    @app.post('/api/dialer/attempts/{attempt}/transcript/delete')
    def delete(attempt:str,request:Request):
        uid=user(request)['id']
        with connect() as db:
            owned(db,uid,attempt);db.execute('UPDATE dial_attempts SET coaching_enabled=0 WHERE id=?',(attempt,));db.execute('DELETE FROM transcript_partials WHERE attempt_id=?',(attempt,));db.execute('DELETE FROM coaching_jobs WHERE attempt_id=?',(attempt,));db.execute('DELETE FROM transcript_segments WHERE attempt_id=? AND user_id=?',(attempt,uid));db.execute('INSERT OR IGNORE INTO transcript_deletions VALUES(?,?)',(attempt,datetime.now(timezone.utc).isoformat()))
        return {'ok':True}

    @app.get('/api/dialer/attempts/{attempt}/transcript/export')
    def export(attempt:str,request:Request):
        data=assistant(attempt,request)
        text='Brassline '+data['mode']+' transcript\n'+'\n'.join(r['created_at']+' '+r['speaker']+': '+r['text'] for r in data['segments'])
        return Response(text,media_type='text/plain',headers={'Content-Disposition':'attachment; filename="brassline-transcript.txt"'})

    @app.post('/api/twilio/transcription')
    async def callback(request:Request):
        _,form=await verified_form(request);attempt=request.query_params.get('attempt','')
        with connect() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM dial_attempts WHERE id=?',(attempt,)).fetchone()
            if not row or row['mode']!='twilio' or row['transcription_provider']!='twilio' or not row['twilio_sid'] or not row['transcription_enabled'] or form.get('CallSid')!=row['twilio_sid']:raise HTTPException(403,'Transcription does not match an authorized call')
            if db.execute('SELECT 1 FROM transcript_deletions WHERE attempt_id=?',(attempt,)).fetchone():return Response(status_code=204)
            sid=form.get('TranscriptionSid','')
            if not sid:raise HTTPException(400,'Missing transcription session')
            if row['transcription_sid'] and row['transcription_sid']!=sid:raise HTTPException(403,'Transcription session does not match')
            db.execute('UPDATE dial_attempts SET transcription_sid=? WHERE id=?',(sid,attempt))
            event=form.get('TranscriptionEvent')
            if event=='transcription-error':db.execute('UPDATE dial_attempts SET transcript_error=? WHERE id=?',('Provider transcription failed; review the calling provider logs.',attempt))
            if event=='transcription-content':
                try:data=json.loads(form.get('TranscriptionData','{}'));text=data['transcript'];sequence=int(form['SequenceId']);track=form['Track']
                except (ValueError,KeyError,TypeError):raise HTTPException(400,'Invalid transcription event')
                if not isinstance(text,str) or len(text)>8000 or track not in ('inbound_track','outbound_track'):raise HTTPException(400,'Invalid transcript content')
                final=str(form.get('Final','')).lower()=='true'
                if not final and track=='outbound_track':
                    db.execute("INSERT INTO transcript_partials(attempt_id,speaker,text,sequence,updated_at) VALUES(?,'prospect',?,?,?) ON CONFLICT(attempt_id) DO UPDATE SET text=excluded.text,sequence=excluded.sequence,updated_at=excluded.updated_at WHERE excluded.sequence>=transcript_partials.sequence",(attempt,text,sequence,__import__('time').time()))
                elif final and track=='outbound_track':db.execute('DELETE FROM transcript_partials WHERE attempt_id=? AND sequence<=?',(attempt,sequence))
                if final and text.strip():db.execute('INSERT OR IGNORE INTO transcript_segments(attempt_id,user_id,provider_key,speaker,text,created_at,source) VALUES(?,?,?,?,?,?,?)',(attempt,row['user_id'],sid+':'+track+':'+str(sequence),'agent' if track=='inbound_track' else 'prospect',text,datetime.now(timezone.utc).isoformat(),'twilio'))
            purge(db)
        if event=='transcription-content' and track=='outbound_track':__import__('coaching_service').queue(attempt)
        return Response(status_code=204)
