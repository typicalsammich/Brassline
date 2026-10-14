"""Local, signed-in Chrome adapter. Google layout changes fail to unknown facts."""
import calendar
import hashlib
import json
import re
from datetime import datetime,timedelta,timezone
from urllib.parse import quote,unquote,urlsplit,parse_qs
from scoring import category_key,settings,phone_key
from website_checks import check_website

class BrowserAttention(RuntimeError):pass

def place_identity(url):
    url=unquote(url)
    match=re.search(r'0x[0-9a-f]+:0x[0-9a-f]+',url,re.I)
    if match:return match.group(0).lower()
    match=re.search(r'!1s([^!/?&]+)',url)
    if match:return match.group(1)
    return ''

def relative_date(value,now=None):
    now=now or datetime.now(timezone.utc)
    if isinstance(value,(int,float)):
        seconds=value/1e6 if value>1e14 else value/1000 if value>1e11 else value
        try:date=datetime.fromtimestamp(seconds,timezone.utc)
        except (ValueError,OSError,OverflowError):return None
        return date.isoformat() if datetime(2005,1,1,tzinfo=timezone.utc)<=date<=now else None
    if not isinstance(value,str):return None
    text=value.lower().strip()
    if text in ('just now','a moment ago'):return now.isoformat()
    text=re.sub(r'^(a|an) ', '1 ',text)
    match=re.fullmatch(r'(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago',text)
    if match:
        amount=int(match.group(1));unit=match.group(2)
        if amount>1000:return None
        if unit in ('month','year'):
            months=amount*(12 if unit=='year' else 1);offset=now.year*12+now.month-1-months;year,month=divmod(offset,12);month+=1
            return now.replace(year=year,month=month,day=min(now.day,calendar.monthrange(year,month)[1])).isoformat()
        days=amount*7 if unit=='week' else amount if unit=='day' else 0
        return (now-timedelta(days=days,hours=amount if unit=='hour' else 0,minutes=amount if unit=='minute' else 0)).isoformat()
    for fmt in ('%b %d, %Y','%B %d, %Y','%Y-%m-%d'):
        try:
            date=datetime.strptime(value,fmt).replace(tzinfo=timezone.utc)
            if date<=now:return date.isoformat()
        except ValueError:pass
    return None

def parse_review_payload(text):
    """Accept only identified review RPC frames, and store facts rather than people/text."""
    cleaned=text[4:].lstrip() if text.startswith(")]}'") else text
    documents=[]
    try:documents.append(json.loads(cleaned))
    except ValueError:
        for line in cleaned.splitlines():
            try:documents.append(json.loads(line))
            except ValueError:continue
    payloads=[]
    def frames(node,depth=0):
        if depth>8:return
        if isinstance(node,list):
            if len(node)>2 and node[0]=='wrb.fr' and node[1] in ('qv9Egd','/MapsUgcPostService.ListUgcPosts') and isinstance(node[2],str):
                try:payloads.append(json.loads(node[2]))
                except ValueError:pass
            else:
                for child in node:frames(child,depth+1)
    for document in documents:
        frames(document)
        # The older listugcposts endpoint returns its payload directly.
        if isinstance(document,list) and len(document)>2 and isinstance(document[2],list) and document[2] and isinstance(document[2][0],list):
            if document[2][0] and isinstance(document[2][0][0],list) and document[2][0][0] and isinstance(document[2][0][0][0],str):payloads.append(document)
    records={};limited=False
    for payload in payloads:
        if not isinstance(payload,list) or len(payload)<3 or not isinstance(payload[2],list):continue
        if len(payload)>6 and isinstance(payload[6],list) and payload[6] and payload[6][0] is True:limited=True
        for record in payload[2]:
            try:
                fields=record[0];review_id=fields[0];date_value=fields[1][6];rating=fields[2][0][0]
                if not isinstance(review_id,str) or not review_id.startswith(('Ch','Ci')) or rating not in (1,2,3,4,5):continue
                date=relative_date(date_value)
                if not date:continue
                reply_date=relative_date(fields[3][3]) if len(fields)>3 and isinstance(fields[3],list) and len(fields[3])>3 else None
                key=hashlib.sha256(review_id.encode()).hexdigest()[:24]
                records[key]={'id_hash':key,'date':date,'date_label':str(date_value),'date_precision':'approximate' if isinstance(date_value,str) and 'ago' in date_value else 'provided','stars':rating,'owner_reply_date':reply_date}
            except (IndexError,TypeError,ValueError):continue
    return list(records.values()),limited

