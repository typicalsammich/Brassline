# Administrator-funded services

Restart start.cmd, then refresh the browser with Ctrl+F5. New pages are Research and Shared services. Existing accounts, personal Twilio connections, leads and history are retained. All new funded features start disabled, with zero allowances for every account, including the administrator. No provider accounts, purchases, deployments or paid requests were made by this update.

## Configure funding

1. Open Shared services as the administrator. Enter provider credentials privately in the form, never in chat. Credentials are encrypted in the database and never populated back into the form. Keep the separate integration.key with private restore materials.
2. For research, connect SerpAPI for listing and general web discovery, or Firecrawl for general web discovery. If both are configured, SerpAPI is used. Optional Apify enrichment retrieves up to three public Instagram posts for a matched profile candidate. You do not need all three providers to start.
3. For calling and native Twilio transcription, first verify and explicitly enable the administrator's existing Twilio connection under Settings. No numbers are purchased by the app. Shared calling uses your authorized numbers and your Twilio account. Members cannot use your number pool until you give them an allowance and they choose administrator-funded calling.
4. Connect your OpenAI API key for live AI coaching. The model is gpt-5.4-mini, with no reasoning, a 120-token output limit, bounded recent conversation and one short suggested response. Add your offer facts, approved pricing and delivery rules. A ChatGPT subscription does not configure this API connection.
5. Set whole-team monthly limits, per-call duration, maximum suggestions per call, and maximum businesses per research run. Acknowledge provider charges before enabling a funded feature.
6. Give each person their monthly limits under Per-account monthly limits, then allow their funded access. Give yourself an allowance too. Zero means no funded use. Limits reset automatically at the start of each UTC calendar month; prior usage remains recorded. Lowering a limit does not erase consumed usage.

For emergency control, turn off funded features globally or pause an account. This blocks new funded requests. Previously reserved calls/transcription may finish within their original time limit. Change or disconnect administrator Twilio credentials only after every call using those credentials is finished. Calls that have already connected are not terminated merely by pausing future access.

Members choose who pays under Shared services. Personal Twilio billing remains separate. If they chose administrator-funded calling and it becomes unavailable, calling is disabled instead of silently switching to personal billing. Personal calls cannot use native administrator-funded Twilio transcription: Twilio bills native transcription to the account that owns the call. Deepgram is now a separate option: the administrator funds Deepgram while each caller pays their own Twilio calling and streaming. See DEEPGRAM-SETUP.md.

## What the limits mean

- Research units: conservatively reserved search-result slots, up to three website pages per audit, and up to three social-post results per enrichment. Different providers charge differently, so units are an application allowance, not a price quote. Recent public search results are cached for 24 hours and can be reused across accounts without another paid search. No private notes, transcripts, scripts or user lead records go into that shared cache.
- Calling minute-equivalents: the app reserves both call legs plus one minute per leg for ringing/rounding. A ten-minute connected call reserves 22 units. If only six units remain, the app can allow at most two connected minutes. Twilio receives the connected-call limit in its Dial timeLimit attribute.
- Transcription minutes: native Twilio reserves the funded call's maximum connected duration. Deepgram reserves two audio minutes per permitted call minute, bounded by the administrator per-session limit and the user/team remaining allowance. Early endings are not refunded.
- AI suggestions: one reserved request per new conversation context, with a per-call cap and monthly user/team caps. Failed or interrupted requests remain reserved because provider charges may already have occurred. Repeated identical partial/final context is deduplicated. Updates are coalesced, with at most one active AI request per call and three concurrent AI requests on the server.

Reservations are conservative and are not refunded automatically. They are not exact provider invoices or a guaranteed dollar spending cap. Use provider-side billing controls and review statements too. These controls cannot prevent charges made directly through someone else's provider credentials outside Brassline.

## Research workflow

Open Research, choose any US location and industry using autocomplete, then choose a bounded number of businesses. The hosted app runs discovery and research without a local Chrome worker. The original Scraper page remains available for local Google-worker searches.

Research combines listing results (when SerpAPI is connected), social-first discovery, a name/location website and owner search, public profile matching, light website checks, public business contacts, website-published owner/founder candidates, and optional public Instagram post evidence. Reports show source links, excerpts, uncertainty and reasons to investigate. Select useful prospects and import them into your private leads; duplicate names, phone numbers, place IDs and source URLs are skipped. Review and confirm qualification evidence in Leads before calling.

A missing website in a bounded search stays unknown, rather than automatically becoming confirmed absence. Website checks inspect HTML and limited About/Contact pages; they do not render mobile layouts, submit forms, prove lost revenue or establish an owner's buying authority. Search/profile matches can be wrong. Recent post dates are candidates until the profile's business identity is confirmed. No owner phone is guessed.

