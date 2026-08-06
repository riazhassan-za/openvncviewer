"""Saved-password encryption.

The property that matters: what lands on disk must not contain the password,
and anything that will not decrypt must come back as None rather than raising
or, worse, as garbage that gets typed at a server.
"""

import sys
import unittest

from openvncviewer import secretstore

WINDOWS_ONLY = unittest.skipUnless(sys.platform.startswith("win"),
                                   "DPAPI is a Windows facility")


class AvailabilityTest(unittest.TestCase):
    def test_availability_matches_the_platform(self):
        self.assertEqual(secretstore.available(), sys.platform.startswith("win"))

    def test_without_a_backend_nothing_is_stored_in_the_clear(self):
        """The fallback must be 'cannot save', never 'save it unprotected'."""
        if secretstore.available():
            self.skipTest("backend present; the fallback path is not exercised")
        self.assertIsNone(secretstore.encrypt("hunter2"))
        self.assertIsNone(secretstore.decrypt("anything"))


@WINDOWS_ONLY
class EncryptionTest(unittest.TestCase):
    def test_round_trip(self):
        for secret in ("hunter2", "a", "x" * 500, "pä55wörd ✓", "  spaces  "):
            with self.subTest(secret=secret):
                self.assertEqual(secretstore.decrypt(secretstore.encrypt(secret)),
                                 secret)

    def test_the_token_does_not_contain_the_password(self):
        token = secretstore.encrypt("SuperSecret123")
        self.assertNotIn("SuperSecret123", token)
        self.assertNotIn("SuperSecret123",
                         __import__("base64").b64decode(token).decode("latin-1"))

    def test_the_same_password_encrypts_differently_each_time(self):
        """Identical tokens would leak that two servers share a password."""
        self.assertNotEqual(secretstore.encrypt("same"),
                            secretstore.encrypt("same"))

    def test_empty_input_stores_nothing(self):
        self.assertIsNone(secretstore.encrypt(""))
        self.assertIsNone(secretstore.encrypt(None))

    def test_undecryptable_tokens_return_none(self):
        for token in ("", None, "not base64 at all!", "aGVsbG8=",
                      "AQAAANCMnd8BFdERjHoAwE/Cl+sBAAAAdeadbeef"):
            with self.subTest(token=token):
                self.assertIsNone(secretstore.decrypt(token))

    def test_a_tampered_token_does_not_decrypt(self):
        token = secretstore.encrypt("hunter2")
        flipped = token[:-8] + ("A" if token[-8] != "A" else "B") + token[-7:]
        self.assertIsNone(secretstore.decrypt(flipped))


if __name__ == "__main__":
    unittest.main()
