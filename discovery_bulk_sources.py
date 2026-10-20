"""Official CA/FL bulk feeds: stream, validate, atomically cache, then keyset-page.

Only reduced public business fields are cached, never residential street addresses.
No paid providers or browser automation. Downloads use the publishers' public flows.
"""
import csv
from contextlib import closing
import hashlib
import http.cookiejar
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPCookieProcessor

CA_URL='https://www.cslb.ca.gov/onlineservices/dataportal/ListByClassification'
FL_URL='https://www2.myfloridalicense.com/sto/file_download/extracts/CONSTRUCTIONLICENSE_1.csv'
FL_DOC='https://www2.myfloridalicense.com/about-us/understanding-dbpr-codes/'
KEYS=('ca_cslb','fl_dbpr_construction')
TTL=7*86400
ROOT=None
LOCK=threading.Lock()
CA_SHEET_REQUIRED={'LicenseNumber','LastUpdated','BusinessType','BusinessName','City','State','PhoneNumber','IssueDate','ExpirationDate','Classification','Status'}
CA_SEGMENTS={'C-36':'PLUMBING','C-39':'ROOFING','C-10':'ELECTRICAL','C-20':'HVAC','B':'GENERAL BUILDING CONTRACTOR',
             'B-2':'REMODELING','C-33':'PAINTING','C-27':'LANDSCAPING','C-53':'POOL CONTRACTOR','C-7':'LOW VOLTAGE ELECTRICAL'}
CA_TRADES={'C36':'Plumbing','C39':'Roofing','C10':'Electrical','C20':'HVAC','C38':'HVAC refrigeration',
           'B':'General contractor','B2':'Residential remodeling','C33':'Painting','C27':'Landscaping','C53':'Pool contractor','C7':'Low voltage electrical'}
FL_TRADES={'CAC':'HVAC air conditioning','RA':'HVAC air conditioning','CBC':'Building contractor','RB':'Building contractor',
           'CCC':'Roofing','RC':'Roofing','CFC':'Plumbing','RF':'Plumbing','CGC':'General contractor','RG':'General contractor',
           'CMC':'HVAC mechanical','RM':'HVAC mechanical','CPC':'Pool contractor','RP':'Pool contractor',
           'CRC':'Residential contractor','RR':'Residential contractor','CSC':'Sheet metal contractor','RS':'Sheet metal contractor',
           'CUC':'Utility excavation contractor','RU':'Utility excavation contractor','CVC':'Solar contractor','SCC':'Specialty contractor','RX':'Specialty contractor'}


class BulkError(ValueError):pass


def configure(folder):
    global ROOT
    ROOT=Path(folder)/'discovery-source-cache'


def now():return datetime.now(timezone.utc).isoformat()


def date(value):
    try:return datetime.strptime(value.strip(),'%m/%d/%Y').replace(tzinfo=timezone.utc)
    except (ValueError,AttributeError):return None


def current(expiration):
    parsed=date(expiration)
    return bool(parsed and parsed.date()>=datetime.now(timezone.utc).date())


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,request,fp,code,msg,headers,newurl):
        target=urlsplit(newurl)
        # Official form may canonicalize its URL. Never follow to another origin/path.
        original=urlsplit(request.full_url)
        if target.scheme!='https' or target.hostname!=original.hostname or not target.path.lower().startswith('/onlineservices/dataportal/'):
            return None
        return super().redirect_request(request,fp,code,msg,headers,newurl)


class Form(HTMLParser):
    def __init__(self):super().__init__();self.values={};self.targets=set()
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='input' and a.get('type','').lower()=='hidden' and a.get('name','').startswith('__'):
            self.values[a['name']]=a.get('value','')
        if tag=='a':
            match=re.search(r"__doPostBack\('([^']+)'",a.get('href',''))
            if match:self.targets.add(match[1])


