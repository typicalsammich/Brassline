# Brassline - calling, sales workflow, and account settings

Latest: Follow-ups, Pipeline, notes/timelines, do-not-contact, private lead assignments, automatic daily backups, confirmed automatic No Answer, the call-helper/transcription integration, email verification/recovery integration, and Launch checks are implemented locally. See LAUNCH-GUIDE.md for the current workflow, service setup and remaining live tests. Provider billing, real email delivery and live transcription remain disabled.

Foundation, Leads, Scraper, Dialer, Analytics, Scripts, Settings, and Team invitations are built. The brass-and-charcoal design uses numeric scores out of 100, readiness rings, score distribution, pipeline and calling charts, matching scrollbars and cursors, and the Brassline logo.

## Start on Windows

Install Python 3.11 or newer if needed. Double-click `start.cmd` for the persistent workspace or `start-practice.cmd` for fictional data. Leave its command window open and visit http://127.0.0.1:8000 if the browser opens before the server is ready. To use updates, close the old command window and launch again. Practice creates a fresh temporary database each launch; practice calls and searches are simulated and never contact businesses. Temporary databases may remain in Windows temp storage until cleaned.

## Accounts and invitations

The first local visitor creates the administrator account with an email and a password of at least 12 characters. Once an account exists, the sign-in screen has a visible Create account button. Additional accounts require a new invitation link or code pasted into the account form. Invitation links open account creation even when another account is signed in. Each account has separate private leads, call history, and analytics; new accounts start empty.

The administrator uses Create invitation link, then Copy link and shares it themselves. Links expire after seven days and can be used once. Invitations are not emailed automatically. Practice uses an automatic fictional account and disables invitations. Use `start.cmd` to create persistent accounts.

## Leads

Search name or phone and filter by numeric score band, result, batch or readiness. Open a business name for its dossier, qualification evidence and history. Clicking a phone copies it. CSV imports allow 2,000 rows / 2 MB. Download the provided CSV example for columns. Records merge within your account first by Google place ID, then normalized US phone, then normalized name. Repeated imports fill missing basic fields while preserving existing verified facts and associate the business with both batches.

Website URLs automatically queue lightweight checks, one at a time. Refresh checks and reopen a dossier for findings. The checker reads the home page and linked About/Contact pages, without submitting forms or rendering on a phone. Missing viewport metadata is a review hint, not a verified usability failure. Fetch errors leave availability unknown. Published owner/manager names and person-specific business numbers may be found with sources; the company number is not labelled a direct owner line. Confirm findings before treating them as scoring evidence. Practice checks are simulated.

Confirm website need, activity, independent ownership and decision-maker evidence, then Save evidence and re-score. Public HTTP/HTTPS source links are required. Activity entries use `2026-10-01 | project post | https://business.example/project`. Unknown information stays unknown and incomplete scores are provisional. Calling readiness requires verified website opportunity, recent activity, independent ownership, an approved category, a public US phone and the configured score threshold (60 initially). Email/social-only prospects remain stored but are not ready for browser calling. Few reviews, home addresses and small size are not automatic exclusions.

`settings.json` contains scoring weights, score bands, qualification threshold, recency, categories and aliases, priority cities and check limits. Re-score saved facts after changing it. Current scores also age with saved activity. CSV export includes the account's evidence, sources and reasons; it is not limited by onscreen filters, and potential spreadsheet formulas are escaped. CSV import reads basic business columns; database backups preserve complete evidence and history.

Leads-page bookkeeping results change Last result but do not count as calls or change Last call. Mark mistake ignored preserves the record while excluding it from totals.

## Scraper and Dialer

See `SCRAPER-GUIDE.md` for the local dedicated Chrome profile, spare Google login, worker setup, autocomplete searches across US areas and industries, startup and recovery. Real Google extraction still needs validation with your authenticated profile. All Google authentication stays on your computer.

See `DIALER-GUIDE.md` for practice calling and optional future Twilio setup. No Twilio account or number is needed now. The real workspace hides call buttons until configured. Practice auto-dial asks for confirmation, starts a simulated call and advances only after you select a result. Hang up alone never advances or records a result. Pause after result stops after the current outcome. Available outcomes include Wrong Number.

## Analytics

Analytics includes Dials -> Answered -> Booked -> Showed -> Signed, daily activity, calling hours and weekdays, distinct-business industry results, and caller-number comparisons. Choose an inclusive range up to 366 days. Dates, hours and weekdays use your Dialer time zone. Only saved, non-ignored Dialer results count; unfinished calls and bookkeeping are excluded. Empty charts stay empty until calls are recorded.

Under Booked meetings -> signed clients, record Showed when the meeting is held, then Signed when the client agrees. These are separate immutable records and never add a dial. Milestones follow the original booked call's date range even when recorded later. Ignore a mistaken Signed update before ignoring Showed. Ignoring the original booked call excludes its milestones from the funnel.

Answer rate = answered / dials; booking rate = booked / answered. Each funnel stage shows its conversion from the preceding stage. Industries count each distinct business once even after repeat calls; caller comparisons count calls. Samples below 20 observations are labelled small: this is a caution, not a statistical confidence calculation. Script comparisons become available after recorded calls with two script IDs; select the in-use script on Scripts before making calls.

## Hosting later

No Render account is needed to finish locally. The included `render.yaml` prepares one web process, a persistent disk, secure cookies and a generated setup token. Upload the app folder to a private GitHub repository before a future Render deployment. Never upload `.venv`, data, databases, backups, worker configuration or secrets. No deployment has been performed.

