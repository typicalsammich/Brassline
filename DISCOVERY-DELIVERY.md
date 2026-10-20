# Discovery delivery — October 20, 2026

Read [DISCOVERY-GUIDE.md](DISCOVERY-GUIDE.md) for setup, schema, file inventory, source schemas, cost arithmetic, Render scheduling, and limitations.

This delivery is an operational **owner-aware multi-state pilot**, not a claim that the complete nationwide acquisition target has been achieved. Automatic discovery and all new paid spending start disabled. It does not change live account settings or place calls.

Implemented: five verified public feed adapters; durable per-account discovery/research jobs; source identity/checksums/cursors/health; conservative role and public-business-phone classification; website checks through the existing SSRF-safe fetcher; publication matching; evidence review; owner-contact confidence separate from the existing score; existing Leads/Dialer integration; administrator budgets plus shared quotas; pre-dispatch cost reservations; private response caching; manual billing receipt reconciliation; server scheduling and an authenticated HTTP-only external wake script. Unrelated businesses are retained separately when they merely share a phone, while contact-level suppression and retry waits still apply.

Not delivered as functioning connectors/features: California personnel/master ingestion, Florida Sunbiz ingestion and non-construction DBPR boards, complete nationwide government coverage, paid carrier/line-intelligence checks, advanced rendered website audits, or autonomous verification of every company's independent ownership and recent trading activity. Those gaps are explicitly shown in the guide/source catalog. Unresolved discoveries remain in Research and require evidence review. **100/300 qualified prospects daily and the $20–$60 operating target have not been demonstrated.**

## Verification performed

- Read-only live metadata, sample normalization and next-page queries succeeded for NY DOS `n9v6-gdp6`, Colorado `4ykn-tg5h`, NYC DCWP `w7w3-xahh`.
- Shipped `python tests/test_discovery.py`: passed with a temporary database and mocked providers. Covers roles/registered-agent distinctions; founder ambiguity; stale data; phone association/direct distinction; fictional-phone rejection; unrelated-business identity; private account access; schema drift; evidence guards; idempotent creation; restart/expired lease recovery; in-flight pause/resume; changed-source holds; monthly/daily spending caps plus shared quotas; cached response reuse; billing authorization and automatic pause after a ceiling breach; and exact company/phone website matching.
- Existing backend suites passed: workbook import, calling lists, dispositions, duplicate/contact follow-ups, emailed/pipeline notes, call history, shifts, callback timezone/reminders, shared services. The duplicate test was updated to the new explicit requirement: distinct business labels sharing a phone remain distinct, while both respect the recipient's retry wait. Call events and lead records remain intact.
- UI checks passed: discovery controls/review/account budgets/literal evidence rendering, calling lists, DTMF/queue controls, helper consent reuse, pipeline/history, recording discovery/filters, No Answer tab, shifts, callback reminders.
- Mobile (390 px) and desktop (1440 px) discovery previews rendered; the mobile preview had no horizontal overflow. Public evidence markup was displayed literally rather than executed.
- Python and JavaScript syntax checks passed for changed application modules.
- Two older workspace UI tests (`test_ui.cjs` authentication fixture and `test_shared_ui.cjs` exact streaming-copy expectation) fail identically against the unchanged published baseline and this build. They are not reported as passing. Targeted current feature suites pass; these old harness fixtures need maintenance separately.

No actual paid searches, Twilio calls, outbound messages, carrier lookups or production database mutations were used for testing. A live Render deployment, external scheduler secret, real provider billing/allowances, production migration/backup and real-world verified lead yield still require confirmation after deployment. Source feed access alone does not validate an owner or prove that a telephone line connects.

## CA/FL follow-up release

Operational CA class-specific XLSX and FL construction CSV connectors are now included. Full free downloads and pagination were validated, and generated parser/cache tests pass. See [FL-CA-SOURCES.md](FL-CA-SOURCES.md) for exact coverage, source counts, schema/role distinctions and activation instructions. No paid services or live user schedules were enabled.
