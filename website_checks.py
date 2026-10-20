"""Small public-site checks. No form submissions; uncertain findings stay candidates."""
import http.client
import ipaddress
import json
import re
import socket
import ssl
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit
from scoring import phone_key, settings

class SiteError(ValueError):pass

def normalize_url(value):
    value=str(value or '').strip()
    if value and '://' not in value:value='https://'+value
    try:u=urlsplit(value)
    except ValueError:raise SiteError('Invalid website address')
    if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:
        raise SiteError('Use a public http or https website address')
    if u.port not in (None,80,443):raise SiteError('Only standard website ports are supported')
    return urlunsplit((u.scheme,u.netloc,u.path or '/',u.query,''))

def public_addresses(host,port):
    try:addresses=socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)
    except socket.gaierror:raise SiteError('Website address could not be resolved; availability remains unknown')
    if not addresses:raise SiteError('No website address found')
    for _,_,_,_,address in addresses:
        ip=ipaddress.ip_address(address[0].split('%')[0])
        if not ip.is_global:raise SiteError('Local, private, and reserved network addresses are not supported')
    return addresses

def fetch_public(url,options=None):
    options=options or settings()['website_checks']
    url=normalize_url(url)
    for hop in range(options['max_redirects']+1):
        u=urlsplit(url);host=u.hostname.encode('idna').decode();port=u.port or (443 if u.scheme=='https' else 80)
        addresses=public_addresses(host,port)
        family,kind,proto,_,address=addresses[0]
        sock=socket.socket(family,kind,proto);sock.settimeout(options['timeout_seconds'])
        conn=None
        try:
            sock.connect(address)
            if u.scheme=='https':sock=ssl.create_default_context().wrap_socket(sock,server_hostname=host)
            conn=http.client.HTTPConnection(host,port,timeout=options['timeout_seconds']);conn.sock=sock
            conn.request('GET',urlunsplit(('','',u.path or '/',u.query,'')),headers={'User-Agent':'BrasslineWebsiteCheck/1.0','Accept':'text/html','Accept-Encoding':'identity'})
            response=conn.getresponse()
            if response.status in (301,302,303,307,308):
                location=response.getheader('Location')
                if not location:raise SiteError('Redirect had no destination')
                url=normalize_url(urljoin(url,location));continue
            content_type=response.getheader('Content-Type','').lower()
            if response.status>=400:return {'url':url,'status':response.status,'html':''}
            if 'text/html' not in content_type and 'application/xhtml' not in content_type:raise SiteError('Website did not return HTML; manual review needed')
            raw=response.read(options['max_bytes']+1)
            if len(raw)>options['max_bytes']:raise SiteError('Page is too large for this light check; manual review needed')
            charset=re.search(r'charset=([\w-]+)',content_type)
            try:html=raw.decode(charset.group(1) if charset else 'utf-8',errors='replace')
            except LookupError:html=raw.decode('utf-8',errors='replace')
            return {'url':url,'status':response.status,'html':html}
        finally:
            if conn:conn.close()
            else:sock.close()
    raise SiteError('Too many redirects; manual review needed')

class Page(HTMLParser):
    def __init__(self):
        super().__init__();self.links=[];self.viewport=False;self.json_documents=[];self.text=[];self.script=None;self.hide=0;self.anchor=None;self.anchor_text=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='meta' and attrs.get('name','').lower()=='viewport':self.viewport='width=device-width' in attrs.get('content','').replace(' ','').lower()
        if tag in ('script','style'):
            self.hide+=1
            if tag=='script' and attrs.get('type','').lower()=='application/ld+json':self.script=[]
        if tag=='a':self.anchor=attrs.get('href','');self.anchor_text=[]
    def handle_data(self,data):
        if self.script is not None:self.script.append(data)
        if not self.hide:
            self.text.append(data)
            if self.anchor is not None:self.anchor_text.append(data)
    def handle_endtag(self,tag):
        if tag=='a' and self.anchor is not None:self.links.append((self.anchor,' '.join(self.anchor_text)));self.anchor=None
        if tag in ('script','style'):
            if self.script is not None:
                try:self.json_documents.append(json.loads(''.join(self.script)))
                except (ValueError,RecursionError):pass
                self.script=None
            self.hide=max(0,self.hide-1)

def objects(value):
    if isinstance(value,dict):
        yield value
        for child in value.values():yield from objects(child)
    elif isinstance(value,list):
        for child in value:yield from objects(child)

