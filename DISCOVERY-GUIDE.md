# Owner-aware discovery pilot

## What is implemented

This extends the existing Scraper, Research, Leads, Shared services and Dialer. It does not replace the Google Maps worker, import archive, existing 100-point score, calling rules, CRM, recordings, or coaching. Automatic Discovery is **off by default**, and paid search has separate, initially zero budgets. Discovery never starts a call.

The initial automated government coverage is **New York State, New York City licensing, and Colorado registries**. This is an extensible multi-state pilot, **not complete nationwide government coverage or a demonstrated 100 qualified leads/day service**. The existing local Google Maps workflow still covers the US.

Public-data discovery runs on the server without Chrome. A durable SQLite queue saves source cursors, source IDs, checksums, per-record profiles, revisions, job attempts and fenced leases. Free jobs retry up to three times with backoff. Expired leases resume after restart. Pausing prevents further claims and fences in-flight writes. Source changes retain previously reviewed evidence and require revalidation. Independent records sharing only a phone are not merged; existing phone-based contact suppression remains enforced separately.

Paid research uses the existing SerpAPI or Firecrawl search adapter and shared research allowances. A request is durably reserved **before** dispatch, with account/day/month and team/month monetary ceilings. Completed responses are privately cached. Ambiguous dispatches are not automatically repeated after failure/restart. Unused conservative reservations are not automatically refunded. Reconciliation can record provider-confirmed actual charges; actual cost is otherwise **unknown**, not zero. No new AI model, contact-data vendor, phone intelligence subscription, private-person lookup or paid rendering is activated.

Public website requests reuse pinned-DNS/SSRF protection, bounded responses and page limits. At most two search candidate hosts are tried when an existing business phone is available. A site is automatically matched only when its published telephone and exact normalized multiword company name match. A structured Person role can verify authority only with an explicit matching `worksFor`/`memberOf` company. Generic mentions, regex matches, agent fields, historic founders and AI output remain candidates. Registry-only CEOs require current-authority review. Website failures and missing viewport tags remain review findings, not automatic absence or confirmed sales opportunities.

**Human evidence review is still required for unresolved identity, independent operation, recent trade activity and material website opportunities.** New prospects enter Leads only when those requirements and the configured owner-contact threshold are satisfied. Daily targets stop further acquisition/paid research after the target is met; raw-record ceilings also bound unsuccessful discovery. Targets are UTC daily counts, not an SLA. Unknown evidence earns no verification credit.

## Enable and use

1. Deploy the source and retain `DATABASE_PATH=/var/data/dashboard.db` on the persistent disk. Back up first using existing Launch checks/operations. New tables are created idempotently on application startup; there is no destructive data conversion or required SQL command. Restart once after deployment. Existing backup snapshots include these tables automatically.
2. Open **Scraper → Automatic Discovery → Discovery settings**. Choose supported sources, source jurisdictions, specific industry terms, daily target and raw ceiling. A source jurisdiction is where the registry is maintained; a registered company's operating address can be outside that state. Name searches are imperfect industry proxies; inspect trade suitability during review. Save with **Enable server-side discovery** checked, then **Start / resume daily jobs**. Refresh for job/source health.
3. Open **Research → Owner verification queue**. Inspect official source links, all named roles, candidate search results, phone classification and website evidence. Use **Inspect evidence & review** to save the actual role, public citations, specific findings and freshness. Registered agents and qualifiers cannot be promoted to owners by selecting those roles. Founder by itself is probable ownership only. Use “Replace previous people evidence” when correcting a wrong match.
4. Qualified reviewed records appear in a dated **Owner-verified discovery** batch in Leads. Existing source identities and strong name/locality/phone matches prevent repeat insertion. Original import rows and call events are not deleted. Owner/phone filters are on Leads; supporting evidence is in the business dossier. Select a batch and **Send to calling list** through the existing workflow. DNC, assignment, outcomes, waits and calling controls continue to apply.
5. The active call panel shows owner/contact classification, website classification and a cautious opening. It makes no unsupported statement that an uncertain website is absent. Qualification is marketing suitability, not legal authorization or proof a phone is connected. Apply your existing outreach, consent, suppression and calling-time procedures.

For free operation leave all discovery spending fields at zero. In **Shared services → Discovery spending controls**, the administrator can configure an account's budgets and daily settings. Member evidence remains private. The first administrator's monthly discovery ceiling is also the **whole-team ceiling**. Each funded member additionally needs the existing shared research allowance, and research must be enabled in the existing shared service configuration. Members cannot grant themselves paid access.

Before authorizing paid search, enter a conservative **maximum per-request price in cents** based on your actual provider contract (including any overage or minimum unit charges). A wrong price assumption cannot cap the provider's invoice. The app stops *dispatches* against reservations, not the provider's independent billing system. Start with a small daily ceiling. A confirmed charge above its reserved ceiling automatically pauses that account's paid discovery. No API keys are stored in discovery settings or returned in its API; existing encrypted integration storage is reused.

