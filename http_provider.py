"""Strict HTTP protocol adapter; no Android/ADB dependency.

The current protected APK does not establish a complete protocol. A verified
protocol profile must supply request schemas and exact response selectors.
Unknown fields are deliberately not inferred from endpoint names.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import requests

from errors import ProviderError


MISSING = object()


def pointer(document, path, default=MISSING):
    if path == "":
        return document
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("JSON pointer must be empty or begin with '/'")
    value = document
    try:
        for token in path[1:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            value = value[int(token)] if isinstance(value, list) else value[token]
        return value
    except (KeyError, IndexError, TypeError, ValueError):
        if default is MISSING:
            raise ProviderError("官方响应缺少已配置的字段，请核对协议配置。", 502, "response_schema_changed")
        return default


def render(value, context):
    """Replace whole-value references only; never evaluate code or expressions."""
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            if value["$ref"] not in context:
                raise ProviderError("登录流程缺少必要的上一步响应数据。", 409, "missing_protocol_context")
            return copy.deepcopy(context[value["$ref"]])
        return {k: render(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, context) for v in value]
    return value


def validate_profile(profile):
    errors = []
    if profile.get("verified") is not True:
        errors.append("请求参数和响应字段尚未验证")
    endpoints = profile.get("endpoints", {})
    for name in ("sms", "verify", "account", "coupons"):
        item = endpoints.get(name)
        if not isinstance(item, dict):
            errors.append(f"缺少 {name} 接口协议")
            continue
        url = urlsplit(item.get("url", ""))
        if (url.scheme != "https" or not url.hostname or
                not url.hostname.endswith(".starbucks.com.cn") or
                url.username or url.password or url.port not in (None, 443)):
            errors.append(f"{name} 必须使用已核实的星巴克 HTTPS 地址")
        if item.get("method") not in ("GET", "POST"):
            errors.append(f"{name} 的 HTTP 方法未确认")
        rules = item.get("success")
        if not isinstance(rules, list) or not rules or any(not isinstance(x, dict) or "pointer" not in x or "equals" not in x for x in rules):
            errors.append(f"{name} 缺少明确的成功条件")
    for key in ("coupon_items_pointer", "coupon_fields"):
        if key not in profile:
            errors.append(f"缺少 {key}")
    fields = profile.get("coupon_fields", {})
    if not isinstance(fields, dict) or not all(k in fields for k in ("id", "title", "status")):
        errors.append("优惠券字段未确认")
    if not fields.get("qr_payload"):
        errors.append("真实券码字段与编码尚未确认")
    return errors


class StarbucksHttpProvider:
    def __init__(self, profile=None, transport=None):
        self.profile = profile
        self.errors = []
        if profile is None:
            path = os.environ.get("STARBUCKS_PROTOCOL_FILE")
            if path:
                try:
                    self.profile = json.loads(Path(path).read_text(encoding="utf-8-sig"))
                except (OSError, ValueError):
                    self.errors = ["无法读取 HTTP 协议配置文件"]
            else:
                self.errors = ["尚缺已验证的手机号登录请求、设备校验与券码协议"]
        if self.profile is not None:
            if isinstance(self.profile, dict):
                self.errors = validate_profile(self.profile)
            else:
                self.errors = ["HTTP 协议配置必须是 JSON 对象"]
        self.http = transport or requests.Session()
        self.http.headers.update({"Accept": "application/json"})
        self.context = {}
        self._authenticated = False
        self._user = None

    def status(self):
        return {"ready": not self.errors, "mode": "http",
                "message": "纯 HTTP 服务已配置，运行时无需连接手机。" if not self.errors else
                "纯 HTTP 尚未就绪：" + "；".join(self.errors) + "。"}

    def ready(self):
        if self.errors:
            raise ProviderError(self.status()["message"], 503, "protocol_incomplete")

    def authenticated(self):
        return self._authenticated

    def user(self):
        return self._user

    def call(self, name):
        self.ready()
        spec = self.profile["endpoints"][name]
        headers = render({**self.profile.get("headers", {}), **spec.get("headers", {})}, self.context)
        kwargs = {"headers": headers, "timeout": (5, 25), "allow_redirects": False}
        if "json" in spec:
            kwargs["json"] = render(spec["json"], self.context)
        if "form" in spec:
            kwargs["data"] = render(spec["form"], self.context)
        if "params" in spec:
            kwargs["params"] = render(spec["params"], self.context)
        try:
            response = self.http.request(spec["method"], spec["url"], **kwargs)
        except requests.RequestException:
            raise ProviderError("连接星巴克官方接口失败，请稍后重试。", 502, "upstream_unreachable") from None
        if response.status_code in (401, 403):
            self._authenticated, self._user = False, None
            raise ProviderError("官方接口要求重新登录或完成人工验证。", 401, "upstream_auth_required")
        if response.status_code == 429:
            raise ProviderError("官方接口请求过于频繁，请稍后重试。", 429, "upstream_rate_limited")
        if not 200 <= response.status_code < 300:
            raise ProviderError(f"官方接口返回 HTTP {response.status_code}，尚未完成操作。", 502, "upstream_http_error")
        try:
            data = response.json()
        except ValueError:
            raise ProviderError("官方接口未返回预期 JSON 数据。", 502, "upstream_not_json") from None
        for rule in spec["success"]:
            if pointer(data, rule["pointer"], MISSING) != rule["equals"]:
                raise ProviderError("官方接口未确认操作成功；请核对验证码、人工校验与协议。", 409, "upstream_rejected")
        for key, selector in spec.get("capture", {}).items():
            self.context[key] = pointer(data, selector)
        return data

    def send_sms(self, phone):
        self.ready()
        if self._authenticated:
            raise ProviderError("请先退出当前网页登录，再切换账号。", 409, "already_authenticated")
        # Drop all prior challenge data and cookies when starting another flow.
        self.context.clear()
        self.http.cookies.clear()
        self.context["phone"] = phone
        self.call("sms")

    def login(self, phone, code):
        self.ready()
        if self.context.get("phone") != phone:
            raise ProviderError("验证码与当前手机号不匹配，请重新获取。", 409, "phone_mismatch")
        self.context["code"] = code
        try:
            self.call("verify")
            # A separate protected account request must confirm authentication.
            data = self.call("account")
            name_path = self.profile.get("user_name_pointer")
            name = pointer(data, name_path, None) if name_path else None
            self._user = {"name": str(name or "我的星巴克账号")}
            self._authenticated = True
        finally:
            self.context.pop("code", None)

    def logout(self):
        self.context.clear()
        self.http.cookies.clear()
        self._authenticated, self._user = False, None

    def coupons(self):
        self.ready()
        if not self._authenticated:
            raise ProviderError("请先登录。", 401, "login_required")
        data = self.call("coupons")
        values = pointer(data, self.profile["coupon_items_pointer"])
        if not isinstance(values, list):
            raise ProviderError("官方好礼券列表格式已变化。", 502, "coupon_schema_changed")
        if len(values) > 500:
            raise ProviderError("券列表超过当前支持范围，请核对分页协议。", 502, "pagination_required")
        output, seen = [], set()
        fields = self.profile["coupon_fields"]
        for value in values:
            item = {}
            for key in ("id", "title", "status", "expires_at", "description", "qr_payload"):
                selector = fields.get(key)
                if selector:
                    item[key] = pointer(value, selector, None)
            if any(item.get(key) in (None, "") for key in ("id", "title", "status")):
                raise ProviderError("官方券数据缺少标识、名称或状态。", 502, "coupon_schema_changed")
            item["id"] = str(item["id"])
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            payload = item.get("qr_payload")
            if payload is not None and (not isinstance(payload, str) or not payload or len(payload.encode("utf-8")) > 2200):
                raise ProviderError("券码内容超出已验证格式，未生成二维码。", 502, "qr_format_unknown")
            output.append(item)
        return output
