import runpy
from pathlib import Path
x=runpy.run_path(str(Path(__file__).with_name('discovery_fixture.py')));c=x['c'];other=x['other'];m=x['m']
with m.connect() as db:db.execute('UPDATE dial_attempts SET result_recorded=1 WHERE user_id=1')
r=c.post('/api/dialer/manual',json={'phone':'9046945640','name':'Uncalled fixture'});assert r.status_code==200
lead=r.json()['lead_id'];one=r.json()['list_id']
two=c.post('/api/dialer/lists',json={'name':'Second day batch','lead_ids':[lead]}).json()['id']
def item(lid):return next(l for l in c.get('/api/dialer').json()['lists'] if l['id']==lid)
assert item(one)['uncalled']==1 and item(two)['uncalled']==1
assert item(two)['created_at']
assert other.post('/api/dialer/lists/group-day',json={'list_ids':[one,two],'day':'2026-10-20'}).status_code==404
assert c.post('/api/dialer/lists/group-day',json={'list_ids':[one,two],'day':'2026-02-30'}).status_code==400
assert c.post('/api/dialer/lists/group-day',json={'list_ids':[one,two],'day':'2026-10-20'}).status_code==200
folder=next(f for f in c.get('/api/dialer').json()['folders'] if f['folder']=='2026-10-20' and f['state']=='active')
assert folder['lists']==2 and folder['count']==1 and folder['uncalled']==1
with m.connect() as db:db.execute("INSERT INTO call_events(user_id,lead_id,result,source) VALUES(1,?,'No Answer','manual')",(lead,))
assert item(one)['uncalled']==0 and item(two)['uncalled']==0
assert next(f for f in c.get('/api/dialer').json()['folders'] if f['folder']=='2026-10-20' and f['state']=='active')['uncalled']==0
assert c.post(f'/api/dialer/lists/{one}/organization',json={'name':item(one)['name'],'folder':'2026-10-20','archived':True}).status_code==200
folder=next(f for f in c.get('/api/dialer').json()['folders'] if f['folder']=='2026-10-20' and f['state']=='active');assert folder['lists']==1
print('PASS: per-list uncalled contacts, shared-contact folder dedup, recorded attempts update counts, date validation, atomic ownership check, archive scopes, new creation timestamps.')
