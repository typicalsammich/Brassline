# Brassline launch guide

The new local features are implemented. Real provider operation and full visual/mobile review still need testing before public launch.

## Try the workflow

Close the old app command window, reopen start.cmd, then press Ctrl+F5 after startup. Your accounts and persistent data are retained.

Open a business in Leads and choose Notes, follow-ups & sales. Add conversation notes, schedule callbacks, enter quoted/agreed USD values, and change its sales stage. Follow-ups shows due-now, due-today and upcoming work. Pipeline groups the opportunities from New through Won/Lost. These values are quotes/agreements, not received payments; pipeline edits do not add calls or create Analytics meeting milestones.

Do not contact prevents calling and scheduling new callbacks, including matching phone/place listings after another import. It cancels open callbacks. The administrator can assign a selected business to a teammate: business details/evidence are copied to their separate workspace, while private notes and call history stay with the sender. The sender's copy is excluded from dialing. Blocked leads, active calls, and existing recipient duplicates cannot be assigned.

Launch checks shows backup and connection status. Back up and test restore file creates a fresh snapshot and verifies a scratch restore file without overwriting your active database.

## Try automatic unanswered calls and coaching free

Close the persistent app window first, then launch start-practice.cmd. Practice has a separate temporary database and fictional businesses.

In Dialer, start practice auto-dial and press Simulate no answer. This saves a simulated provider-confirmed No Answer once and advances to the next eligible lead if auto-dial is running. Pause after result prevents advancing. Manual hang-up does not automatically save an outcome or start another call.

On an active practice call, select Call helper, then Enable practice assistant. Type fictional prospect speech such as How much does it cost? or I already have a website. The simulated transcript and suggested response update. Suggestions use common objection phrases and your selected saved objection scripts. They never speak, send messages, set outcomes, or block contacts automatically.

For real calls, Automatic No Answer relies on the phone provider's exact no-answer status after its ring timeout. The app waits for confirmation, rather than guessing from a browser timer. Answered voicemail, busy, failed, cancelled and manually stopped calls remain manual. Only one call runs per account. Progression runs in the active browser and does not resume just because you reload.

## Activate services later

Paid services remain disabled. Never paste secrets in chat or commit them to GitHub. Configure private environment settings yourself when ready.

- Hosting: the existing Render configuration uses a persistent database disk and one process. The app uses Render's public HTTPS address by default. Set PUBLIC_BASE_URL explicitly to your custom root HTTPS address after connecting a domain.
- Account emails: own a domain with DNS control, verify it with the provider, then privately configure EMAIL_FROM, RESEND_API_KEY and EMAIL_ENABLED=1. A .lat domain should work; the sending domain can differ from the app's domain. Verification links last 24 hours; reset links last 30 minutes and work once. [Resend domain requirements](https://resend.com/docs/dashboard/domains/introduction)
- Calling: keep TWILIO_ENABLED=0 until you have the account, authorized number, credentials, application and public webhook address. No real calling is enabled now.
- Transcription: keep TWILIO_TRANSCRIPTION_ENABLED=0 until that provider feature is available. During a connected call, confirm participant permission before starting transcription in Call helper. Both audio directions are transcribed through the provider; signed final utterances update the app. Prompts refresh every two seconds plus provider/network latency. Stop transcription stops it for the rest of that call. [Twilio real-time transcription](https://www.twilio.com/docs/voice/twiml/transcription)
- Google worker: sign into your dedicated spare-account profile yourself, download its private configuration from Scraper, and start the worker. No worker.json is currently present in the app directory, so the authenticated live search test remains pending. See SCRAPER-GUIDE.md.

## Backups and transcript retention

Non-practice startup checks for a daily SQLite snapshot and retains the newest seven snapshots beside the database. An administrator can force a fresh backup. Each new file is copied to a scratch restore file, checked for SQLite integrity, and checked for readable account/lead/call tables. Production data is not overwritten by that test.

Backups contain all users' data, hashes and sessions. Keep them private and keep an off-server copy using Download backup. Files on the same disk do not protect against losing that disk/service. [Render disk documentation](https://render.com/docs/disks)

Transcript text is retained in the active database for up to 30 days, with hourly cleanup while running. The app stores no audio files. You can export/delete text from the business timeline; deletion prevents late callbacks from recreating it. Existing backups may contain older text, and the speech provider processes live audio separately.

## Verification status

Passed automated API and DOM checks: account separation/recovery; script editing and call snapshots; scheduled callback eligibility; notes/timelines; exact project amounts; duplicate contact suppression; private lead handoffs; signed automatic no-answer classification and duplicate delivery; answered/manual/busy exclusions; one-time practice auto-advance; signed transcript callbacks, roles, privacy, export/deletion and coaching; backup/scratch-restore checks; email verification/reset with mocked delivery; configured HTTPS origin checks; legacy migrations; scraper fixtures; and Analytics regressions.

Still pending: desktop/phone visual walkthrough; authenticated live Google scraping; real email sending/inbox receipt; real browser-to-phone calling and live audio transcription; Render deployment; restart/disk-persistence checks; and an off-server recovery drill. The preview browser has been unable to connect to the local test server.

No live calls, transcription charges, emails, domain purchases or deployments were performed in these tests.

## Administrator-funded research and coaching

Research and Shared services are now available. The administrator can configure encrypted provider connections, enable funded research/calling/transcription/coaching and set team-wide and per-account monthly limits. Members choose funded or personal calling explicitly. Funded native Twilio transcription requires a call on the administrator’s Twilio account; those calls also use the administrator’s numbers and balance. Live AI is opt-in and uses streamed short suggestions from recent partial/final transcript text. All new services default to disabled with zero allowances. Resend domain verification and email delivery controls are in Shared services; registration already allows personal passwords and manual copied recovery links remain available. See SHARED-SERVICES-GUIDE.md for setup, quota definitions, billing limits and remaining live tests.

## Separate Deepgram transcription funding

Shared services now offers Deepgram in addition to native Twilio transcription. An administrator Deepgram key can fund live transcripts on members’ personal Twilio calls, with per-account/team audio-minute limits and a per-session deadline. Callers acknowledge their Twilio streaming charge before starting permitted transcription. Two tracks produce labelled private text and feed the opt-in AI coach; audio recordings/playback are not implemented. See DEEPGRAM-SETUP.md for setup and current initial/ongoing cost estimates. Live provider and Render checks are still pending.


### Six-digit password recovery
Forgot password now opens a dialog to request a six-digit email code. Codes expire after 10 minutes; five incorrect guesses invalidate the code. A new request replaces prior codes. Correct verification consumes the code and grants five minutes to enter and confirm a password of at least 12 characters. Saving the password revokes existing sessions and outstanding recovery permissions. Request and verification rate limits apply; responses do not disclose whether an email has an account. Resend delivery must still be enabled and tested after hosting. Administrator-created recovery links remain available as a separate manual fallback; email-address verification still uses a link. Mocked recovery and UI checks passed; live email delivery remains pending.
