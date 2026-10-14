# Using Brassline

## Open your workspace

Close the app's old command window after an update, then double-click start.cmd. Leave the new window open. Visit http://127.0.0.1:8000 and press Ctrl+F5 if you see an old page. The first launch may install a new image-processing dependency for profile photos.

Use start-practice.cmd for a fresh fictional workspace. Its calls and scraper searches are pretend. Practice data resets each launch, with no real account security or invitations. Use start.cmd for your own saved leads and account.

## Your account

The first person creates the administrator account. Later users need an invitation link. The sign-in screen has Create account and Forgot password options. Use at least twelve characters for passwords.

Click your name/photo at the bottom of the navigation, or Settings. Save a display name, upload a PNG/JPEG/WebP profile picture under 1 MB, or remove it. Your picture is cropped to a square and image metadata is removed. Set your calling goal and time zone here too; Analytics uses that time zone.

Email/password changes require your current password and sign out other sessions. You can also sign out other sessions yourself, or use Sign out in Settings or the navigation. If a call is active, sign-out pauses auto-dial and requests hang-up first; its unfinished result remains available on your next login.

If you forget your password, ask the administrator for a private recovery link. The administrator creates it under Team invitations after entering their own password. The link works once, expires after thirty minutes, and signs out all existing sessions when used. Email sending and email verification are not connected yet. The first administrator should keep their password safely stored; there is no email-based recovery service.

## Invite someone

Open Team invitations and click Create invitation link. Click Copy link to paste it into a message, or Share link to use your device's share sheet. Share it privately yourself. Every link works once and expires after seven days.

Invitees create separate accounts with private leads, scripts, calling history and analytics. Inviting someone does not share your existing leads. Administrators can view member names/email addresses and invitation status, and revoke unused links. A lost raw link cannot be retrieved: revoke it and create another.

Until Render hosting is configured, your localhost invitation address works only on this computer. Once hosted, new links will use the online address.

## Find and qualify leads

Open Scraper and type a US location and an industry. Click the suggested item you want. Real searches need your dedicated local Google Chrome worker; see SCRAPER-GUIDE.md. Practice searches are simulated.

Use Leads to search, import CSV, filter by score/result/batch and inspect a business. Scores are out of 100. Open the business name to see sources, unknown information and website findings. Automatic website findings are hints until you verify them. Confirm evidence and save it before treating the lead as qualified.

Export CSV saves the account's leads. Only the administrator can Download backup, which includes all accounts, password hashes and active sessions, so keep the backup private. See README.md for restoration instructions.

## Prepare your scripts

Open Scripts. Add website-services starters gives you a call pitch, objection answers and a requested follow-up text. You can edit them or click New script to write your own. The three types are Call script, Objection answer and Text message.

Select text in the editor before choosing Bold, Italic, Heading or a highlight color. Split here starts the next reader column. Smaller screens stack columns. Use [Business] wherever the current prospect's name should appear. Text pasted into the editor stays plain so outside formatting does not interfere.

Choose one Call script in use. Tick the objection answers you want displayed, select the reader font size/theme, and Save reader settings. You can preview it with Example Business. Changes to the script selection apply to the next call; a call already started keeps its original script version.

Archive removes a script from active choices, and Restore brings it back. Delete removes it from the library while retaining the version stored with previous calls. Copy text copies a text template; replace your name, contact details and next-step placeholders before sharing. Nothing sends a text automatically.

## Make calls

Real calling is prepared but disabled until Twilio is configured. There is no paid calling enabled now. Use Practice to try the entire flow without a Twilio account or microphone.

Open Dialer. Start practice auto-dial or choose Call for a single eligible lead. Select a result after an answered call: No Answer, Not Interested, Booked, Call Later or Wrong Number. Automatic No Answer can save a provider-confirmed unanswered result and advance when auto-dial is running. Busy, failed, cancelled, answered voicemail and manual hang-up remain manual. Call Later opens a date/time scheduler. Pause after result stops auto-dial after the current result. Rows stay in place until you refresh the list.

During a call, Show script opens the version captured when the call started. The business name fills [Business], and the chosen objection answers appear below the script. The reader stays above your call controls and closes when the call ends. Its text area and objection row can scroll independently.

Leads-page result edits are bookkeeping only and do not count as dials. Wrong entries can be marked ignored while preserving their history. See DIALER-GUIDE.md for the full calling rules and optional future setup.

## Review results

Analytics shows your calling funnel, daily activity, local hours/weekdays, industry results and caller-number comparisons. Apply a date range. Industries count distinct businesses rather than counting repeat calls as separate businesses. Small samples are labelled.

