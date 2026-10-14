"""Bounded public-source research funded by the administrator, private lead imports."""
import hashlib,json,re,secrets,threading,time,urllib.request,urllib.error
from urllib.parse import urlencode,urlsplit
from fastapi import Request,HTTPException
from pydantic import BaseModel,Field
from scoring import phone_key,public_url,score
from scrape_planning import industry,location_index
import shared_services as shared
import website_checks

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None
def provider_json(url,payload=None,headers=None,timeout=35):
    request=urllib.request.Request(url,data=json.dumps(payload).encode() if payload is not None else None,headers={'Content-Type':'application/json','User-Agent':'Brassline/1.0',**(headers or {})})
    try:
        with urllib.request.build_opener(NoRedirect).open(request,timeout=timeout) as response:
            raw=response.read(2000001)
            if len(raw)>2000000:raise ValueError('Oversized provider response')
            return json.loads(raw)
    except urllib.error.HTTPError as error:
        if urlsplit(url).hostname=='api.resend.com':
            messages={401:'Resend rejected the API key. Replace it with a valid key from your Resend account.',403:'Resend denied access (HTTP 403). Domain verification requires a Full access API key; check the key permissions in Resend.',429:'Resend rate limit reached. Wait a moment before verifying again.'}
            raise HTTPException(502,messages.get(error.code,f'Resend returned HTTP {error.code}. Check your Resend dashboard and try verification again.')) from None
        raise HTTPException(502,'Research provider request failed. Check your provider dashboard; no automatic paid retry was made.') from None
    except Exception:
        if urlsplit(url).hostname=='api.resend.com':raise HTTPException(502,'Brassline could not reach Resend or read its response. Check your internet connection and retry domain verification.') from None
        raise HTTPException(502,'Research provider request failed. Check your provider dashboard; no automatic paid retry was made.') from None
def search_web(cfg,query,limit=5,maps=False):
    if cfg.get('serpapi_key'):
        parameters={'api_key':cfg['serpapi_key'],'engine':'google_maps' if maps else 'google','q':query,'hl':'en','gl':'us'}
        if maps:parameters['type']='search'
        result=provider_json('https://serpapi.com/search.json?'+urlencode(parameters))
        if result.get('error'):raise HTTPException(502,'Search provider returned an error; review its dashboard')
        if maps:
            return [{'name':r.get('title',''),'phone':r.get('phone',''),'place_id':r.get('place_id',''),'url':r.get('links',{}).get('place') or r.get('place_id_search') or 'https://www.google.com/maps/search/?api=1&query='+__import__('urllib.parse',fromlist=['quote']).quote(r.get('title','')),
                     'website':r.get('website',''),'snippet':r.get('address',''),'rating':r.get('rating'),'reviews':r.get('reviews'),'closed':bool(r.get('permanently_closed'))} for r in result.get('local_results',[])[:limit]]
        return [{'name':r.get('title',''),'url':r.get('link',''),'snippet':r.get('snippet','')} for r in result.get('organic_results',[])[:limit]]
    if cfg.get('firecrawl_key'):
        result=provider_json('https://api.firecrawl.dev/v2/search',{'query':query,'limit':limit,'country':'US','sources':['web']},{'Authorization':'Bearer '+cfg['firecrawl_key']})
        if not result.get('success'):raise HTTPException(502,'Web discovery failed; review your Firecrawl dashboard')
        data=result.get('data',{});rows=data.get('web',[]) if isinstance(data,dict) else data
        return [{'name':r.get('title',''),'url':r.get('url',''),'snippet':r.get('description',''),'website':''} for r in rows[:limit]]
    raise HTTPException(503,'The administrator has not connected a research provider')
def canonical(url):
    try:
        parsed=urlsplit(url)
        if not public_url(url):return ''
        return parsed.hostname.lower().removeprefix('www.')+parsed.path.rstrip('/')
    except ValueError:return ''
def social(url):return (urlsplit(url).hostname or '').lower().removeprefix('www.') in ('instagram.com','facebook.com','m.facebook.com') if public_url(url) else False
def identity_match(name,row,phone=''):
    if phone_key(phone) and phone_key(phone) in re.sub(r'\D','',row.get('snippet','')):return True
    tokens=[w for w in re.findall(r'[a-z0-9]+',name.lower()) if w not in ('the','and','llc','inc','company','co','services','service')]
    text=(row.get('name','')+' '+row.get('snippet','')).lower()
    return len(tokens)>=2 and all(w in text for w in tokens)

