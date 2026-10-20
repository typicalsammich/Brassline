"""No network: native government CSV schemas, cache atomicity and provenance."""
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import zipfile
from xml.sax.saxutils import escape
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import discovery_bulk_sources as bulk
import discovery_sources as sources
from owner_verification import person


def csv_bytes(rows):
    out=io.StringIO(newline='');writer=csv.writer(out);writer.writerows(rows)
    return out.getvalue().encode('utf-8')


class Response(io.BytesIO):
    def __init__(self,data,kind='text/csv'):
        super().__init__(data);self.headers={'Content-Type':kind}


def ca(identity,name,kind='Sole Owner',status='CLEAR',expiry='12/31/2099'):
    return {'LicenseNo':identity,'BusinessName':name,'FullBusinessName':'Alex Example','BusinessPhone':'(904) 694-5640',
            'BusinessType':kind,'City':'Example City','State':'CA','IssueDate':'01/01/2025','ExpirationDate':expiry,
            'PrimaryStatus':status,'Classifications(s)':'C36 | C39','LastUpdate':'10/20/2026'}


def florida(license='CFC1234567',primary='C',secondary='A',expiration='12/31/2099',business='Fixture Plumbing LLC'):
    return ['06','CFC','EXAMPLE, ALEX',business,'','','','','Example City','FL','33000','01','1234567',primary,secondary,'01/01/2025','10/20/2026',expiration,'','',license,'']


def main():
    (ROOT/'data').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='bulk-feed-tests-',dir=ROOT/'data') as directory:
        bulk.configure(directory)
        header=['LicenseNumber','LastUpdated','BusinessType','BusinessName','City','State','PhoneNumber','IssueDate','ExpirationDate','Classification','Status']
        records=[ca('100','Fixture Plumbing'),ca('101','Fixture Roof Inc',kind='Corporation'),ca('102','Suspended Roof',status='SUSPENDED'),ca('103','Expired Plumbing',expiry='01/01/2020'),ca('104','Unicode Peña Plumbing')]
        rows=[header]+[[r['LicenseNo'],r['LastUpdate'],r['BusinessType'],r['BusinessName'],r['City'],r['State'],r['BusinessPhone'],r['IssueDate'],r['ExpirationDate'],r['Classifications(s)'],r['PrimaryStatus']] for r in records]
        xml='<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        for i,row in enumerate(rows,1):
            xml+='<row r="'+str(i)+'">'+''.join('<c r="'+chr(65+j)+str(i)+'" t="inlineStr"><is><t>'+escape(str(value))+'</t></is></c>' for j,value in enumerate(row))+'</row>'
        xml+='</sheetData></worksheet>'
        xml=xml.replace('</row>','<c r="Z99" t="s"/></row>',1) # Publisher uses empty shared-string cells.
        archive=io.BytesIO()
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:z.writestr('xl/worksheets/sheet1.xml',xml)
        data=archive.getvalue()
        with patch('discovery_bulk_sources.open_feed',side_effect=lambda *args:Response(data,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')) as fetch:
            meta=sources.schema(sources.SOURCES['ca_cslb']);assert 'LicenseNumber' in meta['checked_fields']
            rows,cursor,end=sources.fetch_page('ca_cslb',limit=1,industries=['Plumbers']);assert len(rows)==1 and not end
            row=rows[0];assert row['id']=='100'
            p=sources.normalize('ca_cslb',row,bulk.now());assert p['phone'] and p['people']==[] # Sole-owner business type does not supply a person's name.
            next_rows,next_cursor,end=sources.fetch_page('ca_cslb',cursor,10,['PLUMB']);assert [r['id'] for r in next_rows]==['101','104'] and end
            corporate=sources.normalize('ca_cslb',next_rows[0],bulk.now());assert corporate['people']==[]
            assert 'Peña' in next_rows[1]['name']
            sources.schema(sources.SOURCES['ca_cslb']);assert fetch.call_count==1
            assert 'MailingAddress' not in bulk.cache_path('ca_cslb').read_bytes().decode('latin1')
        with patch('discovery_bulk_sources.open_feed',side_effect=lambda *args:Response(data,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')):
            first,rotation,_=sources.fetch_page('ca_cslb',limit=1,industries=['PLUMB','ROOF'])
            assert json.loads(rotation)['segment']=='C-39'
            second,rotation,_=sources.fetch_page('ca_cslb',rotation,1,['PLUMB','ROOF'])
            assert json.loads(rotation)['segment']=='C-36'
            third,rotation,_=sources.fetch_page('ca_cslb',rotation,1,['PLUMB','ROOF'])
            assert third[0]['id']!=first[0]['id'] # Per-class position survives rotation.
        old=bulk.info(bulk.cache_path('ca_cslb'))
        with patch('discovery_bulk_sources.TTL',0),patch('discovery_bulk_sources.open_feed',return_value=Response(b'changed,headers\nwrong,row\n','text/csv')):
            try:bulk.ensure('ca_cslb');raise AssertionError('Schema change accepted')
            except bulk.BulkError:pass
        assert bulk.info(bulk.cache_path('ca_cslb'))['generation']==old['generation']
        assert not bulk.cache_path('ca_cslb').with_suffix('.building.sqlite3').exists()
        try:bulk.SafeXML(io.BytesIO(b'<!DOCTYPE x [<!ENTITY x "unsafe">]>')).read();raise AssertionError('XML entities accepted')
        except bulk.BulkError:pass
        bad_archive=io.BytesIO()
        with zipfile.ZipFile(bad_archive,'w') as z:z.writestr('xl/worksheets/sheet1.xml',xml.replace('</row>','<c r="Y99"><f>1+1</f><v>2</v></c></row>',1))
        bad_archive.seek(0)
        try:list(bulk.parse_ca_workbook(bad_archive));raise AssertionError('Evidence formula accepted')
        except bulk.BulkError:pass
        data=csv_bytes([florida(),florida('CFC2222222',secondary='I'),florida('CFC3333333',expiration='01/01/2020')])
        with patch('discovery_bulk_sources.open_feed',return_value=Response(data)):
            meta=sources.schema(sources.SOURCES['fl_dbpr_construction']);assert meta['records']==1 and not meta['phone_available']
            rows,cursor,end=sources.fetch_page('fl_dbpr_construction',limit=5,industries=['Plumbers']);assert len(rows)==1 and end
            p=sources.normalize('fl_dbpr_construction',rows[0],bulk.now());assert not p['phone'];assert person(p['people'][0])['status']=='unknown'
            assert p['people'][0]['role']=='License holder / qualifier'
            assert p['license_id']=='CFC1234567' and p['jurisdiction']=='FL'
        # A cursor from an earlier snapshot restarts safely rather than skipping changed rows.
        wrong=json.dumps({'generation':'old-snapshot','after':'zzzz'})
        assert len(sources.fetch_page('fl_dbpr_construction',wrong,10,['PLUMB'])[0])==1
        for bad in [florida()+['unexpected'],['bad','schema']]:
            try:bulk.fl_row(bad);raise AssertionError('Unknown layout accepted')
            except bulk.BulkError:pass
    print('PASS CA/FL schemas, current/active/expiry filters, owner vs qualifier roles, public phones only, Unicode, atomic cache replacement/failure, cached reuse, paging and snapshot reset. No network or paid requests.')


if __name__=='__main__':main()
