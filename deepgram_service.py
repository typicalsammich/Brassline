"""Consent-gated Twilio -> Deepgram bridge. Audio is forwarded in memory only."""
import asyncio,base64,hashlib,hmac,json,math,secrets,threading,time
from contextlib import AsyncExitStack,suppress
from datetime import datetime,timezone
from urllib.parse import urlencode,urlsplit
from fastapi import HTTPException,Request,WebSocket,WebSocketDisconnect
from fastapi.responses import Response
import shared_services as shared

CONNECT=None
ACTIVE=set()
LOCK=threading.Lock()
MAX_BRIDGES=3

def now():return datetime.now(timezone.utc).isoformat()
def dg_url():
    # One socket per known speaker avoids paid diarization and preserves track labels.
    return 'wss://api.deepgram.com/v1/listen?'+urlencode({'model':'nova-3','language':'en-US','encoding':'mulaw','sample_rate':8000,'channels':1,'interim_results':'true','punctuate':'true','endpointing':300,'mip_opt_out':'true'})
def client_for(row):
    from twilio.rest import Client
    from twilio.http.http_client import TwilioHttpClient
    cfg=shared.attempt_config(row)
    return Client(cfg['TWILIO_ACCOUNT_SID'],cfg['TWILIO_AUTH_TOKEN'],http_client=TwilioHttpClient(timeout=8))
def owned(db,uid,attempt):
    row=db.execute('SELECT * FROM dial_attempts WHERE id=? AND user_id=?',(attempt,uid)).fetchone()
    if not row:raise HTTPException(404,'Call not found')
    return row
def update_off(db,attempt,message=''):
    db.execute('UPDATE dial_attempts SET transcription_enabled=0,transcript_pending=0,coaching_enabled=0,transcript_error=? WHERE id=?',(message,attempt))
    db.execute('DELETE FROM transcript_partials WHERE attempt_id=?',(attempt,))
    db.execute("UPDATE coaching_jobs SET state='off',text='',error='',revision=revision+1 WHERE attempt_id=?",(attempt,))
def status(attempt):
    if CONNECT is None:return None
    with CONNECT() as db:row=db.execute('SELECT state,expires,limit_seconds,agent_bytes,prospect_bytes FROM deepgram_sessions WHERE attempt_id=?',(attempt,)).fetchone()
    return dict(row) if row else None

