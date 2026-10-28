from pathlib import Path
import tempfile
import unittest

from app_identity import (
    API_VERSION, CARD_MSR_VERSION, GATEWAY_GRANT_TYPE, GATEWAY_MSR_VERSION,
    MSR_VERSION, PIN_LOGIN_TYPE, SMS_TEMPLATE,
    build_bff2_base, build_device_entity, common_headers, make_installation_id,
    unwrap_bff, user_detail_body,
)
from errors import ProviderError


INSTALLATION = "0123456789abcdef0123456789abcdef"


class AppIdentityTests(unittest.TestCase):
    def test_installation_uuid_is_persistent_and_has_native_format(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "client" / "installation-id"
            first = make_installation_id(path)
            self.assertRegex(first, r"^[0-9a-f]{32}$")
            self.assertEqual(make_installation_id(path), first)
            self.assertEqual(path.read_text().strip(), first)

    def test_invalid_saved_identity_is_not_silently_rotated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "installation-id"
            path.write_text("invalid")
            with self.assertRaises(ProviderError):
                make_installation_id(path)
            self.assertEqual(path.read_text(), "invalid")

    def test_minimum_device_omits_hardware_and_risk_fingerprints(self):
        value = build_device_entity(INSTALLATION)
        self.assertEqual(value, {
            "os_name": "Android", "language": "zh", "timezone": "Asia/Shanghai",
            "id": INSTALLATION, "app_version": "10.30.1",
        })
        self.assertNotIn("manufacturer", value)
        self.assertNotIn("model", value)
        self.assertNotIn("user_agent", value)
        self.assertNotIn("device_external_code", value)

    def test_explicit_actual_device_context_uses_serialized_wire_keys(self):
        context = {"manufacturer": "test-only", "model": "test-runtime", "os_version": "test-os",
                   "language": "zh-CN", "device_external_code": "TEST-ONLY-ACTUAL-CALLER-INPUT"}
        value = build_device_entity(INSTALLATION, context=context)
        self.assertEqual(value["language"], "zh")
        self.assertEqual(value["user_agent"], "Android com.starbucks.cn.platform/10.30.1 (test-runtime; test-os)")
        self.assertEqual(value["device_external_code"], context["device_external_code"])
        self.assertNotIn("deviceFpCode", value)
        self.assertNotIn("latitude", value)

    def test_non_android_context_does_not_get_an_invented_android_device_agent(self):
        value = build_device_entity(INSTALLATION, context={"os_name": "Windows", "model": "test", "os_version": "test"})
        self.assertEqual(value["os_name"], "Windows")
        self.assertNotIn("user_agent", value)

    def test_headers_match_observed_interceptors_and_not_body_agent(self):
        headers = common_headers(INSTALLATION)
        self.assertEqual(headers["User-Agent"], "com.starbucks.cn/10.30.1")
        self.assertEqual(headers["x-language-type"], "zh_CN")
        self.assertNotIn("x-msr-version", headers)
        self.assertEqual(headers["X-API-Version"], "2.0")
        self.assertEqual(headers["x-device-id"], INSTALLATION)
        for key in ("Authorization", "Sm-Token", "x-bs-device-id", "x-member-level"):
            self.assertNotIn(key, headers)

    def test_sdk_values_only_come_from_explicit_context(self):
        headers = common_headers(INSTALLATION, access_token="TEST-ONLY-TOKEN", context={
            "language": "en", "sm_token": "TEST-ONLY-SDK", "device_external_code": "TEST-ONLY-DFP",
            "member_level": "green", "experience_level": "test-level",
        })
        self.assertEqual(headers["Authorization"], "Bearer TEST-ONLY-TOKEN")
        self.assertEqual(headers["x-language-type"], "en_US")
        self.assertEqual(headers["Sm-Token"], "TEST-ONLY-SDK")
        self.assertEqual(headers["x-bs-device-id"], "TEST-ONLY-DFP")
        self.assertEqual(headers["x-member-level"], "GREEN")
        self.assertEqual(headers["x-experience-level"], "TEST-LEVEL")

    def test_msr_version_is_explicit_for_the_selected_route(self):
        self.assertNotIn("x-msr-version", common_headers(INSTALLATION))
        self.assertEqual(common_headers(INSTALLATION, msr_version=GATEWAY_MSR_VERSION)["x-msr-version"], "2")
        self.assertEqual(common_headers(INSTALLATION, msr_version=CARD_MSR_VERSION)["x-msr-version"], "4")
        with self.assertRaises(ProviderError):
            common_headers(INSTALLATION, msr_version="2\r\nInjected: true")

    def test_newline_header_injection_and_missing_identity_are_rejected(self):
        with self.assertRaises(ProviderError):
            common_headers(INSTALLATION, access_token="test\r\nInjected: true")
        with self.assertRaises(ProviderError):
            common_headers(INSTALLATION, context={"sm_token": "test\nsecret"})
        with self.assertRaises(ProviderError):
            build_device_entity("not-an-installation-id")

    def test_bff_success_is_body_code_2xx_and_sms_empty_data_is_valid(self):
        self.assertEqual(unwrap_bff({"code": 200, "data": {}}), {})
        self.assertIsNone(unwrap_bff({"code": 204, "data": None}))
        self.assertEqual(unwrap_bff({"code": 299, "data": {"token": "TEST-ONLY"}}), {"token": "TEST-ONLY"})
        for code in (0, 199, 300, True, "200", None):
            with self.subTest(code=code), self.assertRaises(ProviderError):
                unwrap_bff({"code": code, "data": {"token": "TEST-ONLY"}})

    def test_official_business_error_keeps_message_and_numeric_code(self):
        with self.assertRaises(ProviderError) as error:
            unwrap_bff({"code": 401, "message": "测试错误", "error": {"token": "TEST-PRIVATE"}})
        self.assertEqual(error.exception.message, "测试错误")
        self.assertEqual(error.exception.details, {"official_code": 401})
        self.assertNotIn("TEST-PRIVATE", str(error.exception))

    def test_exact_login_constants_and_auxiliary_request_bodies(self):
        self.assertEqual((API_VERSION, MSR_VERSION), ("2.0", "4"))
        self.assertEqual(PIN_LOGIN_TYPE, "PIN_CODE")
        self.assertEqual(GATEWAY_GRANT_TYPE, "authorization_code")
        self.assertEqual(SMS_TEMPLATE, "create-cn")
        self.assertEqual(user_detail_body(), {"adCode": ""})
        self.assertEqual(build_bff2_base(), {"memberLevel": "", "adCode": "", "birthday": ""})
        self.assertEqual(build_bff2_base({"memberLevel": "test-level", "adCode": "test-code", "birthday": "test-date"}),
                         {"memberLevel": "TEST-LEVEL", "adCode": "test-code", "birthday": "test-date"})


if __name__ == "__main__":
    unittest.main()
