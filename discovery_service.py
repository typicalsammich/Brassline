"""Private, persistent discovery queue running beside the existing SQLite services.

All external I/O happens outside transactions. Leases fence late workers. Paid calls
are reserved before dispatch and ambiguous calls are never automatically retried.
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field
from typing import Literal
import discovery_sources as sources
import owner_verification as owners

DEFAULTS = {'enabled': False, 'daily_target': 100, 'raw_daily_limit': 300,
            'monthly_budget_cents': 0, 'daily_budget_cents': 0, 'paid_enabled': False,
            'search_ceiling_cents': 0, 'industries': ['PLUMB', 'ROOF', 'ELECTRIC', 'HVAC'],
            'states': ['CO', 'NY'], 'sources': ['co_entities', 'ny_entities', 'nyc_licenses'],
            'minimum_confidence': 80, 'require_phone': True}


def stamp():
    return datetime.now(timezone.utc).isoformat()


def cfg_in(db, uid):
    row = db.execute('SELECT config FROM discovery_settings WHERE user_id=?', (uid,)).fetchone()
    return {**DEFAULTS, **(json.loads(row[0]) if row else {})}


def profile_for(db, uid, lid):
    row = db.execute('SELECT profile FROM discovery_records WHERE user_id=? AND lead_id=? ORDER BY updated_at DESC,id DESC LIMIT 1', (uid, lid)).fetchone()
    cfg=cfg_in(db,uid)
    return owners.summarize(json.loads(row[0]),cfg['minimum_confidence'],cfg['require_phone']) if row else None


class Configuration(BaseModel):
    enabled: bool = False
    daily_target: int = Field(default=100, ge=1, le=1000)
    raw_daily_limit: int = Field(default=300, ge=1, le=2000)
    monthly_budget_cents: int = Field(default=0, ge=0, le=6000)
    daily_budget_cents: int = Field(default=0, ge=0, le=6000)
    paid_enabled: bool = False
    search_ceiling_cents: int = Field(default=0, ge=0, le=1000)
    industries: list[str] = Field(default_factory=lambda: list(DEFAULTS['industries']), max_length=20)
    states: list[str] = Field(default_factory=lambda: ['CO', 'NY'], max_length=51)
    sources: list[str] = Field(default_factory=lambda: list(DEFAULTS['sources']), max_length=10)
    minimum_confidence: int = Field(default=80, ge=50, le=100)
    require_phone: bool = True


class Review(BaseModel):
    replace_people: bool = False
    name: str = Field(default='', max_length=160)
    role: Literal['', 'Owner', 'Co-owner', 'Managing member', 'Sole proprietor', 'President', 'CEO', 'Principal', 'Founder', 'Registered agent', 'Qualifier'] = ''
    source_url: str = Field(default='', max_length=2000)
    evidence: str = Field(default='', max_length=3000)
    identity_confirmed: bool = False
    business_validated: bool = False
    independent: bool = False
    activity_verified: bool = False
    activity_date: str = Field(default='', max_length=40)
    activity_source: str = Field(default='', max_length=2000)
    independence_source: str = Field(default='', max_length=2000)
    business_source: str = Field(default='', max_length=2000)
    website_url: str = Field(default='', max_length=2000)
    website_identity_confirmed: bool = False
    website_opportunity: Literal['unknown', 'no_website_verified', 'broken_verified', 'usability_verified', 'contact_verified', 'content_verified', 'dated_review', 'strong'] = 'unknown'
    website_source: str = Field(default='', max_length=2000)
    website_evidence: str = Field(default='', max_length=3000)
    phone: str = Field(default='', max_length=80)
    phone_source: str = Field(default='', max_length=2000)
    phone_evidence: str = Field(default='', max_length=1500)
    published_business_phone: bool = False
    owner_direct: bool = False


class CostReceipt(BaseModel):
    request_key: str = Field(max_length=200)
    actual_cents: int = Field(ge=0,le=100000)
    billing_reference: str = Field(min_length=3,max_length=300)


def install(app, connect, user, practice):
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS discovery_settings(user_id INTEGER PRIMARY KEY,config TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS discovery_sources(user_id INTEGER,source TEXT,cursor TEXT NOT NULL DEFAULT '',
          metadata TEXT NOT NULL DEFAULT '{}',last_success REAL,last_error TEXT NOT NULL DEFAULT '',PRIMARY KEY(user_id,source));
        CREATE TABLE IF NOT EXISTS discovery_records(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,source TEXT NOT NULL,
          source_id TEXT NOT NULL,fingerprint TEXT NOT NULL,profile TEXT NOT NULL,lead_id INTEGER,revision INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(user_id,source,source_id));
        DROP INDEX IF EXISTS discovery_lead_unique;
        CREATE INDEX IF NOT EXISTS discovery_lead_lookup ON discovery_records(user_id,lead_id);
        CREATE TABLE IF NOT EXISTS discovery_jobs(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,kind TEXT NOT NULL,
          job_key TEXT NOT NULL UNIQUE,payload TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'queued',lease TEXT,
          lease_until REAL NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,available_at REAL NOT NULL DEFAULT 0,
          message TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL,updated_at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS discovery_jobs_ready ON discovery_jobs(state,available_at,lease_until);
        CREATE TABLE IF NOT EXISTS discovery_spend(request_key TEXT PRIMARY KEY,user_id INTEGER NOT NULL,record_id INTEGER,
          provider TEXT NOT NULL,reserved_cents INTEGER NOT NULL,actual_cents INTEGER,state TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS discovery_review_log(id INTEGER PRIMARY KEY,user_id INTEGER,record_id INTEGER,review TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS discovery_billing_audit(id INTEGER PRIMARY KEY,admin_id INTEGER,request_key TEXT,actual_cents INTEGER,reference TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS discovery_search_cache(request_key TEXT PRIMARY KEY,user_id INTEGER NOT NULL,response TEXT NOT NULL,created_at TEXT NOT NULL);
        ''')
    stop = threading.Event()
    wake = threading.Event()

    def enqueue(db, uid, kind, key, payload):
        db.execute('INSERT OR IGNORE INTO discovery_jobs(user_id,kind,job_key,payload,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                   (uid, kind, key, json.dumps(payload), time.time(), time.time()))

    def schedule(uid=None, force=False):
        day = stamp()[:10]
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute('SELECT user_id FROM discovery_settings' + (' WHERE user_id=?' if uid else ''), (uid,) if uid else ()).fetchall()
            for row in rows:
                account = row[0]
                cfg = cfg_in(db, account)
                if not cfg['enabled']:
                    continue
                count = db.execute('SELECT count(*) FROM discovery_records WHERE user_id=? AND created_at LIKE ?', (account, day + '%')).fetchone()[0]
                if count >= cfg['raw_daily_limit']:
                    continue
                for key in cfg['sources']:
                    if sources.SOURCES[key].state not in cfg['states']:
                        continue
                    # Force wakes the same idempotent job; it never creates unlimited daily runs.
                    enqueue(db, account, 'source', f'{account}:{day}:{key}', {'source': key, 'day': day})
            if force and uid:
                db.execute("UPDATE discovery_jobs SET available_at=0 WHERE user_id=? AND state='retry'", (uid,))
        wake.set()

    def claim():
        now = time.time()
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("""SELECT j.* FROM discovery_jobs j JOIN discovery_settings s ON s.user_id=j.user_id
                WHERE ((j.state IN ('queued','retry') AND j.available_at<=?) OR (j.state='running' AND j.lease_until<?))
                AND json_extract(s.config,'$.enabled')=1 ORDER BY j.updated_at,j.id LIMIT 1""", (now, now)).fetchone()
            if not row:
                return None
            lease = secrets.token_urlsafe(18)
            db.execute("UPDATE discovery_jobs SET state='running',lease=?,lease_until=?,attempts=attempts+1,updated_at=? WHERE id=?", (lease, now + 180, now, row['id']))
            return {**dict(row), 'lease': lease, 'attempts': row['attempts'] + 1}

    def fenced(db, job):
        row = db.execute("SELECT 1 FROM discovery_jobs WHERE id=? AND lease=? AND state='running' AND lease_until>?", (job['id'], job['lease'], time.time())).fetchone()
        return bool(row and cfg_in(db, job['user_id'])['enabled'])

    def source_job(job, payload):
        uid, key = job['user_id'], payload['source']
        with connect() as db:
            cfg = cfg_in(db, uid)
            retained=db.execute('SELECT count(*) FROM discovery_records WHERE user_id=?',(uid,)).fetchone()[0]
            if retained>=10000:
                raise ValueError('Discovery retention ceiling reached; review/archive records before continuing')
            row = db.execute('SELECT * FROM discovery_sources WHERE user_id=? AND source=?', (uid, key)).fetchone()
            cursor = row['cursor'] if row else ''
            count = db.execute('SELECT count(*) FROM discovery_records WHERE user_id=? AND created_at LIKE ?', (uid, payload['day'] + '%')).fetchone()[0]
            qualified = db.execute("SELECT count(*) FROM discovery_records WHERE user_id=? AND json_extract(profile,'$.qualified_at') LIKE ? AND json_extract(profile,'$.verification_summary.qualified')=1", (uid, payload['day']+'%')).fetchone()[0]
            if count >= cfg['raw_daily_limit'] or qualified >= cfg['daily_target']:
                return
        meta = sources.schema(sources.SOURCES[key])
        rows, next_cursor, exhausted = sources.fetch_page(key, cursor, min(100, cfg['raw_daily_limit'] - count), cfg['industries'])
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not fenced(db, job):
                return
            for raw in rows:
                profile = sources.normalize(key, raw, stamp())
                old = db.execute('SELECT * FROM discovery_records WHERE user_id=? AND source=? AND source_id=?', (uid, key, profile['source_id'])).fetchone()
                if old and old['fingerprint'] == profile['fingerprint']:
                    continue
                # Keep reviewed evidence and historical lead association on changed records.
                if old:
                    before = json.loads(old['profile'])
                    before['latest_source_record'] = profile
                    before['source_changed_review_required'] = True
                    before['business_validated'] = False
                    before.setdefault('verification_summary',{})['qualified']=False
                    db.execute('UPDATE discovery_records SET fingerprint=?,profile=?,revision=revision+1,updated_at=? WHERE id=?', (profile['fingerprint'], json.dumps(before), stamp(), old['id']))
                    rid = old['id']
                else:
                    profile['phone_evidence'] = {'source_url': profile['source_url'], 'retrieved_at': profile['retrieved_at'],
                                                 'published_business': bool(profile['phone']), 'evidence': 'Official licensing contact_phone field' if profile['phone'] else ''}
                    profile['people'] = [{**p, 'retrieved_at': profile['retrieved_at']} for p in profile['people']]
                    rid = db.execute('INSERT INTO discovery_records(user_id,source,source_id,fingerprint,profile,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                                     (uid, key, profile['source_id'], profile['fingerprint'], json.dumps(profile), stamp(), stamp())).lastrowid
                enqueue(db, uid, 'research', f'{uid}:research:{rid}:{profile["fingerprint"]}', {'record_id': rid})
            db.execute('INSERT INTO discovery_sources(user_id,source,cursor,metadata,last_success) VALUES(?,?,?,?,?) ON CONFLICT(user_id,source) DO UPDATE SET cursor=excluded.cursor,metadata=excluded.metadata,last_success=excluded.last_success,last_error=\'\'',
                       (uid, key, '' if exhausted else next_cursor, json.dumps(meta), time.time()))
            # Page at most once per minute. Daily scans are bounded even with mostly unchanged data.
            page_number = payload.get('page', 0)
            if not exhausted and page_number + 1 < (cfg['raw_daily_limit'] + 99) // 100:
                next_key = f'{uid}:{payload["day"]}:{key}:page:{page_number + 1}'
                enqueue(db, uid, 'source', next_key, {**payload, 'page': page_number + 1})
                db.execute('UPDATE discovery_jobs SET available_at=? WHERE job_key=?', (time.time() + 60, next_key))

    def research_job(job, payload):
        uid, rid = job['user_id'], payload['record_id']
        with connect() as db:
            row = db.execute('SELECT * FROM discovery_records WHERE id=? AND user_id=?', (rid, uid)).fetchone()
            if not row:
                return
            revision = row['revision']
            profile = json.loads(row['profile'])
            cfg = cfg_in(db, uid)
        # Registry records are cheap discovery seeds, not evidence of independence or demand.
        if cfg['paid_enabled'] and not practice:
            from shared_services import config, reserve_in
            provider_cfg = config()
            provider = 'serpapi' if provider_cfg.get('serpapi_key') else 'firecrawl' if provider_cfg.get('firecrawl_key') else ''
            request_key = f'discovery:{uid}:{rid}:{row["fingerprint"]}:search'
            reserved = False
            if provider:
                with connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    cfg = cfg_in(db, uid)
                    previous = db.execute('SELECT state FROM discovery_spend WHERE request_key=?', (request_key,)).fetchone()
                    period = stamp()[:7] + '%'
                    monthly = db.execute('SELECT COALESCE(SUM(MAX(reserved_cents,COALESCE(actual_cents,0))),0) FROM discovery_spend WHERE user_id=? AND created_at LIKE ?', (uid, period)).fetchone()[0]
                    daily = db.execute('SELECT COALESCE(SUM(MAX(reserved_cents,COALESCE(actual_cents,0))),0) FROM discovery_spend WHERE user_id=? AND created_at LIKE ?', (uid, stamp()[:10] + '%')).fetchone()[0]
                    cost = cfg['search_ceiling_cents']
                    admin = db.execute('SELECT id FROM users WHERE admin=1 ORDER BY id LIMIT 1').fetchone()
                    team_cap = cfg_in(db, admin[0])['monthly_budget_cents'] if admin else 0
                    team_monthly = db.execute('SELECT COALESCE(SUM(MAX(reserved_cents,COALESCE(actual_cents,0))),0) FROM discovery_spend WHERE created_at LIKE ?', (period,)).fetchone()[0]
                    qualified = db.execute("SELECT count(*) FROM discovery_records WHERE user_id=? AND json_extract(profile,'$.qualified_at') LIKE ? AND json_extract(profile,'$.verification_summary.qualified')=1", (uid, stamp()[:10]+'%')).fetchone()[0]
                    if not previous and fenced(db, job) and cfg['paid_enabled'] and cost > 0 and monthly + cost <= cfg['monthly_budget_cents'] and daily + cost <= cfg['daily_budget_cents'] and team_monthly + cost <= team_cap and qualified < cfg['daily_target']:
                        try:
                            reserve_in(db, uid, 'research', 1, request_key)
                        except HTTPException:
                            profile['search_notice']='Shared research allowance unavailable; paid search was skipped.'
                        else:
                            db.execute("INSERT INTO discovery_spend VALUES(?,?,?,?,?,NULL,'dispatching',?)", (request_key, uid, rid, provider, cost, stamp()))
                            reserved = True
                if reserved:
                    from research_service import search_web
                    try:
                        candidates = search_web(provider_cfg, profile['name'] + ' ' + profile['city'] + ' ' + profile['state'] + ' official website owner contact', limit=5)
                        profile['search_candidates'] = candidates
                        with connect() as db:
                            db.execute('INSERT OR REPLACE INTO discovery_search_cache VALUES(?,?,?,?)',(request_key,uid,json.dumps(candidates),stamp()))
                        state = 'response_received'
                    except Exception:
                        profile['search_error'] = 'Provider request failed or billing status unknown. No automatic paid retry.'
                        state = 'unknown_cost'
                    with connect() as db:
                        db.execute('UPDATE discovery_spend SET state=? WHERE request_key=?', (state, request_key))
                elif previous:
                    with connect() as db:
                        cached=db.execute('SELECT response FROM discovery_search_cache WHERE request_key=? AND user_id=?',(request_key,uid)).fetchone()
                    if cached:
                        profile['search_candidates']=json.loads(cached[0])
                    else:
                        profile.setdefault('search_notice', 'Previous paid dispatch is not repeated automatically. Review provider usage; no cached response was saved.')
                else:
                    profile['search_notice'] = 'Paid search skipped: budget, price ceiling or provider not available.'
        # Search snippets alone never verify a company or an owner. Test at most two
        # candidate sites with the existing pinned-DNS public HTML checker.
        if not profile.get('website_url') and profile.get('phone'):
            from urllib.parse import urlsplit
            import website_checks
            tried=set()
            excluded=('google.com','facebook.com','instagram.com','yelp.com','linkedin.com','bbb.org','mapquest.com')
            for candidate in profile.get('search_candidates',[]):
                url=candidate.get('website') or candidate.get('url','')
                host=urlsplit(url).hostname or ''
                if not host or any(host==domain or host.endswith('.'+domain) for domain in excluded) or host in tried:
                    continue
                tried.add(host)
                try:
                    report=website_checks.check_website(url,business=profile)
                    if report.get('company_identity_confirmed'):
                        profile.update(website_url=url,website_identity_confirmed=True,website_check=report)
                        break
                except Exception:
                    pass
                if len(tried)>=2:
                    break
        if profile.get('website_url') and profile.get('website_identity_confirmed'):
            import website_checks
            try:
                report=profile.get('website_check') or {}
                from scoring import age_days
                age=age_days(report.get('checked_at'),datetime.now(timezone.utc))
                if age is None or age>7:
                    report = website_checks.check_website(profile['website_url'],business=profile)
                profile['website_check'] = report
                # A technical candidate never silently overwrites reviewed findings.
                profile['website_candidates'] = owners.website_result(report, True)
                for candidate in report.get('decision_makers', []):
                    from lead_identity import business_key
                    structured=candidate.get('verification','').startswith('Website-published role') and business_key(candidate.get('organization_name','')).split('|')[0]==business_key(profile['name']).split('|')[0]
                    basis='matched_company_publication' if structured and report.get('company_identity_confirmed') else 'website_candidate'
                    evidence={**candidate, 'source_url': candidate.get('source', ''), 'basis': basis,
                              'identity_confirmed':basis=='matched_company_publication','retrieved_at': report['checked_at'],
                              'evidence': ('Structured company publication: '+candidate['name']+' — '+candidate.get('role','')+' at '+candidate.get('organization_name','')) if basis=='matched_company_publication' else candidate.get('excerpt') or candidate.get('verification', '')}
                    if evidence not in profile.setdefault('people',[]):
                        profile['people'].append(evidence)
            except Exception:
                profile['website_candidates'] = {'classification': 'unknown', 'error': 'Check failed; absence was not established.'}
        profile['last_research_at'] = stamp()
        verification = owners.summarize(profile,cfg['minimum_confidence'],cfg['require_phone'])
        profile['verification_summary']={'person_status':(verification.get('best_person') or {}).get('status','unknown'),
                                         'phone_classification':verification['phone_detail']['classification'],'qualified':verification['qualified']}
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if fenced(db, job):
                db.execute('UPDATE discovery_records SET profile=?,updated_at=?,revision=revision+1 WHERE id=? AND user_id=? AND revision=?', (json.dumps(profile), stamp(), rid, uid, revision))

    def tick():
        job = claim()
        if not job:
            return False
        payload = json.loads(job['payload'])
        try:
            (source_job if job['kind'] == 'source' else research_job)(job, payload)
            state, message, delay = 'complete', 'Checkpoint saved; unresolved facts remain in Research.', 0
        except Exception as error:
            # Never put provider URLs/keys, response bodies or private source text into errors.
            state = 'failed' if job['attempts'] >= 3 else 'retry'
            message = 'Source or processing failed ('+type(error).__name__+'). Review source health and configuration.'
            delay = min(3600, 60 * 2 ** job['attempts'])
            if job['kind'] == 'source':
                with connect() as db:
                    db.execute('INSERT INTO discovery_sources(user_id,source,last_error) VALUES(?,?,?) ON CONFLICT(user_id,source) DO UPDATE SET last_error=excluded.last_error', (job['user_id'], payload['source'], message))
        with connect() as db:
            if not fenced(db,job):
                state,message,delay='queued','Paused or lease expired; checkpoint will resume.',0
            db.execute('UPDATE discovery_jobs SET state=?,message=?,available_at=?,lease_until=0,updated_at=? WHERE id=? AND lease=?', (state, message, time.time() + delay, time.time(), job['id'], job['lease']))
        return True

    def loop():
        while not stop.is_set():
            try:
                if not practice:
                    schedule()
                    if tick():
                        continue
            except Exception:
                pass  # Durable jobs remain retryable; no credentials or stack traces in shared UI.
            wake.wait(30)
            wake.clear()

    @app.get('/api/discovery')
    def dashboard(request: Request):
        account = user(request)
        uid = account['id']
        with connect() as db:
            cfg = cfg_in(db, uid)
            records = db.execute('SELECT * FROM discovery_records WHERE user_id=? ORDER BY updated_at DESC,id DESC LIMIT 100', (uid,)).fetchall()
            profiles = [{**owners.summarize(json.loads(r['profile']), cfg['minimum_confidence'], cfg['require_phone']), 'id': r['id'], 'lead_id': r['lead_id']} for r in records]
            jobs = [dict(r) for r in db.execute('SELECT id,kind,state,attempts,message,updated_at FROM discovery_jobs WHERE user_id=? ORDER BY id DESC LIMIT 20', (uid,))]
            health = [dict(r) for r in db.execute('SELECT source,last_success,last_error FROM discovery_sources WHERE user_id=?', (uid,))]
            spend = [dict(r) for r in db.execute("SELECT provider,count(*) AS requests,SUM(reserved_cents) AS reserved_cents,SUM(actual_cents) AS actual_cents,SUM(actual_cents IS NULL) AS unreconciled FROM discovery_spend WHERE user_id=? AND created_at LIKE ? GROUP BY provider", (uid, stamp()[:7] + '%'))]
            total = db.execute('SELECT count(*) FROM discovery_records WHERE user_id=?', (uid,)).fetchone()[0]
            yields=[dict(r) for r in db.execute("""SELECT source,count(*) AS discovered,
                SUM(json_extract(profile,'$.verification_summary.person_status') IN ('verified_owner','verified_decision_maker')) AS owner_verified,
                SUM(json_extract(profile,'$.verification_summary.qualified')=1) AS qualified
                FROM discovery_records WHERE user_id=? GROUP BY source""",(uid,))]
            monthly=db.execute("""SELECT count(*) AS discovered,
                SUM(json_extract(profile,'$.website.classification') LIKE '%_verified') AS website_qualified,
                SUM(json_extract(profile,'$.verification_summary.person_status') IN ('verified_owner','verified_decision_maker')) AS owner_verified,
                SUM(json_extract(profile,'$.verification_summary.qualified')=1) AS owner_phone_qualified
                FROM discovery_records WHERE user_id=? AND created_at LIKE ?""",(uid,stamp()[:7]+'%')).fetchone()
        return {'config': cfg, 'admin': bool(account.get('admin')), 'sources': [s.public() for s in sources.SOURCES.values()],
                'pending_sources': sources.PENDING, 'records': profiles, 'total': total, 'jobs': jobs, 'source_health': health,
                'spend': spend, 'source_yield':yields,'monthly_cohort':dict(monthly),'practice': practice, 'scheduler': 'Same web process with durable leases; external authenticated wake recommended.',
                'limits': '100 is a target, not a promised yield. Published phone evidence does not test carrier connectivity. Paid costs are reserved ceilings until reconciled.'}

    def save_config(data, request, target=None):
        account = user(request)
        uid = target if target is not None else account['id']
        if target is not None and not account.get('admin'):
            raise HTTPException(403, 'Administrator access required')
        if any(key not in sources.SOURCES for key in data.sources):
            raise HTTPException(400, 'Choose a supported source')
        if any(len(word) < 2 or len(word) > 80 for word in data.industries) or not data.industries:
            raise HTTPException(400, 'Choose 1–20 specific industry search terms')
        if any(len(state) != 2 or not state.isalpha() for state in data.states):
            raise HTTPException(400, 'Use two-letter US state codes')
        if data.paid_enabled and (practice or not data.monthly_budget_cents or not data.daily_budget_cents or not data.search_ceiling_cents):
            raise HTTPException(400, 'Paid search requires explicit daily/monthly ceilings and a per-request ceiling; disabled in practice')
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone():
                raise HTTPException(404,'Account not found')
            before = cfg_in(db, uid)
            values = data.model_dump()
            finance = ('paid_enabled', 'monthly_budget_cents', 'daily_budget_cents', 'search_ceiling_cents')
            if not account.get('admin') and any(values[k] != before[k] for k in finance):
                raise HTTPException(403, 'Only an administrator may authorize discovery spending')
            db.execute('INSERT INTO discovery_settings VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET config=excluded.config', (uid, json.dumps(values)))
            if before['industries'] != values['industries'] or before['sources'] != values['sources']:
                db.execute("UPDATE discovery_sources SET cursor='' WHERE user_id=?", (uid,))
        schedule(uid)
        return {'ok': True}

    @app.post('/api/discovery/settings')
    def save(data: Configuration, request: Request):
        return save_config(data,request)

    @app.get('/api/discovery/accounts/{account_id}/settings')
    def account_config(account_id: int,request: Request):
        if not user(request).get('admin'):
            raise HTTPException(403,'Administrator access required')
        with connect() as db:
            if not db.execute('SELECT 1 FROM users WHERE id=?',(account_id,)).fetchone():
                raise HTTPException(404,'Account not found')
            return {'config':cfg_in(db,account_id)}

    @app.post('/api/discovery/accounts/{account_id}/settings')
    def save_account_config(account_id: int,data: Configuration,request: Request):
        return save_config(data,request,account_id)

    @app.post('/api/discovery/billing/reconcile')
    def reconcile(data: CostReceipt,request: Request):
        admin=user(request)
        if not admin.get('admin'):
            raise HTTPException(403,'Administrator access required')
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM discovery_spend WHERE request_key=?',(data.request_key,)).fetchone()
            if not row:
                raise HTTPException(404,'Usage reservation not found')
            db.execute('UPDATE discovery_spend SET actual_cents=? WHERE request_key=?',(data.actual_cents,data.request_key))
            db.execute('INSERT INTO discovery_billing_audit(admin_id,request_key,actual_cents,reference,created_at) VALUES(?,?,?,?,?)',(admin['id'],data.request_key,data.actual_cents,data.billing_reference,stamp()))
            if data.actual_cents>row['reserved_cents']:
                cfg=cfg_in(db,row['user_id']);cfg['paid_enabled']=False
                db.execute('UPDATE discovery_settings SET config=? WHERE user_id=?',(json.dumps(cfg),row['user_id']))
        return {'ok':True,'paid_paused':data.actual_cents>row['reserved_cents']}

    @app.get('/api/discovery/billing')
    def billing(request: Request):
        if not user(request).get('admin'):
            raise HTTPException(403,'Administrator access required')
        with connect() as db:
            return {'requests':[dict(r) for r in db.execute('SELECT request_key,user_id,provider,reserved_cents,actual_cents,state,created_at FROM discovery_spend ORDER BY created_at DESC LIMIT 100')]}

    @app.post('/api/discovery/start')
    def start(request: Request):
        uid = user(request)['id']
        if practice:
            raise HTTPException(400, 'Government and paid discovery are disabled in practice')
        with connect() as db:
            if not cfg_in(db, uid)['enabled']:
                raise HTTPException(409, 'Save settings with discovery enabled first')
        schedule(uid, True)
        return {'ok': True, 'message': 'Daily jobs queued. No calls will be placed.'}

    @app.post('/api/discovery/pause')
    def pause(request: Request):
        uid = user(request)['id']
        with connect() as db:
            cfg = cfg_in(db, uid)
            cfg['enabled'] = False
            db.execute('INSERT INTO discovery_settings VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET config=excluded.config', (uid, json.dumps(cfg)))
        return {'ok': True}

    @app.post('/api/discovery/wake')
    def trigger(request: Request):
        expected = os.getenv('DISCOVERY_TRIGGER_TOKEN', '')
        supplied = request.headers.get('authorization', '')
        if not expected or len(expected) < 32 or not hmac.compare_digest(supplied, 'Bearer ' + expected):
            raise HTTPException(403, 'Invalid scheduler credentials')
        schedule()
        return {'ok': True}

    @app.post('/api/discovery/records/{rid}/review')
    def review(rid: int, data: Review, request: Request):
        uid = user(request)['id']
        from scoring import public_url, age_days
        if data.name and (not public_url(data.source_url) or not data.evidence.strip() or not data.identity_confirmed):
            raise HTTPException(400, 'Person review requires explicit public evidence and confirmed business identity')
        for flag, source in ((data.business_validated, data.business_source), (data.independent, data.independence_source), (data.activity_verified, data.activity_source)):
            if flag and not public_url(source):
                raise HTTPException(400, 'Business, activity and independence claims each require a public source')
        if data.activity_verified:
            age = age_days(data.activity_date, datetime.now(timezone.utc))
            if age is None or age > 180:
                raise HTTPException(400, 'Activity evidence must be within the last 180 days')
        if data.website_opportunity not in ('unknown', 'dated_review') and (not public_url(data.website_source) or not data.website_evidence.strip()):
            raise HTTPException(400, 'Website conclusions require specific reviewed evidence')
        if data.website_url:
            from website_checks import normalize_url, public_addresses
            normalized = normalize_url(data.website_url)
            from urllib.parse import urlsplit
            public_addresses(urlsplit(normalized).hostname, 443 if normalized.startswith('https:') else 80)
        if data.published_business_phone and (not owners.us_phone(data.phone) or not public_url(data.phone_source) or not data.phone_evidence.strip()):
            raise HTTPException(400, 'A usable public business phone and its publication evidence are required')
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM discovery_records WHERE id=? AND user_id=?', (rid, uid)).fetchone()
            if not row:
                raise HTTPException(404, 'Discovery record not found')
            p = json.loads(row['profile'])
            was_qualified=owners.summarize(p)['qualified']
            if data.replace_people:
                p['people']=[]
            if data.name:
                p['people']=[person for person in p.get('people',[]) if person.get('name','').casefold()!=data.name.casefold()]
                p['people'].append({'name': data.name, 'role': data.role, 'source_url': data.source_url, 'evidence': data.evidence,
                                                  'identity_confirmed': True, 'basis': 'reviewed_public_evidence', 'retrieved_at': stamp()})
            p.update(business_validated=data.business_validated, business_source=data.business_source,
                     independent=data.independent, independence_source=data.independence_source,
                     activity_verified=data.activity_verified, activity_date=data.activity_date, activity_source=data.activity_source,
                     website_url=data.website_url, website_identity_confirmed=data.website_identity_confirmed,
                     website={'classification': data.website_opportunity, 'source_url': data.website_source, 'evidence': data.website_evidence, 'checked_at': stamp()},
                     phone=data.phone or p.get('phone', ''), source_changed_review_required=False)
            if data.phone:
                p['phone_evidence'] = {'published_business': data.published_business_phone, 'source_url': data.phone_source,
                                       'evidence': data.phone_evidence, 'retrieved_at': stamp(), 'person_name': data.name,
                                       'explicit_business_direct': data.owner_direct}
            cfg = cfg_in(db, uid)
            result = owners.summarize(p, cfg['minimum_confidence'], cfg['require_phone'])
            if result['qualified']:
                if not was_qualified or not p.get('qualified_at'):
                    p['qualified_at']=stamp()
            p['verification_summary']={'person_status':(result.get('best_person') or {}).get('status','unknown'),
                                       'phone_classification':result['phone_detail']['classification'],'qualified':result['qualified']}
            lid = row['lead_id']
            if result['qualified'] and not lid:
                # Strong source identity only. Never merge solely on shared phone or agent.
                old = db.execute("SELECT lead_id FROM discovery_records WHERE user_id=? AND source=? AND json_extract(profile,'$.registration_id')=? AND lead_id IS NOT NULL ORDER BY id LIMIT 1", (uid, row['source'],p['registration_id'])).fetchone()
                if not old:
                    from lead_identity import business_key,phones
                    locality=', '.join(filter(None,(p.get('city'),p.get('state'))))
                    matches=[r for r in db.execute('SELECT id,name,city,phone FROM leads WHERE user_id=?',(uid,)) if business_key(r['name'],r['city'])==business_key(p['name'],locality) and phones(r['phone']) & phones(p['phone'])]
                    if len(matches)==1:
                        # An existing lead can retain its own facts/history; attach only a single source profile.
                        candidate=matches[0]
                        if not db.execute('SELECT 1 FROM discovery_records WHERE user_id=? AND lead_id=?',(uid,candidate['id'])).fetchone():
                            old=(candidate['id'],)
                if old:
                    lid = old[0]
                else:
                    batch_name = 'Owner-verified discovery · ' + stamp()[:10]
                    batch = db.execute('SELECT id FROM batches WHERE user_id=? AND name=? AND deleted_at IS NULL', (uid, batch_name)).fetchone()
                    bid = batch[0] if batch else db.execute('INSERT INTO batches(user_id,name) VALUES(?,?)', (uid, batch_name)).lastrowid
                    best = result['best_person']
                    need = {'no_website_verified': 'no_website', 'broken_verified': 'broken', 'usability_verified': 'usability', 'contact_verified': 'usability', 'content_verified': 'content'}[data.website_opportunity]
                    facts = {'website_need': need, 'website_source': data.website_source, 'website_note': data.website_evidence,
                             'website_checked_at': stamp(), 'activity': [{'date': data.activity_date, 'source': data.activity_source, 'kind': 'Reviewed commercial activity'}],
                             'independent': True, 'independence_source': data.independence_source, 'category_approved': True,
                             'owner_name': best['name'], 'owner_role': best['role'], 'owner_source': best['source_url'], 'owner_access': 'authority'}
                    lid = db.execute('INSERT INTO leads(user_id,batch_id,name,phone,category,city,website_url,source_url,facts,review_facts) VALUES(?,?,?,?,?,?,?,?,?,?)',
                                     (uid, bid, p['name'], result['phone_detail']['number'], p.get('category', ''), ', '.join(filter(None, (p.get('city'), p.get('state')))), data.website_url, p['source_url'], json.dumps(facts), '{}')).lastrowid
                    db.execute('INSERT OR IGNORE INTO lead_batches(lead_id,batch_id) VALUES(?,?)', (lid, bid))
            db.execute('UPDATE discovery_records SET profile=?,lead_id=?,revision=revision+1,updated_at=? WHERE id=? AND user_id=?', (json.dumps(p), lid, stamp(), rid, uid))
            db.execute('INSERT INTO discovery_review_log(user_id,record_id,review,created_at) VALUES(?,?,?,?)', (uid, rid, json.dumps(data.model_dump()), stamp()))
            if data.website_url and data.website_identity_confirmed:
                enqueue(db, uid, 'research', f'{uid}:review:{rid}:{secrets.token_hex(8)}', {'record_id': rid})
        wake.set()
        return {'ok': True, 'lead_id': lid, 'qualified': result['qualified']}

    app.state.discovery_tick = tick
    app.state.discovery_schedule = schedule
    previous = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with previous(application):
            thread = threading.Thread(target=loop, name='durable-discovery', daemon=True)
            thread.start()
            try:
                yield
            finally:
                stop.set()
                wake.set()
    app.router.lifespan_context = lifespan
