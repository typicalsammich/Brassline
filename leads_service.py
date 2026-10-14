import csv
import io
import json
import re
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal
from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from scoring import score, settings, phone_key, public_url, age_days
import website_checks

class Activity(BaseModel):
    date:str=Field(max_length=40)
    source:str=Field(max_length=2000)
    kind:str=Field(default='business activity',max_length=100)

class Facts(BaseModel):
    website_need:Literal['unknown','no_website','broken','usability','content','dated','strong']='unknown'
    website_source:str=Field(default='',max_length=2000)
    website_note:str=Field(default='',max_length=1500)
    website_checked_at:str=''
    activity:list[Activity]=Field(default_factory=list,max_length=100)
    independent:bool|None=None
    independence_source:str=Field(default='',max_length=2000)
    owner_name:str=Field(default='',max_length=160)
    owner_role:str=Field(default='',max_length=160)
    owner_phone:str=Field(default='',max_length=80)
    owner_source:str=Field(default='',max_length=2000)
    owner_access:Literal['unknown','identified','authority']='unknown'
    category_approved:bool=False
    permanently_closed:bool=False
    illegitimate:bool=False
    central_website:bool=False
    contact_checked:bool=False
    wrong_industry:bool=False

class Edit(BaseModel):
    name:str=Field(max_length=300)
    phone:str=Field(default='',max_length=80)
    email:str=Field(default='',max_length=250)
    category:str=Field(default='',max_length=150)
    city:str=Field(default='',max_length=150)
    website_url:str=Field(default='',max_length=2000)
    social_url:str=Field(default='',max_length=2000)
    source_url:str=Field(default='',max_length=2000)
    facts:Facts=Field(default_factory=Facts)

class Import(BaseModel):
    filename:str=Field(default='CSV import',max_length=200)
    csv_text:str=Field(max_length=2000000)

class Bookkeeping(BaseModel):
    result:Literal['No Answer','Not Interested','Booked','Call Later','Wrong Number']

FIELDS=['name','phone','email','category','city','website_url','social_url','source_url']

def name_key(value):return ' '.join(re.findall(r'\w+',str(value).casefold()))
def now():return datetime.now(timezone.utc).isoformat()

def run_check(lead,practice):
    if practice:
        return {'checked_at':now(),'practice':True,'pages':[{'url':lead['website_url'],'status':200}],'findings':[{'kind':'mobile_hint','text':'Simulated finding: no mobile viewport declaration. Review before confirming a mobile usability issue.','source':lead['website_url']}],'contacts':[{'type':'business_phone','value':'(951) 555-0101','source':lead['website_url'],'verification':'Made-up practice contact'}],'decision_makers':[{'name':'Alex Rivera','role':'Owner (fictional)','phone':'(951) 555-0102','source':lead['website_url'],'verification':'Made-up practice contact; confirm to see scoring'}],'limitations':['Practice check is simulated and does not visit any website.']}
    try:return website_checks.check_website(lead['website_url'])
    except (ValueError,OSError,website_checks.http.client.HTTPException,RecursionError) as error:return {'checked_at':now(),'pages':[],'findings':[],'contacts':[],'decision_makers':[],'error':str(error),'limitations':['Check failed. Website availability remains unknown; this is not proof the website is broken.']}

def row_dict(row):
    lead=dict(row)
    try:lead['facts']=json.loads(lead.get('facts') or '{}')
    except ValueError:lead['facts']={}
    try:lead['website_check']=json.loads(lead.get('website_check') or '{}')
    except ValueError:lead['website_check']={}
    lead.update(score(lead));return lead

