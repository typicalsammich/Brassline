import hashlib
import hmac
import json
import secrets
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime,timezone
from fastapi import HTTPException,Request
from pydantic import BaseModel,Field
from scoring import score,settings,phone_key
from scrape_planning import industry,location_index,plan_tiles,suggestions
from leads_service import FIELDS,name_key

class NewRun(BaseModel):
    industry:str=Field(min_length=2,max_length=150)
    location_id:str=Field(min_length=2,max_length=160)
    radius_km:int=Field(default=20,ge=5,le=50)
    radius_miles:float|None=Field(default=None,ge=3.2,le=31)
    max_tiles:int=Field(default=12,ge=1,le=60)
    max_leads:int=Field(default=60,ge=1,le=300)

class Claim(BaseModel):worker_name:str=Field(default='My computer',max_length=100)
class Lease(BaseModel):
    run_id:int
    lease_token:str=Field(max_length=150)
class Progress(Lease):
    message:str=Field(default='',max_length=500)
    tile_key:str=Field(default='',max_length=300)
    final_state:str=Field(default='',pattern='^(|completed|failed|cancelled)$')
class Card(Lease):
    name:str=Field(min_length=1,max_length=300)
    phone:str=Field(default='',max_length=80)
    place_id:str=Field(default='',max_length=300)
    source_url:str=Field(max_length=2000)
    category:str=Field(default='',max_length=150)
    cheap_reason:str=Field(default='',max_length=300)
class LeadResult(Lease):
    lead_id:int
    details:dict=Field(default_factory=dict)

