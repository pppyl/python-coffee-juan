"""Starbucks China App HTTP client, reconstructed from version 10.30.1.

No ADB, native process, browser automation or connected phone is used here.
Account credentials and upstream tokens remain in this instance's memory.
"""
from __future__ import annotations

import time
from collections.abc import Mapping

import requests

from app_crypto import build_request, decode_response
from app_identity import (APP_VERSION, GATEWAY_GRANT_TYPE, GATEWAY_MSR_VERSION, PIN_LOGIN_TYPE,
                          SMS_TEMPLATE, build_bff2_base, build_device_entity,
                          common_headers, make_installation_id, unwrap_bff,
                          user_detail_body)
from coupon_protocol import normalize_coupons
from errors import ProviderError


BFF_HOST = "https://bff.starbucks.com.cn"
SMS_PATH = "/app-bff-api/sms/send"
LOGIN_PATH = "/app-bff-api/login"
TOKEN_PATH = "/app-bff-lgc/default/login/access_token"
ACCOUNT_PATH = "/app-bff-api/auth/v2/user/detail"
COUPONS_PATH = "/bff2/user/coupon/list"
_PATHS = frozenset((SMS_PATH, LOGIN_PATH, TOKEN_PATH, ACCOUNT_PATH, COUPONS_PATH))


