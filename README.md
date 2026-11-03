# Pixel Gemini — Telegram bot + Google OAuth + isolated device test profiles

A single Flask web service hosts the website, Google OAuth callback, and Telegram webhook. It is designed to minimize Render services: one **Free Web Service**, no Background Worker and no database.

## Render deployment

1. Create a Render Web Service from this repository on branch `main`.
2. Use the included `render.yaml` Blueprint settings, or set:
   - Build: `pip install -r requirements.txt`
   - Start: `gunicorn app:app --workers 1 --threads 4 --timeout 120`
3. In Render → Environment, set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `TELEGRAM_BOT_TOKEN`, `FLASK_SECRET_KEY`, and `TELEGRAM_WEBHOOK_SECRET`.
4. In Google Cloud Console, add the exact `GOOGLE_REDIRECT_URI` as an Authorized redirect URI.
5. Set Telegram's webhook to `https://YOUR-SERVICE.onrender.com/telegram/webhook` with the configured secret token.
6. Test the website and Telegram commands below.

### Local run

```bash
pip install -r requirements.txt
python app.py
```

Set `PORT` if the default `10000` is busy. For a local OAuth callback, Google
still expects the exact `GOOGLE_REDIRECT_URI` registered in the OAuth client;
use an HTTPS tunnel or a `http://localhost` callback registered for testing.

### Tests

The suite uses stdlib `unittest` and requires no extra test dependencies.

```bash
python -m unittest discover -s tests -v
```

It covers the account portal, missing OAuth configuration, website and
Telegram callbacks, forged/expired OAuth state, unverified emails, webhook
authentication, and every bot command.

## Telegram commands

- `/start` — show command help
- `/login` — connect Google through the official OAuth consent flow
- `/device` — create a synthetic Pixel 10 Pro / Android 16 test profile
- `/status` — show linked email and current synthetic test-profile status
- `/check_offer` — explain the limitation and point to Google's official Google One page
- `/get_link` — return the official Google One URL
- `/logout` — clear the in-memory Google account link

## Synthetic device profiles

`device_simulator.py` creates a test-only profile containing a simulated model, Android version, synthetic IMEI-like label, synthetic Android-ID-like label, session UUID, and test user-agent string. Values are explicitly prefixed/labeled as test data and are intended only for local mock services, unit tests, and defensive fingerprint-detection experiments in systems you control. They are not valid device identifiers and must not be sent to Google or used to impersonate a real device.

The bot does not automate Google One sign-in, inspect private Google pages, alter real browser/device fingerprints for requests to Google, or determine promotion eligibility. Use the official Google One page to check offers manually: https://one.google.com/.

## Free-tier limitations

- Render Free Web Services can spin down when idle; the first webhook or page request after inactivity may be delayed.
- This configuration uses one Gunicorn worker because account and test-profile state is held in process memory. Restarts or sleep/wake can clear it.
- No database is used, and Google access/refresh tokens are not persisted.

## Security

- Google login uses official OAuth 2.0 / OpenID Connect with only `openid`, `email`, and `profile` scopes.
- The bot never asks for Google passwords or one-time codes.
- Telegram OAuth state is signed and expires after 10 minutes; webhook requests are checked using Telegram's secret-token header.
- Only the verified email is retained in process memory.
- If OAuth environment variables are missing, `/health` stays available and `/login` returns `503` with a configuration hint instead of crashing.