## Scheduling on Render

Keep the existing **single web worker** and persistent SQLite disk. A background loop in that process checks enabled accounts every 30 seconds, creates idempotent daily jobs, and resumes durable work after restart. It cannot process work while the host is stopped/asleep.

For a reliable external wake, set a random secret of at least 32 characters as `DISCOVERY_TRIGGER_TOKEN` on the web service. It is optional; without it the external wake endpoint is disabled. Store the same secret on an external scheduler. Set `DISCOVERY_APP_URL=https://your-canonical-app-domain` there and run:

```
python discovery_wake.py
```

Schedule that HTTP-only command every 15 minutes (or at least once daily if your web service stays running). It sends JSON and a Bearer token to `POST /api/discovery/wake`; it never reads SQLite. Configure scheduler failure alerts. The script refuses redirects and does not log tokens. No service was provisioned and no new recurring hosting charge was authorized by this code change.

**Do not put a separate worker/cron service against a second copy of dashboard.db. Render web disks are not a shared database across services.** The external scheduler wakes the existing web process, which owns all job execution. More processes/instances require migration to a shared transactional database/queue first. SQLite writes stay short; provider I/O is outside write transactions. Jobs use 180-second leases, so recovery after an abrupt process loss may wait three minutes.

No new runtime dependencies are required. Existing `ONLINE`, `PUBLIC_BASE_URL`, encrypted integration key, database and Twilio settings are unchanged. Practice mode blocks government discovery and paid enrichment.

## Verified source catalog — checked October 20, 2026

Actual official metadata and read-only sample requests, including a second keyset page, succeeded for each implemented connector. This verifies the feed/schema/pagination, **not individual ownership or telephone connectivity**.

| Connector | Official endpoint / schema | Cadence and limitations |
|---|---|---|
| NY Department of State active entities | `https://data.ny.gov/resource/n9v6-gdp6.json`; metadata `https://data.ny.gov/api/views/n9v6-gdp6.json`. Uses `dos_id`, `current_entity_name`, `chairman_name`, `location_city/state`, filing date. | Official metadata: monthly active-entity snapshot. `chairman_name` is the CEO address-name line, not ownership. No phone column. Service-of-process address is explicitly not necessarily the business location. |
| Colorado Department of State entities | `https://data.colorado.gov/resource/4ykn-tg5h.json`; metadata `https://data.colorado.gov/api/views/4ykn-tg5h.json`. Uses `entityid`, `entityname`, `entitystatus`, principal locality, `entityformdate`, agent fields. | Metadata states daily automated updates but contains a conflicting legacy quality field; health reports fetch success, not a guarantee of data freshness. Public-domain license in metadata. Actual agent columns identify registered agents; description prose mentioning owners is insufficient. No phone column. Good Standing filter. |
| NYC DCWP issued licenses | `https://data.cityofnewyork.us/resource/w7w3-xahh.json`; metadata `https://data.cityofnewyork.us/api/views/w7w3-xahh.json`. Uses `license_nbr`, `business_unique_id`, business/trade names, category/status, `contact_phone`, business locality and dates. | Official metadata: weekly/as needed. Active-license filter. `contact_phone` is a business-contact number, not an owner-direct line or a carrier lookup. |

These are public government open-data feeds; no authentication or CAPTCHA bypass is implemented. Access remains subject to each publisher's conditions and availability. Pages are bounded at 100 records / 4 MB, 25-second timeouts, fixed allowlisted hosts, no redirects, one-minute page spacing. Cursors use Socrata system IDs; fingerprints use stable publisher record IDs. A changed response schema stops the connector rather than guessing columns. Periodic rescans catch updated rows after a complete keyset cycle; this is not a CDC stream. Source metadata records rows-updated timestamps, with health/errors exposed in the interface. Errors and provider payloads never disclose keys in health messages.

Researched but **not implemented**:

