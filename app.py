"""Pixel Gemini: Flask website + Telegram webhook + Google OAuth."""
import hmac
import logging
import os
import secrets
from threading import Lock

import requests
from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from google_auth_oauthlib.flow import Flow
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from device_simulator import create_test_profile

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("pixel-gemini")
app = Flask(__name__)
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "")
# Keep the portal usable (and sessions working) even when deployment env is
# incomplete. A random fallback is process-local, so OAuth readiness still
# depends on the explicit FLASK_SECRET_KEY below.
app.secret_key = FLASK_SECRET_KEY or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SECURE=True,
                  SESSION_COOKIE_SAMESITE="Lax", PERMANENT_SESSION_LIFETIME=3600)

CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email",
          "https://www.googleapis.com/auth/userinfo.profile"]
_accounts = {}
_devices = {}
_accounts_lock = Lock()
_serializer = URLSafeTimedSerializer(FLASK_SECRET_KEY, salt="telegram-google-link-v1") if FLASK_SECRET_KEY else None
OFFER_URL = "https://one.google.com/"


def oauth_ready():
    return bool(FLASK_SECRET_KEY and CLIENT_ID and CLIENT_SECRET and REDIRECT_URI)


def make_flow(state=None):
    if not oauth_ready():
        raise RuntimeError("Google OAuth chưa được cấu hình đầy đủ trên Render.")
    config = {"web": {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": [REDIRECT_URI]}}
    return Flow.from_client_config(config, scopes=SCOPES, state=state, redirect_uri=REDIRECT_URI)


def telegram_send(chat_id, message):
    if not BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN is missing")
        return
    try:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True}, timeout=12)
        r.raise_for_status()
    except requests.RequestException:
        log.exception("Telegram sendMessage failed")


def bot_login_url(chat_id):
    if not _serializer:
        raise RuntimeError("FLASK_SECRET_KEY chưa được cấu hình.")
    flow = make_flow()
    signed_state = _serializer.dumps({"chat_id": int(chat_id)})
    url, _ = flow.authorization_url(state=signed_state, access_type="online",
        include_granted_scopes="true", prompt="select_account")
    return url


def _device_summary(profile):
    return (f"Model: {profile['model']}\nAndroid: {profile['android_version']}\n"
            f"IMEI (synthetic): {profile['imei']}\nAndroid ID (synthetic): {profile['android_id']}\n"
            f"Session: {profile['session_id']}\nEnvironment: {profile['environment']}")


def handle_telegram_update(update):
    message = update.get("message") or update.get("edited_message")
    if not message or not message.get("text"):
        return
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if not chat_id:
        return
    command = message["text"].strip().split()[0].split("@")[0].lower()
    if command == "/start":
        telegram_send(chat_id, "Pixel Gemini bot\n\n/login - Kết nối Google qua OAuth chính thức\n/device - Tạo hồ sơ Pixel mô phỏng để kiểm thử cục bộ\n/status - Xem trạng thái tài khoản và hồ sơ test\n/logout - Ngắt liên kết Google\n/check_offer - Mở hướng dẫn kiểm tra ưu đãi chính thức\n/get_link - Lấy liên kết Google One\n\nIMEI/Android ID trong bot là dữ liệu TEST giả lập, không phải định danh thiết bị thật và không được gửi tới Google. Bot không nhận mật khẩu hoặc OTP.")
    elif command == "/login":
        if chat.get("type") != "private":
            telegram_send(chat_id, "Vui lòng mở chat riêng với bot rồi dùng /login.")
        else:
            try:
                telegram_send(chat_id, "Mở liên kết sau và đăng nhập trên trang Google chính thức:\n" + bot_login_url(chat_id))
            except Exception:
                log.exception("Could not create OAuth URL")
                telegram_send(chat_id, "Chưa thể tạo liên kết đăng nhập. Vui lòng thử lại sau.")
    elif command == "/device":
        profile = create_test_profile()
        with _accounts_lock:
            _devices[chat_id] = profile.to_dict()
        telegram_send(chat_id, "Đã tạo hồ sơ Pixel mô phỏng cho môi trường test:\n\n" +
                      _device_summary(profile.to_dict()) +
                      "\n\nLưu ý: ID tổng hợp chỉ dành cho mock/local testing, không dùng để giả danh thiết bị khi gọi Google.")
    elif command == "/status":
        with _accounts_lock:
            account = _accounts.get(chat_id)
            device = _devices.get(chat_id)
        lines = []
        lines.append("Google: " + (account["email"] if account else "chưa kết nối"))
        lines.append("Device profile: " + ("đã tạo" if device else "chưa tạo — dùng /device"))
        if device:
            lines.append("Model: " + device["model"])
            lines.append("Session: " + device["session_id"])
        telegram_send(chat_id, "\n".join(lines) + "\nTrạng thái được lưu trong RAM của tiến trình và có thể mất khi Render khởi động lại.")
    elif command == "/logout":
        with _accounts_lock:
            _accounts.pop(chat_id, None)
        telegram_send(chat_id, "Đã ngắt liên kết Google khỏi phiên bot hiện tại. Hồ sơ test không bị ảnh hưởng.")
    elif command == "/check_offer":
        with _accounts_lock:
            account = _accounts.get(chat_id)
            device = _devices.get(chat_id)
        if not account:
            telegram_send(chat_id, "Bạn hãy dùng /login để liên kết tài khoản qua Google OAuth trước.")
        else:
            device_note = ("\nHồ sơ test hiện tại: " + device["model"] + " / " + device["session_id"]
                           if device else "\nChưa có hồ sơ test; dùng /device nếu cần kiểm thử mock.")
            telegram_send(chat_id, "Tài khoản đã kết nối: " + account["email"] + device_note +
                          "\n\nBot không thể xác minh eligibility qua API hồ sơ cơ bản và không tự động đăng nhập/quét trang riêng tư. Hãy mở Google One để tự kiểm tra ưu đãi: " + OFFER_URL)
    elif command == "/get_link":
        telegram_send(chat_id, "Liên kết Google One chính thức để tự kiểm tra ưu đãi: " + OFFER_URL)


