# Use the Dialer for free in practice mode

No Twilio account, number, credit card, or microphone permission is needed to try this.

1. Close the old dashboard command window, then double-click `start-practice.cmd`. The launcher updates any missing Python dependencies for free.
2. Open http://127.0.0.1:8000 and select Dialer. Qualified leads enter when their disposition schedule permits; named lists can also contain unresearched imports. Businesses called today stay visible in the list.
3. Set your daily goal and time zone, then click Save goal. The initial goal is 50 and the default time zone is America/New_York; change them to suit you.
4. Click Start practice auto-dial and confirm. The first fictional lead pretends to ring and connect.
5. Choose No Answer, Voicemail, Not Interested, Hung Up, DNC, Callback, Booked, or Wrong Number. That manual choice finishes the call and starts the next eligible lead if auto-dial is still running.
6. Try Hang up without selecting a result: it does not record anything or advance. Choose a result afterward to continue. Pause after result stops auto-dial after you finish the current lead.

The call panel stays at the bottom, with the business name, timer, caller line, mute, pause, hang-up, and quick results. Practice mute changes its on-screen state; there is no real audio connection.

# How the list and numbers work

Row order stays stable during an auto-dial run. Refresh the calling list to load newly due leads. Each saved disposition resets its wait: No Answer and Voicemail return after 24 hours, Hung Up after 7 days, Not Interested after 30 days. This replaces the former two-attempt limit. Callback opens a date/time scheduler and holds the lead until that requested instant; cancelling the scheduler leaves it on hold. A new Callback disposition supersedes old open callbacks. Legacy Call Later results still follow their saved callback time. DNC blocks matching contacts and cancels open callbacks; Wrong Number, assigned-out, Won and Lost leads remain excluded. Booked leads stay excluded. Deliberate manual retries are available for unanswered calls, but do not bypass DNC, Wrong Number, booked leads, callback holds or a Not Interested waiting period.

Today’s dials, answered calls, and bookings come only from non-ignored call_events rows with source `dialer`. An unfinished call is tracked separately and is not counted in those result-based totals until you choose a result. Answer rate is answered / dials; booking rate is booked / answered. By default, Not Interested, Booked, and Call Later count as answered; Wrong Number does not assume someone answered. These definitions live in `settings.json` under `dialer.results`.

One account can have only one unfinished call at a time. Reloading the page shows the unfinished call panel; it does not dial again or resume auto-dial automatically. Choose its result or hang up before starting another call. Results are never overwritten. A double-click or retried save does not duplicate a result. Ignore a mistaken result to exclude it from totals; its history stays saved. The server also supports a replacement result for the same attempt after the previous result is ignored.

When Twilio is missing, the normal app shows a plain calling list and hides all Call / auto-dial buttons. Results selected without an active browser call are bookkeeping, not dials. Bookkeeping saved from Leads likewise never counts as a dial. A failed connection before Twilio dispatches a call is recorded as bookkeeping if you manually choose a result.

# Twilio is prepared, but leave it disabled for now

The official Voice JavaScript SDK is bundled locally and is loaded only when real calling is enabled and you start a call. The browser receives a short-lived token and sends the lead ID plus the call-attempt ID. It never supplies the destination number. The server selects the stored lead number, checks account ownership and call eligibility, and validates Twilio webhook signatures. With Automatic No Answer enabled, only a confirmed no-answer status can save an automatic result. The active browser advances after seeing that saved result if auto-dial is running. Duplicate notifications cannot add duplicate results.

No account was created and no real calls were placed during implementation or testing. Signed webhook and token tests used dummy credentials and no external Twilio requests. Live audio, real telephone delivery, mobile browser behaviour, and actual caller-ID provisioning still need testing when you choose to activate calling.

Later, we will walk through Twilio signup, buying a number, creating an API key and TwiML application, and checking calling requirements and business-number registration one screen at a time. We can finish Analytics and Scripts before doing that or setting up Render.

