import runpy,json,time,sys
from unittest.mock import patch
from datetime import datetime,timezone,timedelta
from pathlib import Path
x=runpy.run_path(str(Path(__file__).with_name('discovery_fixture.py')));c=x['c'];other=x['other'];m=x['m']
import discovery_sources as sources
from discovery_service import stamp,DEFAULTS
import owner_verification as o
from lead_identity import groups

assert not o.us_phone('9515550101') and not o.us_phone('1111111111') and o.us_phone('(904) 694-5640')=='+19046945640'
base={'name':'Alex Example','role':'Owner','source_url':'https://example.com/about','evidence':'Company biography explicitly names Alex Example as owner','basis':'reviewed_public_evidence','identity_confirmed':True,'retrieved_at':stamp()}
assert o.person(base)['status']=='verified_owner'
for role in ['Registered agent','License qualifier','Officer','Employee','Owner or founder candidate']:
 assert o.person({**base,'role':role})['status']=='unknown'
assert o.person({**base,'basis':'AI'})['status']=='probable_owner'
assert o.person({**base,'role':'Founder'})['status']=='probable_owner'
assert o.person({**base,'role':'President'})['status']=='verified_decision_maker'
assert o.person({**base,'retrieved_at':'2020-01-01T00:00:00+00:00'})['possibly_outdated']
e={'source_url':'https://example.com/contact','evidence':'Business tel link','retrieved_at':stamp(),'published_business':True}
assert o.phone_record('9046945640',e,[o.person(base)])['classification']=='owner_associated_business'
assert o.phone_record('9046945640',e,[])['classification']=='general_business'
assert not o.phone_record('9046945640',e,[o.person(base)])['owner_direct']
assert o.phone_record('9046945640',{**e,'person_name':'Alex Example','explicit_business_direct':True},[o.person(base)])['owner_direct']
assert o.website_result({'error':'timeout'},True)['classification']=='unknown'
assert o.website_result({'findings':[{'kind':'availability'}]},False)['classification']=='unknown'
rows=[{'id':1,'name':'Alpha Roof LLC','city':'Jacksonville FL','phone':'9046945640','place_id':''},{'id':2,'name':'Unrelated Electric','city':'Jacksonville FL','phone':'9046945640','place_id':''},{'id':3,'name':'Alpha Roof, Inc.','city':'Jacksonville FL','phone':'9046945640','place_id':''}]
g=groups(rows);assert g[1]==g[3] and g[1]!=g[2]
co=sources.normalize('co_entities',{'entityid':'1','entityname':'Roof Co','agentfirstname':'Fake','agentlastname':'Agent'},stamp())
assert co['people'][0]['role']=='Registered agent' and not co['phone']
assert o.person({**co['people'][0],'retrieved_at':stamp()})['status']=='unknown'
assert c.get('/api/discovery').status_code==200
assert not c.get('/api/discovery').json()['config']['paid_enabled']
assert other.post('/api/discovery/settings',json={**DEFAULTS,'paid_enabled':True,'daily_budget_cents':100,'monthly_budget_cents':2000,'search_ceiling_cents':5}).status_code==403
assert c.post('/api/discovery/wake').status_code==403
# Start enables the saved configuration without granting paid search.
assert c.get('/api/discovery').json()['runtime']['label']=='Paused'
assert c.post('/api/discovery/start').status_code==200
started=c.get('/api/discovery').json()
assert started['config']['enabled'] and not started['config']['paid_enabled']
assert started['runtime']['pending']>0 and 'Queued' in started['runtime']['label']
assert not other.get('/api/discovery').json()['config']['enabled']
assert c.post('/api/discovery/pause').status_code==200
with m.connect() as db:
 db.execute('DELETE FROM discovery_jobs')
settings={**DEFAULTS,'enabled':True,'sources':['nyc_licenses'],'industries':['ROOF'],'raw_daily_limit':2}
assert c.post('/api/discovery/settings',json=settings).status_code==200
assert c.post('/api/discovery/start').status_code==200
raw={'license_nbr':'TEST-1','business_name':'Private Roof Fixture LLC','license_status':'Active','business_category':'Home Improvement Contractor','contact_phone':'9046945640','address_city':'Jacksonville','address_state':'FL','source_row_id':'row-1'}
with patch('discovery_sources.schema',return_value={'checked_fields':['license_nbr']}),patch('discovery_sources.fetch_page',return_value=([raw],'row-1',True)),patch('research_service.search_web',side_effect=AssertionError('Paid service used without authorization')):
 assert m.app.state.discovery_tick()
 assert m.app.state.discovery_tick()