@app.get("/")
def index():
    return render_template("index.html", account=session.get("account"), ready=oauth_ready())


@app.get("/login")
def login():
    if not oauth_ready():
        return render_template("index.html", account=None, ready=False), 503
    try:
        flow = make_flow()
        url, state = flow.authorization_url(access_type="online",
            include_granted_scopes="true", prompt="select_account")
        session["oauth_state"] = state
        session.permanent = True
        return redirect(url)
    except Exception:
        log.exception("Could not start website OAuth")
        flash("Chưa thể bắt đầu đăng nhập. Vui lòng kiểm tra cấu hình OAuth.", "error")
        return redirect(url_for("index"))


@app.get("/oauth2callback")
def oauth2callback():
    received_state = request.args.get("state", "")
    try:
        payload = _serializer.loads(received_state, max_age=600) if _serializer else None
    except (BadSignature, SignatureExpired):
        payload = None

    if isinstance(payload, dict) and isinstance(payload.get("chat_id"), int):
        chat_id = payload["chat_id"]
        if request.args.get("error"):
            telegram_send(chat_id, "Bạn đã hủy hoặc Google không hoàn tất đăng nhập. Hãy dùng /login để thử lại.")
            return "Bạn chưa hoàn tất đăng nhập. Có thể quay lại Telegram.", 200
        try:
            flow = make_flow(state=received_state)
            flow.fetch_token(authorization_response=request.url)
            info = requests.get("https://openidconnect.googleapis.com/v1/userinfo",
                headers={"Authorization": f"Bearer {flow.credentials.token}"}, timeout=15)
            info.raise_for_status()
            profile = info.json()
            if not profile.get("email") or not profile.get("email_verified", False):
                raise RuntimeError("Email chưa được Google xác minh.")
            with _accounts_lock:
                _accounts[chat_id] = {"email": profile["email"]}
            telegram_send(chat_id, "Đăng nhập Google thành công: " + profile["email"] + "\nBạn có thể quay lại Telegram và dùng /status.")
            return "Đăng nhập Google thành công. Bạn có thể quay lại Telegram.", 200
        except Exception:
            log.exception("Telegram Google OAuth callback failed")
            telegram_send(chat_id, "Không thể hoàn tất đăng nhập Google. Vui lòng thử lại bằng /login.")
            return "Không thể hoàn tất đăng nhập. Hãy quay lại Telegram và thử lại.", 400

    expected_state = session.pop("oauth_state", None)
    if (not expected_state or not received_state
            or not hmac.compare_digest(received_state, expected_state)):
        return "Phiên đăng nhập không hợp lệ hoặc đã hết hạn. Vui lòng thử lại.", 400
    if request.args.get("error"):
        flash("Bạn đã hủy hoặc Google không hoàn tất đăng nhập.", "error")
        return redirect(url_for("index"))
    try:
        flow = make_flow(state=expected_state)
        flow.fetch_token(authorization_response=request.url)
        info = requests.get("https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {flow.credentials.token}"}, timeout=15)
        info.raise_for_status()
        profile = info.json()
        if not profile.get("email") or not profile.get("email_verified", False):
            raise RuntimeError("Email chưa được Google xác minh.")
        session.clear()
        session["account"] = {"email": profile["email"], "name": profile.get("name", ""),
                              "picture": profile.get("picture", "")}
        session.permanent = True
        flash("Đăng nhập Google thành công.", "success")
    except Exception:
        log.exception("Website Google OAuth callback failed")
        flash("Không thể hoàn tất đăng nhập Google. Vui lòng thử lại.", "error")
    return redirect(url_for("index"))


@app.post("/logout")
def logout():
    session.clear()
    flash("Bạn đã đăng xuất.", "success")
    return redirect(url_for("index"))


@app.post("/telegram/webhook")
def telegram_webhook():
    supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not WEBHOOK_SECRET or not hmac.compare_digest(supplied, WEBHOOK_SECRET):
        return jsonify({"ok": False}), 403
    try:
        handle_telegram_update(request.get_json(silent=True) or {})
    except Exception:
        log.exception("Telegram update handler failed")
    return jsonify({"ok": True})


@app.get("/health")
def health():
    return jsonify({"status": "ok", "telegram_configured": bool(BOT_TOKEN),
                    "oauth_configured": oauth_ready()})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