Two research jobs may run concurrently, with one unfinished job per account. Stop takes effect after the current provider request. Server restarts mark unfinished jobs interrupted rather than retrying paid requests. Completed partial reports remain available. Provider errors and empty results do not trigger automatic paid retries.

## Live transcription and AI coaching

Use administrator-funded calling, then open Call helper on a connected call. Confirm participant permission before transcription starts. Enable AI separately, acknowledging that transcript text is sent to the administrator's AI provider. The app never starts AI automatically just because you enabled transcription.

Twilio partial prospect speech can trigger a short provisional suggestion before the sentence finishes. Text streams to the browser through authenticated server-sent events, rather than waiting for the old two-second assistant poll. The helper shows measured time to first generated text. Provider/network/model delays still exist; partial speech can change meaning. Actual live latency has not yet been measured. Script-based responses remain a fallback; refusal and stop-contact wording produces a respectful response without another AI charge. The AI never speaks, sets a result or marks Do Not Contact by itself.

Stop AI prevents future suggestions. Stop transcription also disables AI. Deleting transcript text removes stored AI suggestions and partial text and prevents late callbacks from recreating them. Final transcripts and AI text are removed from the active database after 30 days. Partial text expires after one hour. Existing private backups can still contain older text. No audio files are stored by Brassline.

## Resend and password recovery

People already choose their own password when creating an account, and can change it in Settings. Email delivery is only needed to deliver automated Forgot password and verification links.

Resend's current free transactional plan allows 3,000 emails per month, with at most 100 per day. Brassline conservatively enforces those sending caps too. You must own a domain you can configure; buying the domain is a separate annual cost. The sending domain can differ from the app address, and an existing business domain can be used. Resend's testing sender is not a general sender for arbitrary members.

1. Create your Resend account and add a sending domain under Domains.
2. Copy the DNS records Resend displays into the domain registrar's DNS settings. Wait until Resend reports the domain verified. Use the exact displayed records rather than invented examples.
3. Create a Resend API key with access to send emails and read domain verification status. Put it into Shared services. Set the sender to an address on the verified domain, for example accounts@your-domain.lat. A paid email inbox is not required merely to send these account emails.
4. Save with email delivery disabled. Enter your current Brassline password and choose Verify saved Resend domain. This checks domain status without sending an email.
5. Save again with email delivery enabled. The hosted app must have a public root HTTPS URL; Render supplies that after deployment. Test a verification email and inbox receipt yourself before relying on recovery.

Reset links expire after 30 minutes, work once, and invalidate existing sessions and other reset links when used. Responses do not reveal whether an entered email belongs to an account. Request throttling protects against repeated sends. If delivery is unavailable, the administrator can still generate a private single-use recovery link under Team invitations and copy/share it manually. The administrator cannot read a member's chosen password.

Environment-configured email settings still take precedence over saved settings. Keep the Render workspace on Hobby; the existing deployment uses Starter compute plus its 1 GB persistent disk. Do not enable paid providers before you are ready.

## Verification and remaining setup

Passed mocked backend checks: administrator authorization/current password, encrypted secrets/no secret readback, atomic user/team quotas, sponsored account token/call routing, Twilio-enforced call length, native funded transcription and quota rejection, AI consent and streamed results, repeated-event protection, private sourced research and duplicate imports, verified-domain gating, password-reset consumption and session invalidation. DOM interaction checks cover provider/allowance forms, member access, research selection/import and streaming suggestions without stealing focus.

No live provider calls, searches, AI requests, transcription sessions, email delivery or deployment occurred. Remaining before launch: configure real credentials privately, test provider schemas and live behavior, verify desktop/mobile layout, deploy to Render, confirm restart persistence and off-server backup recovery. Research job execution and coaching currently use in-process threads with bounded concurrency; larger teams may need stronger compute or a durable external job worker.

Sources: [OpenAI streaming](https://developers.openai.com/api/docs/guides/streaming-responses), [Firecrawl search](https://docs.firecrawl.dev/api-reference/endpoint/search), [Apify API](https://docs.apify.com/api/v2/actor-run-sync-get-dataset-items-post), [Twilio Dial limits](https://www.twilio.com/docs/voice/twiml/dial), [Resend pricing](https://resend.com/pricing), [Resend verified domains](https://resend.com/docs/dashboard/domains/introduction).


### Six-digit password recovery
Forgot password now opens a dialog to request a six-digit email code. Codes expire after 10 minutes; five incorrect guesses invalidate the code. A new request replaces prior codes. Correct verification consumes the code and grants five minutes to enter and confirm a password of at least 12 characters. Saving the password revokes existing sessions and outstanding recovery permissions. Request and verification rate limits apply; responses do not disclose whether an email has an account. Resend delivery must still be enabled and tested after hosting. Administrator-created recovery links remain available as a separate manual fallback; email-address verification still uses a link. Mocked recovery and UI checks passed; live email delivery remains pending.