r=c.get('/api/discovery').json()['records'][0];rid=r['id']
assert r['phone_detail']['classification']=='general_business' and not r['qualified']
assert not other.get('/api/discovery').json()['records']
assert other.post('/api/discovery/records/'+str(rid)+'/review',json={}).status_code==404
assert c.post('/api/discovery/records/'+str(rid)+'/review',json={'name':'Alex Example'}).status_code==400
review={'name':'Alex Example','role':'Owner','source_url':'https://example.com/about','evidence':'Explicit current owner biography','identity_confirmed':True,
 'business_validated':True,'business_source':'https://example.com','independent':True,'independence_source':'https://example.com/about',
 'activity_verified':True,'activity_source':'https://example.com/projects','activity_date':stamp(),
 'website_opportunity':'usability_verified','website_source':'https://example.com','website_evidence':'Reviewed missing mobile navigation on tested small screen.',
 'phone':'9046945640','phone_source':'https://example.com/contact','phone_evidence':'Published company business phone','published_business_phone':True}
result=c.post('/api/discovery/records/'+str(rid)+'/review',json=review);assert result.status_code==200,result.text
assert result.json()['qualified'] and result.json()['lead_id']
lid=result.json()['lead_id'];assert c.get('/api/leads/'+str(lid)).json()['discovery']['best_person']['status']=='verified_owner'
again=c.post('/api/discovery/records/'+str(rid)+'/review',json=review);assert again.json()['lead_id']==lid
assert other.get('/api/leads/'+str(lid)).status_code==404
# Pause fences an already-claimed job; restarting a lease does not duplicate records.
assert c.post('/api/discovery/pause').status_code==200
assert not m.app.state.discovery_tick()
assert c.post('/api/discovery/settings',json=settings).status_code==200
with m.connect() as db:
 db.execute("UPDATE discovery_jobs SET state='running',lease='old',lease_until=0 WHERE kind='source'")
with patch('discovery_sources.schema',return_value={}),patch('discovery_sources.fetch_page',return_value=([raw],'row-1',True)):
 assert m.app.state.discovery_tick()
assert c.get('/api/discovery').json()['total']==1
# Changed source data is preserved for review, not silently treated as current verification.
with m.connect() as db:db.execute("UPDATE discovery_jobs SET state='queued',available_at=0 WHERE kind='source'")
with patch('discovery_sources.schema',return_value={}),patch('discovery_sources.fetch_page',return_value=([{**raw,'contact_phone':'9046945641'}],'row-1',True)):
 assert m.app.state.discovery_tick()