def start(uid,attempt,ack_streaming):
    if not ack_streaming:raise HTTPException(400,'Acknowledge that Twilio audio streaming is charged to the account paying for this call')
    cfg=shared.config()
    if not cfg['transcription_enabled'] or cfg.get('transcription_provider')!='deepgram' or not cfg.get('deepgram_key'):raise HTTPException(503,'Administrator Deepgram transcription is not connected')
    nonce=secrets.token_urlsafe(32);session_id=secrets.token_urlsafe(18)
    with CONNECT() as db:
        db.execute('BEGIN IMMEDIATE');row=owned(db,uid,attempt)
        if row['status']!='connected' or not row['twilio_sid'] or row['result_recorded']:raise HTTPException(409,'Wait for a connected call and confirm participant permission')
        if row['transcript_pending']:raise HTTPException(409,'A transcription request is already in progress')
        if row['transcription_enabled']:return {'ok':True,'already_started':True}
        if db.execute('SELECT 1 FROM deepgram_sessions WHERE attempt_id=?',(attempt,)).fetchone() or row['transcription_sid']:raise HTTPException(409,'Transcription was already started for this call; stopped sessions cannot restart')
        if db.execute('SELECT 1 FROM transcript_deletions WHERE attempt_id=?',(attempt,)).fetchone():raise HTTPException(409,'This transcript was deleted')
        calling=shared.attempt_config(row);base=calling['PUBLIC_BASE_URL'].rstrip('/')
        if not calling['enabled'] or urlsplit(base).scheme!='https':raise HTTPException(503,'This call needs a configured public HTTPS Twilio connection')
        with LOCK:busy=len(ACTIVE)>=MAX_BRIDGES
        pending=db.execute("SELECT COUNT(*) FROM deepgram_sessions WHERE state IN ('starting','streaming','stopping') AND expires>?",(time.time(),)).fetchone()[0]
        if busy or pending>=MAX_BRIDGES:raise HTTPException(429,'All three transcription slots are in use; try another call later')
        limit=shared.allowance(db,uid)
        remaining=min(limit['transcription']-shared.used(db,uid,'transcription'),cfg['transcription']-shared.used(db,None,'transcription'))
        minutes=min(cfg['max_transcription_minutes'],remaining//2)
        if row['call_limit_seconds']:minutes=min(minutes,row['call_limit_seconds']//60)
        if minutes<1:raise HTTPException(429,'Your Deepgram allowance needs at least two audio minutes for one call minute')
        shared.reserve_in(db,uid,'transcription',2*minutes,'deepgram:'+attempt)
        db.execute('INSERT INTO deepgram_sessions(id,attempt_id,user_id,nonce_hash,state,expires,limit_seconds,created_at) VALUES(?,?,?,?,\'starting\',?,?,?)',(session_id,attempt,uid,hashlib.sha256(nonce.encode()).hexdigest(),time.time()+minutes*60,minutes*60,time.time()))
        db.execute("UPDATE dial_attempts SET transcription_provider='deepgram',transcription_enabled=1,transcript_pending=1,transcript_error='' WHERE id=?",(attempt,))
    try:
        stream=client_for(row).calls(row['twilio_sid']).streams.create(url='wss://'+base.removeprefix('https://')+'/api/twilio/media/'+attempt+'/'+nonce,name='brassline-'+session_id,track='both_tracks',parameter1_name='SessionId',parameter1_value=session_id,status_callback=base+'/api/twilio/stream-status?attempt='+attempt,status_callback_method='POST')
        with CONNECT() as db:
            current=db.execute('SELECT stream_sid,state FROM deepgram_sessions WHERE id=?',(session_id,)).fetchone()
            if current['stream_sid'] and current['stream_sid']!=stream.sid:raise ValueError('Stream identity mismatch')
            db.execute('UPDATE deepgram_sessions SET stream_sid=? WHERE id=?',(stream.sid,session_id))
            db.execute('UPDATE dial_attempts SET transcription_sid=?,transcript_pending=0 WHERE id=?',(stream.sid,attempt))
        return {'ok':True,'provider':'deepgram','max_minutes':minutes,'message':'Deepgram requested. Twilio streaming is billed to the calling account; transcription is billed to the administrator.'}
    except Exception:
        with CONNECT() as db:
            db.execute("UPDATE deepgram_sessions SET state='failed' WHERE id=?",(session_id,))
            update_off(db,attempt,'Could not confirm the audio stream start. Check Twilio logs; no automatic paid retry was made.')
        # A timeout may mean Twilio created the stream; the expired permission prevents forwarding.
        raise HTTPException(502,'Could not confirm Twilio stream start. Check provider logs before making another call.')

def stop(uid,attempt):
    with CONNECT() as db:
        db.execute('BEGIN IMMEDIATE');row=owned(db,uid,attempt);session=db.execute('SELECT * FROM deepgram_sessions WHERE attempt_id=?',(attempt,)).fetchone()
        if not session:return {'ok':True}
        if row['transcript_pending'] and not session['stream_sid']:raise HTTPException(409,'Wait for the stream-start request to finish before stopping')
        db.execute("UPDATE deepgram_sessions SET state='stopping' WHERE id=?",(session['id'],));update_off(db,attempt)
    try:
        if session['stream_sid']:client_for(row).calls(row['twilio_sid']).streams(session['stream_sid']).update(status='stopped')
    except Exception:
        with CONNECT() as db:db.execute('UPDATE dial_attempts SET transcript_error=? WHERE id=?',('Transcription forwarding stopped, but Twilio stream stop was not confirmed. Retry Stop transcription or hang up.',attempt))
        raise HTTPException(502,'Audio forwarding stopped. Could not confirm Twilio streaming stop; retry or hang up to stop its billing.')
    with CONNECT() as db:db.execute("UPDATE deepgram_sessions SET state='stopped' WHERE id=?",(session['id'],))
    return {'ok':True}

def eligible(session,row):
    return session and session['state'] in ('starting','streaming') and session['expires']>time.time() and row and row['transcription_enabled'] and row['transcription_provider']=='deepgram' and not row['result_recorded'] and row['status']=='connected'
def validate_start(message,session,row,cfg):
    start=message.get('start',{});fmt=start.get('mediaFormat',{})
    return (message.get('event')=='start' and start.get('accountSid')==cfg['TWILIO_ACCOUNT_SID'] and start.get('callSid')==row['twilio_sid'] and bool(start.get('streamSid')) and start.get('customParameters',{}).get('SessionId')==session['id'] and set(start.get('tracks',[]))=={'inbound','outbound'} and fmt.get('encoding')=='audio/x-mulaw' and fmt.get('sampleRate')==8000 and fmt.get('channels')==1 and (not session['stream_sid'] or session['stream_sid']==start['streamSid']))

def save_result(session_id,speaker,event):
    if event.get('type')!='Results':return
    try:
        text=event['channel']['alternatives'][0]['transcript'];start=float(event['start']);duration=float(event['duration']);final=event.get('is_final') is True
    except (KeyError,IndexError,TypeError,ValueError):return
    if not isinstance(text,str) or not text.strip() or len(text)>8000 or not math.isfinite(start) or not math.isfinite(duration) or start<0 or duration<0:return
    with CONNECT() as db:
        db.execute('BEGIN IMMEDIATE');session=db.execute('SELECT * FROM deepgram_sessions WHERE id=?',(session_id,)).fetchone()
        row=db.execute('SELECT * FROM dial_attempts WHERE id=?',(session['attempt_id'],)).fetchone() if session else None
        if not eligible(session,row) or db.execute('SELECT 1 FROM transcript_deletions WHERE attempt_id=?',(session['attempt_id'],)).fetchone():return
        sequence=int(start*1000)
        if final:
            provider_key='deepgram:'+session_id+':'+speaker+':'+str(sequence)+':'+str(round(duration*1000))
            db.execute('INSERT OR IGNORE INTO transcript_segments(attempt_id,user_id,provider_key,speaker,text,created_at,source) VALUES(?,?,?,?,?,?,\'deepgram\')',(session['attempt_id'],session['user_id'],provider_key,speaker,text,now()))
            if speaker=='prospect':db.execute('DELETE FROM transcript_partials WHERE attempt_id=? AND sequence<=?',(session['attempt_id'],sequence))
        elif speaker=='prospect':
            db.execute("INSERT INTO transcript_partials(attempt_id,speaker,text,sequence,updated_at) VALUES(?,'prospect',?,?,?) ON CONFLICT(attempt_id) DO UPDATE SET text=excluded.text,sequence=excluded.sequence,updated_at=excluded.updated_at WHERE excluded.sequence>=transcript_partials.sequence",(session['attempt_id'],text,sequence,time.time()))
    if speaker=='prospect':__import__('coaching_service').queue(session['attempt_id'])

async def bridge(websocket,session,row,provider_connect=None):
    """Two bounded mono sockets, concurrent results, no audio files or unbounded queues."""
    from websockets.asyncio.client import connect
    provider_connect=provider_connect or connect;tasks=[];failure='';reason='Transcription stopped.'
    cfg=shared.config();sent={'agent':0,'prospect':0};last_chunk={'agent':0,'prospect':0};last_send={'agent':time.monotonic(),'prospect':time.monotonic()}
    try:
        async with AsyncExitStack() as stack:
            sockets={speaker:await stack.enter_async_context(provider_connect(dg_url(),additional_headers={'Authorization':'Token '+cfg['deepgram_key']},open_timeout=8,close_timeout=2,ping_interval=20,max_size=65536,max_queue=8,compression=None,proxy=None)) for speaker in ('agent','prospect')}
            async def results(speaker):
                async for raw in sockets[speaker]:
                    if len(raw)>65536:raise ValueError('Oversized transcription event')
                    event=json.loads(raw)
                    if event.get('type')=='Error':raise ValueError('Deepgram returned an error')
                    await asyncio.to_thread(save_result,session['id'],speaker,event)
            async def media():
                while True:
                    raw=await websocket.receive_text()
                    if len(raw)>18000:raise ValueError('Oversized media event')
                    message=json.loads(raw)
                    if message.get('streamSid')!=session['stream_sid']:raise ValueError('Wrong audio stream')
                    if message.get('event')=='stop':return
                    if message.get('event')!='media':continue
                    payload=message.get('media',{});track=payload.get('track');speaker={'inbound':'agent','outbound':'prospect'}.get(track)
                    if not speaker:raise ValueError('Unknown audio track')
                    chunk=int(payload.get('chunk',0))
                    if chunk<=last_chunk[speaker]:continue
                    packet=base64.b64decode(payload.get('payload',''),validate=True)
                    if not packet or len(packet)>8000:raise ValueError('Invalid audio frame')
                    # Enforce audio duration as well as wall time; forged fast frames cannot overspend.
                    if sent[speaker]+len(packet)>session['limit_seconds']*8000:return
                    last_chunk[speaker]=chunk;sent[speaker]+=len(packet)
                    await sockets[speaker].send(packet);last_send[speaker]=time.monotonic()
            async def monitor():
                while True:
                    await asyncio.sleep(.5)
                    with CONNECT() as db:
                        current=db.execute('SELECT * FROM deepgram_sessions WHERE id=?',(session['id'],)).fetchone();call=db.execute('SELECT * FROM dial_attempts WHERE id=?',(row['id'],)).fetchone()
                        deleted=db.execute('SELECT 1 FROM transcript_deletions WHERE attempt_id=?',(row['id'],)).fetchone()
                    if not eligible(current,call) or deleted:return
                    for speaker in sockets:
                        if time.monotonic()-last_send[speaker]>3:
                            await sockets[speaker].send(json.dumps({'type':'KeepAlive'}));last_send[speaker]=time.monotonic()
            tasks=[asyncio.create_task(media()),asyncio.create_task(monitor()),asyncio.create_task(results('agent')),asyncio.create_task(results('prospect'))]
            done,_=await asyncio.wait(tasks,timeout=max(.1,session['expires']-time.time()),return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task.exception():raise task.exception()
            # Drain final provider results only while the call still has permitted time remaining.
            if session['expires']>time.time():
                for socket in sockets.values():
                    with suppress(Exception):await socket.send(json.dumps({'type':'Finalize'}))
                await asyncio.sleep(min(.15,max(0,session['expires']-time.time())))
            reason='Transcription stopped or reached its allowance; the phone call can continue.'
    except (WebSocketDisconnect,asyncio.CancelledError):pass
    except Exception:failure='Deepgram audio connection failed. Call audio can continue; review provider setup. No automatic paid reconnect was made.'
    finally:
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        with CONNECT() as db:
            db.execute('UPDATE deepgram_sessions SET state=?,agent_bytes=?,prospect_bytes=? WHERE id=?',('failed' if failure else 'stopped',sent['agent'],sent['prospect'],session['id']))
            update_off(db,row['id'],failure or reason)
        try:await asyncio.to_thread(lambda:client_for(row).calls(row['twilio_sid']).streams(session['stream_sid']).update(status='stopped'))
        except Exception:
            with CONNECT() as db:
                db.execute("UPDATE deepgram_sessions SET state='stopping' WHERE id=?",(session['id'],))
                db.execute('UPDATE dial_attempts SET transcript_error=? WHERE id=?',((failure or reason)+' Twilio stream stop was not confirmed; hang up or retry Stop transcription to end streaming billing.',row['id']))
        with suppress(Exception):await websocket.close(code=1000)

def install(app,connect,user,practice,verified_form):
    global CONNECT
    CONNECT=connect
    with connect() as db:
        columns={r[1] for r in db.execute('PRAGMA table_info(dial_attempts)')}
        if 'transcription_provider' not in columns:db.execute("ALTER TABLE dial_attempts ADD COLUMN transcription_provider TEXT NOT NULL DEFAULT 'twilio'")
        db.executescript('''CREATE TABLE IF NOT EXISTS deepgram_sessions(id TEXT PRIMARY KEY,attempt_id TEXT UNIQUE NOT NULL REFERENCES dial_attempts(id),user_id INTEGER NOT NULL REFERENCES users(id),nonce_hash TEXT NOT NULL,state TEXT NOT NULL,expires REAL NOT NULL,limit_seconds INTEGER NOT NULL,stream_sid TEXT,agent_bytes INTEGER NOT NULL DEFAULT 0,prospect_bytes INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL);''')
        # A restarted process cannot resume sockets safely or retry billable audio automatically.
        for row in db.execute("SELECT attempt_id FROM deepgram_sessions WHERE state IN ('starting','streaming','stopping')"):
            update_off(db,row['attempt_id'],'Server restarted; transcription stopped. Finish or hang up the call before starting another.')
        db.execute("UPDATE deepgram_sessions SET state='interrupted' WHERE state IN ('starting','streaming','stopping')")
    @app.websocket('/api/twilio/media/{attempt}/{nonce}')
    async def media_socket(websocket:WebSocket,attempt:str,nonce:str):
        with connect() as db:
            session=db.execute('SELECT * FROM deepgram_sessions WHERE attempt_id=?',(attempt,)).fetchone();row=db.execute('SELECT * FROM dial_attempts WHERE id=?',(attempt,)).fetchone()
        if practice or not eligible(session,row) or not hmac.compare_digest(session['nonce_hash'],hashlib.sha256(nonce.encode()).hexdigest()):await websocket.close(code=1008);return
        cfg=shared.attempt_config(row)
        from twilio.request_validator import RequestValidator
        validator=RequestValidator(cfg['TWILIO_AUTH_TOKEN']);url=cfg['PUBLIC_BASE_URL'].rstrip('/')+websocket.url.path
        signature=websocket.headers.get('x-twilio-signature','')
        if websocket.url.query or not any(validator.validate(candidate,{},signature) for candidate in (url,url.replace('https://','wss://',1))):await websocket.close(code=1008);return
        with LOCK:
            reject=session['id'] in ACTIVE or len(ACTIVE)>=MAX_BRIDGES
            if not reject:ACTIVE.add(session['id'])
        if reject:await websocket.close(code=1013);return
        try:
            await websocket.accept()
            async def read_start():
                for _ in range(2):
                    raw=await websocket.receive_text()
                    if len(raw)>18000:raise ValueError('Oversized start')
                    message=json.loads(raw)
                    if message.get('event')=='connected':continue
                    return message
                raise ValueError('Missing stream start')
            message=await asyncio.wait_for(read_start(),8)
            if not validate_start(message,session,row,cfg):await websocket.close(code=1008);return
            with connect() as db:
                db.execute('BEGIN IMMEDIATE');latest=db.execute('SELECT * FROM deepgram_sessions WHERE id=?',(session['id'],)).fetchone();fresh=db.execute('SELECT * FROM dial_attempts WHERE id=?',(attempt,)).fetchone()
                reject=not eligible(latest,fresh) or latest['state']!='starting'
                if not reject:
                    sid=message['start']['streamSid'];db.execute("UPDATE deepgram_sessions SET state='streaming',stream_sid=? WHERE id=?",(sid,session['id']));db.execute('UPDATE dial_attempts SET transcription_sid=?,transcript_pending=0 WHERE id=?',(sid,attempt))
                    session=dict(latest);session['stream_sid']=sid;session['state']='streaming'
            if reject:await websocket.close(code=1008);return
            await bridge(websocket,session,row)
        except Exception:
            with suppress(Exception):await websocket.close(code=1008)
        finally:
            with LOCK:ACTIVE.discard(session['id'])
    @app.post('/api/twilio/stream-status')
    async def stream_status(request:Request):
        _,form=await verified_form(request);attempt=request.query_params.get('attempt','')
        with connect() as db:
            db.execute('BEGIN IMMEDIATE');session=db.execute('SELECT * FROM deepgram_sessions WHERE attempt_id=?',(attempt,)).fetchone();row=db.execute('SELECT * FROM dial_attempts WHERE id=?',(attempt,)).fetchone()
            if not session or not row or form.get('CallSid')!=row['twilio_sid'] or form.get('StreamName')!='brassline-'+session['id'] or not form.get('StreamSid') or (session['stream_sid'] and session['stream_sid']!=form['StreamSid']):raise HTTPException(403,'Audio stream callback does not match')
            db.execute('UPDATE deepgram_sessions SET stream_sid=COALESCE(stream_sid,?) WHERE id=?',(form['StreamSid'],session['id']))
            if form.get('StreamEvent') in ('stream-stopped','stream-error'):
                db.execute('UPDATE deepgram_sessions SET state=? WHERE id=?',('failed' if form['StreamEvent']=='stream-error' else 'stopped',session['id']))
                update_off(db,attempt,'Twilio audio stream failed. Check provider logs.' if form['StreamEvent']=='stream-error' else 'Audio stream stopped.')
        return Response(status_code=204)
    # Clean up orphaned Twilio streams after a process restart or missing WebSocket handshake.
    stop_event=threading.Event()
    def cleanup():
        while not stop_event.wait(10):
            try:
                with connect() as db:rows=db.execute("SELECT d.*,a.twilio_sid,a.provider_owner,a.sponsored,a.mode,a.user_id AS caller_user,a.transcription_provider FROM deepgram_sessions d JOIN dial_attempts a ON a.id=d.attempt_id WHERE d.stream_sid IS NOT NULL AND (d.state IN ('interrupted','failed','stopping','streaming') OR (d.state='starting' AND d.created_at<?))",(time.time()-20,)).fetchall()
            except Exception:continue
            for session in rows:
                with LOCK:active=session['id'] in ACTIVE
                if active:continue
                try:
                    with connect() as db:call=db.execute('SELECT * FROM dial_attempts WHERE id=?',(session['attempt_id'],)).fetchone()
                    client_for(call).calls(call['twilio_sid']).streams(session['stream_sid']).update(status='stopped')
                    with connect() as db:db.execute("UPDATE deepgram_sessions SET state='stopped' WHERE id=?",(session['id'],));update_off(db,session['attempt_id'],'Transcription stopped after an interrupted audio connection.')
                except Exception:pass
    from contextlib import asynccontextmanager
    previous=app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(current):
        async with previous(current):
            stop_event.clear()
            if not practice:threading.Thread(target=cleanup,daemon=True,name='stream-cleanup').start()
            try:yield
            finally:stop_event.set()
    app.router.lifespan_context=lifespan