def open_feed(key,segment='C-36'):
    opener=build_opener(NoRedirect(),HTTPCookieProcessor(http.cookiejar.CookieJar()))
    if key=='fl_dbpr_construction':
        return opener.open(Request(FL_URL,headers={'User-Agent':'Brassline public-data pilot/1.0'}),timeout=30)
    if key!='ca_cslb':raise BulkError('Unknown bulk source')
    def page(data=None):
        with opener.open(Request(CA_URL,data=data,headers={'User-Agent':'Brassline public-data pilot/1.0'}),timeout=30) as response:
            text=response.read(1_000_001)
            if len(text)>1_000_000:raise BulkError('CA download form exceeds size limit')
            form=Form();form.feed(text.decode('utf-8',errors='replace'));return form
    first=page()
    if '__VIEWSTATE' not in first.values or '__EVENTVALIDATION' not in first.values:
        raise BulkError('CA public download form changed; no request submitted')
    if segment not in CA_SEGMENTS:raise BulkError('Unsupported CA classification')
    fields={**first.values,'__EVENTTARGET':'ctl00$MainContent$btnSearch','__EVENTARGUMENT':'','ctl00$MainContent$lbClassification':segment}
    return opener.open(Request(CA_URL,data=urlencode(fields).encode(),headers={'User-Agent':'Brassline public-data pilot/1.0'}),timeout=90)


class LimitedStream(io.RawIOBase):
    def __init__(self,response):self.response=response;self.total=0;self.digest=hashlib.sha256();self.deadline=time.monotonic()+480
    def readable(self):return True
    def readinto(self,target):
        if time.monotonic()>self.deadline:raise BulkError('Bulk refresh exceeded eight-minute processing limit')
        block=self.response.read(min(len(target),65536));self.total+=len(block)
        if self.total>256_000_000:raise BulkError('Bulk response exceeds 256 MB stream limit')
        self.digest.update(block);target[:len(block)]=block;return len(block)


def ca_row(row):
    if row.get('PrimaryStatus','').strip().upper()!='CLEAR' or not current(row.get('ExpirationDate','')):return None
    identity=row.get('LicenseNo','').strip()
    if not identity.isdigit() or not row.get('BusinessName','').strip():raise BulkError('CA record identity schema changed')
    classes=[re.sub(r'[^A-Z0-9]','',part.upper()) for part in row.get('Classifications(s)','').split('|')]
    return {'id':identity,'name':row['BusinessName'].strip(),'full_name':row.get('FullBusinessName','').strip(),
            'phone':row.get('BusinessPhone','').strip(),'business_type':row.get('BusinessType','').strip(),
            'city':row.get('City','').strip(),'state':row.get('State','').strip() or 'CA',
            'category':' | '.join(CA_TRADES.get(c,c+' contractor') for c in classes if c),
            'classes':' | '.join(classes),'license_status':'CLEAR','expiration':row['ExpirationDate'].strip(),
            'issued':row.get('IssueDate','').strip(),'last_update':row.get('LastUpdate','').strip()}


def fl_row(row):
    # Official legacy construction export: 21 documented columns + optional blank trailer.
    if len(row) not in (21,22) or (len(row)==22 and row[21].strip()) or row[0].strip()!='06':
        raise BulkError('FL construction CSV layout changed; no positional guessing')
    rank=row[1].strip();license_number=row[20].strip()
    if rank not in FL_TRADES:return None
    if row[13].strip()!='C' or row[14].strip()!='A' or not current(row[17]):return None
    if not license_number or not row[2].strip():raise BulkError('FL license identity missing')
    # A DBA is associated with the license holder; it does not establish ownership.
    business=row[3].strip() or row[2].strip()
    suffix=hashlib.sha256(business.casefold().encode()).hexdigest()[:16]
    return {'id':license_number+':'+suffix,'license_id':license_number,'name':business,
            'licensee':row[2].strip(),'phone':'','city':row[8].strip(),'state':row[9].strip() or 'FL',
            'category':FL_TRADES[rank],'rank':rank,'license_status':'Current, Active',
            'expiration':row[17].strip(),'issued':row[15].strip()}


def parse_rows(key,stream):
    if key!='fl_dbpr_construction':raise BulkError('Unsupported CSV feed')
    for values in csv.reader(stream):
        if not values:continue
        row=fl_row(values)
        if row:yield row


class SafeXML:
    def __init__(self,stream):self.stream=stream;self.tail=b''
    def read(self,size=-1):
        data=self.stream.read(size)
        check=(self.tail+data).upper()
        if b'<!DOCTYPE' in check or b'<!ENTITY' in check:raise BulkError('XML entity declarations are not supported')
        self.tail=check[-16:];return data