class StarbucksAppHttpProvider:
    def __init__(self, transport=None, *, installation_id=None, identity_context=None):
        self.http = transport or requests.Session()
        self.installation_id = installation_id or make_installation_id()
        self.identity_context = dict(identity_context or {})
        self.access_token = None
        self.profile = None
        self._user = None
        self.sent_phone = None
        self.sms_at = 0
        self._configuration_error = None
        try:
            # Offline framing also checks the locally retained client material.
            build_request("POST", SMS_PATH, {}, version=APP_VERSION)
        except (RuntimeError, ValueError, OSError) as error:
            self._configuration_error = str(error)

    def status(self):
        return {"ready": self._configuration_error is None, "mode": "http",
                "message": self._configuration_error or "纯 HTTP 登录服务已连接，无需连接手机。"}

    def authenticated(self):
        return bool(self.access_token and self.profile is not None)

    def user(self):
        return self._user

    def _clear_auth(self):
        self.access_token = None
        self.profile = None
        self._user = None

    def _request(self, path, payload, *, form=False, authenticated=False, extra_headers=None):
        if self._configuration_error:
            raise ProviderError(self._configuration_error, 503, "protocol_configuration_missing")
        if path not in _PATHS:
            raise ValueError("Unsupported protocol path")
        if authenticated and not self.authenticated():
            raise ProviderError("请先完成登录。", 401, "login_required")
        wire, protocol_headers = build_request("POST", path, payload, version=APP_VERSION, form=form)
        headers = common_headers(self.installation_id, access_token=self.access_token,
                                 context=self.identity_context)
        headers.update(protocol_headers)
        headers["lang"] = headers["x-language-type"]
        if extra_headers:
            headers.update(extra_headers)
        try:
            response = self.http.request("POST", BFF_HOST + path, data=wire, headers=headers,
                                         timeout=(8, 25), allow_redirects=False)
        except requests.RequestException:
            # Never retry an SMS automatically: the first request may have sent it.
            raise ProviderError("官方接口暂时无法连接，请稍后查看短信或重试。", 502, "starbucks_unreachable") from None
        if response.status_code in (401, 403):
            self._clear_auth()
            raise ProviderError("星巴克要求重新登录或完成额外验证。", 401, "starbucks_auth_required")
        if response.status_code == 429:
            raise ProviderError("星巴克限制了请求频率，请稍后再试。", 429, "starbucks_rate_limited")
        if not 200 <= response.status_code < 300 or response.status_code == 298:
            raise ProviderError(f"星巴克网关返回 HTTP {response.status_code}，未完成操作。", 502, "starbucks_gateway_error")
        data = decode_response(response.content, protocol_headers)
        if not isinstance(data, Mapping):
            raise ProviderError("官方接口没有返回预期的业务数据。", 502, "starbucks_response_unrecognized")
        return data

    def send_sms(self, phone):
        if self.authenticated():
            raise ProviderError("请先退出当前网页登录，再切换账号。", 409, "already_authenticated")
        # Field order follows the recovered DTO; null fields are omitted by Gson.
        payload = {"device": build_device_entity(self.installation_id, context=self.identity_context),
                   "phoneNumber": phone, "template": SMS_TEMPLATE, "token": ""}
        response = self._request(SMS_PATH, payload)
        unwrap_bff(response)
        self.sent_phone, self.sms_at = phone, time.monotonic()

    def login(self, phone, code):
        if phone != self.sent_phone or time.monotonic() - self.sms_at > 600:
            raise ProviderError("请先为当前手机号获取验证码。", 409, "sms_required")
        if len(code) != 4 or not code.isascii() or not code.isdigit():
            raise ProviderError("请输入短信中的 4 位验证码。", 400, "invalid_pin_length")
        self._clear_auth()
        payload = {"cellPhone": phone, "pin": code,
                   "device": build_device_entity(self.installation_id, context=self.identity_context)}
        login_data = unwrap_bff(self._request(LOGIN_PATH, payload, form=True,
                                             extra_headers={"loginType": PIN_LOGIN_TYPE}))
        login_token = login_data.get("token") if isinstance(login_data, Mapping) else None
        if not isinstance(login_token, str) or not login_token.strip():
            raise ProviderError("官方登录流程尚未返回授权码。", 409, "login_challenge_required")
        token_data = unwrap_bff(self._request(TOKEN_PATH, {"code": login_token,
                                                           "grant_type": GATEWAY_GRANT_TYPE},
                                             extra_headers={"x-msr-version": GATEWAY_MSR_VERSION}))
        access_token = token_data.get("access_token") if isinstance(token_data, Mapping) else None
        if not isinstance(access_token, str) or not access_token.strip():
            raise ProviderError("官方接口尚未返回登录令牌。", 409, "access_token_missing")
        self.access_token = access_token
        try:
            # This endpoint's BffResponse.code is a string. The native service
            # confirms the protected Customer only when data.extra is present.
            result = self._request(ACCOUNT_PATH, user_detail_body(),
                                   extra_headers={"Accept": "application/vnd.api+json"})
            profile = result.get("data")
            if not isinstance(profile, Mapping) or profile.get("extra") is None:
                raise ProviderError("官方接口尚未确认账号资料。", 409, "account_probe_failed")
            self.profile = profile
            name = profile.get("nickName") or profile.get("userName")
            self._user = {"name": name if isinstance(name, str) else "我的星巴克账号"}
            self.sent_phone = None
        except Exception:
            self._clear_auth()
            raise

    def logout(self):
        self._clear_auth()
        self.sent_phone = None
        self.http.cookies.clear()

    def coupons(self):
        if not self.authenticated():
            raise ProviderError("请先完成登录。", 401, "login_required")
        rows = []
        for page in range(1, 31):
            request = {"base": build_bff2_base(),
                       "params": {"scene": "ME", "channel": "ALL", "pageNum": page}}
            data = unwrap_bff(self._request(COUPONS_PATH, request, authenticated=True))
            if not isinstance(data, Mapping) or not isinstance(data.get("couponList"), list):
                raise ProviderError("官方优惠券列表格式发生变化。", 502, "coupon_schema_changed")
            rows.extend(data["couponList"])
            has_next = data.get("hasNext")
            if not isinstance(has_next, bool):
                raise ProviderError("官方优惠券分页信息缺失。", 502, "coupon_pagination_unknown")
            if not has_next:
                return normalize_coupons({"couponList": rows})
        raise ProviderError("券列表页数超出当前支持范围，未返回不完整列表。", 502, "coupon_pagination_limit")
