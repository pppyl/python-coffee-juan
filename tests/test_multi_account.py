import unittest
from unittest.mock import patch

from app import create_app
from errors import ProviderError


class IsolatedProvider:
    def __init__(self):
        self.phone = None
        self.logged_in = False
        self.fail = False

    def status(self):
        return {"ready": True, "mode": "test", "message": "测试专用"}

    def authenticated(self):
        return self.logged_in

    def user(self):
        return {"name": "测试账号"} if self.logged_in else None

    def send_sms(self, phone):
        self.phone = phone

    def login(self, phone, code):
        self.logged_in = phone == self.phone and code == "1234"

    def logout(self):
        self.logged_in = False

    def coupons(self):
        if self.fail:
            self.logged_in = False
            raise ProviderError("测试登录失效", 401, "starbucks_auth_required")
        return [{"id": "same-coupon-id", "title": "测试券", "status": "AVAILABLE",
                 "qr_payload": "TEST-ONLY-NOT-REDEEMABLE-" + self.phone}]


class MultiAccountFixture(unittest.TestCase):
    def setUp(self):
        self.now = 100000
        patch("app.time.monotonic", side_effect=lambda: self.now).start()
        self.addCleanup(patch.stopall)
        self.providers = []

        def factory():
            provider = IsolatedProvider()
            self.providers.append(provider)
            return provider

        self.app = create_app(factory)
        self.app.testing = True
        self.client = self.app.test_client()
        self.csrf = self.client.get("/api/status").json["csrf_token"]

    def post(self, path, payload):
        return self.client.post(path, json=payload, headers={"X-CSRFToken": self.csrf})

    def add(self, phone, code="1234"):
        self.now += 61
        self.assertEqual(self.post("/api/auth/sms", {"phone": phone}).status_code, 200)
        return self.post("/api/auth/login", {"phone": phone, "code": code})


class MultiAccountTests(MultiAccountFixture):
    def test_successful_additions_do_not_consume_failed_login_attempt_limit(self):
        for suffix in range(6):
            self.assertEqual(self.add("1390000000" + str(suffix)).status_code, 200)
        self.assertEqual(len(self.client.get("/api/status").json["accounts"]), 6)

    def test_two_logins_keep_independent_providers_and_mask_phone(self):
        first = self.add("13900000000").json["account"]
        second = self.add("13800000000").json["account"]
        self.assertNotEqual(first["id"], second["id"])
        status = self.client.get("/api/status").json
        self.assertEqual(len(status["accounts"]), 2)
        self.assertTrue(all(account["authenticated"] for account in status["accounts"]))
        self.assertEqual(first["phone"], "139****0000")
        self.assertTrue(self.providers[0].authenticated())
        self.assertTrue(self.providers[1].authenticated())
        self.assertIsNot(self.providers[0], self.providers[1])

    def test_identical_coupon_ids_cannot_cross_account_qr_caches(self):
        self.add("13900000000")
        self.add("13800000000")
        result = self.client.get("/api/accounts/coupons").json
        self.assertEqual((result["total_accounts"], result["succeeded"], result["total_coupons"]), (2, 2, 2))
        coupons = [item["coupons"][0] for item in result["results"]]
        self.assertEqual(coupons[0]["id"], coupons[1]["id"])
        self.assertNotEqual(coupons[0]["qr_url"], coupons[1]["qr_url"])
        self.assertTrue(all("qr_payload" not in row for row in coupons))
        images = [self.client.get(coupon["qr_url"]) for coupon in coupons]
        self.assertTrue(all(image.status_code == 200 for image in images))
        self.assertNotEqual(images[0].data, images[1].data)

    def test_failed_account_does_not_block_other_accounts(self):
        for phone in ("13900000000", "13800000000", "13700000000"):
            self.add(phone)
        self.providers[1].fail = True
        response = self.client.get("/api/accounts/coupons")
        self.assertEqual(response.status_code, 200)
        result = response.json
        self.assertEqual((result["succeeded"], result["failed"], result["total_coupons"]), (2, 1, 2))
        self.assertEqual([row["ok"] for row in result["results"]], [True, False, True])
        self.assertFalse(result["accounts"][1]["authenticated"])
        self.assertEqual(result["results"][1]["coupons"], [])

    def test_failed_new_login_leaves_existing_account_usable(self):
        first = self.add("13900000000").json["account"]
        self.assertEqual(self.add("13800000000", "0000").status_code, 409)
        status = self.client.get("/api/status").json
        self.assertEqual(len(status["accounts"]), 1)
        self.assertEqual(self.client.get("/api/coupons?account_id=" + first["id"]).status_code, 200)

    def test_logout_one_revokes_only_its_qr(self):
        first = self.add("13900000000").json["account"]
        second = self.add("13800000000").json["account"]
        result = self.client.get("/api/accounts/coupons").json
        urls = [item["coupons"][0]["qr_url"] for item in result["results"]]
        self.assertEqual(self.post("/api/accounts/" + first["id"] + "/logout", {}).status_code, 200)
        self.assertEqual(self.client.get(urls[0]).status_code, 401)
        self.assertEqual(self.client.get(urls[1]).status_code, 200)
        self.assertEqual(self.client.get("/api/status").json["accounts"][0]["id"], second["id"])
        self.post("/api/auth/logout", {})
        self.assertEqual(self.client.get(urls[1]).status_code, 401)
        self.assertEqual(self.client.get("/api/status").json["accounts"], [])

    def test_other_browser_cannot_fetch_or_remove_any_account(self):
        account = self.add("13900000000").json["account"]
        url = self.client.get("/api/coupons").json["coupons"][0]["qr_url"]
        stranger = self.app.test_client()
        status = stranger.get("/api/status").json
        self.assertEqual(status["accounts"], [])
        self.assertEqual(stranger.get("/api/accounts/coupons").status_code, 401)
        self.assertEqual(stranger.get(url).status_code, 401)
        self.assertEqual(stranger.post("/api/accounts/" + account["id"] + "/logout", json={},
                                      headers={"X-CSRFToken": status["csrf_token"]}).status_code, 401)
        self.assertTrue(self.client.get("/api/status").json["authenticated"])

    def test_duplicate_login_is_blocked_and_expired_account_can_be_replaced(self):
        first = self.add("13900000000").json["account"]
        self.now += 61
        with patch.object(self.providers[1], "send_sms", side_effect=AssertionError("must not send")):
            self.assertEqual(self.post("/api/auth/sms", {"phone": "13900000000"}).status_code, 409)
        self.providers[0].logged_in = False
        renewed = self.add("13900000000").json
        self.assertEqual(renewed["account"]["id"], first["id"])
        self.assertEqual(len(renewed["accounts"]), 1)
        self.assertTrue(renewed["account"]["authenticated"])


if __name__ == "__main__":
    unittest.main()
