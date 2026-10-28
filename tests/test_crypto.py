import unittest

from h5_crypto import compact_json, decrypt_text, post_frame, sm3_hex


class H5CryptoTests(unittest.TestCase):
    def test_sm3_standard_vector(self):
        self.assertEqual(sm3_hex('abc'), '66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0')

    def test_observed_unsigned_empty_diagnostic_frame(self):
        payload = {'nonce': 0.123456}
        frame, signature = post_frame(payload)
        self.assertEqual(signature, 'dd661d50c28421d5506cc95173824d95fd43b0bcfa5f50b013241fc0cdcd863b')
        self.assertEqual(decrypt_text(frame['encrypted']), compact_json(payload))

    def test_nonfinite_values_are_rejected(self):
        with self.assertRaises(ValueError): post_frame({'nonce': float('nan')})


if __name__ == '__main__':
    unittest.main()