def xml_nodes(stream,tag):
    for event,element in ET.iterparse(SafeXML(stream),events=('end',)):
        if element.tag.rsplit('}',1)[-1]==tag:
            yield element
            element.clear()


def parse_ca_workbook(stream):
    """Bounded read-only XLSX reader. Never executes formulae or external links."""
    with zipfile.ZipFile(stream) as archive:
        entries=archive.infolist()
        if len(entries)>100 or sum(e.file_size for e in entries)>120_000_000:
            raise BulkError('CA workbook exceeds bounded expansion size')
        strings=[];text_bytes=0
        if 'xl/sharedStrings.xml' in archive.namelist():
            with archive.open('xl/sharedStrings.xml') as source:
                for element in xml_nodes(source,'si'):
                    value=''.join(e.text or '' for e in element.iter() if e.tag.rsplit('}',1)[-1]=='t')
                    text_bytes+=len(value.encode('utf-8'));strings.append(value)
                    if len(strings)>500000 or text_bytes>40_000_000:raise BulkError('CA workbook string table exceeds memory limit')
        sheets=[e.filename for e in entries if re.fullmatch(r'xl/worksheets/[^/]+\.xml',e.filename)]
        if len(sheets)!=1:raise BulkError('CA workbook sheet layout changed')
        header=None;count=0
        with archive.open(sheets[0]) as source:
            for element in xml_nodes(source,'row'):
                count+=1
                if count>250000:raise BulkError('CA classification workbook exceeds row limit')
                values={}
                for cell in element:
                    if cell.tag.rsplit('}',1)[-1]!='c':continue
                    if any(e.tag.rsplit('}',1)[-1]=='f' for e in cell):raise BulkError('CA evidence workbook contains formula cells')
                    column=re.match(r'([A-Z]+)',cell.get('r',''))
                    if not column:raise BulkError('CA workbook cell reference changed')
                    index=0
                    for char in column[1]:index=index*26+ord(char)-64
                    if index>100:raise BulkError('CA workbook column limit exceeded')
                    kind=cell.get('t','n');value=next((e.text or '' for e in cell if e.tag.rsplit('}',1)[-1]=='v'),'')
                    if kind=='s':
                        if value:
                            if not value.isdigit() or int(value)>=len(strings):raise BulkError('Invalid CA shared string reference')
                            value=strings[int(value)]
                    elif kind=='inlineStr':value=''.join(e.text or '' for e in cell.iter() if e.tag.rsplit('}',1)[-1]=='t')
                    elif kind not in ('n','str'):raise BulkError('Unsupported CA cell type')
                    values[index-1]=str(value)
                if not values:continue
                row=[values.get(i,'') for i in range(max(values)+1)]
                if header is None:
                    header=row
                    if not CA_SHEET_REQUIRED<=set(header):raise BulkError('CA classification workbook headers changed')
                    continue
                record=dict(zip(header,row))
                normalized=ca_row({'LicenseNo':record.get('LicenseNumber',''),'LastUpdate':record.get('LastUpdated',''),
                                   'BusinessName':record.get('BusinessName',''),'FullBusinessName':'',
                                   'BusinessPhone':record.get('PhoneNumber',''),'BusinessType':record.get('BusinessType',''),
                                   'City':record.get('City',''),'State':record.get('State',''),'IssueDate':record.get('IssueDate',''),
                                   'ExpirationDate':record.get('ExpirationDate',''),'PrimaryStatus':record.get('Status',''),
                                   'Classifications(s)':record.get('Classification','')})
                if normalized:yield normalized
        if header is None:raise BulkError('CA workbook has no header row')


def cache_path(key,segment='C-36'):
    if key not in KEYS or ROOT is None:raise BulkError('Bulk cache is not configured')
    if key=='ca_cslb' and segment not in CA_SEGMENTS:raise BulkError('Unsupported CA cache segment')
    return ROOT/(key+('_'+segment.replace('-','') if key=='ca_cslb' else '')+'.sqlite3')


def info(path):
    if not path.exists():return None
    with closing(sqlite3.connect(path)) as db:
        db.row_factory=sqlite3.Row
        row=db.execute('SELECT * FROM metadata').fetchone()
        return dict(row) if row else None


