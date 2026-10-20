"""Bounded, public government feeds. Registry roles are never inferred ownership."""
import hashlib
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    key: str
    name: str
    host: str
    dataset: str
    id_field: str
    name_field: str
    state: str
    cadence: str
    required: tuple
    limitation: str

    @property
    def url(self):
        return f'https://{self.host}/resource/{self.dataset}.json'

    def public(self):
        return {**self.__dict__, 'url': self.url,
                'metadata_url': f'https://{self.host}/api/views/{self.dataset}.json'}


SOURCES = {
    s.key: s for s in (
        Source('ny_entities', 'NY Department of State · active entities', 'data.ny.gov', 'n9v6-gdp6',
               'dos_id', 'current_entity_name', 'NY', 'Monthly', ('dos_id', 'current_entity_name'),
               'CEO is a distinct role, not ownership. Service-of-process addresses are not business locations. No phones.'),
        Source('co_entities', 'Colorado Department of State · entities', 'data.colorado.gov', '4ykn-tg5h',
               'entityid', 'entityname', 'CO', 'Daily according to update metadata; monitor freshness', ('entityid', 'entityname', 'entitystatus'),
               'Agent fields identify registered agents, not owners. No phone field. Registration does not establish current trade activity.'),
        Source('nyc_licenses', 'NYC DCWP · issued licenses', 'data.cityofnewyork.us', 'w7w3-xahh',
               'license_nbr', 'business_name', 'NY', 'Weekly', ('license_nbr', 'business_name', 'license_status'),
               'Contact phone is a published business contact, not an owner-direct number or proof the line is connected.'),
    )
}

# Researched but intentionally not represented as functioning connectors.
PENDING = [
    {'name': 'California CSLB', 'url': 'https://www.cslb.ca.gov/onlineservices/dataportal/ContractorList',
     'status': 'Manual master/personnel CSV downloads; automated form/download schema not validated.'},
    {'name': 'Florida Sunbiz', 'url': 'https://dos.fl.gov/sunbiz/other-services/data-downloads/',
     'status': 'Daily and quarterly bulk SFTP files; fixed-width ingestion not implemented.'},
    {'name': 'Florida DBPR', 'url': 'https://www2.myfloridalicense.com/open-government/',
     'status': 'Public extracts available; per-board layouts/download endpoints not yet validated.'},
]


class SourceError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def get_json(url):
    """Only fixed government hosts; no arbitrary URLs, redirects, credentials or paid calls."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in {s.host for s in SOURCES.values()}:
        raise SourceError('Unsupported government host')
    req = urllib.request.Request(url, headers={'User-Agent': 'Brassline public-data pilot/1.0', 'Accept': 'application/json'})
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=25) as response:
        body = response.read(4_000_001)
        if len(body) > 4_000_000:
            raise SourceError('Source response exceeds the bounded page size')
        return json.loads(body)


def schema(source):
    data = get_json(f'https://{source.host}/api/views/{source.dataset}.json')
    fields = {c.get('fieldName') for c in data.get('columns', [])}
    if not set(source.required) <= fields:
        raise SourceError('Official dataset schema changed; connector stopped for review')
    return {'rows_updated_at': data.get('rowsUpdatedAt'), 'metadata_url': source.public()['metadata_url'],
            'license': data.get('licenseId'), 'checked_fields': sorted(fields - {None})}


def fetch_page(key, cursor='', limit=100, industries=()):
    source = SOURCES[key]
    # SoQL system IDs are opaque strings. Keyset paging avoids offset drift on inserts.
    quote = lambda v: str(v).replace("'", "''")
    where = []
    if cursor:
        where.append(":id > '" + quote(cursor) + "'")
    if key == 'co_entities':
        where.append("entitystatus = 'Good Standing'")
    elif key == 'nyc_licenses':
        where.append("license_status = 'Active'")
    if industries:
        field = 'business_category' if key == 'nyc_licenses' else source.name_field
        clauses = [f"upper({field}) like '%{quote(word.upper()).replace('%', '').replace('_', '')}%'" for word in industries]
        where.append('(' + ' OR '.join(clauses) + ')')
    params = {'$select': '*, :id AS source_row_id', '$order': ':id', '$limit': min(100, max(1, limit))}
    if where:
        params['$where'] = ' AND '.join(where)
    rows = get_json(source.url + '?' + urllib.parse.urlencode(params))
    if not isinstance(rows, list) or any(not set(source.required) <= set(row) or not row.get('source_row_id') for row in rows):
        raise SourceError('Official response schema changed; cursor was not advanced')
    return rows, str(rows[-1]['source_row_id']) if rows else cursor, len(rows) < params['$limit']


def normalize(key, row, retrieved):
    s = SOURCES[key]
    # Deliberately exclude residential agent / officer addresses from normalized leads.
    city = row.get('principalcity', '') if key == 'co_entities' else row.get('address_city', '') if key == 'nyc_licenses' else row.get('location_city', '')
    state = row.get('principalstate', s.state) if key == 'co_entities' else row.get('address_state', s.state) if key == 'nyc_licenses' else row.get('location_state', s.state)
    url = s.url + '?' + urllib.parse.urlencode({'$where': s.id_field + "='" + str(row[s.id_field]).replace("'", "''") + "'"})
    people = []
    if key == 'ny_entities' and row.get('chairman_name'):
        people.append({'name': row['chairman_name'], 'role': 'CEO', 'source_url': url,
                       'evidence': 'NY DOS chairman_name: address name line for CEO; current authority requires review.', 'basis': 'registry'})
    if key == 'co_entities':
        agent = ' '.join(row.get(k, '') for k in ('agentfirstname', 'agentmiddlename', 'agentlastname')).strip()
        if agent:
            people.append({'name': agent, 'role': 'Registered agent', 'source_url': url,
                           'evidence': 'Registered agent fields; not ownership evidence.', 'basis': 'registry'})
    return {'source': key, 'source_id': str(row[s.id_field]), 'registration_id': str(row.get('business_unique_id') or row[s.id_field]),
            'jurisdiction': s.state, 'name': row[s.name_field], 'city': city, 'state': state,
            'category': row.get('business_category', ''), 'phone': row.get('contact_phone', ''),
            'source_url': url, 'retrieved_at': retrieved, 'people': people,
            'registration_date': row.get('entityformdate') or row.get('initial_dos_filing_date') or row.get('license_creation_date', ''),
            'fingerprint': hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()}
