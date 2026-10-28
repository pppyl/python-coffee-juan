"""Offline contracts: no real requests or SMS are sent."""
import json
import unittest
from unittest.mock import Mock, patch

import requests

from errors import ProviderError
from starbucks_client import (ACCOUNT_PATH, BFF_HOST, COUPONS_PATH, LOGIN_PATH,
                             SMS_PATH, TOKEN_PATH, StarbucksAppHttpProvider)

PHONE = "13900000000"
INSTALLATION = "0123456789abcdef0123456789abcdef"


def frame(method, path, payload, **options):
    return json.dumps(payload).encode(), {"test-protocol": "test-only"}


def response(data, status=200):
    return Mock(status_code=status, content=json.dumps(data).encode())


class StarbucksClientTests(unittest.TestCase):
    def setUp(self):
        self.framing = patch("starbucks_client.build_request", side_effect=frame).start()
        patch("starbucks_client.decode_response", side_effect=lambda raw, headers: json.loads(raw)).start()
        self.addCleanup(patch.stopall)
        self.transport = Mock()
        self.provider = StarbucksAppHttpProvider(self.transport, installation_id=INSTALLATION)

    def send(self):
        self.transport.request.return_value = response({"code": 200, "data": {}})
        self.provider.send_sms(PHONE)

    def prepare_login(self, profile=None):
        self.send()
        self.transport.request.reset_mock()
        self.transport.request.side_effect = [
            response({"code": 200, "data": {"token": "TEST-ONLY-CODE"}}),
            response({"code": 200, "data": {"access_token": "TEST-ONLY-ACCESS"}}),
            response({"code": "200", "data": profile if profile is not None else {"extra": {}, "nickName": "测试账号"}}),
        ]

    def test_sms_contract_and_no_automatic_retry(self):
        self.send()
        call = self.transport.request.call_args
        self.assertEqual(call.args, ("POST", BFF_HOST + SMS_PATH))
        body = json.loads(call.kwargs["data"])
        self.assertEqual((body["phoneNumber"], body["template"], body["token"]), (PHONE, "create-cn", ""))
        self.assertEqual(body["device"]["id"], INSTALLATION)
        self.assertFalse(call.kwargs["allow_redirects"])
        self.assertNotIn("x-msr-version", call.kwargs["headers"])
        self.transport.request.reset_mock()
        self.transport.request.side_effect = requests.Timeout()
        with self.assertRaises(ProviderError):
            self.provider.send_sms(PHONE)
        self.assertEqual(self.transport.request.call_count, 1)

    def test_login_exchange_and_protected_profile(self):
        self.prepare_login()
        self.provider.login(PHONE, "1234")
        calls = self.transport.request.call_args_list
        self.assertEqual([call.args[1] for call in calls], [BFF_HOST + p for p in (LOGIN_PATH, TOKEN_PATH, ACCOUNT_PATH)])
        self.assertEqual(calls[0].kwargs["headers"]["loginType"], "PIN_CODE")
        self.assertTrue(self.framing.call_args_list[-3].kwargs["form"])
        self.assertEqual(json.loads(calls[0].kwargs["data"])["pin"], "1234")
        self.assertEqual(json.loads(calls[1].kwargs["data"]), {"code": "TEST-ONLY-CODE", "grant_type": "authorization_code"})
        self.assertEqual(calls[1].kwargs["headers"]["x-msr-version"], "2")
        self.assertNotIn("x-msr-version", calls[0].kwargs["headers"])
        self.assertNotIn("x-msr-version", calls[2].kwargs["headers"])
        self.assertEqual(calls[2].kwargs["headers"]["Authorization"], "Bearer TEST-ONLY-ACCESS")
        self.assertEqual(calls[2].kwargs["headers"]["Accept"], "application/vnd.api+json")
        self.assertTrue(self.provider.authenticated())
        self.assertEqual(self.provider.user(), {"name": "测试账号"})
        self.assertIsNone(self.provider.sent_phone)

    def test_token_without_profile_is_not_logged_in(self):
        self.prepare_login(profile={})
        with self.assertRaises(ProviderError) as caught:
            self.provider.login(PHONE, "1234")
        self.assertEqual(caught.exception.code, "account_probe_failed")
        self.assertFalse(self.provider.authenticated())
        self.assertIsNone(self.provider.access_token)

    def test_business_error_does_not_record_sent_sms(self):
        self.transport.request.return_value = response({"code": 400, "message": "测试拒绝"})
        with self.assertRaises(ProviderError):
            self.provider.send_sms(PHONE)
        self.assertIsNone(self.provider.sent_phone)

    def test_login_validates_phone_and_four_digits_before_network(self):
        self.send()
        self.transport.request.reset_mock()
        for phone, code in (("13800000000", "1234"), (PHONE, "123456"), (PHONE, "１２３４")):
            with self.subTest(phone=phone, code=code), self.assertRaises(ProviderError):
                self.provider.login(phone, code)
        self.transport.request.assert_not_called()

    def test_all_pages_keep_exact_official_qr_content(self):
        self.provider.access_token, self.provider.profile = "TEST-ONLY-ACCESS", {"extra": {}}
        row = {"couponEntity": {"memberBenefitId": "test-one", "title": "测试券", "status": "AVAILABLE"},
               "couponStyle": {"qrcode": {"content": " TEST-ONLY=NOT-REDEEMABLE "}}}
        self.transport.request.side_effect = [
            response({"code": 200, "data": {"couponList": [row], "hasNext": True}}),
            response({"code": 200, "data": {"couponList": [], "hasNext": False}}),
        ]
        self.assertEqual(self.provider.coupons()[0]["qr_payload"], " TEST-ONLY=NOT-REDEEMABLE ")
        calls = self.transport.request.call_args_list
        self.assertEqual(len(calls), 2)
        for page, call in enumerate(calls, 1):
            self.assertEqual(call.args[1], BFF_HOST + COUPONS_PATH)
            self.assertEqual(json.loads(call.kwargs["data"])["params"], {"scene": "ME", "channel": "ALL", "pageNum": page})
            self.assertNotIn("x-msr-version", call.kwargs["headers"])

    def test_missing_pagination_never_returns_partial_list(self):
        self.provider.access_token, self.provider.profile = "TEST-ONLY-ACCESS", {"extra": {}}
        self.transport.request.return_value = response({"code": 200, "data": {"couponList": []}})
        with self.assertRaises(ProviderError) as caught:
            self.provider.coupons()
        self.assertEqual(caught.exception.code, "coupon_pagination_unknown")

    def test_expired_auth_clears_local_login(self):
        self.provider.access_token, self.provider.profile = "TEST-ONLY-ACCESS", {"extra": {}}
        self.transport.request.return_value = response({}, 401)
        with self.assertRaises(ProviderError):
            self.provider.coupons()
        self.assertFalse(self.provider.authenticated())


if __name__ == "__main__":
    unittest.main()
