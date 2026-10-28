"""Source-backed native App identity and response contracts for the HTTP client.

DeviceEntity / CommonInterceptor sources are retained under
evidence/login-unpacked-source; vk.y, vk.v and vk.o are retained under
evidence/identity-helper-source. SDK fingerprints are caller-provided only.
MSR version is route-specific; see evidence/msr-builder-contract.txt.
"""

from collections.abc import Mapping
from pathlib import Path
import re
import threading
import uuid

from errors import ProviderError


APP_VERSION = "10.30.1"
API_VERSION = "2.0"
CARD_MSR_VERSION = "4"
MSR_VERSION = CARD_MSR_VERSION  # Compatibility alias; not a common header default.
GATEWAY_MSR_VERSION = "2"
PIN_LOGIN_TYPE = "PIN_CODE"
GATEWAY_GRANT_TYPE = "authorization_code"
SMS_TEMPLATE = "create-cn"

_id_lock = threading.Lock()
_id_pattern = re.compile(r"[0-9a-fA-F]{32}\Z")


def _text(value, name, *, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ProviderError("客户端身份字段无效：" + name, 400, "invalid_client_identity")
    return value


def _installation_id(value):
    if not isinstance(value, str) or not _id_pattern.fullmatch(value):
        raise ProviderError("客户端安装标识无效。", 500, "invalid_installation_id")
    return value.lower()


def make_installation_id(path=None):
    """Persist a random UUID without hyphens, matching native vk.y.b().

    This is a local installation identifier, not a claimed hardware identifier
    or an SDK risk fingerprint. Invalid existing files are never overwritten.
    """
    destination = Path(path) if path is not None else Path(__file__).parent / "runtime" / "installation-id"
    with _id_lock:
        try:
            if destination.exists():
                return _installation_id(destination.read_text(encoding="ascii").strip())
            destination.parent.mkdir(parents=True, exist_ok=True)
            generated = uuid.uuid4().hex
            try:
                with destination.open("x", encoding="ascii") as handle:
                    handle.write(generated + "\n")
            except FileExistsError:
                return _installation_id(destination.read_text(encoding="ascii").strip())
            return generated
        except (OSError, UnicodeError) as error:
            raise ProviderError("无法读取或保存客户端安装标识。", 500, "installation_id_unavailable") from error


def _context(context):
    if context is None:
        return {}
    if not isinstance(context, Mapping):
        raise ProviderError("客户端身份上下文无效。", 400, "invalid_client_identity")
    return context


def _language(value):
    value = _text(value, "language")
    primary = value.replace("_", "-").split("-", 1)[0].lower()
    return "zh" if primary in {"zh", "cn"} else primary


def build_device_entity(installation_id, version=APP_VERSION, context=None):
    """Build exact DeviceEntity wire keys, omitting nullable unavailable values.

    Context keys use the recovered JSON names, such as manufacturer, model,
    os_version, user_agent and device_external_code. No model, OS version,
    location, native risk token or browser fingerprint is fabricated.
    """
    context = _context(context)
    version = _text(version, "app_version")
    result = {
        "os_name": _text(context.get("os_name", "Android"), "os_name"),
        "language": _language(context.get("language", "zh")),
        "timezone": _text(context.get("timezone", "Asia/Shanghai"), "timezone"),
        "id": _installation_id(installation_id),
        "app_version": version,
    }
    for key in ("manufacturer", "model", "os_version", "latitude", "longitude", "device_external_code", "user_agent"):
        value = _text(context.get(key), key, optional=True)
        if value is not None:
            result[key] = value
    # vk.z.c uses this format only with actual Android model/OS metadata.
    if "user_agent" not in result and result["os_name"] == "Android" and result.get("model") and result.get("os_version"):
        result["user_agent"] = (
            f"Android com.starbucks.cn.platform/{version} "
            f"({result['model']}; {result['os_version']})"
        )
    return result


def common_headers(installation_id, version=APP_VERSION, access_token=None, context=None, *, msr_version=None):
    """Return common headers; caller adds only its route's explicit MSR version.

    Native login/unified and coupon/pickup builders omit x-msr-version.
    Gateway token exchange declares 2; account-card builders explicitly add 4.
    SDK fields are supplied, never made up.
    """
    context = _context(context)
    version = _text(version, "app_version")
    language = _language(context.get("language", "zh"))
    result = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-API-Version": API_VERSION,
        "User-Agent": "com.starbucks.cn/" + version,
        "x-phone-type": "ANDROID",
        "x-app-version": version,
        "x-device-id": _installation_id(installation_id),
        "x-language-type": "zh_CN" if language == "zh" else "en_US",
    }
    if msr_version is not None:
        result["x-msr-version"] = _text(msr_version, "msr_version")
    if access_token is not None:
        access_token = _text(access_token, "access_token")
        if not access_token.strip():
            raise ProviderError("登录令牌为空。", 401, "missing_access_token")
        result["Authorization"] = "Bearer " + access_token
        for source_key, header in (("member_level", "x-member-level"), ("experience_level", "x-experience-level")):
            value = _text(context.get(source_key), source_key, optional=True)
            if value is not None:
                result[header] = value.upper()
    for source_key, header in (("device_external_code", "x-bs-device-id"), ("sm_token", "Sm-Token")):
        value = _text(context.get(source_key), source_key, optional=True)
        if value is not None:
            result[header] = value
    return result


def unwrap_bff(response_json):
    """Enforce BffResponseWrapperKt.isSuccess: integer body code in [200,300).

    The HTTP transport must independently reject unsuccessful HTTP statuses.
    SuccessResponse for SMS is an empty DTO, so {} or null data is allowed here;
    login callers must separately require a nonblank data.token/access_token.
    """
    if not isinstance(response_json, Mapping):
        raise ProviderError("星巴克返回了无法识别的响应。", 502, "invalid_bff_response")
    code = response_json.get("code")
    if type(code) is not int or not 200 <= code < 300:
        message = response_json.get("message")
        if not isinstance(message, str) or not message.strip():
            message = "星巴克接口未返回成功状态。"
        official_code = code if type(code) is int or isinstance(code, str) else None
        raise ProviderError(message, 409, "starbucks_api_error", official_code=official_code)
    return response_json.get("data")


def user_detail_body(ad_code=""):
    """UserDetailRequestBody has one nonnull field; the native caller uses ''."""
    return {"adCode": _text(ad_code, "adCode")}


def build_bff2_base(context=None):
    """Exact BaseEntity fields; caller supplies actual account/location values."""
    context = _context(context)
    return {
        "memberLevel": _text(context.get("memberLevel", ""), "memberLevel").upper(),
        "adCode": _text(context.get("adCode", ""), "adCode"),
        "birthday": _text(context.get("birthday", ""), "birthday"),
    }
