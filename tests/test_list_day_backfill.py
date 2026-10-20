import runpy
from pathlib import Path
from zoneinfo import ZoneInfo
x=runpy.run_path(str(Path(__file__).with_name('discovery_fixture.py')));c=x['c'];m=x['m']
from dialer_service import group_saved_list_days
with m.connect() as db:
 bid=db.execute("INSERT INTO batches(user_id,name,created_at) VALUES(1,'Old import','2026-10-16 15:00:00')").lastrowid
 lead=db.execute("INSERT INTO leads(user_id,batch_id,name,phone) VALUES(1,?,'Day fixture','9046945640')",(bid,)).lastrowid
 def add(name,created='',folder=''):
  lid=db.execute('INSERT INTO calling_lists(user_id,name) VALUES(1,?)',(name,)).lastrowid
  db.execute('UPDATE calling_lists SET created_at=?,folder=? WHERE id=?',(created,folder,lid))
  db.execute('INSERT INTO calling_list_members VALUES(?,?)',(lid,lead));return lid
 new=add('New list','2026-10-20T02:00:00+00:00');old=add('Old list');custom=add('Custom','','My folder');manual=add('Manual numbers');empty=db.execute("INSERT INTO calling_lists(user_id,name) VALUES(1,'Unknown')").lastrowid
 db.execute("UPDATE calling_lists SET created_at='' WHERE id=?",(empty,))
 group_saved_list_days(db,1,ZoneInfo('America/New_York'))
 def get(lid):return dict(db.execute('SELECT * FROM calling_lists WHERE id=?',(lid,)).fetchone())
 assert get(new)['folder']=='2026-10-19'
 assert get(old)['folder']=='2026-10-16' and 'import' in get(old)['day_basis']
 assert get(custom)['folder']=='My folder' and not get(manual)['folder'] and not get(empty)['folder']
 db.execute("UPDATE calling_lists SET folder='' WHERE id=?",(new,));group_saved_list_days(db,1,ZoneInfo('America/New_York'));assert not get(new)['folder']
print('PASS: timezone-correct creation days, evidence-backed legacy import grouping, custom folders retained, special/unknown lists unfiled, one-time backfill.')