class Research(BaseModel):
    industry:str=Field(min_length=2,max_length=150)
    location_id:str=Field(min_length=2,max_length=160)
    max_results:int=Field(default=5,ge=1,le=20)
class Import(BaseModel):indices:list[int]=Field(min_length=1,max_length=20)

def install(app,connect,user,practice):
    with connect() as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS research_runs(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),query TEXT NOT NULL,state TEXT NOT NULL,report TEXT NOT NULL DEFAULT '[]',message TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL,cancelled INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS public_research_cache(cache_key TEXT PRIMARY KEY,payload TEXT NOT NULL,expires REAL NOT NULL);''')
        db.execute("UPDATE research_runs SET state='interrupted',message='Server restarted. Completed reports remain available; start a new search to continue.' WHERE state IN ('queued','running')")
    slots=threading.BoundedSemaphore(2)
    def cancelled(run):
        with connect() as db:row=db.execute('SELECT cancelled FROM research_runs WHERE id=?',(run,)).fetchone()
        return not row or bool(row['cancelled'])
    def lookup(uid,run,cfg,query,limit,maps=False):
        key=hashlib.sha256(json.dumps([query,limit,maps,'serpapi' if cfg.get('serpapi_key') else 'firecrawl']).encode()).hexdigest()
        with connect() as db:cached=db.execute('SELECT payload FROM public_research_cache WHERE cache_key=? AND expires>?',(key,time.time())).fetchone()
        if cached:return json.loads(cached['payload'])
        if cancelled(run):raise HTTPException(409,'Research cancelled')
        # Search units conservatively reserve result slots rather than treating calls as equal prices.
        shared.reserve(uid,'research',limit,'research:'+run+':'+key)
        rows=search_web(cfg,query,limit,maps)
        with connect() as db:db.execute('INSERT OR REPLACE INTO public_research_cache VALUES(?,?,?)',(key,json.dumps(rows),time.time()+86400))
        return rows
    def inspect_candidate(uid,run,cfg,seed,trade,place):
        name=seed.get('name','').strip()[:300];source=seed.get('url','');phone=seed.get('phone','')[:80]
        rows=lookup(uid,run,cfg,'"'+name+'" '+place['label']+' website owner contact',5)
        social_rows=lookup(uid,run,cfg,'"'+name+'" '+place['label']+' (site:instagram.com OR site:facebook.com)',3)
        matches=[r for r in rows+social_rows if identity_match(name,r,phone) and public_url(r.get('url',''))]
        evidence=[{'kind':'discovery','url':source,'text':seed.get('snippet','')[:600],'status':'Business identity and activity need review'}] if public_url(source) else []
        evidence.extend({'kind':'search evidence','url':r['url'],'text':r.get('snippet','')[:600],'status':'Search excerpt; verify on the source page'} for r in matches)
        profiles=[r['url'] for r in matches if social(r['url'])]
        website=seed.get('website','')
        excluded=('google.com','yelp.com','bbb.org','angi.com','yellowpages.com','mapquest.com','thumbtack.com','nextdoor.com')
        if not website:
            candidates=[r['url'] for r in matches if not social(r['url']) and not any((urlsplit(r['url']).hostname or '').endswith(h) for h in excluded)]
            if candidates:website=candidates[0]
        check={};limitations=['Discovery is not proof of current operations, buying authority, or website absence. Confirm matches and findings before outreach.']
        if public_url(website):
            shared.reserve(uid,'research',3,'website:'+run+':'+hashlib.sha256(website.encode()).hexdigest())
            try:check=website_checks.check_website(website)
            except Exception:check={'findings':[],'decision_makers':[],'contacts':[],'limitations':['Website check failed; availability remains unknown.']}
            evidence.extend({'kind':'website finding','url':r['source'],'text':r['text'],'status':'Review candidate'} for r in check.get('findings',[]))
        else:limitations.append('No dedicated website found in this bounded search; do not label as confirmed missing without further checking.')
        activity=[]
        # Optional public Instagram evidence; capped to three posts per confirmed profile candidate.
        instagram=next((p for p in profiles if (urlsplit(p).hostname or '').endswith('instagram.com') and len(urlsplit(p).path.strip('/').split('/'))==1),None)
        if cfg.get('apify_key') and instagram:
            shared.reserve(uid,'research',3,'social:'+run+':'+hashlib.sha256(instagram.encode()).hexdigest())
            try:
                posts=provider_json('https://api.apify.com/v2/acts/apify~instagram-scraper/run-sync-get-dataset-items?timeout=60&memory=256',{'directUrls':[instagram],'resultsType':'posts','resultsLimit':3},{'Authorization':'Bearer '+cfg['apify_key']},70)
                for post in posts[:3] if isinstance(posts,list) else []:
                    if public_url(post.get('url','')):
                        evidence.append({'kind':'public social post','url':post['url'],'text':str(post.get('caption',''))[:600],'date':post.get('timestamp',''),'status':'Confirm this profile belongs to the business'})
                        # Post dates alone are not confirmed company activity until identity is reviewed.
                        activity.append({'date':post.get('timestamp',''),'source':post['url'],'kind':'Public post candidate'})
            except HTTPException:limitations.append('Public social enrichment was unavailable; review linked profiles manually.')
        contacts=check.get('contacts',[]);owner_candidates=check.get('decision_makers',[])
        if not phone:phone=next((r['value'] for r in contacts if r['type']=='business_phone'),'')
        email=next((r['value'] for r in contacts if r['type']=='business_email'),'')
        facts={'website_need':'unknown','category_approved':True,'permanently_closed':bool(seed.get('closed'))}
        candidate={'name':name,'phone':phone,'email':email,'category':trade['category'],'city':place['label'],'place_id':seed.get('place_id',''),'source_url':source,'website_url':website if public_url(website) else '', 'social_url':profiles[0] if profiles else '', 'facts':facts,'website_check':check,
                   'evidence':evidence,'decision_maker_candidates':owner_candidates,'activity_candidates':activity,'limitations':limitations,'review_facts':{'research_source':'Administrator-funded public research','evidence':evidence,'limitations':limitations}}
        result=score(candidate);candidate['score']=result['score'];candidate['reasons']=result['reasons']
        candidate['why_contact']=('Website review found '+str(len([f for f in check.get('findings',[]) if f['kind']!='unknown']))+' issue candidates; confirm their impact.' if website else 'Website need is worth checking: no dedicated site found in this bounded search.')
        if seed.get('closed'):candidate['why_contact']='Skip: listing reports permanent closure.'
        candidate['review_required']=True
        return candidate
    def work(uid,run,data):
        report=[]
        try:
            if not slots.acquire(blocking=False):raise HTTPException(429,'Two research jobs are already active. Try later.')
            try:
                cfg=shared.config();place=location_index()[data.location_id];trade=industry(data.industry)
                with connect() as db:db.execute("UPDATE research_runs SET state='running',message='Searching listings, social profiles and independent businesses' WHERE id=?",(run,))
                rows=lookup(uid,run,cfg,trade['wordings'][0]+' in '+place['label'],data.max_results,bool(cfg.get('serpapi_key')))
                # Find social-first companies too, then deduplicate against listing discoveries.
                extras=lookup(uid,run,cfg,trade['wordings'][0]+' '+place['label']+' (site:instagram.com OR site:facebook.com)',min(5,data.max_results))
                seen=set()
                for seed in rows+extras:
                    if cancelled(run):break
                    key=phone_key(seed.get('phone')) or seed.get('place_id') or re.sub(r'\W','',seed.get('name','').lower())
                    if not key or key in seen or not public_url(seed.get('url','')):continue
                    seen.add(key)
                    if len(report)>=data.max_results:break
                    report.append(inspect_candidate(uid,run,cfg,seed,trade,place))
                    with connect() as db:db.execute('UPDATE research_runs SET report=?,message=? WHERE id=?',(json.dumps(report),'Researched '+str(len(report))+' businesses; evidence needs review',run))
                with connect() as db:db.execute('UPDATE research_runs SET state=?,message=? WHERE id=?',('cancelled' if cancelled(run) else 'complete','Completed reports are available. Import selected prospects after review.',run))
            finally:slots.release()
        except Exception as error:
            message=error.detail if isinstance(error,HTTPException) else 'Research interrupted. Review provider settings and usage; paid requests are not retried automatically.'
            with connect() as db:db.execute("UPDATE research_runs SET state='stopped',report=?,message=? WHERE id=?",(json.dumps(report),str(message),run))
    @app.get('/api/research')
    def dashboard(request:Request):
        uid=user(request)['id']
        with connect() as db:rows=db.execute('SELECT * FROM research_runs WHERE user_id=? ORDER BY created_at DESC LIMIT 20',(uid,)).fetchall()
        return {'runs':[{**dict(row),'report':json.loads(row['report'])} for row in rows]}
    @app.post('/api/research/start')
    def start(data:Research,request:Request):
        uid=user(request)['id'];cfg=shared.config()
        if practice:raise HTTPException(400,'Paid research is disabled in practice')
        if data.location_id not in location_index():raise HTTPException(400,'Choose a US city or state suggestion')
        if data.max_results>cfg['max_research_results']:raise HTTPException(400,'This search exceeds the administrator per-run limit')
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            limit=shared.allowance(db,uid)
            if not cfg['research_enabled'] or limit['paused'] or not limit['research']:raise HTTPException(403,'Ask the administrator to enable your research allowance')
            if db.execute("SELECT 1 FROM research_runs WHERE user_id=? AND state IN ('queued','running')",(uid,)).fetchone():raise HTTPException(409,'Finish or stop your current research job first')
            run=secrets.token_urlsafe(18);db.execute('INSERT INTO research_runs(id,user_id,query,state,created_at) VALUES(?,?,?,\'queued\',?)',(run,uid,json.dumps(data.model_dump()),time.time()))
        threading.Thread(target=work,args=(uid,run,data),daemon=True,name='public-research').start()
        return {'ok':True,'id':run}
    @app.post('/api/research/{run}/stop')
    def stop(run:str,request:Request):
        with connect() as db:
            if db.execute('UPDATE research_runs SET cancelled=1 WHERE id=? AND user_id=?',(run,user(request)['id'])).rowcount!=1:raise HTTPException(404,'Research not found')
        return {'ok':True}
    @app.post('/api/research/{run}/import')
    def import_selected(run:str,data:Import,request:Request):
        uid=user(request)['id']
        with connect() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM research_runs WHERE id=? AND user_id=?',(run,uid)).fetchone()
            if not row:raise HTTPException(404,'Research not found')
            report=json.loads(row['report']);indices=list(dict.fromkeys(data.indices))
            if any(i<0 or i>=len(report) for i in indices):raise HTTPException(400,'Select valid research results')
            batch=db.execute('INSERT INTO batches(user_id,name) VALUES(?,?)',(uid,'Research · '+run[:8])).lastrowid;added=0;duplicates=0
            for index in indices:
                item=report[index];existing=db.execute('SELECT id,name,phone,place_id,source_url FROM leads WHERE user_id=?',(uid,)).fetchall()
                duplicate=any((item.get('place_id') and r['place_id']==item['place_id']) or (phone_key(item['phone']) and phone_key(r['phone'])==phone_key(item['phone'])) or canonical(r['source_url'])==canonical(item['source_url']) or re.sub(r'\W','',r['name'].lower())==re.sub(r'\W','',item['name'].lower()) for r in existing)
                if duplicate:duplicates+=1;continue
                fields=['name','phone','email','category','city','place_id','website_url','social_url','source_url']
                lid=db.execute('INSERT INTO leads(user_id,batch_id,'+','.join(fields)+',facts,website_check,review_facts) VALUES('+','.join('?' for _ in range(14))+')',[uid,batch]+[item.get(k,'') for k in fields]+[json.dumps(item['facts']),json.dumps(item['website_check']),json.dumps(item['review_facts'])]).lastrowid
                db.execute('INSERT INTO lead_batches VALUES(?,?)',(lid,batch));added+=1
            if not added:db.execute('DELETE FROM batches WHERE id=?',(batch,))
        return {'ok':True,'added':added,'duplicates':duplicates}
    app.state.research_work=work