def inspect_pages(home, extra_pages=()):
    checked_at=datetime.now(timezone.utc).isoformat()
    report={'checked_at':checked_at,'pages':[],'findings':[],'contacts':[],'decision_makers':[],'limitations':['This light check does not render mobile layouts or test live forms. A missing feature is a review suggestion, not proof of a broken website.']}
    parsed=[]
    for item in [home,*extra_pages]:
        url=item['url'];status=item['status'];report['pages'].append({'url':url,'status':status})
        if status>=400:
            report['findings'].append({'kind':'availability','text':f'Supplied page returned HTTP {status}; verify the address and retry before confirming a website problem.','source':url});continue
        page=Page();page.feed(item['html']);parsed.append((url,page))
        if not page.viewport:report['findings'].append({'kind':'mobile_hint','text':'No device-width viewport declaration found. Check the page on a phone before confirming a mobile problem.','source':url})
        for href,_ in page.links:
            if href.lower().startswith('tel:') and phone_key(href[4:]):report['contacts'].append({'type':'business_phone','value':href[4:].split('?')[0],'source':url,'verification':'Public website listing; working status unverified'})
            if href.lower().startswith('mailto:') and '@' in href[7:]:report['contacts'].append({'type':'business_email','value':href[7:].split('?')[0],'source':url,'verification':'Public website listing'})
        for doc in page.json_documents:
            for obj in objects(doc):
                kinds=obj.get('@type',[]);kinds=kinds if isinstance(kinds,list) else [kinds]
                if 'Person' in kinds and obj.get('name') and any(word in str(obj.get('jobTitle','')).lower() for word in ('owner','president','manager','ceo','founder')):
                    organization=obj.get('worksFor') or obj.get('memberOf') or {}
                    organization=organization.get('name','') if isinstance(organization,dict) else ''
                    report['decision_makers'].append({'name':str(obj['name'])[:160],'role':str(obj.get('jobTitle',''))[:160],'phone':str(obj.get('telephone',''))[:80] if phone_key(obj.get('telephone')) else '', 'organization_name':str(organization)[:300],'source':url,'verification':'Website-published role; current authority needs confirmation'})
                if obj.get('founder'):
                    founders=obj['founder'] if isinstance(obj['founder'],list) else [obj['founder']]
                    for founder in founders:
                        if isinstance(founder,dict) and founder.get('name'):
                            report['decision_makers'].append({'name':str(founder['name'])[:160],'role':'Founder (not proof of current ownership)','phone':str(founder.get('telephone',''))[:80] if phone_key(founder.get('telephone')) else '', 'source':url,'verification':'Website-published founder; current authority needs confirmation'})
        text=' '.join(page.text)
        for match in re.finditer(r'\b(?:owner|owned and operated by|founder|president)\s*[:\-–]?\s+([A-Z][a-z]{1,25}(?:\s+[A-Z][a-z]{1,25}){1,2})\b',text):
            report['decision_makers'].append({'name':match.group(1),'role':'Owner or founder candidate','phone':'','source':url,'verification':'Text match; confirm identity and role','excerpt':text[max(0,match.start()-35):match.end()+60]})
    for key in ('contacts','decision_makers'):
        unique={}
        for item in report[key]:unique[json.dumps(item,sort_keys=True)]=item
        report[key]=list(unique.values())[:20]
    if not report['findings']:report['findings'].append({'kind':'unknown','text':'No obvious issue found by the light HTML check. This does not establish that the site is functional or mobile-friendly.','source':home['url']})
    return report,parsed

def check_website(url,business=None):
    home=fetch_public(url)
    first,parsed=inspect_pages(home)
    extra=[]
    if parsed:
        base,page=parsed[0]
        candidates=[]
        for href,label in page.links:
            target=urljoin(base,href)
            if urlsplit(target).hostname==urlsplit(base).hostname and urlsplit(target).scheme in ('http','https') and re.search(r'about|contact|team',label+' '+urlsplit(target).path,re.I):
                target=target.split('#')[0]
                if target!=base and target not in candidates:candidates.append(target)
        for target in candidates[:settings()['website_checks']['max_pages']-1]:
            try:extra.append(fetch_public(target))
            except (SiteError,OSError,http.client.HTTPException) as error:first['limitations'].append('Could not check '+target+': '+str(error))
    report,parsed=inspect_pages(home,extra);report['limitations']=first['limitations']
    if business:
        from lead_identity import business_key
        name=business_key(business.get('name','')).split('|')[0]
        text=' '.join(' '.join(p.text) for _,p in parsed)
        normalized=business_key(text).split('|')[0]
        phone=phone_key(business.get('phone',''))
        phone_matches=bool(phone and any(phone_key(c.get('value'))==phone for c in report['contacts'] if c['type']=='business_phone'))
        report['company_identity_confirmed']=bool(len(name.split())>=2 and name in normalized and phone_matches)
        report['identity_evidence']='Exact normalized company name and matching published business telephone on supplied website.' if report['company_identity_confirmed'] else 'Company identity remains unconfirmed.'
    return report
