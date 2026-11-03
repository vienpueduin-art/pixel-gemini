import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ["FLASK_SECRET_KEY"] = "test-secret"
os.environ["GOOGLE_CLIENT_ID"] = "test-client"
os.environ["GOOGLE_CLIENT_SECRET"] = "test-client-secret"
os.environ["GOOGLE_REDIRECT_URI"] = "http://localhost/oauth2callback"
os.environ["TELEGRAM_BOT_TOKEN"] = "test-token"
os.environ["TELEGRAM_WEBHOOK_SECRET"] = "test-webhook"

from itsdangerous import SignatureExpired

import app as app_module
import time
from device_simulator import create_test_profile

WEBHOOK_HEADERS = {"X-Telegram-Bot-Api-Secret-Token": "test-webhook"}


class FakeFlow:
    def __init__(self, token="token"):
        self.credentials = SimpleNamespace(token=token)
        self.authorization_response = None

    def fetch_token(self, authorization_response):
        self.authorization_response = authorization_response


def fake_userinfo(email="user@example.com", verified=True):
    response = MagicMock()
    response.json.return_value = {
        "email": email,
        "email_verified": verified,
        "name": "Test User",
        "picture": "",
    }
    response.raise_for_status.return_value = None
    return response


def telegram_update(text, chat_id=99, chat_type="private"):
    return {
        "message": {
            "text": text,
            "chat": {"id": chat_id, "type": chat_type},
        }
    }


