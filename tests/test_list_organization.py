import runpy
from pathlib import Path
x=runpy.run_path(str(Path(__file__).with_name('discovery_fixture.py')));c=x['c'];other=x['other'];m=x['m']
r=c.post('/api/dialer/manual',json={'phone':'9046945640','name':'Organization fixture'});assert r.status_code==200
lid=r.json()['list_id'];lead=r.json()['lead_id'];path=f'/api/dialer/lists/{lid}/organization'
org={'name':'Website prospects','folder':'Regional campaigns','favorite':True,'archived':True}
assert other.post(path,json=org).status_code==404
with m.connect() as db:db.execute('UPDATE dial_attempts SET result_recorded=0 WHERE user_id=1')
assert c.post(path,json=org).status_code==409
with m.connect() as db:db.execute('UPDATE dial_attempts SET result_recorded=1 WHERE user_id=1')
assert c.post(path,json=org).status_code==200
lists=c.get('/api/dialer').json()['lists'];item=next(l for l in lists if l['id']==lid)
assert item['folder']=='Regional campaigns' and item['favorite'] and item['archived']
assert c.get(f'/api/dialer?list_id={lid}').status_code==404
assert c.post(path,json={**org,'archived':False}).status_code==200
assert c.get(f'/api/dialer?list_id={lid}').status_code==200
assert c.post(path,json={**org,'name':' '}).status_code==400
with m.connect() as db:
 assert db.execute('SELECT 1 FROM leads WHERE id=?',(lead,)).fetchone()
 assert db.execute('SELECT 1 FROM calling_list_members WHERE list_id=? AND lead_id=?',(lid,lead)).fetchone()
print('PASS list organization: isolation, pending-call guard, rename/folder/star persistence, archive excludes queue, restore preserves membership and lead.')

