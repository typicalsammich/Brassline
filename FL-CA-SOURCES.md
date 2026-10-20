# Florida and California discovery

Both connectors use official public government downloads. No additional API key, paid account or subscription is required for these downloads. Hosting bandwidth/storage limits still apply. Existing paid research remains separately disabled/budgeted and is never activated by choosing a public source.

## Enable on your account

1. Open **Scraper → Automatic Discovery → Discovery settings**.
2. Select **California CSLB · statewide contractor trades** and/or **Florida DBPR · construction licenses**. Selecting a source adds its state code to the jurisdiction field automatically. Existing saved source selections are otherwise preserved.
3. Choose industries, save with discovery enabled, then **Start / resume daily jobs**. Source pages share the daily raw-record allowance so one selected source does not consume it all at the first page.
4. Inspect results under **Research → Owner verification queue**. The first cache refresh can take several minutes; job status explains that. Later pages use local caches. **Sources and job health** shows fetch state and cache snapshot information where available.

Existing enabled accounts are not silently switched to new sources. No live account settings, budgets or calling lists were changed by deployment.

## California

Official entry point: [CSLB public data portal](https://www.cslb.ca.gov/onlineservices/dataportal/), which links to [contractors by classification](https://www.cslb.ca.gov/onlineservices/dataportal/ListByClassification).

The adapter follows the normal public form POST with the page's hidden validation fields and the selected classification. It does not bypass authentication, CAPTCHA or rate limits. Only HTTPS redirects within the same official origin and data-portal path are accepted.

Statewide supported classes:

| Class | Trade |
|---|---|
| C-36 | Plumbing |
| C-39 | Roofing |
| C-10 | Electrical |
| C-20 | HVAC |
| B | General building |
| B-2 | Residential remodeling |
| C-33 | Painting |
| C-27 | Landscaping |
| C-53 | Pools |
| C-7 | Low-voltage electrical |

Each class is downloaded and cached separately. Applicable classes rotate between pages, preserving each class's position. Overlapping licenses retain one source identity in the private discovery records. Current cache statistics can count the same license in multiple class files; they are counts of public source entries, not distinct qualified leads.

Verified columns: `LicenseNumber`, `LastUpdated`, `BusinessType`, `BusinessName`, `City`, `State`, `PhoneNumber`, `IssueDate`, `ExpirationDate`, `Classification`, `Status`. The official workbook also has street address, ZIP and county columns; street addresses are discarded rather than cached or imported. Only `CLEAR` licenses with nonexpired dates are accepted.

The workbook does **not** provide an owner's separate full-name field. `Sole Owner` is a business type, not a named owner. The connector therefore does not guess owner names from company names. Company website research or reviewed public evidence remains necessary. The phone is explicitly published as a business phone; carrier connectivity and owner-direct status remain unverified.

Live validation completed October 20, 2026: a complete official plumbing workbook (1,767,825 compressed bytes) parsed **17,572 CLEAR, unexpired records**, with successful first and next pages through the adapter. Those are source records, not owner-qualified prospects. Additional complete downloads for roofing, electrical and HVAC also passed live parsing. Other supported classes use the same public form and validated workbook schema.

The full statewide master CSV request was interrupted by the publisher/network during testing. This connector deliberately uses the smaller official classification XLSX exports; it does not depend on a partially downloaded master file. Personnel exports and unsupported classes are not implemented.

## Florida

Official file: [DBPR construction license extract](https://www2.myfloridalicense.com/sto/file_download/extracts/CONSTRUCTIONLICENSE_1.csv).

Official definitions: [DBPR code tables](https://www2.myfloridalicense.com/about-us/understanding-dbpr-codes/) and [DBPR public-records layout/disclaimer](https://www2.myfloridalicense.com/sto/documents/readme.pdf).

The adapter validates the legacy construction layout: 21 documented fields plus an optional empty trailing field. It requires board `06`, known contractor rank, primary status `C` (Current), secondary status `A` (Active), and a nonexpired date. Header changes, unexpected extra fields and unknown positional layouts stop processing. It does not ingest the old `_2`/`_3` files observed with expired 2020-era records.

Supported construction ranks include plumbing, roofing, HVAC/mechanical, building/general/residential, pool, sheet-metal, utility, solar and specialty contractors, including corresponding registered categories. **Florida's separate electrical board is not included in this construction feed.** Sunbiz corporations are also a different, unimplemented connector.

The licensee's name and associated DBA are preserved as public license information. The person is labeled **License holder / qualifier**, never a verified owner merely because a name appears in the extract. This feed has **no business-phone column**, so a phone remains unknown until additional permitted research or evidence review. No residential address is imported or cached.

Live validation completed October 20, 2026: the complete official file (47,052,015 bytes) yielded **97,127 Current, Active, unexpired supported construction records**, with successful first and next pages. These are not qualified-owner counts. The general DBPR page can reject requests even while the linked public CSV remains available; no access-control bypass is used.

## Persistence and resource limits

- Public normalized source caches live beside the primary database under `discovery-source-cache/`. Private evidence remains in the existing account-scoped discovery tables.
- Cache writes use separate staging databases, integrity checks and atomic replacement. A failed transfer keeps the previous cache and account cursor intact. Expired snapshots are not silently reused after a failed refresh.
- Seven-day cache reuse avoids downloading full files for every account/page. Snapshots are not real-time license checks; confirm current licensing at the publisher when it matters.
- Generation-bound cursors restart a class/feed scan after a changed snapshot. Previously discovered licenses deduplicate by source identity. There is no change-data-capture guarantee; high-volume users should monitor rescans and yield.
- Native source jobs get fifteen-minute leases to allow an initial refresh; other discovery jobs retain their three-minute leases. HTTP/socket, stream-size, workbook expansion, row and string-table limits bound processing.
- At least 200 MB free space is required before a refresh. Normalized files are capped at 150 MB each; CA compressed workbooks at 32 MB, expanded archive content at 120 MB, 250,000 rows and 500,000 shared strings. The source panel reports failures if a publisher grows beyond the pilot limits. Monitor the existing Render disk and backups before expanding coverage.
- The XLSX reader does not execute formulas, follow external links or extract filesystem paths. XML entity declarations and unexpected worksheet layouts are rejected. No new runtime dependency is required.
- The existing Render single-worker/persistent-disk arrangement remains sufficient. Cache files are disposable public source data and are not part of the private SQLite backup snapshots; missing caches rebuild through the official downloads. Do not share a copied SQLite database between independent web/cron instances.

## Checks

Run `python tests/test_discovery_bulk.py` for generated CA/FL schema, active/expiry, Unicode, phone/role separation, cache reuse/failure atomicity, paging and cursor tests. Run `python tests/test_discovery.py` for the existing discovery isolation, durable jobs and budget checks. The automated tests make no network calls, paid searches or telephone calls. Live validation used free official downloads only.
