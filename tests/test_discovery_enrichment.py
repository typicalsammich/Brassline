import runpy,json,time
from pathlib import Path
x=runpy.run_path(str(Path(__file__).with_name('discovery_fixture.py')));c=x['c'];other=x['other'];m=x['m']
from discovery_service import DEFAULTS,stamp
assert c.post('/api/discovery/research-existing').status_code==409
cfg={**DEFAULTS,'enabled':True,'paid_enabled':False}
assert c.post('/api/discovery/settings',json=cfg).status_code==200
assert c.post('/api/discovery/research-existing').status_code==409
cfg.update(paid_enabled=True,monthly_budget_cents=1000,daily_budget_cents=100,search_ceiling_cents=5)
assert c.post('/api/discovery/settings',json=cfg).status_code==200
with m.connect() as db:
 db.execute('DELETE FROM discovery_jobs')
 p={'name':'Contact Research Example','city':'Miami','state':'FL','phone':'','people':[],'source':'fl_dbpr_construction','source_url':'https://example.com','retrieved_at':stamp()}
 rid=db.execute('INSERT INTO discovery_records(user_id,source,source_id,fingerprint,profile,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(1,'fl_dbpr_construction','retry-test','test',json.dumps(p),stamp(),stamp())).lastrowid
assert c.post('/api/discovery/research-existing').status_code==200
with m.connect() as db:
 jobs=db.execute("SELECT * FROM discovery_jobs WHERE user_id=1 AND kind='research'").fetchall()
 assert len(jobs)==1 and json.loads(jobs[0]['payload'])['record_id']==rid
assert '0 existing' in c.post('/api/discovery/research-existing').json()['message']
assert other.post('/api/discovery/research-existing').status_code==409
with m.connect() as db:
 db.execute("UPDATE discovery_jobs SET state='complete'")
assert '1 existing' in c.post('/api/discovery/research-existing').json()['message']
print('PASS: explicit paid authorization, queue existing records at daily source cap, idempotent pending requests, account isolation, completed work can resume without new record imports.')

from unittest.mock import patch
cfg['paid_enabled']=False
assert c.post('/api/discovery/settings',json=cfg).status_code==200
with m.connect() as db:
 db.execute("DELETE FROM discovery_jobs WHERE kind='source'")
 p['search_candidates']=[{'url':'https://example.com','name':p['name']}]
 db.execute('UPDATE discovery_records SET profile=? WHERE id=?',(json.dumps(p),rid))
report={'checked_at':stamp(),'company_identity_confirmed':False,'contacts':[{'type':'business_phone','value':'+19046945640','source':'https://example.com/contact'}],'decision_makers':[{'name':'Example Person','role':'Owner','source':'https://example.com/about','verification':'Text match'}]}
with patch('website_checks.check_website',return_value=report) as check:
 assert m.app.state.discovery_tick()
 assert check.called
with m.connect() as db:
 saved=json.loads(db.execute('SELECT profile FROM discovery_records WHERE id=?',(rid,)).fetchone()[0])
 assert saved['contact_candidates'][0]['value']=='+19046945640'
 assert not saved['phone'] and not saved['people'][0]['identity_confirmed']
print('PASS: records without phones still inspect candidate sites; found phones and people retain unverified source evidence, not invented identity matches.')
