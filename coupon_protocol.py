"""Normalize the observed Starbucks coupon schemas, without deriving QR codes.

Modern source chain: tc.a.c -> og.a.d -> CouponResponse -> BaseCoupon.
Retained sources are under evidence/coupon-helper-source/ and
evidence/coupon-restored-source/com/starbucks/cn/common/model/.
The restored APK provenance is recorded in evidence/offline-dex/.
"""

from collections.abc import Mapping
import hashlib
import json

from errors import ProviderError


def _schema_error():
    return ProviderError(
        "星巴克返回的优惠券结构已变化，暂时无法读取。",
        status=502,
        code="coupon_schema_changed",
    )


def _text(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise _schema_error()
    return value


def _mapping(value):
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise _schema_error()
    return value


def _items(response):
    """Accept CouponResponse or the observed ResponseCommonData.data wrapper."""
    body = _mapping(response)
    if "couponList" not in body:
        body = _mapping(body.get("data"))
    if "couponList" not in body:
        raise _schema_error()
    coupons = body["couponList"]
    if coupons is None:  # The recovered DTO explicitly permits a null list.
        return []
    if not isinstance(coupons, list):
        raise _schema_error()
    return coupons


def _qr_content(qrcode):
    content = _text(qrcode.get("content"))
    # Do not trim, transform, decode or reconstruct the actual credential.
    return content if content and content.strip() else None


def _modern(record):
    entity = _mapping(record.get("couponEntity"))
    style = _mapping(record.get("couponStyle"))
    if not entity:
        if record.get("couponSetList"):
            return None  # A grouping container can still contain real children.
        raise _schema_error()
    qrcode = _mapping(style.get("qrcode"))
    status = _text(entity.get("status"))
    payload = _qr_content(qrcode)
    # These exact enum values are defined by common.model.CouponState.
    if status in {"EXPIRED", "USED"}:
        payload = None
    return {
        "id": _text(entity.get("memberBenefitId")) or _text(entity.get("id")),
        "title": _text(entity.get("title")),
        "status": status,
        "expires_at": _text(entity.get("expiredDate"))
        or _text(style.get("couponExpiryTimeStr")),
        "description": _text(entity.get("usageDescription"))
        or _text(qrcode.get("reminder")),
        "qr_payload": payload,
    }


def _legacy(record):
    """The older CouponListData schema is opt-in, never guessed from fields."""
    qrcode = _mapping(record.get("qrcode"))
    display = qrcode.get("isDisplay")
    expired = record.get("isExpireCoupon")
    if display is not None and not isinstance(display, bool):
        raise _schema_error()
    if expired is not None and not isinstance(expired, bool):
        raise _schema_error()
    status = _text(record.get("status"))
    payload = _qr_content(qrcode)
    if display is False or expired is True or status in {"Expired", "Used"}:
        payload = None
    return {
        "id": _text(record.get("memberBenefitId")) or _text(record.get("couponNo")),
        "title": _text(record.get("couponName")),
        "status": status,
        "expires_at": _text(record.get("expiryDate"))
        or _text(record.get("couponExpiryTimeStr")),
        "description": _text(record.get("alternativeUsageDescription"))
        or _text(record.get("usageDescription"))
        or _text(qrcode.get("reminder")),
        "qr_payload": payload,
    }


def normalize_coupons(response, *, legacy=False):
    """Return normalized coupon records; qr_payload must stay server-side.

    A modern grouped row is itself a coupon, followed by couponSetList children.
    Duplicate normalized records are coalesced. If an upstream identifier is
    reused for different records, a deterministic opaque suffix prevents one
    coupon from replacing another in the server QR cache. Missing identifiers
    receive an opaque local UI key, which is never used to create a QR payload.

    Expiration strings and unknown statuses are preserved as supplied. This
    function does not guess timestamp units, QR lifetimes or status meanings.
    """
    output = []
    pending = list(reversed(_items(response)))
    seen = set()
    allocated = set()
    normalizer = _legacy if legacy else _modern
    children_key = "couponList" if legacy else "couponSetList"

    while pending:
        record = pending.pop()
        if not isinstance(record, Mapping):
            raise _schema_error()
        children = record.get(children_key)
        if children is not None:
            if not isinstance(children, list):
                raise _schema_error()
            pending.extend(reversed(children))

        item = normalizer(record)
        if item is None:
            continue
        # Only normalized data determines identity; no unrelated upstream fields.
        canonical = json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        identifier = item["id"] or "coupon-" + digest[:24]
        duplicate = (identifier, digest)
        if duplicate in seen:
            continue
        seen.add(duplicate)

        unique_id = identifier
        if unique_id in allocated:
            unique_id = identifier + "~" + digest[:16]
            counter = 1
            while unique_id in allocated:
                unique_id = identifier + "~" + digest[:16] + "-" + str(counter)
                counter += 1
        allocated.add(unique_id)
        item["id"] = unique_id
        output.append(item)
    return output
