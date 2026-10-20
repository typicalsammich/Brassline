import runpy
from pathlib import Path
x=runpy.run_path(str(Path(__file__).with_name('discovery_fixture.py')));c=x['c'];other=x['other'];m=x['m']
r=c.post('/api/dialer/manual',json={'phone':'9046945640','name':'List removal fixture'});assert r.status_code==200,r.text
lid=r.json()['list_id'];lead=r.json()['lead_id']
assert other.post(f'/api/dialer/lists/{lid}/delete').status_code==404
with m.connect() as db:
 before=db.execute('SELECT count(*) FROM dial_attempts WHERE user_id=1').fetchone()[0]
 db.execute("UPDATE dial_attempts SET result_recorded=0 WHERE user_id=1")
assert c.post(f'/api/dialer/lists/{lid}/delete').status_code==409
with m.connect() as db:db.execute('UPDATE dial_attempts SET result_recorded=1 WHERE user_id=1')
assert c.post(f'/api/dialer/lists/{lid}/delete').status_code==200
assert c.get(f'/api/dialer?list_id={lid}').status_code==404
with m.connect() as db:
 assert db.execute('SELECT 1 FROM leads WHERE id=?',(lead,)).fetchone()
 assert db.execute('SELECT count(*) FROM dial_attempts WHERE user_id=1').fetchone()[0]==before
 assert not db.execute('SELECT 1 FROM calling_list_members WHERE list_id=?',(lid,)).fetchone()
assert c.post(f'/api/dialer/lists/{lid}/delete').status_code==404
print('PASS: whole list removal preserves lead/history, blocks unfinished calls, isolates accounts and clears membership.')