def install(app,connect,user,practice):
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS scraper_runs(id INTEGER PRIMARY KEY,user_id INTEGER REFERENCES users(id),batch_id INTEGER REFERENCES batches(id),config TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'queued',message TEXT NOT NULL DEFAULT 'Waiting for your local worker',created_at TEXT NOT NULL,updated_at REAL NOT NULL,lease_token TEXT,tiles_done INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS search_tiles(user_id INTEGER REFERENCES users(id),tile_key TEXT,run_id INTEGER REFERENCES scraper_runs(id),completed_at TEXT NOT NULL,PRIMARY KEY(user_id,tile_key));
        CREATE TABLE IF NOT EXISTS run_leads(run_id INTEGER REFERENCES scraper_runs(id),lead_id INTEGER REFERENCES leads(id),checked INTEGER NOT NULL DEFAULT 0,qualified INTEGER NOT NULL DEFAULT 0,opened INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(run_id,lead_id));
        CREATE TABLE IF NOT EXISTS worker_keys(user_id INTEGER PRIMARY KEY REFERENCES users(id),token_hash TEXT NOT NULL,created_at REAL NOT NULL,last_seen REAL NOT NULL DEFAULT 0,worker_name TEXT NOT NULL DEFAULT 'My computer');
        ''')

    def authenticate(request):
        token=request.headers.get('authorization','').removeprefix('Bearer ')
        if not token:raise HTTPException(401,'Worker connection is missing')
        with connect() as db:
            row=db.execute('SELECT * FROM worker_keys WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            if not row:raise HTTPException(401,'Worker connection has been revoked. Download a new worker configuration.')
            db.execute('UPDATE worker_keys SET last_seen=? WHERE user_id=?',(time.time(),row['user_id']))
            return row['user_id']

    def lease(db,uid,data):
        run=db.execute('SELECT * FROM scraper_runs WHERE id=? AND user_id=?',(data.run_id,uid)).fetchone()
        if not run or run['state'] not in ('running','cancel_requested') or not hmac.compare_digest(run['lease_token'] or '',data.lease_token):raise HTTPException(409,'Run is no longer assigned to this worker')
        if time.time()-run['updated_at']>settings()['scraper']['lease_seconds']:raise HTTPException(409,'Worker lease expired; reconnect and resume')
        return run

    def claim_run(uid,worker_name):
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE scraper_runs SET state='queued',lease_token=NULL,message='Worker disconnected; waiting to resume' WHERE user_id=? AND state='running' AND updated_at<?",(uid,time.time()-settings()['scraper']['lease_seconds']))
            db.execute("UPDATE scraper_runs SET state='cancelled',message='Stopped after worker disconnected' WHERE user_id=? AND state='cancel_requested' AND updated_at<?",(uid,time.time()-settings()['scraper']['lease_seconds']))
            if db.execute("SELECT id FROM scraper_runs WHERE user_id=? AND state IN ('running','cancel_requested')",(uid,)).fetchone():return None
            run=db.execute("SELECT * FROM scraper_runs WHERE user_id=? AND state='queued' ORDER BY id LIMIT 1",(uid,)).fetchone()
            if not run:return None
            token=secrets.token_urlsafe(24)
            db.execute("UPDATE scraper_runs SET state='running',lease_token=?,updated_at=?,message='Starting local Chrome' WHERE id=?",(token,time.time(),run['id']))
            db.execute('UPDATE worker_keys SET worker_name=? WHERE user_id=?',(worker_name,uid))
            job={'id':run['id'],'lease_token':token,'config':json.loads(run['config'])}
            done={r[0] for r in db.execute('SELECT tile_key FROM search_tiles WHERE user_id=?',(uid,))}
            job['config']['tiles']=[tile for tile in job['config']['tiles'] if tile['key'] not in done]
            return job

    def reserve(uid,data):
        with connect() as db:
            db.execute('BEGIN IMMEDIATE');run=lease(db,uid,data);config=json.loads(run['config'])
            if run['state']=='cancel_requested':return {'lead_id':None,'should_open':False,'cancel':True}
            existing=db.execute('SELECT * FROM leads WHERE user_id=? AND place_id=?',(uid,data.place_id)).fetchone() if data.place_id else None
            if not existing:
                candidates=db.execute('SELECT id,name,phone FROM leads WHERE user_id=?',(uid,)).fetchall()
                if phone_key(data.phone):existing=next((r for r in candidates if phone_key(r['phone'])==phone_key(data.phone)),None)
                if not existing:existing=next((r for r in candidates if name_key(r['name'])==name_key(data.name)),None)
            if existing:lead_id=existing['id'];new=False
            else:
                facts={'category_approved':not data.cheap_reason,'website_need':'unknown'}
                if data.cheap_reason=='Permanently closed':facts['permanently_closed']=True
                elif data.cheap_reason:facts['wrong_industry']=True
                raw={'scrape_status':'skipped_from_card' if data.cheap_reason else 'reserved_before_open','source':data.source_url,'checked_at':datetime.now(timezone.utc).isoformat(),'skip_reason':data.cheap_reason}
                lead_id=db.execute('INSERT INTO leads(user_id,batch_id,name,phone,place_id,category,city,source_url,facts,review_facts) VALUES(?,?,?,?,?,?,?,?,?,?)',(uid,run['batch_id'],data.name,data.phone,data.place_id,data.category,config['location']['city'] or config['location']['state'],data.source_url,json.dumps(facts),json.dumps(raw))).lastrowid;new=True
            db.execute('INSERT OR IGNORE INTO lead_batches VALUES(?,?)',(lead_id,run['batch_id']))
            qualified=0
            if not new:
                from leads_service import row_dict
                qualified=int(row_dict(db.execute('SELECT * FROM leads WHERE id=?',(lead_id,)).fetchone())['state']=='ready')
            db.execute('INSERT OR IGNORE INTO run_leads(run_id,lead_id,checked,qualified,opened) VALUES(?,?,?,?,?)',(run['id'],lead_id,int(not new or bool(data.cheap_reason)),qualified,int(new and not data.cheap_reason)))
            db.execute('UPDATE scraper_runs SET updated_at=?,message=? WHERE id=?',(time.time(),'Checking '+data.name,run['id']))
            return {'lead_id':lead_id,'should_open':new and not data.cheap_reason,'cancel':run['state']=='cancel_requested'}

    def save_result(uid,data):
        from leads_service import row_dict
        if len(json.dumps(data.details))>250000:raise HTTPException(400,'Lead facts are too large')
        with connect() as db:
            db.execute('BEGIN IMMEDIATE');run=lease(db,uid,data)
            association=db.execute('SELECT * FROM run_leads WHERE run_id=? AND lead_id=?',(run['id'],data.lead_id)).fetchone()
            if not association or not association['opened']:raise HTTPException(403,'This lead was not reserved for this run')
            row=db.execute('SELECT * FROM leads WHERE id=? AND user_id=?',(data.lead_id,uid)).fetchone()
            facts=json.loads(row['facts'] or '{}');facts.update(data.details.get('facts',{}))
            # Facts from the worker have the same types as manually edited evidence.
            from leads_service import Facts
            facts=Facts.model_validate(facts).model_dump()
            updates={field:str(data.details.get(field) or row[field] or '')[:2000] for field in FIELDS}
            raw=data.details.get('review_facts',{});raw['checked_at']=datetime.now(timezone.utc).isoformat()
            db.execute('UPDATE leads SET '+','.join(f+'=?' for f in FIELDS)+',facts=?,review_facts=?,website_check=? WHERE id=? AND user_id=?',[updates[f] for f in FIELDS]+[json.dumps(facts),json.dumps(raw),json.dumps(data.details.get('website_check',{})),data.lead_id,uid])
            lead=row_dict(db.execute('SELECT * FROM leads WHERE id=?',(data.lead_id,)).fetchone())
            db.execute('UPDATE leads SET score=?,grade=? WHERE id=?',(lead['score'],lead['score'],data.lead_id))
            db.execute('UPDATE run_leads SET checked=1,qualified=? WHERE run_id=? AND lead_id=?',(int(lead['state']=='ready'),run['id'],data.lead_id))
            db.execute('UPDATE scraper_runs SET updated_at=? WHERE id=?',(time.time(),run['id']))
        return {'saved':True,'score':lead['score'],'state':lead['state']}

    def progress(uid,data):
        with connect() as db:
            db.execute('BEGIN IMMEDIATE');run=lease(db,uid,data)
            if data.tile_key:
                valid={t['key'] for t in json.loads(run['config'])['tiles']}
                if data.tile_key not in valid:raise HTTPException(400,'Tile does not belong to this run')
                added=db.execute('INSERT OR IGNORE INTO search_tiles VALUES(?,?,?,?)',(uid,data.tile_key,run['id'],datetime.now(timezone.utc).isoformat())).rowcount
                if added:db.execute('UPDATE scraper_runs SET tiles_done=tiles_done+1 WHERE id=?',(run['id'],))
            state=data.final_state or run['state']
            if run['state']=='cancel_requested' and data.final_state=='completed':state='cancelled'
            db.execute('UPDATE scraper_runs SET state=?,message=?,updated_at=? WHERE id=?',(state,data.message,time.time(),run['id']))
            return {'cancel':state=='cancel_requested'}

    @app.get('/api/scraper/suggestions')
    def predict(request:Request,kind:str='location',q:str=''):
        user(request)
        if kind not in ('location','industry') or len(q)>100:raise HTTPException(400,'Invalid search')
        return suggestions(kind,q)

    @app.get('/api/scraper')
    def dashboard(request:Request):
        uid=user(request)['id']
        with connect() as db:
            rows=db.execute('''SELECT scraper_runs.*,batches.deleted_at FROM scraper_runs JOIN batches ON batches.id=scraper_runs.batch_id WHERE scraper_runs.user_id=? ORDER BY scraper_runs.id DESC LIMIT 100''',(uid,)).fetchall()
            runs=[]
            for row in rows:
                if row['deleted_at']:
                    age=(datetime.now(timezone.utc)-datetime.fromisoformat(row['deleted_at'])).days
                    if age>=30:continue
                totals=db.execute('SELECT COUNT(*) found,COALESCE(SUM(checked),0) checked,COALESCE(SUM(qualified),0) qualified FROM run_leads WHERE run_id=?',(row['id'],)).fetchone()
                config=json.loads(row['config'])
                runs.append({'id':row['id'],'batch_id':row['batch_id'],'industry':config['industry']['label'],'location':config['location']['label'],'state':row['state'],'message':row['message'],'created_at':row['created_at'],'deleted_at':row['deleted_at'],'tiles_done':row['tiles_done'],'tiles_total':len(config['tiles']),**dict(totals)})
            for batch in db.execute('SELECT * FROM batches WHERE user_id=? AND deleted_at IS NOT NULL AND id NOT IN (SELECT batch_id FROM scraper_runs) ORDER BY id DESC',(uid,)):
                if (datetime.now(timezone.utc)-datetime.fromisoformat(batch['deleted_at'])).days>=30:continue
                found=db.execute('SELECT COUNT(*) FROM lead_batches WHERE batch_id=?',(batch['id'],)).fetchone()[0]
                runs.append({'id':None,'batch_id':batch['id'],'industry':'CSV import','location':batch['name'],'state':'deleted','message':'Restore available for 30 days','created_at':batch['created_at'],'deleted_at':batch['deleted_at'],'tiles_done':0,'tiles_total':0,'found':found,'checked':0,'qualified':0})
            key=db.execute('SELECT last_seen,worker_name FROM worker_keys WHERE user_id=?',(uid,)).fetchone()
            worker={'connected':practice or bool(key and time.time()-key['last_seen']<45),'configured':practice or bool(key),'name':'Practice worker' if practice else key['worker_name'] if key else 'Not connected'}
            current=next((r for r in runs if not r['deleted_at'] and r['state'] in ('running','queued','cancel_requested')),None)
            latest=next((r for r in runs if not r['deleted_at']),None)
            worker['last_seen']=key['last_seen'] if key else None
            worker['state']='practice' if practice else 'setup_needed' if not worker['configured'] else 'offline' if not worker['connected'] else 'searching' if current and current['state']=='running' else 'stopping' if current and current['state']=='cancel_requested' else 'waiting' if current else 'needs_attention' if latest and latest['state']=='failed' else 'idle'
            worker['message']=current['message'] if current else latest['message'] if latest and latest['state']=='failed' else 'No active search job'
        return {'runs':runs,'worker':worker,'practice':practice}

    @app.post('/api/scraper/runs')
    def create_run(data:NewRun,request:Request):
        uid=user(request)['id'];location=location_index().get(data.location_id)
        if not location:raise HTTPException(400,'Choose a city or state from the suggestions')
        selected=industry(data.industry)
        radius_km=data.radius_miles*1.609344 if data.radius_miles is not None else data.radius_km
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT id FROM scraper_runs WHERE user_id=? AND state IN ('queued','running','cancel_requested')",(uid,)).fetchone():raise HTTPException(409,'Finish or stop the current run before starting another')
            done=[r[0] for r in db.execute('SELECT tile_key FROM search_tiles WHERE user_id=?',(uid,))]
            tiles,remaining=plan_tiles(location,selected['wordings'],done,radius_km,data.max_tiles)
            if not tiles:raise HTTPException(400,'All planned tiles for this area and industry have already been searched. Choose a larger radius or another area.')
            config={'industry':selected,'location':location,'tiles':tiles,'radius_km':radius_km,'radius_miles':radius_km/1.609344,'max_leads':data.max_leads,'remaining_tiles_estimate':remaining,'scraper':settings()['scraper']}
            batch=db.execute('INSERT INTO batches(user_id,name) VALUES(?,?)',(uid,selected['label']+' · '+location['label'])).lastrowid
            run=db.execute('INSERT INTO scraper_runs(user_id,batch_id,config,created_at,updated_at) VALUES(?,?,?,?,?)',(uid,batch,json.dumps(config),datetime.now(timezone.utc).isoformat(),time.time())).lastrowid
        return {'run_id':run,'tiles':len(tiles),'remaining_tiles_estimate':remaining}

    @app.post('/api/scraper/runs/{run_id}/stop')
    def stop_run(run_id:int,request:Request):
        uid=user(request)['id']
        with connect() as db:
            row=db.execute('SELECT state FROM scraper_runs WHERE id=? AND user_id=?',(run_id,uid)).fetchone()
            if not row:raise HTTPException(404,'Run not found')
            if row['state']=='queued':db.execute("UPDATE scraper_runs SET state='cancelled',message='Stopped before starting' WHERE id=?",(run_id,))
            elif row['state']=='running':db.execute("UPDATE scraper_runs SET state='cancel_requested',message='Stopping after the current business' WHERE id=?",(run_id,))
        return {'ok':True}

    @app.post('/api/batches/{batch_id}/trash')
    def trash(batch_id:int,request:Request):
        uid=user(request)['id']
        with connect() as db:
            row=db.execute('SELECT * FROM batches WHERE id=? AND user_id=?',(batch_id,uid)).fetchone()
            if not row:raise HTTPException(404,'Batch not found')
            if db.execute("SELECT id FROM scraper_runs WHERE batch_id=? AND state IN ('queued','running','cancel_requested')",(batch_id,)).fetchone():raise HTTPException(409,'Stop the run before moving its batch to Recently deleted')
            db.execute('UPDATE batches SET deleted_at=? WHERE id=?',(datetime.now(timezone.utc).isoformat(),batch_id))
        return {'ok':True}

    @app.post('/api/batches/{batch_id}/restore')
    def restore(batch_id:int,request:Request):
        uid=user(request)['id']
        with connect() as db:
            row=db.execute('SELECT * FROM batches WHERE id=? AND user_id=?',(batch_id,uid)).fetchone()
            if not row:raise HTTPException(404,'Batch not found')
            if row['deleted_at'] and (datetime.now(timezone.utc)-datetime.fromisoformat(row['deleted_at'])).days>=30:raise HTTPException(410,'The 30-day restore period has expired')
            db.execute('UPDATE batches SET deleted_at=NULL WHERE id=?',(batch_id,))
        return {'ok':True}

    @app.post('/api/worker/configuration')
    def worker_configuration(request:Request):
        uid=user(request)['id']
        if practice:raise HTTPException(400,'Practice has a built-in simulated worker')
        token=secrets.token_urlsafe(32)
        with connect() as db:db.execute('INSERT OR REPLACE INTO worker_keys(user_id,token_hash,created_at) VALUES(?,?,?)',(uid,hashlib.sha256(token.encode()).hexdigest(),time.time()))
        return {'dashboard_url':str(request.base_url).rstrip('/'),'worker_token':token,'worker_name':'My computer'}

    @app.post('/api/worker/revoke')
    def revoke(request:Request):
        with connect() as db:db.execute('DELETE FROM worker_keys WHERE user_id=?',(user(request)['id'],))
        return {'ok':True}

    @app.post('/api/worker/claim')
    def worker_claim(data:Claim,request:Request):return {'job':claim_run(authenticate(request),data.worker_name)}
    @app.post('/api/worker/reserve')
    def worker_reserve(data:Card,request:Request):return reserve(authenticate(request),data)
    @app.post('/api/worker/result')
    def worker_result(data:LeadResult,request:Request):return save_result(authenticate(request),data)
    @app.post('/api/worker/progress')
    def worker_progress(data:Progress,request:Request):return progress(authenticate(request),data)

    stop=threading.Event()
    def simulate():
        while not stop.wait(.3):
            with connect() as db:row=db.execute('SELECT id FROM users LIMIT 1').fetchone()
            if not row:continue
            job=claim_run(row['id'],'Practice worker')
            if not job:continue
            try:
                uid=row['id'];config=job['config'];cancel=False
                for i in range(min(6,config['max_leads'])):
                    if stop.wait(.4):break
                    source=f"https://example.test/practice/{job['id']}/{i}"
                    data=Card(run_id=job['id'],lease_token=job['lease_token'],name=f"Practice {config['industry']['label']} {job['id']}-{i+1}",phone='(951) 555-01'+str(i+10).zfill(2),place_id=f"practice-{job['id']}-{i}",source_url=source,category=config['industry']['category'])
                    reserved=reserve(uid,data)
                    if reserved['cancel']:cancel=True;break
                    if not reserved['should_open']:continue
                    timestamp=datetime.now(timezone.utc).isoformat()
                    facts={'category_approved':True,'website_need':'unknown' if i==1 else 'strong' if i==5 else 'no_website','website_source':source,'website_checked_at':timestamp,'website_note':'Simulated practice finding, not a real business.','independent':True,'independence_source':source,'activity':[{'date':timestamp,'source':source,'kind':'customer review'}],'permanently_closed':i==4}
                    save_result(uid,LeadResult(run_id=job['id'],lease_token=job['lease_token'],lead_id=reserved['lead_id'],details={'facts':facts,'review_facts':{'practice':True,'review_dates':[timestamp],'review_count':12,'rating':4.5,'star_breakdown':{'5':8,'4':2,'3':2,'2':0,'1':0}}}))
                if not cancel:
                    for tile in config['tiles']:
                        result=progress(uid,Progress(run_id=job['id'],lease_token=job['lease_token'],tile_key=tile['key'],message='Practice tile checked'))
                        if result['cancel']:cancel=True;break
                progress(uid,Progress(run_id=job['id'],lease_token=job['lease_token'],final_state='cancelled' if cancel else 'completed',message='Practice stopped' if cancel else 'Practice run complete · fictional businesses only'))
            except Exception:
                with connect() as db:db.execute("UPDATE scraper_runs SET state='failed',message='Practice run failed; restart practice mode' WHERE id=?",(job['id'],))
    previous=app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        async with previous(application):
            if practice:threading.Thread(target=simulate,daemon=True,name='practice-scraper').start()
            try:yield
            finally:stop.set()
    app.router.lifespan_context=lifespan