Under Booked meetings -> signed clients, record Showed after a meeting happens, then Signed after the client agrees. These updates do not add dials. They stay attached to the original booked call for date-range reporting. Ignore mistakes rather than deleting history; ignore Signed before ignoring its Showed record.

Script comparisons appear after calls with two different script IDs. The selected script ID and original script version are stored with each call, so later script edits cannot rewrite the call history.

## What still needs live setup

The app remains local. Render deployment, authenticated real Google scraping, real email delivery and Twilio calling/transcription still need setup and live checks. API and interface interaction tests passed; the preview browser timed out connecting to the local server, so full visual verification remains pending.

## New follow-up and sales workflow

Open a business in Leads and select Notes, follow-ups & sales. Add conversation notes, schedule callbacks, save quoted/agreed USD project values, and set a sales stage. Your notes and calls appear together in the timeline. Follow-ups shows due-now, due-today, upcoming and completed/cancelled work. Date/time entry uses your device's time zone and is stored in UTC.

Pipeline groups your own allowed opportunities into New, Contacted, Meeting booked, Proposal sent, Won and Lost. Values represent quotes/agreements, not received payments. Pipeline changes do not add calls or create Analytics meeting milestones.

Do not contact is separate from Not Interested. It prevents further dialing and new callbacks, including matching phone/place listings after another import, and cancels open callbacks. Explicitly remove the block to contact that business again.

The administrator can assign one of their businesses to a member. Business details and qualification evidence are copied into the member's separate workspace. Your private notes and call history are not copied, and your original is excluded from dialing. Blocked leads, active calls and recipient duplicates cannot be assigned.

## New call helper and launch checks

During a practice call, select Call helper, Enable practice assistant, and type a fictional prospect statement. The simulated transcript and suggested reply update from common objection phrases and selected objection scripts. Real transcription remains disabled until its provider is connected and you confirm permission on the connected call.

Launch checks shows daily backups and service connections. The app retains seven SQLite snapshots beside the database; each new snapshot receives a scratch-restore integrity/read check. Keep a private off-server downloaded backup too.

Verification and password-reset email flows are implemented, with delivery disabled until you verify a sending domain and configure the email provider privately. A .lat domain should work with DNS control. See LAUNCH-GUIDE.md for setup, retention, verification evidence and remaining live tests.

## Your own Twilio account

Every user can open Settings → Twilio connection to save their own Twilio account and existing Voice numbers. Twilio charges that connected account directly for calls and transcription. The connection is private to the user; other members do not use your number or balance. Saving leaves calling disabled until verification and explicit activation. Restart start.cmd and refresh the page after this update.

## Administrator-funded research and coaching

Research and Shared services are now available. The administrator can configure encrypted provider connections, enable funded research/calling/transcription/coaching and set team-wide and per-account monthly limits. Members choose funded or personal calling explicitly. Funded native Twilio transcription requires a call on the administrator’s Twilio account; those calls also use the administrator’s numbers and balance. Live AI is opt-in and uses streamed short suggestions from recent partial/final transcript text. All new services default to disabled with zero allowances. Resend domain verification and email delivery controls are in Shared services; registration already allows personal passwords and manual copied recovery links remain available. See SHARED-SERVICES-GUIDE.md for setup, quota definitions, billing limits and remaining live tests.

## Separate Deepgram transcription funding

Shared services now offers Deepgram in addition to native Twilio transcription. An administrator Deepgram key can fund live transcripts on members’ personal Twilio calls, with per-account/team audio-minute limits and a per-session deadline. Callers acknowledge their Twilio streaming charge before starting permitted transcription. Two tracks produce labelled private text and feed the opt-in AI coach; audio recordings/playback are not implemented. See DEEPGRAM-SETUP.md for setup and current initial/ongoing cost estimates. Live provider and Render checks are still pending.


### Six-digit password recovery
Forgot password now opens a dialog to request a six-digit email code. Codes expire after 10 minutes; five incorrect guesses invalidate the code. A new request replaces prior codes. Correct verification consumes the code and grants five minutes to enter and confirm a password of at least 12 characters. Saving the password revokes existing sessions and outstanding recovery permissions. Request and verification rate limits apply; responses do not disclose whether an email has an account. Resend delivery must still be enabled and tested after hosting. Administrator-created recovery links remain available as a separate manual fallback; email-address verification still uses a link. Mocked recovery and UI checks passed; live email delivery remains pending.


### Welcome tour and Help
The first dashboard visit opens a short walkthrough with Next, Back, and Skip tour. Finishing or skipping is saved on your account across devices. Help sits at the bottom of the sidebar and includes searchable feature explanations, account recovery guidance, setup requirements, and Replay welcome tour. The walkthrough highlights navigation but does not run research, place calls, or enable services. Administrators receive an additional setup step.
