import copy
import unittest

from coupon_protocol import normalize_coupons
from errors import ProviderError


def coupon(identifier="test-benefit", *, payload="TEST-ONLY-NOT-REDEEMABLE", status="AVAILABLE"):
    return {
        "couponEntity": {
            "id": "test-entity",
            "memberBenefitId": identifier,
            "title": "测试券",
            "status": status,
            "expiredDate": "2099-12-31",
            "usageDescription": "仅用于测试",
        },
        "couponStyle": {"qrcode": {"content": payload, "reminder": "测试提示"}},
    }


class CouponProtocolTests(unittest.TestCase):
    def test_modern_gson_fields_and_exact_qr_credential(self):
        row = coupon(payload=" TEST-ONLY=NOT-REDEEMABLE ")
        original = copy.deepcopy(row)
        value = normalize_coupons({"data": {"couponList": [row], "hasNext": False}})[0]
        self.assertEqual(value, {
            "id": "test-benefit", "title": "测试券", "status": "AVAILABLE",
            "expires_at": "2099-12-31", "description": "仅用于测试",
            "qr_payload": " TEST-ONLY=NOT-REDEEMABLE ",
        })
        self.assertEqual(row, original)

    def test_group_parent_children_and_duplicate_entries(self):
        first, second = coupon("one"), coupon("two", payload="TEST-ONLY-SECOND")
        grouped = copy.deepcopy(first)
        grouped["couponSetList"] = [first, second]
        rows = normalize_coupons({"couponList": [grouped, second]})
        self.assertEqual([row["id"] for row in rows], ["one", "two"])

    def test_grouping_container_without_entity_keeps_children(self):
        rows = normalize_coupons({"couponList": [{"couponSetList": [coupon()]}]})
        self.assertEqual(len(rows), 1)

    def test_conflicting_upstream_ids_cannot_overwrite_qr(self):
        first = coupon("same", payload="TEST-ONLY-ONE")
        second = coupon("same", payload="TEST-ONLY-TWO")
        rows = normalize_coupons({"couponList": [first, second]})
        self.assertEqual(len({row["id"] for row in rows}), 2)
        self.assertEqual([row["qr_payload"] for row in rows], ["TEST-ONLY-ONE", "TEST-ONLY-TWO"])
        self.assertEqual(rows, normalize_coupons({"couponList": [first, second]}))

    def test_missing_identifier_gets_local_key_never_qr(self):
        row = coupon(payload=None)
        row["couponEntity"].pop("id")
        row["couponEntity"].pop("memberBenefitId")
        value = normalize_coupons({"couponList": [row]})[0]
        self.assertTrue(value["id"].startswith("coupon-"))
        self.assertIsNone(value["qr_payload"])

    def test_images_and_other_code_fields_are_not_qr_fallbacks(self):
        row = coupon()
        row["couponStyle"] = {"image": "https://example.invalid/artwork.png"}
        row["couponEntity"]["coupon_code"] = "TEST-ONLY-IDENTIFIER"
        self.assertIsNone(normalize_coupons({"couponList": [row]})[0]["qr_payload"])

    def test_only_observed_expired_and_used_modern_states_disable_qr(self):
        rows = normalize_coupons({"couponList": [
            coupon("expired", status="EXPIRED"), coupon("used", status="USED"),
            coupon("future", status="UPSTREAM_NEW_STATE"),
        ]})
        self.assertIsNone(rows[0]["qr_payload"])
        self.assertIsNone(rows[1]["qr_payload"])
        self.assertEqual(rows[2]["status"], "UPSTREAM_NEW_STATE")
        self.assertTrue(rows[2]["qr_payload"])

    def test_expiry_text_is_not_guessed_or_rewritten(self):
        row = coupon()
        row["couponEntity"]["expiredDate"] = "上游提供的有效期说明"
        self.assertEqual(normalize_coupons({"couponList": [row]})[0]["expires_at"], "上游提供的有效期说明")

    def test_legacy_requires_explicit_selection_and_honors_display_flag(self):
        row = {"couponNo": "test-legacy", "couponName": "测试旧券",
               "qrcode": {"content": "TEST-ONLY-LEGACY", "isDisplay": False}}
        with self.assertRaises(ProviderError):
            normalize_coupons({"couponList": [row]})
        self.assertIsNone(normalize_coupons({"couponList": [row]}, legacy=True)[0]["qr_payload"])
        row["qrcode"]["isDisplay"] = True
        self.assertEqual(normalize_coupons({"couponList": [row]}, legacy=True)[0]["qr_payload"], "TEST-ONLY-LEGACY")
        row["isExpireCoupon"] = True
        self.assertIsNone(normalize_coupons({"couponList": [row]}, legacy=True)[0]["qr_payload"])

    def test_nullable_dto_list_is_empty_but_missing_or_changed_schema_fails(self):
        self.assertEqual(normalize_coupons({"data": {"couponList": None}}), [])
        self.assertEqual(normalize_coupons({"couponList": []}), [])
        for raw in ({"data": {}}, {"couponList": {}}, {"result": {"couponList": []}},
                    {"couponList": [None]}, {"couponList": [{"couponEntity": "wrong"}]},
                    {"couponList": [{"couponSetList": "wrong"}]}):
            with self.subTest(raw=raw), self.assertRaises(ProviderError) as error:
                normalize_coupons(raw)
            self.assertEqual(error.exception.code, "coupon_schema_changed")

    def test_invalid_qr_types_fail_without_disclosing_credential(self):
        row = coupon(payload={"secret": "TEST-ONLY-PRIVATE"})
        with self.assertRaises(ProviderError) as error:
            normalize_coupons({"couponList": [row]})
        self.assertNotIn("TEST-ONLY-PRIVATE", str(error.exception))


if __name__ == "__main__":
    unittest.main()