assert not c.get('/api/discovery').json()['records'][0]['qualified']
assert c.get('/api/leads/'+str(lid)).json()['state']=='waiting'
assert other.get('/api/discovery/accounts/1/settings').status_code==403
# Hard monetary caps are additional to the existing shared research quotas.
paid={**settings,'paid_enabled':True,'monthly_budget_cents':10,'daily_budget_cents':10,'search_ceiling_cents':6}
assert c.post('/api/discovery/settings',json=paid).status_code==200
with m.connect() as db:
 uid=db.execute("SELECT id FROM users WHERE email='owner@example.test'").fetchone()[0]
 db.execute('DELETE FROM discovery_jobs')
 db.execute('INSERT OR REPLACE INTO service_allowances(user_id,paused,research) VALUES(?,0,100)',(uid,))
 for i in range(2):
  profile={'name':'Cost Test Fixture '+str(i),'city':'Test','state':'FL','source_url':'https://example.com','phone':'','people':[]}
  ident=db.execute('INSERT INTO discovery_records(user_id,source,source_id,fingerprint,profile,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(uid,'nyc_licenses','COST'+str(i),'cost'+str(i),json.dumps(profile),stamp(),stamp())).lastrowid
  db.execute('INSERT INTO discovery_jobs(user_id,kind,job_key,payload,created_at,updated_at) VALUES(?,?,?,?,?,?)',(uid,'research','cost'+str(i),json.dumps({'record_id':ident}),time.time(),time.time()))
with patch('shared_services.config',return_value={'research_enabled':True,'research':100,'serpapi_key':'mock-only-never-used','firecrawl_key':''}),patch('research_service.search_web',return_value=[{'name':'Cached business','url':'https://example.com'}]) as search:
 assert m.app.state.discovery_tick()
 assert m.app.state.discovery_tick()
 assert search.call_count==1
 with m.connect() as db:db.execute("UPDATE discovery_jobs SET state='queued',available_at=0 WHERE job_key='cost0'")
 assert m.app.state.discovery_tick()
 assert search.call_count==1 # cached result, not a second charge after restart
with m.connect() as db:
 spend=db.execute('SELECT * FROM discovery_spend').fetchall();assert len(spend)==1 and spend[0]['reserved_cents']==6 and spend[0]['actual_cents'] is None
 key=spend[0]['request_key']
assert other.post('/api/discovery/billing/reconcile',json={'request_key':key,'actual_cents':7,'billing_reference':'mock receipt'}).status_code==403
assert c.post('/api/discovery/billing/reconcile',json={'request_key':key,'actual_cents':7,'billing_reference':'mock receipt'}).json()['paid_paused']
assert not c.get('/api/discovery').json()['config']['paid_enabled']
# Source parser never silently proceeds with schema drift.
with patch('discovery_sources.get_json',return_value={'columns':[{'fieldName':'renamed'}]}):
 try:sources.schema(sources.SOURCES['nyc_licenses']);raise AssertionError('Schema drift accepted')
 except sources.SourceError:pass
import website_checks
html='''<html><body><h1>Private Roof Fixture LLC</h1><a href="tel:+19046945640">Phone</a>
<script type="application/ld+json">{"@type":"Person","name":"Alex Example","jobTitle":"Owner","worksFor":{"@type":"Organization","name":"Private Roof Fixture LLC"}}</script></body></html>'''
with patch('website_checks.fetch_public',return_value={'url':'https://example.com','status':200,'html':html}):
 audit=website_checks.check_website('https://example.com',{'name':'Private Roof Fixture LLC','phone':'9046945640'})
 assert audit['company_identity_confirmed'] and audit['decision_makers'][0]['organization_name']=='Private Roof Fixture LLC'
 wrong=website_checks.check_website('https://example.com',{'name':'Unrelated Electric','phone':'9046945640'})
 assert not wrong['company_identity_confirmed']
assert other.post('/api/discovery/settings',json=settings).status_code==200
def pause_during_fetch(*args,**kwargs):
 assert other.post('/api/discovery/pause').status_code==200
 return [raw],'row-1',True
with patch('discovery_sources.schema',return_value={}),patch('discovery_sources.fetch_page',side_effect=pause_during_fetch):
 assert m.app.state.discovery_tick()
assert not other.get('/api/discovery').json()['records']
assert any(j['state']=='queued' for j in other.get('/api/discovery').json()['jobs'])
assert other.post('/api/discovery/settings',json=settings).status_code==200
with patch('discovery_sources.schema',return_value={}),patch('discovery_sources.fetch_page',return_value=([raw],'row-1',True)):
 assert m.app.state.discovery_tick()
assert len(other.get('/api/discovery').json()['records'])==1
# Both new native source types reach the private queue without phone/owner invention.
native_settings={**DEFAULTS,'enabled':True,'sources':['ca_cslb','fl_dbpr_construction'],'states':['CA','FL'],'raw_daily_limit':100}
assert c.post('/api/discovery/settings',json=native_settings).status_code==200
ca_native={'id':'CA-FIXTURE','name':'Native CA Plumbing','business_type':'Sole Owner','phone':'9046945640','city':'Example','state':'CA','category':'Plumbing','expiration':'12/31/2099','issued':'01/01/2025','license_status':'CLEAR'}
fl_native={'id':'CFC-FIXTURE:abc','license_id':'CFC-FIXTURE','licensee':'EXAMPLE, ALEX','name':'Native FL Plumbing','phone':'','city':'Example','state':'FL','category':'Plumbing','expiration':'12/31/2099','issued':'01/01/2025','license_status':'Current, Active'}
def native_page(key,*args,**kwargs):return ([ca_native if key=='ca_cslb' else fl_native],'',True)
with patch('discovery_sources.schema',return_value={}),patch('discovery_sources.fetch_page',side_effect=native_page):
 for _ in range(6):m.app.state.discovery_tick()
native={r['source']:r for r in c.get('/api/discovery').json()['records'] if r.get('source') in ('ca_cslb','fl_dbpr_construction')}
assert set(native)=={'ca_cslb','fl_dbpr_construction'}
assert native['ca_cslb']['phone_detail']['classification']=='general_business' and not native['ca_cslb']['people']
assert native['fl_dbpr_construction']['phone_detail']['classification']=='none' and native['fl_dbpr_construction']['people'][0]['status']=='unknown'
print('PASS: official role normalization, fictional-phone rejection, evidence/status separation, business identity, private records, review requirements, idempotent lead creation, restart recovery, pause fencing, changed-source holds; no paid APIs or calls.')
print('PASS: monetary caps + shared units, pre-dispatch reservations, durable response cache, no automatic paid re-dispatch, administrator-only billing reconciliation and pause on cost-ceiling breach, official schema-drift stop. All provider calls mocked.')
