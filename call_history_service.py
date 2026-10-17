"""Private call history and explicit, opt-in provider recordings."""
import json,re
from datetime import datetime,timezone,timedelta
from urllib.parse import urlsplit,urljoin
from fastapi import Request,HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

def now():return datetime.now(timezone.utc).isoformat()
class Record(BaseModel):consent:bool=False;acknowledge_charges:bool=False

def install(app,connect,user,practice,verified_form):
    with connect() as db:
        cols={r[1] for r in db.execute('PRAGMA table_info(dial_attempts)')}
        for name,kind in [('answered_at','TEXT'),('ended_at','TEXT'),('duration_seconds','INTEGER'),('recording_pending','INTEGER NOT NULL DEFAULT 0')]:
            if name not in cols:db.execute('ALTER TABLE dial_attempts ADD COLUMN '+name+' '+kind)
        db.execute('CREATE TABLE IF NOT EXISTS call_recordings(sid TEXT PRIMARY KEY,attempt_id TEXT NOT NULL,user_id INTEGER NOT NULL,call_sid TEXT NOT NULL,status TEXT NOT NULL,duration_seconds INTEGER,created_at TEXT NOT NULL)')
        db.execute('CREATE INDEX IF NOT EXISTS recording_owner ON call_recordings(user_id,attempt_id)')
    def owned(db,uid,aid):
        row=db.execute('SELECT a.*,l.name,l.deleted_at AS lead_deleted_at FROM dial_attempts a JOIN leads l ON l.id=a.lead_id WHERE a.id=? AND a.user_id=?',(aid,uid)).fetchone()
        if not row:raise HTTPException(404,'Call not found')
        return row
    def item(db,row):
        value={k:row[k] for k in ('id','lead_id','name','phone','caller_number','started_at','answered_at','ended_at','duration_seconds','status','mode','result_recorded','lead_deleted_at','call_note')}
        event=db.execute('SELECT result FROM call_events WHERE attempt_id=? AND user_id=? AND ignored=0 ORDER BY id DESC LIMIT 1',(row['id'],row['user_id'])).fetchone()
        value['result']=event['result'] if event else None
        value['transcription_started']=bool(row['transcription_sid'] or row['transcription_enabled'])
        value['transcript_error']=row['transcript_error']
        value['coach_profile']=json.loads(row['coach_profile_snapshot'] or '{}').get('name','Default coach')
        value['script']=json.loads(row['script_snapshot'] or '{}').get('title','No script')
        cutoff=(datetime.now(timezone.utc)-timedelta(days=30)).isoformat()
        value['has_transcript']=bool(db.execute('SELECT 1 FROM transcript_segments WHERE attempt_id=? AND user_id=? AND created_at>=?',(row['id'],row['user_id'],cutoff)).fetchone())
        value['recordings']=[dict(r) for r in db.execute("SELECT sid,status,duration_seconds,created_at FROM call_recordings WHERE attempt_id=? AND user_id=? AND status!='deleted' ORDER BY created_at",(row['id'],row['user_id']))]
        return value
    @app.get('/api/call-history')
    def history(request:Request):
        uid=user(request)['id'];q=request.query_params.get('q','')[:200].strip();result=request.query_params.get('result','')[:80];lead=request.query_params.get('lead_id','0')
        try:offset=max(0,min(100000,int(request.query_params.get('offset','0'))));lid=int(lead)
        except ValueError:raise HTTPException(400,'Invalid history filter')
        where='a.user_id=?';args=[uid]
        if request.query_params.get('recordings_only')=='1':where+=" AND EXISTS(SELECT 1 FROM call_recordings r WHERE r.attempt_id=a.id AND r.user_id=a.user_id AND r.status!='deleted')"
        if q:
            digits=re.sub(r'[^0-9]','',q);phone_query=digits if len(digits)>=3 else q
            where+=' AND (l.name LIKE ? OR a.phone LIKE ?)';args+=['%'+q+'%','%'+phone_query+'%']
        if lid:where+=' AND a.lead_id=?';args.append(lid)
        if result:where+=' AND EXISTS(SELECT 1 FROM call_events e WHERE e.attempt_id=a.id AND e.user_id=a.user_id AND e.ignored=0 AND e.result=?)';args.append(result)
        with connect() as db:
            total=db.execute('SELECT COUNT(*) FROM dial_attempts a JOIN leads l ON l.id=a.lead_id WHERE '+where,args).fetchone()[0]
            rows=db.execute('SELECT a.*,l.name,l.deleted_at AS lead_deleted_at FROM dial_attempts a JOIN leads l ON l.id=a.lead_id WHERE '+where+' ORDER BY a.started_at DESC,a.id DESC LIMIT 40 OFFSET ?',args+[offset]).fetchall()
            return {'calls':[item(db,r) for r in rows],'total':total,'offset':offset,'limit':40}
    @app.get('/api/call-history/{attempt_id}')
    def detail(attempt_id:str,request:Request):
        uid=user(request)['id']
        with connect() as db:
            row=owned(db,uid,attempt_id);data=item(db,row);cutoff=(datetime.now(timezone.utc)-timedelta(days=30)).isoformat()
            data['segments']=[dict(r) for r in db.execute('SELECT speaker,text,created_at FROM transcript_segments WHERE attempt_id=? AND user_id=? AND created_at>=? ORDER BY id',(attempt_id,uid,cutoff))]
            return data
    def client(row):
        from shared_services import attempt_config
        from twilio.rest import Client
        from twilio.http.http_client import TwilioHttpClient
        cfg=attempt_config(row)
        return Client(cfg['TWILIO_ACCOUNT_SID'],cfg['TWILIO_AUTH_TOKEN'],http_client=TwilioHttpClient(timeout=12)),cfg
    @app.post('/api/call-history/{attempt_id}/refresh-media')
    def refresh_media(attempt_id:str,request:Request):
        uid=user(request)['id']
        with connect() as db:row=owned(db,uid,attempt_id)
        if row['mode']!='twilio':raise HTTPException(400,'Practice calls do not have Twilio audio')
        call_sids={sid for sid in (row['child_sid'],row['twilio_sid']) if sid}
        if not call_sids:return {'found':0,'note':'No provider call was connected for this attempt.'}
        try:
            provider,cfg=client(row);found=0;seen=set()
            for call_sid in call_sids:
                recordings=provider.calls(call_sid).recordings.list(limit=20)
                for recording in recordings:
                    if not re.fullmatch(r'RE[0-9a-fA-F]{32}',str(recording.sid)):continue
                    if recording.call_sid not in call_sids or recording.sid in seen:continue
                    seen.add(recording.sid)
                    status=str(recording.status)
                    if status not in ('completed','in-progress','processing','absent','failed'):continue
                    try:duration=int(recording.duration) if recording.duration is not None else None
                    except (TypeError,ValueError):duration=None
                    with connect() as db:
                        db.execute('INSERT INTO call_recordings(sid,attempt_id,user_id,call_sid,status,duration_seconds,created_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(sid) DO UPDATE SET status=excluded.status,duration_seconds=excluded.duration_seconds WHERE call_recordings.user_id=excluded.user_id AND call_recordings.attempt_id=excluded.attempt_id AND call_recordings.status!=\'deleted\'',(recording.sid,attempt_id,uid,recording.call_sid,status,duration,now()))
                    found+=1
        except Exception:raise HTTPException(502,'Could not check Twilio for recordings. No recording was started and no automatic retry was made.')
        return {'found':found,'note':'Provider recordings checked. This does not record or transcribe earlier calls.'}

    @app.post('/api/call-history/{attempt_id}/recordings/start')
    def start_recording(attempt_id:str,data:Record,request:Request):
        uid=user(request)['id']
        if not data.consent or not data.acknowledge_charges:raise HTTPException(400,'Confirm participant permission and Twilio recording/storage charges')
        with connect() as db:
            db.execute('BEGIN IMMEDIATE');row=owned(db,uid,attempt_id)
            if practice or row['mode']!='twilio':raise HTTPException(400,'Audio recording is available only during real calls')
            if row['status']!='connected' or row['result_recorded'] or not row['twilio_sid']:raise HTTPException(409,'Connect the call before recording')
            if row['recording_pending'] or db.execute("SELECT 1 FROM call_recordings WHERE attempt_id=? AND status IN ('in-progress','processing')",(attempt_id,)).fetchone():raise HTTPException(409,'Recording is already active or processing')
            db.execute('UPDATE dial_attempts SET recording_pending=1 WHERE id=?',(attempt_id,))
        try:
            provider,cfg=client(row);call_sid=row['child_sid'] or row['twilio_sid']
            recording=provider.calls(call_sid).recordings.create(recording_channels='dual',recording_track='both',recording_status_callback=cfg['PUBLIC_BASE_URL'].rstrip('/')+'/api/twilio/recording-status?attempt='+attempt_id,recording_status_callback_method='POST',recording_status_callback_event=['completed','absent'])
            if not re.fullmatch(r'RE[0-9a-fA-F]{32}',recording.sid):raise ValueError('Invalid recording response')
            with connect() as db:db.execute("INSERT OR IGNORE INTO call_recordings VALUES(?,?,?,?,'in-progress',NULL,?)",(recording.sid,attempt_id,uid,call_sid,now()))
        except Exception:raise HTTPException(502,'Recording could not be confirmed with Twilio. Check the provider call log before trying again; no automatic retry was made.')
        finally:
            with connect() as db:db.execute('UPDATE dial_attempts SET recording_pending=0 WHERE id=?',(attempt_id,))
        return {'ok':True,'sid':recording.sid}
    @app.post('/api/call-history/{attempt_id}/recordings/stop')
    def stop_recording(attempt_id:str,request:Request):
        uid=user(request)['id']
        with connect() as db:
            row=owned(db,uid,attempt_id);recordings=db.execute("SELECT * FROM call_recordings WHERE attempt_id=? AND user_id=? AND status='in-progress'",(attempt_id,uid)).fetchall()
        for recording in recordings:
            try:provider,_=client(row);provider.calls(recording['call_sid']).recordings(recording['sid']).update(status='stopped')
            except Exception:raise HTTPException(502,'Could not confirm recording stopped. Check Twilio before continuing.')
            with connect() as db:db.execute("UPDATE call_recordings SET status='processing' WHERE sid=? AND status='in-progress'",(recording['sid'],))
        return {'ok':True}
    @app.post('/api/twilio/recording-status')
    async def recording_status(request:Request):
        cfg,form=await verified_form(request);aid=request.query_params.get('attempt','');sid=form.get('RecordingSid','');state=form.get('RecordingStatus','')
        if not re.fullmatch(r'RE[0-9a-fA-F]{32}',sid) or state not in ('completed','absent','failed'):raise HTTPException(400,'Invalid recording event')
        with connect() as db:
            row=db.execute('SELECT * FROM dial_attempts WHERE id=?',(aid,)).fetchone()
            if not row or form.get('CallSid') not in (row['twilio_sid'],row['child_sid']):raise HTTPException(403,'Recording call does not match')
            existing=db.execute('SELECT * FROM call_recordings WHERE sid=?',(sid,)).fetchone()
            if existing and (existing['attempt_id']!=aid or existing['user_id']!=row['user_id']):raise HTTPException(403,'Recording owner does not match')
            duration=form.get('RecordingDuration','');seconds=min(int(duration),86400) if str(duration).isdigit() else None
            db.execute('INSERT OR IGNORE INTO call_recordings VALUES(?,?,?,?,?,?,?)',(sid,aid,row['user_id'],form['CallSid'],state,seconds,now()))
            db.execute("UPDATE call_recordings SET status=?,duration_seconds=? WHERE sid=? AND status!='deleted'",(state,seconds,sid))
        return Response(status_code=204)
    @app.get('/api/call-history/recordings/{sid}/audio')
    def audio(sid:str,request:Request):
        import requests
        uid=user(request)['id']
        with connect() as db:
            recording=db.execute("SELECT * FROM call_recordings WHERE sid=? AND user_id=? AND status='completed'",(sid,uid)).fetchone()
            if not recording:raise HTTPException(404,'Completed recording not found')
            row=owned(db,uid,recording['attempt_id'])
        _,cfg=client(row);url='https://api.twilio.com/2010-04-01/Accounts/'+cfg['TWILIO_ACCOUNT_SID']+'/Recordings/'+sid+'.mp3'
        try:
            for _ in range(4):
                parsed=urlsplit(url);host=parsed.hostname or ''
                if parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in (None,443) or not (host=='api.twilio.com' or host.endswith('.twilio.com') or host.endswith('.amazonaws.com') or host.endswith('.twiliocdn.com')):raise ValueError('Invalid media location')
                auth=(cfg['TWILIO_ACCOUNT_SID'],cfg['TWILIO_AUTH_TOKEN']) if host=='api.twilio.com' else None
                with requests.get(url,auth=auth,allow_redirects=False,stream=True,timeout=(5,20)) as response:
                    if response.status_code in (301,302,303,307,308):url=urljoin(url,response.headers['Location']);continue
                    if response.status_code!=200:raise ValueError('Media unavailable')
                    chunks=[];size=0
                    for chunk in response.iter_content(65536):
                        size+=len(chunk)
                        if size>25*1024*1024:raise ValueError('Recording too large')
                        chunks.append(chunk)
                    return Response(b''.join(chunks),media_type='audio/mpeg',headers={'Cache-Control':'private, no-store','Content-Disposition':'inline; filename="brassline-call.mp3"'})
            raise ValueError('Too many redirects')
        except Exception:raise HTTPException(502,'Recording audio is unavailable from Twilio. The app has not exposed your provider credentials.')
    @app.post('/api/call-history/recordings/{sid}/delete')
    def delete_recording(sid:str,request:Request):
        uid=user(request)['id']
        with connect() as db:
            recording=db.execute("SELECT * FROM call_recordings WHERE sid=? AND user_id=? AND status!='deleted'",(sid,uid)).fetchone()
            if not recording:raise HTTPException(404,'Recording not found')
            row=owned(db,uid,recording['attempt_id'])
        try:provider,_=client(row);provider.recordings(sid).delete()
        except Exception:raise HTTPException(502,'Could not confirm deletion with Twilio')
        with connect() as db:db.execute("UPDATE call_recordings SET status='deleted' WHERE sid=? AND user_id=?",(sid,uid))
        return {'ok':True}
