import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"
# Bekende dubbelen die hetzelfde doen (NFKD en NFD geven na het weghalen van accenten dezelfde tekst).
KNOWN_TWINS = {"foldText"}
FUNCTION = re.compile(r"^(?:async\s+)?function\s+(\w+)\s*\(.*?^\}", re.M | re.S)


class StaticScriptsTest(unittest.TestCase):
    def test_no_global_function_is_defined_differently_in_two_scripts(self):
        # De scripts delen één globale scope: de laatst geladen definitie wint, stilletjes. Zo overschreef
        # een oude `renderStoreChoice` in picnic.js eens de nieuwe in app.js (Jumbo verdween uit de keuzelijst).
        # Een identieke kopie (zoals `postJson`) is onschuldig en telt niet mee.
        seen: dict[str, tuple[str, str]] = {}
        for script in sorted(STATIC.glob("*.js")):
            for match in FUNCTION.finditer(script.read_text(encoding="utf-8")):
                name, body = match.group(1), match.group(0)
                if name in seen and seen[name][1] != body and name not in KNOWN_TWINS:
                    self.fail(f"{name} staat in {seen[name][0]} en in {script.name} met een andere inhoud")
                seen.setdefault(name, (script.name, body))


if __name__ == "__main__":
    unittest.main()
