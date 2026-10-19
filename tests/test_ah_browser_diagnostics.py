import unittest

from mealieshopper import ah_browser


class FakeDriver:
    title = "AH.nl Login"

    def find_elements(self, *args, **kwargs):
        return []

    def execute_script(self, script):
        return "  Inloggen\n\n Het ingevoerde wachtwoord  klopt niet.  "


class CollectDiagnosticsTest(unittest.TestCase):
    def test_visible_page_text_is_included_and_collapsed(self):
        result = ah_browser._collect_form_diagnostics(FakeDriver())
        self.assertIn("title='AH.nl Login'", result)
        self.assertIn("page='Inloggen Het ingevoerde wachtwoord klopt niet.'", result)

    def test_long_page_text_is_truncated(self):
        driver = FakeDriver()
        driver.execute_script = lambda script: "x" * 1000
        result = ah_browser._collect_form_diagnostics(driver)
        self.assertIn("page='" + "x" * 300 + "'", result)
        self.assertNotIn("x" * 301, result)


if __name__ == "__main__":
    unittest.main()
