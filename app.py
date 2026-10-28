"""Local Starbucks account/coupon console. Run: python app.py"""
from __future__ import annotations

import io
import os
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import urlsplit

import qrcode
from flask import Flask, jsonify, render_template, request, send_file, session
from errors import ProviderError
from coupon_export import copy_text, image_download, matches_category


@dataclass
class AccountState:
    id: str
    phone: str
    provider: object
    coupons: dict = field(default_factory=dict)
    coupons_at: float = 0
    lock: threading.RLock = field(default_factory=threading.RLock)


@dataclass
class BrowserState:
    csrf: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    last_seen: float = field(default_factory=time.monotonic)
    sms_at: float = 0
    sent_phone: str | None = None
    login_attempts: list = field(default_factory=list)
    provider: object = None
    accounts: dict = field(default_factory=dict)
    active_account_id: str | None = None
    lock: threading.RLock = field(default_factory=threading.RLock)


def create_app(provider_factory=None):
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_bytes(32)
    app.config.update(SESSION_COOKIE_NAME="starbucks_local", SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=2), MAX_CONTENT_LENGTH=8192)
    if provider_factory is None:
        if os.environ.get("STARBUCKS_PROTOCOL_FILE"):
            from http_provider import StarbucksHttpProvider
            provider_factory = StarbucksHttpProvider
        else:
            from starbucks_client import StarbucksAppHttpProvider
            provider_factory = StarbucksAppHttpProvider
    factory = provider_factory
    states = {}
    states_lock = threading.Lock()

    def current():
        now = time.monotonic()
        with states_lock:
            for key, value in list(states.items()):
                if now - value.last_seen > 7200:
                    del states[key]
            key = session.get("browser_id")
            if key not in states:
                key = secrets.token_urlsafe(32)
                session["browser_id"] = key
                session.permanent = True
                states[key] = BrowserState(provider=factory())
            value = states[key]
            value.last_seen = now
            return value

    def result(**payload):
        return jsonify(ok=True, csrf_token=current().csrf, **payload)

    def body():
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            raise ProviderError("请求内容必须是 JSON 对象。", 400, "invalid_json")
        return value

    def phone_number(value):
        phone = str(value or "").strip()
        if not re.fullmatch(r"1[3-9]\d{9}", phone):
            raise ProviderError("请输入正确的 11 位中国大陆手机号码。", 400, "invalid_phone")
        return phone

    def account_summary(account):
        user = account.provider.user() or {}
        return {"id": account.id, "phone": account.phone[:3] + "****" + account.phone[-4:],
                "name": user.get("name") or "我的星巴克账号",
                "authenticated": account.provider.authenticated()}

    def account_list(state):
        return [account_summary(account) for account in state.accounts.values()]

    def selected_account(state, account_id=None):
        key = account_id or state.active_account_id
        account = state.accounts.get(key)
        if not account:
            raise ProviderError("请先登录对应账号。", 401, "login_required")
        return account

    def require_login(account):
        if not account.provider.authenticated():
            raise ProviderError("请先完成登录。", 401, "login_required")

    def read_coupons(account):
        with account.lock:
            require_login(account)
            try:
                values = account.provider.coupons()
                account.coupons = {str(x["id"]): x for x in values}
                account.coupons_at = time.monotonic()
                output = []
                from urllib.parse import quote
                for key, coupon in account.coupons.items():
                    public = {k: v for k, v in coupon.items() if not k.startswith("_") and k != "qr_payload"}
                    if coupon.get("qr_payload") or coupon.get("_qr_image"):
                        public["qr_url"] = ("/api/accounts/" + quote(account.id, safe="") + "/coupons/"
                                            + quote(key, safe="") + "/qr.png?v=" + secrets.token_hex(4))
                    output.append(public)
                return {"ok": True, "account": account_summary(account), "coupons": output}
            except Exception:
                account.coupons.clear()
                raise

    def read_account_result(account):
        try:
            return read_coupons(account)
        except ProviderError as error:
            return {"ok": False, "account": account_summary(account), "coupons": [],
                    "error": error.code, "message": error.message}
        except Exception:
            app.logger.exception("Coupon request failed")
            return {"ok": False, "account": account_summary(account), "coupons": [],
                    "error": "account_request_failed", "message": "该账号获取失败，请单独重试。"}

    def export_rows(state, data):
        category = data.get("category", "all")
        matches_category({}, category)  # Validate even when there are no coupons.
        ids = data.get("account_ids")
        if not isinstance(ids, list) or not ids or any(not isinstance(key, str) for key in ids):
            raise ProviderError("请先获取账号好礼券，再选择分类。", 400, "export_accounts_required")
        coupon_id = data.get("coupon_id")
        if coupon_id is not None and (not isinstance(coupon_id, str) or len(ids) != 1):
            raise ProviderError("单张券导出参数无效。", 400, "invalid_coupon_selection")
        rows = []
        with state.lock:
            accounts = [selected_account(state, key) for key in dict.fromkeys(ids)]
            for account in accounts:
                with account.lock:
                    require_login(account)
                    if not account.coupons_at or time.monotonic() - account.coupons_at > 300:
                        raise ProviderError("券码缓存已过期，请重新获取好礼券后再导出。", 410, "qr_expired")
                    for key, coupon in account.coupons.items():
                        if coupon_id is not None and key != coupon_id:
                            continue
                        if coupon.get("qr_payload") and matches_category(coupon, category):
                            rows.append({**coupon, "title": coupon.get("title") or "好礼券",
                                         "account_phone": account_summary(account)["phone"]})
        if not rows:
            raise ProviderError("此分类没有可导出的二维码，请先获取对应好礼券。", 404, "no_matching_coupons")
        return rows, category

    @app.before_request
    def protect_requests():
        # Only the loopback web origin may control the account/device.
        if urlsplit(request.host_url).hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ProviderError("仅允许通过本机地址访问。", 403, "invalid_host")
        if request.path.startswith("/api/") and request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("Origin")
            if origin and urlsplit(origin).netloc != request.host:
                raise ProviderError("请求来源不匹配。", 403, "invalid_origin")
            if not secrets.compare_digest(request.headers.get("X-CSRFToken", ""), current().csrf):
                raise ProviderError("页面会话已更新，请刷新页面后重试。", 403, "csrf_failed")

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    @app.errorhandler(ProviderError)
    def provider_error(error):
        return jsonify(ok=False, error=error.code, message=error.message,
                       csrf_token=current().csrf, **error.details), error.status

    @app.get("/")
    def index():
        current()
        return render_template("index.html")

    @app.get("/health")
    def health():
        return jsonify(ok=True, service="starbucks-local", port=5700)

    @app.get("/api/status")
    def status():
        state = current()
        with state.lock:
            account = state.accounts.get(state.active_account_id)
            return result(authenticated=any(a.provider.authenticated() for a in state.accounts.values()),
                          provider=state.provider.status(), user=account.provider.user() if account else None,
                          accounts=account_list(state), active_account_id=state.active_account_id)

    @app.post("/api/auth/sms")
    def send_sms():
        state = current()
        phone = phone_number(body().get("phone"))
        with state.lock:
            if any(a.phone == phone and a.provider.authenticated() for a in state.accounts.values()):
                raise ProviderError("该账号已登录，可直接获取好礼券。", 409, "account_already_added")
            wait = int(60 - (time.monotonic() - state.sms_at))
            if wait > 0:
                raise ProviderError(f"请在 {wait} 秒后重试。", 429, "sms_cooldown", retry_after=wait)
            outcome = state.provider.send_sms(phone) or {}
            state.sms_at, state.sent_phone = time.monotonic(), phone
            return result(message="已请求发送验证码，请查看短信。", retry_after=60, **outcome)

    @app.post("/api/auth/login")
    def login():
        state, data = current(), body()
        phone, code = phone_number(data.get("phone")), str(data.get("code") or "").strip()
        if not re.fullmatch(r"\d{4,8}", code):
            raise ProviderError("请输入 4 至 8 位短信验证码。", 400, "invalid_code")
        with state.lock:
            now = time.monotonic()
            state.login_attempts[:] = [x for x in state.login_attempts if now - x < 600]
            if len(state.login_attempts) >= 5:
                raise ProviderError("尝试次数过多，请稍后重试。", 429, "login_rate_limited")
            if state.sent_phone != phone or now - state.sms_at > 600:
                raise ProviderError("请先为这个手机号获取验证码。", 409, "sms_required")
            state.login_attempts.append(now)
            outcome = state.provider.login(phone, code) or {}
            if not state.provider.authenticated():
                raise ProviderError("官方流程尚未确认登录成功。", 409, "login_unconfirmed")
            state.login_attempts.clear()
            previous = next((a for a in state.accounts.values() if a.phone == phone), None)
            if previous:
                with previous.lock:
                    previous.provider.logout()
                    previous.coupons.clear()
                    previous.provider = state.provider
                account = previous
            else:
                account = AccountState(secrets.token_urlsafe(18), phone, state.provider)
                state.accounts[account.id] = account
            state.active_account_id, state.sent_phone = account.id, None
            state.provider = factory()  # A new login never replaces another account's provider.
            return result(user=account.provider.user(), account=account_summary(account),
                          accounts=account_list(state), active_account_id=account.id,
                          message="账号登录成功，可以继续添加其他账号。", **outcome)

    @app.post("/api/auth/logout")
    def logout():
        state = current()
        with state.lock:
            for account in state.accounts.values():
                with account.lock:
                    account.provider.logout()
                    account.coupons.clear()
            state.accounts.clear()
            state.active_account_id = None
            state.provider.logout()
            state.sent_phone = None
            return result(message="已退出全部账号。", accounts=[])

    @app.post("/api/accounts/<account_id>/logout")
    def logout_account(account_id):
        state = current()
        with state.lock:
            account = selected_account(state, account_id)
            with account.lock:
                account.provider.logout()
                account.coupons.clear()
            del state.accounts[account_id]
            if state.active_account_id == account_id:
                state.active_account_id = next(iter(state.accounts), None)
            return result(message="该账号已退出。", accounts=account_list(state),
                          active_account_id=state.active_account_id)

    @app.get("/api/accounts/coupons")
    def all_coupons():
        state = current()
        with state.lock:
            accounts = list(state.accounts.values())
        if not accounts:
            raise ProviderError("请先添加并登录账号。", 401, "login_required")
        with ThreadPoolExecutor(max_workers=min(4, len(accounts))) as pool:
            results = list(pool.map(read_account_result, accounts))
        with state.lock:
            # A concurrent logout must not publish a removed account's results.
            results = [item for item in results if item["account"]["id"] in state.accounts]
            summaries = account_list(state)
        succeeded = sum(item["ok"] for item in results)
        return result(results=results, accounts=summaries, total_accounts=len(results),
                      succeeded=succeeded, failed=len(results) - succeeded,
                      total_coupons=sum(len(item["coupons"]) for item in results),
                      message=f"已获取 {succeeded}/{len(results)} 个账号的好礼券。")

    @app.get("/api/coupons")
    def coupons():
        state = current()
        with state.lock:
            account = selected_account(state, request.args.get("account_id"))
        data = read_coupons(account)
        return result(coupons=data["coupons"], account=data["account"],
                      message="已读取该账号的好礼券。")

    @app.post("/api/exports/copy")
    def export_copy():
        rows, category = export_rows(current(), body())
        return result(text=copy_text(rows), count=len(rows), category=category)

    @app.post("/api/exports/download")
    def export_download():
        rows, category = export_rows(current(), body())
        data, mimetype, name = image_download(rows, category)
        return send_file(io.BytesIO(data), mimetype=mimetype, as_attachment=True,
                         download_name=name, max_age=0)

    @app.get("/api/accounts/<account_id>/coupons/<coupon_id>/qr.png")
    @app.get("/api/coupons/<coupon_id>/qr.png")
    def coupon_qr(coupon_id, account_id=None):
        state = current()
        with state.lock:
            account = selected_account(state, account_id)
        with account.lock:
            require_login(account)
            if time.monotonic() - account.coupons_at > 300:
                raise ProviderError("券码已过刷新时间，请重新获取好礼券。", 410, "qr_expired")
            coupon = account.coupons.get(coupon_id)
            if not coupon:
                raise ProviderError("未找到当前会话中的券。", 404, "coupon_not_found")
            data = coupon.get("_qr_image")
            if not data:
                payload = coupon.get("qr_payload")
                if not payload:
                    raise ProviderError("官方尚未提供此券的二维码。", 409, "qr_unavailable")
                image = qrcode.make(payload)
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                data = buffer.getvalue()
            return send_file(io.BytesIO(data), mimetype="image/png", max_age=0)

    return app


app = create_app()

if __name__ == "__main__":
    from waitress import serve
    serve(app, host="127.0.0.1", port=5700, threads=4)
