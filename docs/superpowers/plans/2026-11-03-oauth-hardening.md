# OAuth Hardening and Test Coverage Plan (target main)

> Adapted from `2026-11-03-google-oauth-integration.md` because the PR base is
> now `vienpueduin-art/pixel-gemini:main`, which already ships Google OAuth.
> This plan adds the missing verification and fixes the gaps it exposes.

**Goal:** Make the existing Google OAuth + Telegram webhook implementation on
`main` testable and safe when configuration is missing or state is invalid.

**Architecture:** Existing Flask app is unchanged in shape. A stdlib
`unittest` suite exercises `/`, `/health`, `/login`, `/oauth2callback`,
`/logout`, and `/telegram/webhook`. Small fixes make those tests pass.

**Tech Stack:** Python 3.12, Flask, google-auth-oauthlib, requests,
itsdangerous, stdlib `unittest`.

**Spec:** No separate spec for this branch. The feature already exists on
`main`; this plan defines the hardening scope. The earlier local-port design is
kept in the port branch, not in this PR.

## Global Constraints

- No password or OTP collection.
- Only `openid`, `email`, `profile` scopes; only verified emails linked.
- OAuth state expires after 600 seconds.
- Webhook requires the configured secret header.
- App must start and answer `/health` with missing OAuth configuration.
- Keep `device_simulator.py` test-profile behaviour unchanged.

---

### Task 1: Regression tests for the current OAuth app

**Files:**
- Create: `tests/test_app.py`
- Create: `tests/__init__.py`

**Interfaces:**
- Consumes: `app.app`, `app.oauth_ready`, `app.make_flow`,
  `app.telegram_send`, `app._serializer`, `app._accounts`, `app._devices`.
- Produces: `unittest` coverage for missing configuration, website OAuth,
  Telegram OAuth, webhook authentication, and bot commands.

- [ ] Write `tests/test_app.py` with `AppTestCase`, fake flow/userinfo helpers,
  and tests for:
  - `/health` reports OAuth/Telegram readiness.
  - `/` renders the portal and the missing-config hint.
  - `/login` returns 503 when OAuth is unconfigured.
  - `/login` redirects to `accounts.google.com` and stores `oauth_state`.
  - Website callback links the verified email in the session.
  - Forged and expired state return 400.
  - Unverified email returns 400 and links nothing.
  - Signed Telegram state links the chat and calls `telegram_send`.
  - Webhook rejects missing/wrong secret with 403.
  - `/start`, `/login` (private and group), `/device`, `/status`, `/logout`,
    `/check_offer`, `/get_link` behave as documented.
  - Telegram API failure does not fail the webhook response.
- [ ] Run `python -m unittest discover -s tests -v` and record the failures.

### Task 2: Fix missing-config and state handling

**Files:**
- Modify: `app.py`

**Interfaces:**
- Produces: `index()` safe when `app.secret_key` is empty; `login()` returns
  `503` with the config hint; `oauth2callback()` returns `400` for invalid or
  expired state and uses `hmac.compare_digest` for website state.

- [ ] Guard session access in `index()` so `/` renders when no secret key is set.
- [ ] In `login()`, return the portal with status 503 when `oauth_ready()` is false.
- [ ] In `oauth2callback()`, reject invalid/expired state with status 400.
- [ ] Compare website OAuth state with `hmac.compare_digest`.
- [ ] Re-run `python -m unittest discover -s tests -v` until green.

### Task 3: Documentation and final verification

**Files:**
- Modify: `README.md`

- [ ] Fix the stale deployment branch reference (`main`, not the merged PR branch).
- [ ] Add a testing section with `python -m unittest discover -s tests -v`.
- [ ] Confirm setup steps and environment table still match `app.py`.
- [ ] Run the suite, `python -c "import app"`, and a secret-scan grep.
- [ ] Commit and push `feat/oauth-tests-docs`, open a PR against
  `vienpueduin-art/pixel-gemini:main`.

## Acceptance

- All tests pass.
- `/health` stays up with incomplete env.
- Missing config produces a clear 503, not a 500.
- Forged/expired OAuth state never links an account.
- README matches the actual `main` branch and test command.
