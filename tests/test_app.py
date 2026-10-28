import unittest
from unittest.mock import patch

from app import create_app
from errors import ProviderError
from http_provider import StarbucksHttpProvider, pointer, render


class TestProvider:
    """Synthetic data lives only in tests, never in the running application."""
    def __init__(self): self.logged_in = False
    def status(self): return {"ready": True, "mode": "test", "message": "test"}
    def authenticated(self): return self.logged_in
    def user(self): return {"name": "Test"} if self.logged_in else None
    def send_sms(self, phone): return None
    def login(self, phone, code): self.logged_in = code == "123456"
    def logout(self): self.logged_in = False
    def coupons(self): return [{"id": "test-only", "title": "Test fixture", "status": "test", "qr_payload": "TEST-ONLY-NOT-A-COUPON"}]


class AppTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestProvider)
        self.app.testing = True
        self.client = self.app.test_client()
        self.csrf = self.client.get('/api/status').json['csrf_token']

    def post(self, path, data, client=None, token=None):
        return (client or self.client).post(path, json=data, headers={'X-CSRFToken': token or self.csrf})

    def login(self):
        self.assertEqual(self.post('/api/auth/sms', {'phone': '13900000000'}).status_code, 200)
        self.assertEqual(self.post('/api/auth/login', {'phone': '13900000000', 'code': '123456'}).status_code, 200)

    def test_mutation_requires_csrf_and_same_origin(self):
        self.assertEqual(self.client.post('/api/auth/sms', json={'phone': '13900000000'}).status_code, 403)
        response = self.client.post('/api/auth/sms', json={'phone': '13900000000'}, headers={'X-CSRFToken': self.csrf, 'Origin': 'https://example.org'})
        self.assertEqual(response.status_code, 403)

    def test_validation_precedes_provider_call(self):
        with patch.object(TestProvider, 'send_sms', side_effect=AssertionError('must not send')):
            self.assertEqual(self.post('/api/auth/sms', {'phone': 'bad'}).status_code, 400)
        self.assertEqual(self.post('/api/auth/login', {'phone': '13900000000', 'code': '123456'}).status_code, 409)

    def test_sms_cooldown_prevents_repeated_send(self):
        self.post('/api/auth/sms', {'phone': '13900000000'})
        self.assertEqual(self.post('/api/auth/sms', {'phone': '13900000000'}).status_code, 429)

    def test_successful_transport_is_not_successful_login(self):
        self.post('/api/auth/sms', {'phone': '13900000000'})
        response = self.post('/api/auth/login', {'phone': '13900000000', 'code': '000000'})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(self.client.get('/api/status').json['authenticated'])

    def test_qr_only_after_auth_and_uses_private_server_data(self):
        self.assertEqual(self.client.get('/api/coupons/test-only/qr.png').status_code, 401)
        self.login()
        data = self.client.get('/api/coupons').json
        self.assertNotIn('qr_payload', data['coupons'][0])
        response = self.client.get(data['coupons'][0]['qr_url'])
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertIn('no-store', response.headers['Cache-Control'])
        stranger = self.app.test_client()
        self.assertEqual(stranger.get(data['coupons'][0]['qr_url']).status_code, 401)

    def test_logout_revokes_cached_coupon(self):
        self.login()
        data = self.client.get('/api/coupons').json
        self.post('/api/auth/logout', {})
        self.assertEqual(self.client.get(data['coupons'][0]['qr_url']).status_code, 401)

    def test_host_header_is_restricted(self):
        self.assertEqual(self.client.get('/api/status', headers={'Host': 'evil.example'}).status_code, 403)

    def test_unverified_profile_never_sends_sms(self):
        provider = StarbucksHttpProvider(profile={'verified': False})
        with patch.object(provider.http, 'request', side_effect=AssertionError('network must stay unused')):
            with self.assertRaises(ProviderError) as error:
                provider.send_sms('13900000000')
            self.assertEqual(error.exception.status, 503)

    def test_json_references_preserve_types_and_handle_escaping(self):
        self.assertEqual(pointer({'a/b': {'~': [42]}}, '/a~1b/~0/0'), 42)
        self.assertEqual(render({'value': {'$ref': 'result'}}, {'result': {'a': 1}}), {'value': {'a': 1}})
        with self.assertRaises(ProviderError): render({'$ref': 'absent'}, {})


class HttpAdapterTests(unittest.TestCase):
    def provider(self):
        # The endpoint paths/field names in this fixture are synthetic contracts.
        profile = {
            'verified': True,
            'endpoints': {name: {'url': 'https://profile.starbucks.com.cn/test-fixture/' + name,
                                 'method': 'POST', 'success': [{'pointer': '/ok', 'equals': True}]}
                          for name in ['sms', 'verify', 'account', 'coupons']},
            'coupon_items_pointer': '/items',
            'coupon_fields': {'id': '/id', 'title': '/title', 'status': '/status', 'qr_payload': '/code'},
        }
        return StarbucksHttpProvider(profile)

    @staticmethod
    def response(status, data):
        from unittest.mock import Mock
        value = Mock(status_code=status)
        value.json.return_value = data
        return value

    def test_account_probe_must_succeed_before_authentication(self):
        provider = self.provider()
        provider.context = {'phone': '13900000000'}
        with patch.object(provider.http, 'request', side_effect=[self.response(200, {'ok': True}), self.response(401, {})]):
            with self.assertRaises(ProviderError): provider.login('13900000000', '123456')
        self.assertFalse(provider.authenticated())
        self.assertNotIn('code', provider.context)

    def test_redirect_is_not_followed_or_treated_as_login(self):
        provider = self.provider()
        with patch.object(provider.http, 'request', return_value=self.response(302, {})) as request:
            with self.assertRaises(ProviderError): provider.send_sms('13900000000')
            self.assertIs(request.call_args.kwargs['allow_redirects'], False)

    def test_qr_is_never_inferred_from_coupon_id(self):
        provider = self.provider()
        provider._authenticated = True
        value = {'ok': True, 'items': [{'id': '123456', 'title': 'Test', 'status': 'Test'}]}
        with patch.object(provider.http, 'request', return_value=self.response(200, value)):
            coupons = provider.coupons()
        self.assertIsNone(coupons[0].get('qr_payload'))

    def test_response_schema_change_is_an_error(self):
        provider = self.provider()
        provider._authenticated = True
        with patch.object(provider.http, 'request', return_value=self.response(200, {'ok': True, 'items': {}})):
            with self.assertRaises(ProviderError) as error: provider.coupons()
        self.assertEqual(error.exception.code, 'coupon_schema_changed')


if __name__ == '__main__':
    unittest.main()
