# Connect Twilio in Brassline

Open Settings and find Twilio connection. Dialer also has a Connect Twilio shortcut. Every member manages their own private connection. Calls and live transcripts are billed directly to the Twilio account that member connects. Members never inherit another member’s credentials or the server’s Twilio account. Practice shows setup information but does not save credentials.

## When you are ready

You do not need a Twilio account to use the app's other sections or practice calls. No account, phone number or paid service was purchased/enabled in this update.

1. Obtain your Twilio account and an existing US number that supports Voice. The app does not purchase numbers.
2. In the Twilio console, create an API key and a TwiML application for browser calling.
3. Enter Account SID, Auth token, API key SID/secret, TwiML app SID and your public root HTTPS app address in Brassline. Enter credentials privately in the app, never in chat.
4. Add your existing caller number in +1 format. Numbers are available only to your own Brassline account. Optional region coordinates support the previously configured nearest-region routing.
5. Enter your current Brassline password and Save connection. Saving leaves calling off. Blank credential fields preserve already saved values.
6. Copy the displayed Voice webhook URL into the TwiML app's Voice request URL in Twilio and choose POST.
7. Enter your current password and Verify connection. This performs read-only provider requests to check credentials/API-key access, account ownership, Voice capability and the TwiML app webhook configuration. It does not place a test call or prove end-to-end telephone delivery.
8. When ready, acknowledge provider charges and choose Enable real calling. Calling is a separate action from saving and verifying. The enabled connection is used immediately; you can then refresh Dialer.

Your local 127.0.0.1 address cannot receive Twilio's callbacks. Use a public HTTPS deployment before testing real delivery. Provider microphone/audio/mobile behavior still needs a live test. [Twilio browser-calling setup](https://www.twilio.com/docs/voice/sdks/javascript/get-started)

## Saved credentials and recovery

Credentials are encrypted in the SQLite database and never populated back into the browser form. By default, the separate encryption key is integration.key beside your database. Keep it private and retain it with your private restore materials. A database backup alone cannot decrypt a saved connection after losing that key. Each member can clear and re-enter their own saved setup if needed.

For hosted deployments, an optional INTEGRATION_ENCRYPTION_KEY server environment value can supply the Fernet key instead of a file. Configure it before saving the connection, and preserve it privately across restores. Never commit keys, .env files, databases or backups to GitHub.

Legacy Twilio settings in the server environment apply only to the administrator. Ordinary members use their own saved credentials. The public app URL is shared when configured by the host. Managed fields are labelled in the form. Change those through host settings. If TWILIO_ENABLED is managed there, use that environment control to enable/disable calling. A managed connection must be disabled before editing it through the app.

Your own unfinished calls must be resolved before saving, verifying, enabling/disabling or disconnecting the integration. Disconnect saved connection removes the app's saved setup; it does not cancel your provider account or release its numbers.

## Design update and verification

Brassline now uses a custom interlocking connection mark and the existing brass/charcoal palette. Active-navigation end arrows were removed; Dialer and invitation icons no longer use arrow symbols.

Passed: encrypted storage, member isolation/current-password protection, secret readback and validation-error protection, public HTTPS validation, private number access, verify-before-enable, explicit charge acknowledgement, disable/disconnect, form controls, webhook copying, and existing dialer/signed-callback regression tests. Provider verification was mocked: no live Twilio requests or calls occurred in these tests. Full browser visual and live provider checks remain pending.

Restart start.cmd and hard-refresh with Ctrl+F5 to load the new setup routes and branding.

Separate-account verification: mocked two-account tests cover independent settings and activation, encrypted storage, account-specific browser tokens, signed callback rejection across accounts, correct hang-up credentials, and independent disconnect. No actual provider charges or calls were made.
