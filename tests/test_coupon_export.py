import io
import unittest
import zipfile

from PIL import Image

from coupon_export import copy_text, image_download, matches_category, named_png
from errors import ProviderError
from test_multi_account import MultiAccountFixture


class CouponExportTests(unittest.TestCase):
    def row(self, title="8元电子代金券"):
        return {"title": title, "account_phone": "139****0000", "expires_at": "2099-12-31",
                "qr_payload": " TEST-ONLY=NOT-REDEEMABLE "}

    def test_amount_categories_do_not_match_larger_numbers_or_decimal_values(self):
        for value in ("8", "10", "15", "20", "30"):
            self.assertTrue(matches_category({"title": value + "元电子代金券"}, value))
        for title in ("18元券", "80元券", "108元券", "8.5元券", "8折券", "无金额优惠券"):
            self.assertFalse(matches_category({"title": title}, "8"))
            self.assertTrue(matches_category({"title": title}, "all"))
        self.assertTrue(matches_category({"title": "8.00元优惠券"}, "8"))
        for invalid in ("50", "ALL", {}, [], None):
            with self.assertRaises(ProviderError):
                matches_category({}, invalid)

    def test_copy_keeps_name_account_and_exact_qr_text(self):
        row = self.row()
        self.assertEqual(copy_text([row]), "8元电子代金券\n账号：139****0000\n TEST-ONLY=NOT-REDEEMABLE ")

    def test_named_png_contains_title_region_and_independently_decodable_qr(self):
        data = named_png(self.row())
        image = Image.open(io.BytesIO(data)).convert("RGB")
        self.assertEqual(image.width, 960)
        self.assertNotEqual(image.crop((64, 64, 896, 120)).getextrema(), ((255, 255),) * 3)
        try:
            import cv2
            import numpy as np
        except ImportError:
            return  # Optional independent decoder used in local verification.
        decoded, _, _ = cv2.QRCodeDetector().detectAndDecode(np.array(image))
        self.assertEqual(decoded, self.row()["qr_payload"])

    def test_zip_has_distinct_safe_named_pngs_even_for_duplicate_titles(self):
        rows = [self.row("8元券/测试"), self.row("8元券/测试")]
        data, mime, name = image_download(rows, "8")
        self.assertEqual((mime, name), ("application/zip", "好礼券_8元.zip"))
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            self.assertEqual(len(names), 2)
            self.assertEqual(len(set(names)), 2)
            self.assertTrue(all("/" not in item and "8元券_测试" in item for item in names))
            self.assertTrue(all(archive.read(item).startswith(b"\x89PNG") for item in names))


class AccountExportTests(MultiAccountFixture):
    def setUp(self):
        super().setUp()
        self.first = self.add("13900000000").json["account"]
        self.second = self.add("13800000000").json["account"]
        for provider, title in zip(self.providers[:2], ("8元电子代金券", "15元电子代金券")):
            phone = provider.phone
            provider.coupons = lambda title=title, phone=phone: [{
                "id": "same-coupon-id", "title": title, "status": "AVAILABLE",
                "qr_payload": "TEST-ONLY-NOT-REDEEMABLE-" + phone}]
        self.client.get("/api/accounts/coupons")

    def payload(self, category="all"):
        return {"category": category, "account_ids": [self.first["id"], self.second["id"]]}

    def test_category_exports_span_accounts_without_exposing_other_values(self):
        response = self.post("/api/exports/copy", self.payload("8"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["count"], 1)
        self.assertIn("8元电子代金券", response.json["text"])
        self.assertNotIn("15元电子代金券", response.json["text"])
        self.assertEqual(self.post("/api/exports/copy", self.payload()).json["count"], 2)
        self.assertEqual(self.post("/api/exports/copy", self.payload("10")).status_code, 404)

    def test_export_requires_csrf_and_owned_accounts(self):
        self.assertEqual(self.client.post("/api/exports/copy", json=self.payload()).status_code, 403)
        stranger = self.app.test_client()
        token = stranger.get("/api/status").json["csrf_token"]
        self.assertEqual(stranger.post("/api/exports/copy", json=self.payload(), headers={"X-CSRFToken": token}).status_code, 401)

    def test_single_png_and_multi_zip_with_name(self):
        single = self.post("/api/exports/download", self.payload("8"))
        self.assertEqual(single.mimetype, "image/png")
        self.assertTrue(single.data.startswith(b"\x89PNG"))
        self.assertIn("attachment", single.headers["Content-Disposition"])
        both = self.post("/api/exports/download", self.payload())
        self.assertEqual(both.mimetype, "application/zip")
        self.assertEqual(len(zipfile.ZipFile(io.BytesIO(both.data)).namelist()), 2)

    def test_expired_cache_and_logged_out_accounts_cannot_be_exported(self):
        self.now += 301
        self.assertEqual(self.post("/api/exports/copy", self.payload()).status_code, 410)
        self.post("/api/accounts/" + self.first["id"] + "/logout", {})
        self.assertEqual(self.post("/api/exports/download", self.payload()).status_code, 401)


if __name__ == "__main__":
    unittest.main()
