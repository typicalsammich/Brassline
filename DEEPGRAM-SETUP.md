# Deepgram live transcription

Deepgram is now an optional transcription provider in Brassline. The administrator funds the Deepgram account, while each caller can keep their own Twilio account and number. Their Twilio account pays for the phone call and Media Streams. This integration does not record audio files or add a recording player. Final transcript text is saved privately, exportable, and subject to the existing 30-day active-database retention.

Restart start.cmd to install the added websockets dependency and load the routes, then refresh the browser with Ctrl+F5. The existing setup remains disabled by default. Native Twilio transcription is still selectable; an existing active session is stopped through its original provider even if the administrator changes the provider setting.

## Setup tomorrow

1. Deploy the prepared app to Render with the Starter server and 1 GB persistent disk, keeping the workspace on Hobby. Keep one server process. Confirm accounts and leads survive a restart before inviting people.
2. Create your Deepgram account and API key. Check whether your account has the advertised $200 starter credit. This is initial credit, not a recurring free allowance. Enter the key privately in Shared services; it is encrypted and is never returned to members or populated back into the form.
3. Choose Deepgram under Live transcription provider. Set a team-wide transcription allowance and a maximum transcription duration per call. Enable funded transcription only when you are ready and acknowledge provider charges. You do not need an administrator Twilio number merely to fund Deepgram transcription.
4. Give each member a transcription allowance and allow their funded access. Give yourself an allowance too. Deepgram reserves two audio-minute units for each call minute, one per speaker. A ten-minute session reserves 20 units; a member with only two units remaining can start at most one minute. Reservations are conservative and are not refunded automatically.
5. Each caller configures their own verified Twilio account, Voice number, API key and TwiML application in Settings, then chooses My own Twilio account under Shared services. If you separately enable administrator-funded calling, those calls and streaming charges are paid by your Twilio account instead.
6. On a connected call, open Call helper. Confirm participant permission and acknowledge that Twilio audio streaming uses the balance of the account paying for the call. Select Permission confirmed → start transcription.
7. Enable AI coaching separately if you have configured your OpenAI API connection, offer facts and coaching allowances. Live partial/final transcript text feeds the existing streamed coaching interface.

The app must be reachable at its configured root HTTPS address; Twilio opens a secure WebSocket to that host. Localhost is not reachable by Twilio. The stream is created after permission on the existing browser call, rather than changing the call's TwiML or interrupting the conversation. It receives both tracks. Parent browser-call inbound audio is labelled agent, and the child phone-call audio in the outbound track is labelled prospect.

## Stops, limits and failures

- Stop transcription disables coaching, closes audio forwarding, and requests Twilio to stop its stream. The phone call can continue without transcription.
- The session has both a wall-clock deadline and a per-speaker byte-duration limit. A paused or stopped session cannot restart during the same call; make a new call for a fresh session. Exceeding an account/team quota prevents stream creation before a provider request is made.
- Session paths have random hashed tokens, and the WebSocket handshake validates the calling account's Twilio signature. The stream's Account SID, Call SID, Stream SID, session ID and audio format must also match. Replayed connections and cross-call audio are rejected before connecting to Deepgram.
- Up to three transcription bridges run at a time on this initial server. Each uses two bounded mono Deepgram connections. Audio is forwarded as 8 kHz mu-law bytes without writing recordings to disk. Known tracks avoid a paid diarization add-on. Model-improvement use is opted out with mip_opt_out=true.
- Partial text updates the assistant; only final text is kept as transcript segments. Duplicate audio chunks and repeated final results are ignored. Deleted transcript text cannot be recreated by late results.
- A failed provider connection does not automatically reconnect and spend more credit. An interrupted or missing stream handshake is cleaned up through a Twilio stop request. If the app cannot confirm Twilio stopped, it displays a message to retry Stop transcription or hang up; this matters because Twilio streaming can continue to incur charges while its stream remains active.
- Server restarts do not resume transcription automatically. Finish/hang up any interrupted call before starting another. Live provider/network behavior, speech accuracy and end-to-end latency still need testing.

## Initial and ongoing cost estimates

Prices checked October 14, 2026, in USD, before tax and usage overages:

| Item | Initial budget |
|---|---:|
| Render Starter + 1 GB disk | $7.25 for a month |
| Example non-premium .lat domain at Namecheap | About $2 for year one, including its listed ICANN fee; roughly $41.18 to renew |
| OpenAI API prepaid credit for coaching | $5 minimum purchase; credit is consumed by API usage |
| Deepgram | $0 initially if your account receives and retains the advertised $200 starter credit |
| Resend account emails | $0 within 3,000/month and 100/day, after domain verification |
| Initial research testing | SerpAPI free tier lists 250 searches/month; optional Apify free tier includes $5 usage/month |

That gives an illustrative $14.25 first-month/startup budget for hosting, sending domain and AI credit, assuming eligible Deepgram credit and research within free allowances. It excludes any personal Twilio account. If you want to make your own calls, additionally budget around $20 for Twilio's initial prepaid balance (confirm the required amount at checkout), giving roughly $34.25 before taxes. Number rental and call/stream charges are deducted from that Twilio balance rather than being an additional full deposit. Each teammate pays for their own Twilio setup if they select personal calling.

After Deepgram starter credit is used, current English Nova-3 streaming is $0.0048/audio minute on promotional pricing, or $0.0077 at the listed regular rate. With two tracks, this is about $9.60 or $15.40 per 1,000 call minutes, paid by the administrator. Twilio Media Streams adds about $4.40 per 1,000 call minutes to the calling account. Most US browser-to-phone calling is approximately $18 per 1,000 minutes across the two call legs, plus a $1.15/month local number. AI coaching, research, taxes, provider rounding and hosting overages are separate usage costs. Provider promotions and prices can change.

Sources: [Deepgram pricing](https://deepgram.com/pricing), [Deepgram streaming API](https://developers.deepgram.com/reference/speech-to-text/listen-streaming), [Deepgram model-improvement update](https://developers.deepgram.com/changelog/2026/3/5), [Twilio Streams API](https://www.twilio.com/docs/voice/api/stream-resource), [Twilio pricing](https://www.twilio.com/en-us/voice/pricing/us), [Twilio account funding](https://www.twilio.com/en-us/blog/getting-the-most-out-of-your-voice-project), [Render pricing](https://render.com/pricing), [Namecheap .lat](https://www.namecheap.com/domains/registration/gtld/lat/), [OpenAI prepaid billing](https://help.openai.com/en/articles/8264644-setting-up-and-managing-prepaid-api-billing), [Resend pricing](https://resend.com/pricing), [SerpAPI pricing](https://serpapi.com/pricing), [Apify pricing](https://apify.com/pricing).

## Verification

Passed: real ASGI HTTP/WebSocket tests with mocked providers for personal Twilio versus administrator Deepgram billing, encrypted secret isolation, consent/streaming acknowledgment, channel-minute reservation and quota rejection, signature/call/session/replay checks, dual-track forwarding and speaker labels, private saved transcripts, duplicate suppression, deletion, stopping without hanging up, audio-duration caps and wall-time expiry. DOM checks cover the provider selector, saved-key clearing and call-helper streaming acknowledgment. Existing shared-service and account/Twilio workflows remain covered.

No live Twilio calls, Deepgram requests, transcription charges, AI requests, emails or deployment were performed. Render deployment, real provider permissions/schema checks, inbox receipt, audio accuracy/latency and desktop/mobile visual testing remain pending.