On the future HTTPS host, enter SETUP_TOKEN from the service environment into first-account creation yourself, then remove it after creating your administrator. Never share it in chat. Host settings are ONLINE=1 and DATABASE_PATH=/var/data/dashboard.db. Optional Twilio configuration is deferred; `.env.example` contains blank placeholders. Practice is local only.

## Backup and verification

Administrator Download backup includes all users' data, password hashes, sessions and invitations. Keep it private. To restore: stop the app, preserve the current database, replace the configured database with the backup and restart. Restoring also restores its sessions and invitations. Ordinary accounts cannot download a full backup.

Account creation, invitation rules, account separation, immutable history, API analytics, milestone corrections and frontend DOM interaction checks passed. Browser visual review, authenticated live Google scraping and real Twilio calls remain pending. API and DOM checks do not prove live provider operation. If you still see the old sign-in form, restart the app and hard-refresh with Ctrl+F5.

## Scripts and account settings

See USER-GUIDE.md for the full everyday workflow. Scripts supports call scripts, objection answers and text-message templates; creating, editing, archiving/restoring and deleting; bold, italic, headings, three highlight colors and column splits; selecting one in-use call script and up to twelve objection answers; reader size and charcoal, paper or high-contrast themes. Add website-services starters creates a permission-first pitch, three objection answers and a requested follow-up text, without duplicating existing starter titles. Replace personal placeholders before using text templates. No messages are sent automatically.

During a call, Show script opens the saved version of the in-use script, with [Business] replaced safely by the prospect name. The reader stays above the call controls and closes on hang-up or when the call ends. Objection cards have their own scrollable row below the main script. Smaller screens stack script columns. Each call attempt captures an immutable script version; later edits or deleting a library script do not overwrite it. Script comparisons appear in Analytics after calls with two different scripts.

Settings supports display names, private profile photos (PNG/JPEG/WebP under 1 MB, normalized to a 256-pixel JPEG without metadata), email/password changes with the current password, dial goals/time zones, session expiry listings, signing out other sessions and signing out. Changing email/password revokes other sessions and pending reset links. Email verification/delivery is not connected; the administrator can create a private one-use recovery link for a member after confirming their own password. Recovery links expire after thirty minutes and revoke all the member's sessions after use.

Team invitations replaces the former footer with a dedicated page. Administrators can create links, copy/share them, view statuses/members and revoke unused links. Old invitation tokens are still supported; the database migration retains existing accounts and data. Raw invitation and recovery tokens are not saved in the database, so existing invitation links cannot be retrieved later. If lost, revoke the old link and create a new one. Local links only work on this computer until hosting is configured.

Account and script API checks and frontend interaction checks passed. The local preview server started successfully, but the preview browser timed out connecting to it; full visual verification remains pending. Real Google scraping and paid provider calls still require their live setup and verification.

## Branding and Twilio setup update

The logo now uses a custom interlocking connection mark with the existing brass/charcoal palette. Active-navigation end arrows are removed, and Dialer/invitation navigation icons no longer use arrows.

Settings now includes Twilio connection; Dialer has a Connect Twilio shortcut. The administrator can save account/API/TwiML app credentials, add existing US Voice numbers, assign members or explicitly share a number with all members, copy the webhook URL, verify the provider configuration, and separately enable/disable calling or disconnect the saved setup. Current password is required for changes. Settings controlled by environment variables are labelled and remain managed by the host. Saving a connection cannot silently enable billing. No account or number is purchased by this feature.

Credentials are encrypted using cryptography/Fernet in the database, with integration.key stored separately beside it (or INTEGRATION_ENCRYPTION_KEY in host environment). Preserve the key privately with your restore materials: the SQLite backup alone cannot decrypt a saved connection after losing that key. Credentials are not returned to the form, and validation errors omit submitted values. Re-entering a saved connection is possible after an administrator clears it.

The encryption dependency was installed in the local app environment. API and DOM tests passed, including existing calling behavior. Provider verification used a mock; no live Twilio calls/requests occurred. See TWILIO-SETUP.md and restart the app before using the new routes.

## Administrator-funded research and coaching

Research and Shared services are now available. The administrator can configure encrypted provider connections, enable funded research/calling/transcription/coaching and set team-wide and per-account monthly limits. Members choose funded or personal calling explicitly. Funded native Twilio transcription requires a call on the administrator’s Twilio account; those calls also use the administrator’s numbers and balance. Live AI is opt-in and uses streamed short suggestions from recent partial/final transcript text. All new services default to disabled with zero allowances. Resend domain verification and email delivery controls are in Shared services; registration already allows personal passwords and manual copied recovery links remain available. See SHARED-SERVICES-GUIDE.md for setup, quota definitions, billing limits and remaining live tests.

## Separate Deepgram transcription funding

Shared services now offers Deepgram in addition to native Twilio transcription. An administrator Deepgram key can fund live transcripts on members’ personal Twilio calls, with per-account/team audio-minute limits and a per-session deadline. Callers acknowledge their Twilio streaming charge before starting permitted transcription. Two tracks produce labelled private text and feed the opt-in AI coach; audio recordings/playback are a separate opt-in Twilio feature in Call history. See DEEPGRAM-SETUP.md for setup and current initial/ongoing cost estimates. Live provider and Render checks are still pending.

## Owner-aware automatic discovery

See [DISCOVERY-GUIDE.md](DISCOVERY-GUIDE.md) for the default-off government-data pilot, evidence review, spending caps, initial CA/FL/NY/CO source coverage, limitations and HTTP-only scheduling on Render. Discovery never automatically calls a lead.