class AppTestCase(unittest.TestCase):
    def setUp(self):
        app_module._accounts.clear()
        app_module._devices.clear()
        app_module.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = app_module.app.test_client()
        self.saved = {
            "CLIENT_ID": app_module.CLIENT_ID,
            "CLIENT_SECRET": app_module.CLIENT_SECRET,
            "REDIRECT_URI": app_module.REDIRECT_URI,
            "BOT_TOKEN": app_module.BOT_TOKEN,
            "WEBHOOK_SECRET": app_module.WEBHOOK_SECRET,
            "secret_key": app_module.app.secret_key,
        }

    def tearDown(self):
        app_module.CLIENT_ID = self.saved["CLIENT_ID"]
        app_module.CLIENT_SECRET = self.saved["CLIENT_SECRET"]
        app_module.REDIRECT_URI = self.saved["REDIRECT_URI"]
        app_module.BOT_TOKEN = self.saved["BOT_TOKEN"]
        app_module.WEBHOOK_SECRET = self.saved["WEBHOOK_SECRET"]
        app_module.app.secret_key = self.saved["secret_key"]
        app_module._accounts.clear()
        app_module._devices.clear()

    def post(self, text, chat_id=99, chat_type="private", headers=WEBHOOK_HEADERS):
        return self.client.post(
            "/telegram/webhook",
            json=telegram_update(text, chat_id, chat_type),
            headers=headers,
        )

    def clear_oauth_config(self):
        app_module.CLIENT_ID = ""
        app_module.CLIENT_SECRET = ""
        app_module.REDIRECT_URI = ""

    # ── Health, portal, login ────────────────────────────────────────────────

    def test_health_reports_ready(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["oauth_configured"])
        self.assertTrue(body["telegram_configured"])

    def test_health_without_oauth_config_stays_up(self):
        self.clear_oauth_config()
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["oauth_configured"])

    def test_index_renders_portal(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Tiếp tục với Google", response.get_data(as_text=True))

    def test_index_without_secret_key_still_renders(self):
        self.clear_oauth_config()
        app_module.app.secret_key = ""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Chưa cấu hình OAuth", response.get_data(as_text=True)
        )

    def test_login_without_oauth_config_returns_503(self):
        self.clear_oauth_config()
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 503)
        self.assertIn(
            "Chưa cấu hình OAuth", response.get_data(as_text=True)
        )

    def test_login_redirects_to_google_and_stores_state(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 302)
        self.assertIn("accounts.google.com", response.headers["Location"])
        with self.client.session_transaction() as sess:
            self.assertIn("oauth_state", sess)

    # ── OAuth callback ───────────────────────────────────────────────────────

    def test_website_callback_links_account(self):
        with self.client.session_transaction() as sess:
            sess["oauth_state"] = "web-state"
            sess["oauth_state_issued_at"] = time.time()
        with patch.object(app_module, "make_flow", return_value=FakeFlow()), \
                patch.object(app_module.requests, "get",
                             return_value=fake_userinfo()):
            response = self.client.get(
                "/oauth2callback?state=web-state&code=abc"
            )
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as sess:
            self.assertEqual(sess["account"]["email"], "user@example.com")

    def test_invalid_state_returns_400(self):
        response = self.client.get("/oauth2callback?state=forged&code=abc")
        self.assertEqual(response.status_code, 400)

    def test_expired_signed_state_returns_400(self):
        with patch.object(
            app_module._serializer, "loads",
            side_effect=SignatureExpired("expired"),
        ):
            response = self.client.get(
                "/oauth2callback?state=old&code=abc"
            )
        self.assertEqual(response.status_code, 400)

    def test_non_ascii_forged_state_returns_400(self):
        with self.client.session_transaction() as sess:
            sess["oauth_state"] = "expected-state"
        response = self.client.get(
            "/oauth2callback?state=%C3%A9&code=abc"
        )
        self.assertEqual(response.status_code, 400)

    def test_expired_website_state_returns_400(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as sess:
            state = sess["oauth_state"]
            sess["oauth_state_issued_at"] = time.time() - 601
        with patch.object(app_module, "make_flow", return_value=FakeFlow()), \
                patch.object(app_module.requests, "get",
                             return_value=fake_userinfo()):
            response = self.client.get(
                f"/oauth2callback?state={state}&code=abc"
            )
        self.assertEqual(response.status_code, 400)

    def test_website_unverified_email_is_rejected(self):
        with self.client.session_transaction() as sess:
            sess["oauth_state"] = "web-state"
            sess["oauth_state_issued_at"] = time.time()
        with patch.object(app_module, "make_flow", return_value=FakeFlow()), \
                patch.object(app_module.requests, "get",
                             return_value=fake_userinfo(verified=False)):
            response = self.client.get(
                "/oauth2callback?state=web-state&code=abc"
            )
        self.assertEqual(response.status_code, 400)
        with self.client.session_transaction() as sess:
            self.assertNotIn("account", sess)

    def test_unverified_email_is_rejected(self):
        state = app_module._serializer.dumps({"chat_id": 99})
        with patch.object(app_module, "make_flow", return_value=FakeFlow()), \
                patch.object(app_module.requests, "get",
                             return_value=fake_userinfo(verified=False)), \
                patch.object(app_module, "telegram_send"):
            response = self.client.get(
                f"/oauth2callback?state={state}&code=abc"
            )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(99, app_module._accounts)

    def test_telegram_signed_state_links_chat(self):
        state = app_module._serializer.dumps({"chat_id": 99})
        with patch.object(app_module, "make_flow", return_value=FakeFlow()), \
                patch.object(app_module.requests, "get",
                             return_value=fake_userinfo()), \
                patch.object(app_module, "telegram_send") as send:
            response = self.client.get(
                f"/oauth2callback?state={state}&code=abc"
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(app_module._accounts[99]["email"], "user@example.com")
        send.assert_called_once()

    # ── Telegram webhook ─────────────────────────────────────────────────────

    def test_webhook_rejects_missing_or_wrong_secret(self):
        self.assertEqual(self.post("/start", headers={}).status_code, 403)
        self.assertEqual(
            self.post(
                "/start",
                headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            ).status_code,
            403,
        )

    def test_webhook_rejects_unconfigured_secret(self):
        app_module.WEBHOOK_SECRET = ""
        self.assertEqual(self.post("/start", headers={}).status_code, 403)

    def test_start_sends_help(self):
        with patch.object(app_module, "telegram_send") as send:
            self.assertEqual(self.post("/start").status_code, 200)
        self.assertIn("/login", send.call_args.args[1])

    def test_login_in_group_is_refused(self):
        with patch.object(app_module, "telegram_send") as send:
            self.post("/login", chat_type="group")
        self.assertIn("chat riêng", send.call_args.args[1])

    def test_login_in_private_sends_oauth_url(self):
        with patch.object(app_module, "telegram_send") as send:
            self.post("/login")
        self.assertIn("accounts.google.com", send.call_args.args[1])

    def test_device_creates_profile(self):
        with patch.object(app_module, "telegram_send") as send:
            self.post("/device")
        self.assertIn("Pixel 10 Pro", send.call_args.args[1])
        self.assertIn(99, app_module._devices)

    def test_status_reports_account_and_device(self):
        app_module._accounts[99] = {"email": "user@example.com"}
        app_module._devices[99] = create_test_profile().to_dict()
        with patch.object(app_module, "telegram_send") as send:
            self.post("/status")
        message = send.call_args.args[1]
        self.assertIn("user@example.com", message)
        self.assertIn("Pixel 10 Pro", message)

    def test_status_after_restart_reports_not_connected(self):
        with patch.object(app_module, "telegram_send") as send:
            self.assertEqual(self.post("/status").status_code, 200)
        message = send.call_args.args[1]
        self.assertIn("chưa kết nối", message)
        self.assertIn("chưa tạo", message)

    def test_check_offer_requires_login(self):
        with patch.object(app_module, "telegram_send") as send:
            self.post("/check_offer")
        self.assertIn("/login", send.call_args.args[1])

    def test_check_offer_with_account_returns_official_url(self):
        app_module._accounts[99] = {"email": "user@example.com"}
        with patch.object(app_module, "telegram_send") as send:
            self.post("/check_offer")
        self.assertIn("https://one.google.com/", send.call_args.args[1])

    def test_logout_removes_account(self):
        app_module._accounts[99] = {"email": "user@example.com"}
        with patch.object(app_module, "telegram_send"):
            self.post("/logout")
        self.assertNotIn(99, app_module._accounts)

    def test_get_link_returns_official_url(self):
        with patch.object(app_module, "telegram_send") as send:
            self.post("/get_link")
        self.assertIn("https://one.google.com/", send.call_args.args[1])

    def test_telegram_failure_does_not_fail_webhook(self):
        app_module._accounts[99] = {"email": "user@example.com"}
        with patch.object(
            app_module.requests, "post",
            side_effect=app_module.requests.RequestException("down"),
        ):
            self.assertEqual(self.post("/status").status_code, 200)


if __name__ == "__main__":
    unittest.main()
