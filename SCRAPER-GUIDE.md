# Scraper: try practice first

1. Close the previous dashboard command window and double-click `start-practice.cmd`.
2. Open http://127.0.0.1:8000 and select Scraper.
3. Type the start of a US city or state. Click a suggestion. Type an industry, click a suggestion, or use your own industry text.
4. Click Start practice search. Watch the progress and found / checked / qualified counts. Practice uses six fictional businesses; it never visits Google or calls anyone.
5. Open Leads to inspect the new batch. Back on Scraper, move the completed batch to Recently deleted, then restore it. A lead shared with another active batch remains visible.

# Connect the real worker locally

Keep using local development until all sections are ready; Render can wait.

1. Install Google Chrome if needed. Close the practice dashboard and double-click `start.cmd`. Open the app, create your administrator account if needed, and select Scraper.
2. Double-click `open-google-profile.cmd` in this folder. A separate Chrome profile opens. Sign into your **spare** Google account yourself. Close every window belonging to this dedicated profile before starting the worker. Do not use your normal Chrome profile.
3. On Scraper, click Download worker configuration. Keep `worker.json` private. It grants the local worker access to your account's scrape jobs and results; it does not grant access to another person's records, invitations, or whole-database backups. Downloading a new configuration revokes the previous one. Never share its contents in chat or commit it to Git.
4. Double-click `start-worker.cmd`. First launch installs Playwright. When asked, select the downloaded `worker.json`; the worker saves it in the ignored `data` folder. Leave the worker window open. The Scraper page should say Local worker connected.
5. Choose a city and industry. For the first real test, select 6 tiles and a 20-business check limit. Click Start search. The worker opens real Chrome off-screen and works one business at a time. Check the resulting business records and warnings before increasing the run size.
6. After that short test succeeds, double-click `enable-worker-autostart.cmd`. It creates a Windows Startup shortcut that runs the worker quietly when you sign into Windows. If Windows blocks the PowerShell script, keep the error window open and ask for help; this launcher does not change your execution policy.

The sign-in and authenticated Google Maps extraction have not been tested in your spare profile yet. Fixture and practice tests verify the application flow and parsers, not Google's live layout. Some businesses will stay Waiting to be checked because website absence, independent ownership, current decision-maker authority, or review dates could not be verified automatically. The app will not invent those facts to produce more ready leads.

# What the worker does

The dashboard remembers completed (wording, map tile) searches per account. A new run uses new tiles where possible; unfinished tiles can resume. Cities use an adjustable radius around approximate postal-locality coordinates. Whole-state runs use a bounding grid. This is not a promise of exhaustive coverage or exact state boundaries; inspect address warnings for border results.

The worker reads the loaded result list in one pass per scroll. Closed businesses and a clearly different industry can be rejected from their cards. Small review counts and low ratings alone are not exclusions under your chosen rules. Businesses already stored in your account are associated with the new batch without reopening them. A new business is reserved before opening, so an interrupted check cannot cause repeated automatic visits; an interrupted lead stays stored with unknown facts and a warning.

A share link resolves to its place page and that page is loaded again before reviews. Review count and rating are calculated only from the five-row star breakdown. Newest reviews are selected in signed-in Chrome; dates are read from matching review network responses. Relative dates are saved with an approximate-date label. Owner reply dates are stored when readable. Unrecognized, signed-out, limited, or unrelated responses do not become verified review facts. Review authors and full review text are not stored.

Supplied websites are checked for observable HTML facts and public owner/manager candidates, with source links. A published person's own phone is distinguished from the main company number. Website absence, mobile usability, working forms, and current purchasing authority are not inferred from missing metadata. Review suggestions stay unknown until evidence is confirmed.

Your computer only makes outgoing connections. The dashboard stores jobs; the worker claims one, reports progress, and returns results. No incoming worker port is opened. Each invited person downloads their own worker configuration and uses their own dedicated local profile/computer. A worker disconnected for ten minutes releases its job for resumption; reservations and completed tiles stay saved.

# If a scrape fails

- **Local worker offline:** make sure the computer is awake and connected, and start `start-worker.cmd`. If access was revoked, download a new configuration, replace `data/worker.json` yourself, and restart.
- **Chrome profile already in use:** close the dedicated Chrome window and restart the worker. Keep your normal profile separate.
- **Google asks for sign-in or a check:** stop the worker, open `open-google-profile.cmd`, complete it yourself, close the dedicated window, then restart the worker and queue a new run. The worker does not bypass Google's checks.
- **Review layout or response format changed:** records remain unknown and include a warning. Do not classify the business as inactive. Share the warning text without credentials so the adapter can be repaired.
- **Website check failed:** availability remains unknown. Verify the supplied URL and retry; a timeout is not proof of a broken website.

Worker diagnostics live in `data/worker.log`. Google cookies live in `data/google-scraper-profile`. Both are ignored by Git. To stop automatic startup, remove the Brassline worker shortcut from Windows' Startup folder, then close the worker process. Disconnect worker in the app revokes its access.

Deleted batches can be restored for 30 days. After that, they remain archived to preserve call history, but the app no longer offers restoration. A backup preserves the entire database. Switching to Render later requires a newly downloaded worker configuration containing the hosted HTTPS address.