Use Settings > Twilio connection, or the Connect Twilio shortcut in Dialer, for the new administrator setup form. See TWILIO-SETUP.md. Credentials are encrypted, caller-number sharing is explicit, and saving/verifying leaves calling off until you enable it. Server environment settings remain an optional managed alternative; the following variable names describe that alternative:

- `TWILIO_ENABLED`: leave at `0` now. Real calling needs an explicit `1` plus every required setting.
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_API_KEY_SID`, `TWILIO_API_KEY_SECRET`: enter your own values privately later.
- `TWILIO_TWIML_APP_SID`: your outgoing Voice application's SID.
- `PUBLIC_BASE_URL`: the app's public HTTPS address. The voice webhook is `/api/twilio/voice` using POST. Number status callbacks use `/api/twilio/status` automatically. The local app alone does not provide a public webhook address.
- `TWILIO_NUMBERS_JSON`: a list of number objects with `number` in +1 format. Optional `users` restricts a number to specific account email addresses; omit it only if all invited accounts should share that number. Optional `latitude` and `longitude` identify the approximate calling region. These numbers must be purchased/authorized on the configured Twilio account; the app does not provision them.

Caller selection matches the lead's area code first and rotates between equal numbers. If you configure destination `area_code_centres` in `settings.json` and caller region coordinates, the server chooses the geographically nearest configured region. When that location information is unavailable, it rotates through the allowed pool and labels the fallback; it does not guess that numeric area-code differences represent distance.

# If a call fails

An error pauses auto-dial. Use Hang up, then choose a result yourself. If Twilio cannot confirm that a call ended, the app keeps the attempt unfinished and prevents the next call; retry hang-up or resolve it in Twilio before continuing. Busy, failed, cancelled, answered/voicemail and manually stopped calls do not receive an automatic No Answer. Only the provider's explicit no-answer status can do so.

Free practice data is separate from real data and resets when you restart practice mode. Backups remain available from the administrator account.

Twilio SDK documentation: https://www.twilio.com/docs/voice/sdks/javascript

Webhook signature validation: https://www.twilio.com/docs/usage/security

# Call helper and practice testing

Automatic No Answer is on by default and can be switched off beside your goal/time-zone settings. The provider ring timeout is 25 seconds; the app waits for its confirmation rather than guessing from a browser timer. Automatic results are labelled provider-confirmed in history. Reloading the page never resumes automatic calling by itself.

In practice, Simulate no answer saves a simulated confirmed result and advances if auto-dial is running. Call helper has Enable practice assistant and a box for fictional prospect speech. Its transcript and suggested reply update without capturing audio. Suggestions use common objection phrases and your selected saved objection scripts. They never speak or change outcomes themselves.

Live transcription remains disabled until TWILIO_TRANSCRIPTION_ENABLED=1 and calling is configured. On a connected call, confirm participant permission in Call helper before starting it. Both audio directions are processed by the provider; signed final utterances arrive with agent/prospect labels. Stop transcription stops it for that call. The app refreshes every two seconds plus provider/network latency.

Transcript text is private to the owning account, retained up to 30 days in the active database, and can be exported/deleted from the business timeline. Deletion prevents late callbacks from recreating text. Older backups can retain it. The app saves no audio files; the provider processes live audio. No real audio was transcribed in implementation tests.

See LAUNCH-GUIDE.md for the new workflow, remaining live checks, and private service configuration.

## Save and find audio recordings

During a connected real call, the bottom call bar has Record call. Confirm participant permission and Twilio recording/storage charges to start. The button changes to Recording · Stop; audio starts at that moment, not retroactively. Stopping or hanging up lets Twilio process the recording. Open the prominent Saved recordings button in Dialer, then View recording to play, download or delete audio. Call history & recordings shows all attempts, including those with no audio. Audio is held by Twilio until deleted; history and transcript text remain private to the account.
