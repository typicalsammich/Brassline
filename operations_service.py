import os,secrets,sqlite3,threading,time,json,shutil
from pathlib import Path
from contextlib import closing,asynccontextmanager
from datetime import datetime,timezone
from fastapi import Request,HTTPException

def install(app,connect,user,practice,db_path):
    folder=Path(db_path).parent/'backups';lock=threading.Lock();stop=threading.Event()
    with connect() as db:db.execute('CREATE TABLE IF NOT EXISTS backup_runs(id INTEGER PRIMARY KEY,path TEXT NOT NULL,created_at TEXT NOT NULL,state TEXT NOT NULL,detail TEXT NOT NULL)')
    def snapshot(force=False):
        if practice:return {'state':'disabled','detail':'Practice backups are disabled'}
        if not lock.acquire(blocking=False):return {'state':'running','detail':'A backup is already running'}
        path=None
        verified=False
        try:
            today=datetime.now(timezone.utc).date().isoformat()
            with connect() as db:
                if not force and db.execute("SELECT 1 FROM backup_runs WHERE state='verified' AND created_at LIKE ?",(today+'%',)).fetchone():return {'state':'current'}
            folder.mkdir(parents=True,exist_ok=True);path=folder/('brassline-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(3)+'.sqlite3')
            with connect() as src,closing(sqlite3.connect(path)) as dst:src.backup(dst)
            restore=folder/('restore-test-'+secrets.token_hex(8)+'.sqlite3')
            try:
                shutil.copyfile(path,restore)
                with closing(sqlite3.connect(restore)) as restored:
                    if restored.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Backup verification failed')
                    restored.execute('SELECT COUNT(*) FROM users').fetchone();restored.execute('SELECT COUNT(*) FROM leads').fetchone();restored.execute('SELECT COUNT(*) FROM call_events').fetchone()
            finally:restore.unlink(missing_ok=True)
            verified=True
            created=datetime.now(timezone.utc).isoformat()
            with connect() as db:
                db.execute('INSERT INTO backup_runs(path,created_at,state,detail) VALUES(?,?,?,?)',(path.name,created,'verified','SQLite backup copied to a scratch database and integrity/read checks passed.'))
                files=sorted(folder.glob('brassline-*.sqlite3'),key=lambda p:p.stat().st_mtime,reverse=True)
                for old in files[7:]:old.unlink()
            return {'state':'verified','detail':'Backup created and restore-file check passed'}
        except Exception:
            if path and not verified:
                try:path.unlink(missing_ok=True)
                except OSError:pass
            with connect() as db:db.execute('INSERT INTO backup_runs(path,created_at,state,detail) VALUES(?,?,?,?)',('',datetime.now(timezone.utc).isoformat(),'failed','Automatic backup failed. Check available disk space and server permissions.'))
            return {'state':'failed','detail':'Backup failed; check disk space and server permissions'}
        finally:lock.release()
    def background():
        while not stop.is_set():
            try:
                from datetime import timedelta
                with connect() as db:
                    cutoff=(datetime.now(timezone.utc)-timedelta(days=30)).isoformat()
                    db.execute('DELETE FROM transcript_segments WHERE created_at<?',(cutoff,))
                    db.execute('DELETE FROM coaching_jobs WHERE attempt_id IN (SELECT id FROM dial_attempts WHERE started_at<?)',(cutoff,))
                    db.execute('DELETE FROM transcript_partials WHERE updated_at<?',(time.time()-3600,))
                    db.execute('DELETE FROM public_research_cache WHERE expires<?',(time.time(),))
            except Exception:pass
            if os.getenv('BACKUP_ENABLED','1')=='1':snapshot()
            stop.wait(3600)
    previous=app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(current):
        async with previous(current):
            stop.clear()
            if not practice:threading.Thread(target=background,daemon=True,name='database-backups').start()
            try:yield
            finally:stop.set()
    app.router.lifespan_context=lifespan
    @app.post('/api/operations/backup')
    def backup(request:Request):
        if not user(request)['admin']:raise HTTPException(403,'Administrator access required')
        return snapshot(True)
    @app.get('/api/operations')
    def status(request:Request):
        u=user(request)
        if not u['admin']:raise HTTPException(403,'Administrator access required')
        from dialer_service import twilio_config
        from call_assistant_service import transcription_ready
        from email_service import email_ready
        with connect() as db:
            recent=[dict(r) for r in db.execute('SELECT created_at,state,detail,path FROM backup_runs ORDER BY id DESC LIMIT 10')]
            key=db.execute('SELECT last_seen,worker_name FROM worker_keys WHERE user_id=?',(u['id'],)).fetchone()
        return {'practice':practice,'backups_enabled':not practice and os.getenv('BACKUP_ENABLED','1')=='1','backups':recent,'backup_location':str(folder),'backup_retained':7,'worker_connected':bool(key and time.time()-key['last_seen']<45),'worker_name':key['worker_name'] if key else '', 'email_ready':email_ready(),'calling_ready':twilio_config(u['id'])['enabled'],'transcription_ready':transcription_ready() and twilio_config(u['id'])['enabled'],'online':os.getenv('ONLINE')=='1','live_checks':{'google':'needs authenticated live test','calling':'needs configured provider and live test','mobile':'needs browser walkthrough'}}
