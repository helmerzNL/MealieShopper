import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import ah  # noqa: E402


class ExtractOAuthCodeTest(unittest.TestCase):
    def test_full_url(self):
        self.assertEqual(ah.extract_oauth_code("appie://login-exit?code=abc123"), "abc123")

    def test_console_line_with_noise(self):
        line = "Failed to launch 'appie://login-exit?code=abc%2B123&state=x' because the protocol"
        self.assertEqual(ah.extract_oauth_code(line), "abc+123")

    def test_quoted_url(self):
        self.assertEqual(ah.extract_oauth_code("'appie://login-exit?code=abc123'"), "abc123")

    def test_bare_code(self):
        self.assertEqual(ah.extract_oauth_code("  abc123\n"), "abc123")


class ExchangeErrorTest(unittest.TestCase):
    def test_rejected_code(self):
        self.assertIn("verlopen of al gebruikt", ah._explain_exchange_error("400: bad code"))

    def test_blocked(self):
        self.assertIn("403", ah._explain_exchange_error("403: <H1>Access Denied</H1>"))

    def test_other_errors_pass_through(self):
        self.assertEqual(ah._explain_exchange_error("500: boom"), "500: boom")


class RotatedRefreshTokenTest(unittest.TestCase):
    def test_new_token_is_saved(self):
        with mock.patch.object(ah, "save_refresh_token") as save:
            ah._store_rotated_refresh_token("old", "new")
        save.assert_called_once_with("new")

    def test_same_or_missing_token_is_ignored(self):
        with mock.patch.object(ah, "save_refresh_token") as save:
            ah._store_rotated_refresh_token("old", "old")
            ah._store_rotated_refresh_token("old", None)
        save.assert_not_called()

    def test_get_user_token_persists_rotation(self):
        response = {"access_token": "acc", "refresh_token": "rotated", "expires_in": 3600}
        ah.user_token_cache = None
        with mock.patch.object(ah, "configured_refresh_token", return_value="old"), \
                mock.patch.object(ah, "_request_json", return_value=response), \
                mock.patch.object(ah, "save_refresh_token") as save:
            self.assertEqual(ah.get_user_token(), "acc")
        save.assert_called_once_with("rotated")
        ah.user_token_cache = None

    def test_verify_token_returns_rotated_token(self):
        fake = mock.Mock(ok=True)
        fake.json.return_value = {"refresh_token": "rotated"}
        with mock.patch.object(ah.requests, "post", return_value=fake):
            result = ah.verify_token("old")
        self.assertEqual(result, {"ok": True, "type": "refresh", "refreshToken": "rotated"})


if __name__ == "__main__":
    unittest.main()
