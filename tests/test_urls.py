"""Een openbare http(s)-URL (urls.public_http_url): alleen http en https, en geen adres op je eigen netwerk."""

import unittest

from helpers import db  # noqa: F401 - zet het pad naar de app
from mealieshopper import urls


class PublicHttpUrlTest(unittest.TestCase):
    def test_a_public_address_comes_back_stripped(self) -> None:
        self.assertEqual(urls.public_http_url("  https://www.ah.nl/allerhande/recept/x "), "https://www.ah.nl/allerhande/recept/x")
        self.assertEqual(urls.public_http_url("http://93.184.216.34/recipe"), "http://93.184.216.34/recipe")

    def test_private_and_local_hosts_are_refused(self) -> None:
        for value in ("http://localhost:9000/x", "http://mealie/x", "http://10.0.0.5/x", "http://[::1]/x", "http://192.168.1.10/r", "http://nas.local/x", "http://169.254.169.254/latest"):
            with self.subTest(value=value), self.assertRaises(urls.UnsafeUrl) as caught:
                urls.public_http_url(value)
            self.assertIn("eigen netwerk", str(caught.exception))

    def test_other_schemes_and_junk_are_refused(self) -> None:
        for value in ("ftp://ah.nl/x", "javascript:alert(1)", "file:///etc/passwd", "gewoon tekst", "http://", "", None, 5):
            with self.subTest(value=value), self.assertRaises(urls.UnsafeUrl) as caught:
                urls.public_http_url(value)
            self.assertIn("http:// of https://", str(caught.exception))

    def test_private_host_is_the_rule_the_mcp_tool_used(self) -> None:
        self.assertTrue(urls.private_host("localhost"))
        self.assertTrue(urls.private_host("nas"))
        self.assertFalse(urls.private_host("www.ah.nl"))
        self.assertFalse(urls.private_host("93.184.216.34"))


if __name__ == "__main__":
    unittest.main()