def star_summary(labels):
    counts={}
    for label in labels:
        match=re.search(r'([1-5])\s+stars?\s*[,.:\-]?\s*([\d,]+)\s+reviews?',label,re.I)
        if match:counts[int(match.group(1))]=int(match.group(2).replace(',',''))
    if set(counts)!={1,2,3,4,5}:return {'star_breakdown':None,'review_count':None,'rating':None}
    total=sum(counts.values())
    return {'star_breakdown':{str(k):v for k,v in counts.items()},'review_count':total,'rating':round(sum(k*v for k,v in counts.items())/total,2) if total else None}

def same_industry(category,target):
    if not category:return None
    config=settings();actual=category_key(category,config);expected=target['category']
    if actual==expected:return True
    words=set(re.findall(r'[a-z]+',actual));wanted=set(re.findall(r'[a-z]+',expected))
    singular=lambda value:value[:-1] if value.endswith('s') else value
    return bool({singular(w) for w in words}&{singular(w) for w in wanted}-{'service','business','company','companies','contractor','contractors'})

class MapsBrowser:
    def __init__(self,profile,heartbeat):self.profile=profile;self.heartbeat=heartbeat
    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self.runtime=sync_playwright().start()
        self.context=self.runtime.chromium.launch_persistent_context(str(self.profile),channel='chrome',headless=False,viewport={'width':1280,'height':900},locale='en-US',args=['--window-position=-16000,-16000','--window-size=1280,900'])
        self.search=self.context.new_page();self.detail=self.context.new_page();self.payloads=[];self.current_identity='';self.capture_reviews=False
        self.detail.on('response',self.capture)
        return self
    def __exit__(self,*args):self.context.close();self.runtime.stop()
    def attention(self,page):
        if '/sorry/' in page.url or page.locator('iframe[src*="recaptcha"]').count():raise BrowserAttention('Google needs attention. Stop the worker, open the dedicated Google profile, and complete the check yourself.')
    def capture(self,response):
        if not self.capture_reviews:return
        request=response.request
        combined=unquote(response.url+' '+(request.post_data or ''))
        if not any(token in combined for token in ('listugcposts','qv9Egd','ListUgcPosts')):return
        if not self.current_identity or self.current_identity not in combined:return
        try:self.payloads.append(response.text())
        except Exception:pass
    def result_cards(self,tile,location):
        query=tile['wording']+' '+location['state']
        url=f"https://www.google.com/maps/search/{quote(query)}/@{tile['lat']},{tile['lon']},{settings()['scraper']['map_zoom']}z?hl=en"
        self.search.goto(url,wait_until='domcontentloaded',timeout=45000);self.attention(self.search)
        feed=self.search.locator('[role="feed"]')
        try:feed.wait_for(state='visible',timeout=15000)
        except Exception:raise BrowserAttention('Google did not show a readable results list. Check the dedicated profile; this tile has not been marked complete.')
        recenter=self.search.get_by_role('button',name='Search this area',exact=True)
        if recenter.count() and recenter.first.is_visible():recenter.first.click();self.search.wait_for_timeout(1800)
        previous=set();stalls=0
        for _ in range(settings()['scraper']['max_scrolls']):
            self.attention(self.search)
            # One DOM pass over the whole loaded list per scroll.
            rows=feed.evaluate('''node => Array.from(node.querySelectorAll('a[href*="/maps/place/"]')).map(a => {const card=a.closest('[role="article"]') || a.closest('.Nv2PK') || a.parentElement;return {name:a.getAttribute('aria-label') || '',url:a.href,text:card.innerText};})''')
            fresh=[]
            for row in rows:
                identity=place_identity(row['url']) or row['name'].strip().lower()
                if not row['name'] or identity in previous:continue
                previous.add(identity);lines=[line.strip() for line in row['text'].splitlines() if line.strip()]
                phone=re.search(r'(?:\+1[ -]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}',row['text'])
                category=''
                for i,line in enumerate(lines[:-1]):
                    if re.match(r'^\d\.\d\s*\(',line):category=lines[i+1].split('·')[0].strip();break
                fresh.append({'name':row['name'][:300],'source_url':row['url'],'place_id':place_identity(row['url']),'phone':phone.group(0) if phone else '', 'category':category[:150], 'closed':'permanently closed' in row['text'].lower()})
            yield fresh,False
            if not fresh:stalls+=1
            else:stalls=0
            exhausted='reached the end of the list' in feed.inner_text().lower()
            if exhausted:yield [],True;return
            if stalls>=4:raise BrowserAttention('Results stopped loading before an end marker. The unfinished tile will be retried later; known businesses will not be reopened.')
            feed.evaluate('node => node.scrollTop = node.scrollHeight');self.search.wait_for_timeout(1400)
            if self.heartbeat('Reading results list'):return
        raise BrowserAttention('Result-list scroll limit reached. This tile remains unfinished for a later run.')
    def business(self,card,target,location):
        self.capture_reviews=False;self.payloads=[]
        # Resolve share redirects, then reload the actual place page before reviews.
        self.detail.goto(card['source_url'],wait_until='domcontentloaded',timeout=45000)
        self.detail.wait_for_timeout(1200);self.attention(self.detail)
        canonical=self.detail.url
        if '/maps/place/' not in canonical:raise BrowserAttention('The Maps link did not resolve to a business page; facts remain unknown.')
        self.detail.goto(canonical,wait_until='domcontentloaded',timeout=45000);self.detail.locator('h1').first.wait_for(timeout=15000)
        self.current_identity=place_identity(self.detail.url)
        facts={'category_approved':True,'website_need':'unknown'};details={'source_url':canonical,'facts':facts};raw={'source':canonical,'scrape_status':'checked','review_dates':[],'owner_reply_dates':[],'warnings':[]}
        category=self.detail.locator('button[jsaction*="category"]')
        if category.count():details['category']=category.first.inner_text().strip();facts['wrong_industry']=same_industry(details['category'],target) is False
        phone=self.detail.locator('button[data-item-id^="phone:"]')
        if phone.count():
            phone_label=phone.first.get_attribute('data-item-id') or ''
            match=re.search(r'(?:tel:)?(\+?[\d ()-]{10,})',phone_label)
            if match and phone_key(match.group(1)):details['phone']=match.group(1)
        address=self.detail.locator('button[data-item-id="address"]')
        if address.count():
            raw['address']=address.first.get_attribute('aria-label') or address.first.inner_text()
            if location['kind']=='state' and not re.search(r'\b'+re.escape(location['state_code'])+r'\b',raw['address']):
                raw['warnings'].append('Business address does not confirm the requested state; review location before calling.');facts['category_approved']=False
        authority=self.detail.locator('a[data-item-id="authority"]')
        if authority.count():
            website=authority.first.get_attribute('href') or ''
            if urlsplit(website).hostname in ('www.google.com','google.com'):website=parse_qs(urlsplit(website).query).get('q',[website])[0]
            host=urlsplit(website).hostname or ''
            if any(host==domain or host.endswith('.'+domain) for domain in ('facebook.com','instagram.com','linkedin.com')):details['social_url']=website
            else:details['website_url']=website
        if details.get('website_url'):
            try:
                report=check_website(details['website_url']);details['website_check']=report
                for contact in report['contacts']:
                    if contact['type']=='business_email' and not details.get('email'):details['email']=contact['value']
                    if contact['type']=='business_phone' and not details.get('phone'):details['phone']=contact['value']
                if report['decision_makers']:
                    owner=report['decision_makers'][0];facts.update(owner_name=owner['name'],owner_role=owner['role'],owner_phone=owner['phone'],owner_source=owner['source'],owner_access='unknown')
            except Exception:raw['warnings'].append('Website check could not complete; website opportunity remains unknown.')
        else:raw['warnings'].append('No dedicated website link on Maps. Absence has not been verified against other sources.')
        reviews=self.detail.get_by_role('tab',name=re.compile('Reviews',re.I))
        if not reviews.count():raw['warnings'].append('No readable Reviews tab; review facts remain unknown.');details['review_facts']=raw;return details
        reviews.first.click();self.detail.wait_for_timeout(1200)
        tables=self.detail.locator('table,[role="table"]').evaluate_all('''tables => tables.filter(t=>t.getClientRects().length&&getComputedStyle(t).visibility!=='hidden').map(table=>Array.from(table.querySelectorAll('[aria-label]')).map(n=>n.getAttribute('aria-label')))''')
        summaries=[star_summary(labels) for labels in tables]
        summaries=[item for item in summaries if item['star_breakdown'] is not None]
        summary=summaries[0] if len(summaries)==1 else star_summary([]);raw.update(summary)
        if summary['star_breakdown'] is None:raw['warnings'].append('Star breakdown unreadable; rating and review count were not taken from other page numbers.')
        account=self.detail.locator('[aria-label^="Google Account:"]')
        if not account.count():raw['warnings'].append('Signed-in account could not be verified; newest review dates remain unknown.');details['review_facts']=raw;return details
        sort=self.detail.get_by_role('button',name=re.compile('Sort reviews|Sort',re.I))
        if not sort.count():raw['warnings'].append('Newest-first sorting is unavailable; review dates remain unknown.');details['review_facts']=raw;return details
        sort.first.click();newest=self.detail.get_by_role('menuitemradio',name='Newest',exact=True)
        if not newest.count():newest=self.detail.get_by_text('Newest',exact=True)
        if not newest.count():raw['warnings'].append('Could not select newest-first reviews; dates remain unknown.');details['review_facts']=raw;return details
        self.payloads=[];self.capture_reviews=True;newest.first.click();self.detail.wait_for_timeout(1800)
        for _ in range(settings()['scraper']['max_review_scrolls']):
            self.attention(self.detail)
            self.detail.evaluate('''() => {const review=document.querySelector('[data-review-id]');if(!review)return;let p=review.parentElement;while(p&&p.scrollHeight<=p.clientHeight+100)p=p.parentElement;if(p)p.scrollTop=p.scrollHeight;}''')
            self.detail.wait_for_timeout(1200)
            if self.heartbeat('Reading newest review data'):break
        self.capture_reviews=False;records={};limited=False
        for payload in self.payloads:
            parsed,thin=parse_review_payload(payload);limited=limited or thin
            for record in parsed:records[record['id_hash']]=record
        if limited:raw['warnings'].append('Google returned a limited review view; dates remain unknown.')
        elif records:
            raw['reviews']=list(records.values());raw['review_dates']=[r['date'] for r in records.values()];raw['owner_reply_dates']=[r['owner_reply_date'] for r in records.values() if r['owner_reply_date']]
            facts['activity']=[{'date':r['date'],'source':canonical,'kind':'customer review'} for r in records.values()][:100]
        else:raw['warnings'].append('Review network format was not recognized for this business; dates remain unknown.')
        details['review_facts']=raw;return details