def list_for(connect, uid):
    with connect() as db:
        rows=db.execute('''SELECT leads.*, batches.name AS batch_name FROM leads LEFT JOIN batches ON batches.id=leads.batch_id WHERE leads.user_id=? AND (NOT EXISTS (SELECT 1 FROM lead_batches WHERE lead_id=leads.id) OR EXISTS (SELECT 1 FROM lead_batches JOIN batches b ON b.id=lead_batches.batch_id WHERE lead_id=leads.id AND b.deleted_at IS NULL))''',(uid,)).fetchall()
        results=[row_dict(row) for row in rows]
        for lead in results:
            job=db.execute('SELECT state FROM website_jobs WHERE lead_id=?',(lead['id'],)).fetchone()
            lead['website_check_state']=job['state'] if job else ('complete' if lead['website_check'] else 'not_checked')
            event=db.execute('SELECT result,created_at FROM call_events WHERE user_id=? AND lead_id=? AND ignored=0 ORDER BY id DESC LIMIT 1',(uid,lead['id'])).fetchone()
            lead['last_result']=event['result'] if event else ''
            from crm_service import blocked
            meta=db.execute('SELECT * FROM lead_sales WHERE user_id=? AND lead_id=?',(uid,lead['id'])).fetchone()
            lead.update(sales_stage=meta['stage'] if meta else 'New',quoted_cents=meta['quoted_cents'] if meta else None,agreed_cents=meta['agreed_cents'] if meta else None,do_not_contact=bool((meta and meta['do_not_contact']) or blocked(db,uid,lead)),assigned_out=bool(meta and meta['assigned_out']))
            lead['followup_due']=bool(db.execute("SELECT 1 FROM followups WHERE user_id=? AND lead_id=? AND state='open' AND due_at<=?",(uid,lead['id'],now())).fetchone())
            call=db.execute("SELECT created_at FROM call_events WHERE user_id=? AND lead_id=? AND ignored=0 AND source='dialer' ORDER BY id DESC LIMIT 1",(uid,lead['id'])).fetchone()
            lead['last_call']=call['created_at'] if call else ''
            lead['batch_ids']=[r[0] for r in db.execute('SELECT batch_id FROM lead_batches JOIN batches ON batches.id=lead_batches.batch_id WHERE lead_id=? AND batches.deleted_at IS NULL',(lead['id'],))]
    return sorted(results,key=lambda lead:(not lead['priority_market'],-lead['score'],lead['id']))

