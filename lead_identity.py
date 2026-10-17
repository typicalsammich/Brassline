"""Conservative contact matching shared by imports and calling lists."""
import re,unicodedata
from scoring import phone_key

def phones(value):
    direct=phone_key(value)
    if direct:return {direct}
    result=set()
    for raw in re.findall(r'(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]*\d{3}[ .-]*\d{4}(?!\d)',str(value or '')):
        key=phone_key(raw)
        if key:result.add(key)
    return result

def business_key(name,city=''):
    name=unicodedata.normalize('NFKD',str(name or '')).encode('ascii','ignore').decode().casefold().replace('&',' and ')
    parts=re.findall(r'[a-z0-9]+',name)
    while parts and parts[-1] in ('llc','inc','incorporated','ltd','limited','corp','corporation'):parts.pop()
    return ' '.join(parts)+'|'+re.sub(r'[^a-z0-9]','',str(city or '').casefold()) if parts else ''

def keys(row):
    result={'phone:'+p for p in phones(row['phone'])}
    if row['place_id']:result.add('place:'+row['place_id'])
    name=business_key(row['name'],row['city'])
    if name:result.add('name:'+name)
    return result

def groups(rows):
    parent={r['id']:r['id'] for r in rows};seen={}
    def root(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
        return i
    for row in rows:
        for key in keys(row):
            if key in seen:
                a,b=root(row['id']),root(seen[key]);parent[max(a,b)]=min(a,b)
            else:seen[key]=row['id']
    return {i:root(i) for i in parent}

def matching_ids(db,uid,lid):
    rows=db.execute('SELECT id,name,phone,place_id,city FROM leads WHERE user_id=?',(uid,)).fetchall()
    mapping=groups(rows);root=mapping.get(lid,lid)
    return [i for i,r in mapping.items() if r==root] or [lid]