def ensure(key,segment='C-36'):
    path=cache_path(key,segment)
    with LOCK:
        previous=info(path)
        if previous and time.time()-previous['fetched_epoch']<TTL:return previous
        ROOT.mkdir(parents=True,exist_ok=True)
        import shutil
        if shutil.disk_usage(ROOT).free<200_000_000:raise BulkError('Bulk refresh needs at least 200 MB free disk space')
        # Only this explicitly named cache staging file is replaced/removed.
        temporary=path.with_suffix('.building.sqlite3');temporary.unlink(missing_ok=True)
        for suffix in ('-journal','-wal','-shm'):
            temporary.with_name(temporary.name+suffix).unlink(missing_ok=True)
        try:
            with closing(sqlite3.connect(temporary)) as db:
                db.execute('CREATE TABLE records(id TEXT PRIMARY KEY,payload TEXT NOT NULL,search_text TEXT NOT NULL,expires TEXT NOT NULL)')
                db.execute('CREATE TABLE metadata(fetched_epoch REAL,retrieved_at TEXT,generation TEXT,records INTEGER,stream_bytes INTEGER)')
                count=0
                with (open_feed(key,segment) if key=='ca_cslb' else open_feed(key)) as response:
                    content_type=response.headers.get('Content-Type','').lower()
                    expected='spreadsheetml' if key=='ca_cslb' else 'csv'
                    if expected not in content_type:raise BulkError('Official bulk response has unexpected file type')
                    limited=LimitedStream(response)
                    temporary_workbook=None
                    if key=='ca_cslb':
                        import tempfile
                        temporary_workbook=tempfile.SpooledTemporaryFile(max_size=1_000_000,dir=ROOT)
                        try:
                            while True:
                                block=limited.read(65536)
                                if not block:break
                                temporary_workbook.write(block)
                                if temporary_workbook.tell()>32_000_000:raise BulkError('CA workbook exceeds 32 MB download limit')
                            temporary_workbook.seek(0)
                        except BaseException:
                            temporary_workbook.close();raise
                        records=parse_ca_workbook(temporary_workbook)
                    else:
                        text=io.TextIOWrapper(io.BufferedReader(limited),encoding='utf-8-sig',newline='')
                        records=parse_rows(key,text)
                    try:
                      for record in records:
                        payload=json.dumps(record,ensure_ascii=False,separators=(',',':'))
                        search=(record['name']+' '+record['category']+' '+record.get('classes','')).upper()
                        db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?,?)',(record['id'],payload,search,date(record['expiration']).date().isoformat()));count+=1
                        if count%10000==0:
                            if db.execute('PRAGMA page_count').fetchone()[0]*db.execute('PRAGMA page_size').fetchone()[0]>150_000_000:
                                raise BulkError('Normalized source cache exceeds 150 MB pilot limit')
                    finally:
                        records.close()
                        if temporary_workbook:temporary_workbook.close()
                    if not count:raise BulkError('Official feed produced no current records; previous cache retained')
                    generation=limited.digest.hexdigest();size=limited.total
                count=db.execute('SELECT count(*) FROM records').fetchone()[0]
                db.execute('INSERT INTO metadata VALUES(?,?,?,?,?)',(time.time(),now(),generation,count,size))
                db.commit()
                if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise BulkError('Bulk cache integrity check failed')
            os.replace(temporary,path)
            return info(path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise


def schema(key):
    if key=='ca_cslb':
        cached=[info(cache_path(key,code)) for code in CA_SEGMENTS] if ROOT else []
        cached=[m for m in cached if m]
        return {'metadata_url':CA_URL,'checked_fields':sorted(CA_SHEET_REQUIRED),'cache_days':7,'phone_available':True,'classifications':list(CA_SEGMENTS),'format':'Statewide class-specific XLSX',
                'records':sum(m['records'] for m in cached),'retrieved_at':min((m['retrieved_at'] for m in cached),default='')}
    metadata=ensure(key)
    return {**metadata,'metadata_url':CA_URL if key=='ca_cslb' else FL_DOC,
            'checked_fields': ['Board','Occupation','Licensee','DBA','City','State','License Number','Primary Status','Secondary Status','Expiration','Alternate License Number'],
            'cache_days':7,'phone_available':key=='ca_cslb'}


def search_terms(industries):
    result=[]
    for word in industries:
        word=word.strip().upper()
        for stem in ('PLUMB','ROOF','ELECTRIC','HVAC','PAINT','LANDSCAP','REMODEL'):
            if stem in word:word=stem;break
        if word:result.append(word)
    return result


def fetch_page(key,cursor='',limit=100,industries=()):
    after='';segment='C-36';saved={};terms=search_terms(industries)
    selected=[code for code,label in CA_SEGMENTS.items() if not terms or any(term in label or term==code.upper() for term in terms)]
    if not selected:selected=list(CA_SEGMENTS)
    if key=='ca_cslb':segment=selected[0]
    if cursor:
        try:
            saved=json.loads(cursor)
            if not isinstance(saved,dict):raise ValueError('Checkpoint must be an object')
            if key=='ca_cslb' and saved.get('segment') in selected:segment=saved['segment']
        except (ValueError,TypeError,KeyError):raise BulkError('Invalid bulk checkpoint')
    if key=='ca_cslb' and not cursor:segment=selected[0]
    metadata=ensure(key,segment)
    checkpoints=saved.get('segments',{}) if key=='ca_cslb' else {}
    position=checkpoints.get(segment,saved)
    if position.get('generation')==metadata['generation']:after=str(position.get('after',''))
    limit=min(100,max(1,limit))
    where='id>? AND expires>=?';args=[after,datetime.now(timezone.utc).date().isoformat()]
    if terms:
        where+=' AND ('+' OR '.join('search_text LIKE ? ESCAPE \'!\'' for _ in terms)+')'
        args.extend('%'+term.replace('!','!!').replace('%','!%').replace('_','!_')+'%' for term in terms)
    with closing(sqlite3.connect(cache_path(key,segment))) as db:
        rows=db.execute('SELECT id,payload FROM records WHERE '+where+' ORDER BY id LIMIT ?',args+[limit+1]).fetchall()
    found=[{**json.loads(payload),'snapshot_at':metadata['retrieved_at']} for _,payload in rows[:limit]]
    checkpoint={'generation':metadata['generation'],'after':rows[min(len(rows),limit)-1][0] if rows else after}
    exhausted=len(rows)<=limit
    if key=='ca_cslb':
        checkpoints[segment]={**checkpoint,'done':exhausted}
        pending=[code for code in selected if not checkpoints.get(code,{}).get('done')]
        if pending:
            ordered=selected[selected.index(segment)+1:]+selected[:selected.index(segment)+1]
            next_segment=next(code for code in ordered if code in pending)
            checkpoint={'segment':next_segment,'segments':checkpoints};exhausted=False
        else:
            checkpoint={'segment':segment,'segments':checkpoints};exhausted=True
    return found,json.dumps(checkpoint,separators=(',',':')),exhausted


def normalize(key,row,retrieved):
    source=CA_URL if key=='ca_cslb' else FL_URL
    people=[];snapshot=row.get('snapshot_at',retrieved)
    if key=='fl_dbpr_construction':
        people.append({'name':row['licensee'],'role':'License holder / qualifier','source_url':source,
                       'evidence':'DBPR construction license '+row['license_id']+' associates this licensee with the listed DBA. Ownership is not established.',
                       'basis':'registry','retrieved_at':snapshot})
    return {'source':key,'source_id':row['id'],'registration_id':row['id'],'jurisdiction':'CA' if key=='ca_cslb' else 'FL',
            'license_id':row.get('license_id',row['id']),'name':row['name'],'city':row['city'],'state':row['state'],
            'category':row['category'],'phone':row['phone'],'source_url':source,'retrieved_at':snapshot,'people':people,
            'phone_evidence_text':'CSLB explicitly published PhoneNumber field' if key=='ca_cslb' else '',
            'business_type':row.get('business_type',''),
            'registration_date':row.get('issued',''),'license_status':row['license_status'],'license_expiration':row['expiration'],
            'fingerprint':hashlib.sha256(json.dumps({k:v for k,v in row.items() if k!='snapshot_at'},sort_keys=True).encode()).hexdigest()}
