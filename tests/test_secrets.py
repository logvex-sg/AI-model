"""Tests for secret redaction."""

from __future__ import annotations

import unittest

from kali_ops.secrets import REDACTED, contains_secret, find_secrets, redact


class RedactionTests(unittest.TestCase):
    def test_github_token_is_redacted(self) -> None:
        text = "token ghp_" + "a" * 30
        self.assertNotIn("ghp_", redact(text))
        self.assertIn(REDACTED, redact(text))

    def test_aws_key_is_redacted(self) -> None:
        self.assertIn(REDACTED, redact("AKIAIOSFODNN7EXAMPLE"))

    def test_jwt_is_redacted(self) -> None:
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghijklmnop"
        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9", redact(jwt))

    def test_private_key_block_is_redacted(self) -> None:
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"
        self.assertEqual(redact(pem), REDACTED)

    def test_password_assignment_is_redacted(self) -> None:
        out = redact("password=hunter2secret")
        self.assertNotIn("hunter2secret", out)
        self.assertIn(REDACTED, out)

    def test_bearer_header_is_redacted(self) -> None:
        out = redact("Authorization: Bearer abcdefghijklmnop")
        self.assertNotIn("abcdefghijklmnop", out)

    def test_plain_text_is_untouched(self) -> None:
        self.assertEqual(redact("just a normal log line"), "just a normal log line")

    def test_contains_secret_detects(self) -> None:
        self.assertTrue(contains_secret("AKIAIOSFODNN7EXAMPLE"))
        self.assertFalse(contains_secret("nothing here"))

    def test_find_secrets_names_patterns(self) -> None:
        names = find_secrets("AKIAIOSFODNN7EXAMPLE")
        self.assertIn("aws_access_key", names)


if __name__ == "__main__":
    unittest.main()