- [California CSLB master portal](https://www.cslb.ca.gov/onlineservices/dataportal/ContractorList): free master and personnel CSV/Excel via a selection form. Includes business phones and distinct personnel titles. Automated form/download handling and CSV layout validation remain to be implemented; no guessed direct download URL is used.
- [Florida Sunbiz downloads](https://dos.fl.gov/sunbiz/other-services/data-downloads/): workday daily and quarterly active-entity bulk files through the official SFTP service. Fixed-width parser, SFTP transport and checkpoint mapping are not implemented.
- [Florida DBPR open government](https://www2.myfloridalicense.com/open-government/): public board extracts exist. Board-specific layouts and exact download endpoints have not been validated, so there is no falsely active connector.

## Costs and expected yield

The feeds and basic HTML requests carry **no per-request vendor fee**. Existing hosting/disk charges still apply; an external scheduler can have its own charge. No new hosting subscription is bundled. Search uses the existing SerpAPI or Firecrawl account and its actual contract/credits. An API key alone does not prove there is available paid capacity. AI/contact intelligence is not required by this pilot.

Illustrative **arithmetic, not current vendor quotes or a yield forecast**:

| Daily search requests | Requests / 30 days | At an assumed $0.01/request | At an assumed $0.02/request |
|---|---:|---:|---:|
| 20 | 600 | $6 | $12 |
| 100 | 3,000 | $30 | $60 |
| 300 | 9,000 | $90 | $180 |

100 qualified/day could require far more than 100 searches/day. At 10% final yield, 100 qualified/day needs approximately 1,000 raw candidates/day; 300 needs 3,000. That exceeds the default raw cap and is not demonstrated within $20–$60/month. Free registry inputs often lack phones and owners, so free-only runs can produce **zero** fully qualified prospects. Keep quality standards; measure pilot yield before scaling.

Dashboard spending is reserved ceiling vs provider-reconciled actual cents, with requests by provider, source yields, and monthly import-cohort qualification counts. Cost-per-cohort counts use current-month discovery spending divided by the matching import cohort; activity involving older records can affect those ratios. They are operational estimates, not invoice attribution or proof of ROI. Source yields reflect the last saved verification summary; current record views recalculate evidence age. Budget accounting uses UTC day/month and counts ambiguous requests conservatively. Existing shared service usage is not refunded by cost reconciliation.

Use **Shared services → Reconcile provider billing** to enter provider-confirmed charges and invoice references. The administrator-only `GET /api/discovery/billing` exposes reservation identifiers without lead evidence. Administrator billing reconciliation API (manual provider receipt verification): `POST /api/discovery/billing/reconcile` with `request_key`, `actual_cents`, `billing_reference`. Receipt changes are audited. This does not initiate payment or automatically query invoices. There is no automatic billing-data synchronization.

## Schema and modified files

New tables: `discovery_settings`, `discovery_sources`, `discovery_records`, `discovery_jobs`, `discovery_spend`, `discovery_search_cache`, `discovery_review_log`, `discovery_billing_audit`. Existing lead/import/call/history tables are preserved. Review and source provenance are account-scoped. Discovery configuration defaults are in `discovery_service.py`; do not edit a production DB to enable spending.

New files: `discovery_sources.py`, `owner_verification.py`, `discovery_service.py`, `discovery_wake.py`, `static/discovery.js`, this guide. Modified: `research_service.py`, `leads_service.py`, `lead_identity.py`, `scraper_service.py`, `website_checks.py`, `static/scraper.js`, `static/research.js`, `static/leads.js`, `static/dialer.js`, `static/shared_services.js`, `dialer_service.py`, `.env.example`, `README.md`.

## Limits and production verification still required

- This is a conservative pilot, not the entire nationwide expansion described in the master request. Remaining state connectors, optional phone intelligence, browser-rendered/advanced technical audits and autonomous independent-operation/activity verification are not implemented. No social/private-person scraping is added.
- Material website judgments and unresolved owner identity still require evidence review. A publication verifies a publicly asserted role, not an independent legal ownership audit. “Verified business phone” means verified publication/association; connected/carrier status remains explicitly untested. No paid phone lookup adapter is shipped.
- Initial free sources are limited geographically; arbitrary state selection does not create missing connectors. Source filters match entity names or licensed categories, so industry matching can miss businesses. No absence claim is made from an unsuccessful query.
- Account retention is capped at 10,000 discovery records for the pilot. Larger operation needs a retention/storage plan. Original imports, discovery evidence and call history are never silently purged to meet a target. Historic data already merged under older phone-only logic is not reconstructed by this change.
- Source identities prevent duplicate discovery; original imported duplicate correction is conservative. Conflicting names/locations remain separate for review. A strong source ID can associate multiple license records with one lead without deleting either record.
- New tables create automatically, but existing production migration/backup, Render worker lifecycle, real shared allowance configuration, phone connectivity, live role matches, daily yield, actual invoice reconciliation, scheduler wake and prolonged restart recovery still need live verification. No production data was modified and no real paid provider call was used for tests.
- Existing Twilio/private CRM behavior is preserved; no new legal eligibility certification is implied. The existing suppression and user controls remain required. A qualifying lead is never automatically dialed.

Automated coverage is documented in the delivery notes: official source adapters, role/phone classification, unknown website treatment, isolation, review guards, idempotence, restart/pause, monetary caps, cached paid responses, billing authorization and legacy calling/import regressions. Test fixtures use scratch databases and mocked paid providers.

### Run the shipped backend checks

After installing the existing requirements, run `python tests/test_discovery.py` from the application directory. It uses a temporary database, fake credentials and mocked paid providers; it does not touch the production database or contact paid providers. The additional browser/workflow regression checks were run in the development workspace.