def install(app,connect,user,practice):
    with connect() as db:
        columns={row[1] for row in db.execute('PRAGMA table_info(leads)')}
        for name in ['email','category','city','website_url','social_url','source_url','facts','website_check']:
            if name not in columns:db.execute(f"ALTER TABLE leads ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
        db.execute('CREATE TABLE IF NOT EXISTS lead_batches(lead_id INTEGER REFERENCES leads(id),batch_id INTEGER REFERENCES batches(id),PRIMARY KEY(lead_id,batch_id))')
        db.execute('INSERT OR IGNORE INTO lead_batches SELECT id,batch_id FROM leads WHERE batch_id IS NOT NULL')
        db.execute('CREATE INDEX IF NOT EXISTS lead_owner ON leads(user_id)')
        db.execute('CREATE INDEX IF NOT EXISTS event_owner_lead ON call_events(user_id,lead_id,id)')
        db.execute("CREATE TABLE IF NOT EXISTS website_jobs(lead_id INTEGER PRIMARY KEY REFERENCES leads(id),state TEXT NOT NULL DEFAULT 'queued')")

    def queue(db,lead_id):
        db.execute("INSERT INTO website_jobs VALUES(?,'queued') ON CONFLICT(lead_id) DO UPDATE SET state='queued'",(lead_id,))

    stop=threading.Event()
    def website_worker():
        while not stop.is_set():
            try:
                with connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    job=db.execute("SELECT leads.* FROM website_jobs JOIN leads ON leads.id=website_jobs.lead_id WHERE state='queued' ORDER BY leads.id LIMIT 1").fetchone()
                    if job:db.execute("UPDATE website_jobs SET state='running' WHERE lead_id=?",(job['id'],))
                if not job:stop.wait(1);continue
                report=run_check(dict(job),practice)
                with connect() as db:
                    current=db.execute('SELECT website_url FROM leads WHERE id=?',(job['id'],)).fetchone()
                    if current and current['website_url']==job['website_url']:
                        db.execute('UPDATE leads SET website_check=? WHERE id=?',(json.dumps(report),job['id']))
                        db.execute("UPDATE website_jobs SET state='complete' WHERE lead_id=? AND state='running'",(job['id'],))
                    else:queue(db,job['id'])
                stop.wait(1)
            except Exception:
                # Preserve queued work across temporary database failures.
                stop.wait(2)
    def start_checks():
        with connect() as db:db.execute("UPDATE website_jobs SET state='queued' WHERE state='running'")
        stop.clear();threading.Thread(target=website_worker,daemon=True,name='website-checks').start()
    @asynccontextmanager
    async def lifespan(_app):
        start_checks()
        try:yield
        finally:stop.set()
    app.router.lifespan_context=lifespan

    def owned(db,uid,lead_id):
        row=db.execute('SELECT * FROM leads WHERE id=? AND user_id=?',(lead_id,uid)).fetchone()
        if not row:raise HTTPException(404,'Lead not found')
        return row

    def save_score(db,lead):
        computed=score(lead)
        db.execute('UPDATE leads SET score=?,grade=? WHERE id=?',(computed['score'],computed['grade'],lead['id']))

    @app.get('/api/batches')
    def batches(request:Request):
        uid=user(request)['id']
        with connect() as db:return [dict(r) for r in db.execute('SELECT id,name,created_at FROM batches WHERE user_id=? AND deleted_at IS NULL ORDER BY id DESC',(uid,))]

    @app.get('/api/scoring-settings')
    def scoring_settings(request:Request):user(request);return settings()

    @app.post('/api/leads/import')
    def import_csv(data:Import,request:Request):
        uid=user(request)['id']
        try:
            reader=csv.DictReader(io.StringIO(data.csv_text.lstrip('\ufeff')))
            if not reader.fieldnames:raise HTTPException(400,'CSV needs a header row')
            reader.fieldnames=[h.strip().lower() for h in reader.fieldnames]
            if 'name' not in reader.fieldnames:raise HTTPException(400,'CSV needs a name column. Download the example for the supported columns.')
            rows=[]
            for row in reader:
                if len(rows)>=2000:raise HTTPException(400,'Import at most 2,000 businesses at a time')
                rows.append(row)
        except csv.Error:raise HTTPException(400,'Could not read that CSV file')
        added=merged=0;warnings=[]
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            batch=db.execute('INSERT INTO batches(user_id,name) VALUES(?,?)',(uid,data.filename)).lastrowid
            saved=[dict(r) for r in db.execute('SELECT * FROM leads WHERE user_id=? ORDER BY id',(uid,))]
            by_place={};by_phone={};by_name={}
            def remember(item):
                if item.get('place_id'):by_place.setdefault(item['place_id'],item)
                if phone_key(item.get('phone')):by_phone.setdefault(phone_key(item['phone']),item)
                if name_key(item.get('name')):by_name.setdefault(name_key(item['name']),item)
            for item in saved:remember(item)
            for number,row in enumerate(rows,2):
                item={field:(row.get(field) or '').strip()[:2000] for field in FIELDS}
                item['place_id']=(row.get('place_id') or '').strip()[:300]
                if not item['name']:warnings.append(f'Row {number}: no business name; not imported');continue
                item['name']=item['name'][:300]
                existing=by_place.get(item['place_id']) if item['place_id'] else None
                if not existing and phone_key(item['phone']):existing=by_phone.get(phone_key(item['phone']))
                if not existing:existing=by_name.get(name_key(item['name']))
                if existing:
                    lead_id=existing['id'];merged+=1
                    # Preserve all verified data; only fill previously empty fields.
                    for field in FIELDS+['place_id']:
                        if not existing[field] and item[field]:
                            db.execute(f'UPDATE leads SET {field}=? WHERE id=?',(item[field],lead_id));existing[field]=item[field]
                else:
                    lead_id=db.execute('INSERT INTO leads(user_id,batch_id,'+','.join(FIELDS+['place_id'])+') VALUES('+','.join('?' for _ in range(len(FIELDS)+3))+')',[uid,batch]+[item[f] for f in FIELDS+['place_id']]).lastrowid;added+=1
                db.execute('INSERT OR IGNORE INTO lead_batches VALUES(?,?)',(lead_id,batch))
                current=owned(db,uid,lead_id)
                remember(existing if existing else dict(current))
                if current['website_url'] and not current['website_check']:queue(db,lead_id)
                save_score(db,row_dict(owned(db,uid,lead_id)))
        return {'added':added,'merged':merged,'warnings':warnings[:30],'batch_id':batch}

    @app.post('/api/leads/rescore')
    def rescore(request:Request):
        uid=user(request)['id']
        with connect() as db:
            rows=db.execute('SELECT * FROM leads WHERE user_id=?',(uid,)).fetchall()
            for row in rows:save_score(db,row_dict(row))
        return {'rescored':len(rows)}

    @app.get('/api/leads/export')
    def export(request:Request):
        output=io.StringIO(newline='');writer=csv.writer(output)
        fields=['name','phone','place_id','email','category','city','website_url','social_url','source_url','score','state','last_result','last_call','owner_name','owner_role','owner_phone','owner_source','qualification_reason','evidence_sources','facts_json','website_check_json','review_facts_json']
        writer.writerow(fields)
        for lead in list_for(connect,user(request)['id']):
            lead.update({f:lead['facts'].get(f,'') for f in ['owner_name','owner_role','owner_phone','owner_source']})
            lead['qualification_reason']='; '.join(lead['reasons']);lead['evidence_sources']='; '.join(s['url'] for s in lead['sources']);lead['facts_json']=json.dumps(lead['facts']);lead['website_check_json']=json.dumps(lead['website_check']);lead['review_facts_json']=lead.get('review_facts','{}')
            values=[]
            for field in fields:
                value=str(lead.get(field) or '')
                if value.lstrip().startswith(('=','+','-','@')):value="'"+value
                values.append(value)
            writer.writerow(values)
        return Response('\ufeff'+output.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="brassline-leads.csv"'})

    @app.get('/api/leads/{lead_id}')
    def detail(lead_id:int,request:Request):
        uid=user(request)['id']
        with connect() as db:
            lead=row_dict(owned(db,uid,lead_id))
            lead['events']=[dict(r) for r in db.execute('SELECT * FROM call_events WHERE user_id=? AND lead_id=? ORDER BY id DESC',(uid,lead_id))]
            return lead

    @app.post('/api/leads/{lead_id}/edit')
    def edit(lead_id:int,data:Edit,request:Request):
        uid=user(request)['id'];facts=data.facts.model_dump()
        if not data.name.strip():raise HTTPException(400,'Business name is required')
        if facts['website_need']!='unknown' and not public_url(facts['website_source']):raise HTTPException(400,'Add a source URL supporting the website finding')
        if facts['independent'] is True and not public_url(facts['independence_source']):raise HTTPException(400,'Add a source URL supporting independent ownership')
        if facts['owner_access']!='unknown' and (not facts['owner_name'] or not public_url(facts['owner_source'])):raise HTTPException(400,'Add a decision-maker name and supporting source URL')
        if any(not public_url(item['source']) for item in facts['activity']):raise HTTPException(400,'Every activity entry needs a public source URL')
        if any(age_days(item['date'],datetime.now(timezone.utc)) is None for item in facts['activity']):raise HTTPException(400,'Activity dates must be valid dates in the past or today')
        facts['website_checked_at']=now() if facts['website_need']!='unknown' else ''
        with connect() as db:
            old=owned(db,uid,lead_id)
            db.execute('UPDATE leads SET '+','.join(f+'=?' for f in FIELDS)+',facts=? WHERE id=? AND user_id=?',[getattr(data,f).strip() for f in FIELDS]+[json.dumps(facts),lead_id,uid])
            lead=row_dict(owned(db,uid,lead_id));save_score(db,lead)
            if lead['website_url'] and old['website_url']!=lead['website_url']:
                db.execute("UPDATE leads SET website_check='' WHERE id=?",(lead_id,));queue(db,lead_id)
        return lead

    @app.post('/api/leads/{lead_id}/website-check')
    def website_check(lead_id:int,request:Request):
        uid=user(request)['id']
        with connect() as db:lead=row_dict(owned(db,uid,lead_id))
        if not lead['website_url']:raise HTTPException(400,'Add a website URL first. A missing link alone does not prove there is no website.')
        with connect() as db:queue(db,lead_id)
        return {'queued':True,'note':'Website check queued. It runs automatically, one business at a time.'}

    @app.post('/api/leads/{lead_id}/result')
    def bookkeeping(lead_id:int,data:Bookkeeping,request:Request):
        uid=user(request)['id']
        with connect() as db:
            owned(db,uid,lead_id)
            db.execute('INSERT INTO call_events(user_id,lead_id,result,source,created_at) VALUES(?,?,?,?,?)',(uid,lead_id,data.result,'leads',now()))
        return {'ok':True,'note':'Bookkeeping saved. This does not count as a dial.'}
