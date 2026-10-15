import json,re
from pathlib import Path

def library(connect,uid):
    saved=json.loads((Path(__file__).parent/'coach_objections.json').read_text(encoding='utf-8'))
    with connect() as db:
        rows=db.execute("SELECT title,body FROM scripts WHERE user_id=? AND kind='objection' AND archived=0 AND deleted=0 ORDER BY id LIMIT 12",(uid,)).fetchall()
    return saved+[{'objection':r['title'],'response':re.sub('<[^>]*>',' ',r['body'])[:900]} for r in rows]

def context(connect,uid):
    return '\nSaved objection reference (adapt only when relevant; never recite all of them; respect refusal):\n'+json.dumps(library(connect,uid),ensure_ascii=False)[:14000]

