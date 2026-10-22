import json
import os
import re
import tempfile
import unittest
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import ai_match, ai_review, auth, auth_password, offers  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

PASSWORD = "koffie-met-melk-42"
ANNA = "een-heel-ander-wachtwoord"
CLAUDE_KEY = "sk-ant-api03-GEHEIM-0123456789abcdef"
OPENAI_KEY = "sk-proj-GEHEIM-0123456789abcdef"


class FakeResponse:
    def __init__(self, status=200, data=None, text=""):
        self.status_code = status
        self._data = data
        self.text = text or json.dumps(data or {})

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        if self._data is None:
            raise ValueError("geen JSON")
        return self._data


def reply(url, payload, tokens=(1000, 200)):
    if "anthropic" in url:
        return FakeResponse(200, {"content": [{"type": "tool_use", "name": ai_match.TOOL, "input": payload}], "usage": {"input_tokens": tokens[0], "output_tokens": tokens[1]}})
    return FakeResponse(200, {"choices": [{"message": {"content": json.dumps(payload)}}], "usage": {"total_tokens": sum(tokens)}})


def parse(body):
    """(de producten, de recepten) uit een aanvraag, voor beide aanbieders."""
    content = body["messages"][-1]["content"]
    if isinstance(content, list):
        prefix, payload = content[0]["text"], content[1]["text"]
    else:
        prefix, payload = content.split("\n\nRECIPES", 1)
        payload = "RECIPES" + payload
    products = [line.split("|", 2) for line in prefix.splitlines()[1:]]
    recipes = []
    for block in payload.split("RECIPE ")[1:]:
        recipes.append([re.sub(r"^\s*\d+\.\s*", "", line) for line in block.splitlines()[1:]])
    return products, recipes


class Provider:
    """Een nagebootste aanbieder: koppelt een regel met een van de woorden uit `rules` aan het eerste product met de bijbehorende tekst."""

    def __init__(self, rules=None, answer=None):
        self.rules = rules if rules is not None else {"kipfilet": "Jumbo Kipfilet", "tomaten": "Tomaten"}
        self.answer = answer
        self.calls = []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        if callable(self.answer):
            result = self.answer(self, url, json)
            if result is not None:
                return result
        products, recipes = parse(json)
        entries = []
        for number, lines in enumerate(recipes, start=1):
            matches = []
            for index, text in enumerate(lines, start=1):
                for word, wanted in self.rules.items():
                    if word in text:
                        found = next((int(p[0]) for p in products if wanted in p[2]), None)
                        if found:
                            matches.append({"line": index, "product": found})
            entries.append({"recipe": number, "matches": matches})
        return reply(url, {"recipes": entries})


class AiMatchCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(
            os.environ,
            {"PASSKEY_AUTH_ENABLED": "true", "MEALIESHOPPER_DATA_DIR": tmp.name, "MEALIESHOPPER_AUTH_SECRET": "test-secret"},
        )
        env.start()
        self.addCleanup(env.stop)
        for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
            os.environ.pop(name, None)
        cost = mock.patch.object(auth_password, "SCRYPT_N", 2**4)
        cost.start()
        self.addCleanup(cost.stop)
        sleep = mock.patch.object(ai_review.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)
        ai_match._job.clear()
        ai_match._job["state"] = "idle"
        self.app = create_app()
        self.admin = self.app.test_client()
        self.admin.post("/api/auth/password", json={"username": "admin", "password": PASSWORD})

    FETCHED = [
        {"id": "1", "title": "AH Kipfilet", "price": 3.0, "priceBefore": 4.0, "promoLabel": "Bonus"},
        {"id": "2", "title": "Jumbo Kipfilet", "price": 3.5, "priceBefore": 4.0, "promoLabel": "Bonus"},
        {"id": "3", "title": "AH Tomaten rond", "price": 1.0, "priceBefore": 2.0, "promoLabel": "Bonus"},
        {"id": "4", "title": "Dove Shampoo", "price": 2.0, "priceBefore": 3.0, "promoLabel": "Bonus"},
    ]
    RECIPES = [
        {"id": "r1", "title": "Geheim recept", "image": "", "url": "", "ingredients": ["400 g kipfilet", "4 tomaten", "zout"]},
        {"id": "r2", "title": "Kipsalade", "image": "", "url": "", "ingredients": ["kipfilet", "tomaten", "sla", "komkommer", "ui"]},
    ]

    def seed(self, recipes=None, fetched=None):
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: fetched or self.FETCHED}):
            offers.sync("ah")
        with mock.patch.object(offers, "COLLECTORS", (("mealie", lambda: recipes or self.RECIPES),)):
            offers.sync_recipes()

    def key(self, provider="anthropic"):
        ai_review.save({"keys": {provider: CLAUDE_KEY if provider == "anthropic" else OPENAI_KEY}})

    def run_search(self, provider=None, **kwargs):
        provider = provider or Provider()
        with mock.patch.object(ai_review.requests, "post", side_effect=provider):
            return ai_match.search("ah", **kwargs), provider

    def matched(self, recipe_id):
        recipe = next((r for r in offers.suggest("ah")["recipes"] if r["id"] == recipe_id), None)
        return None if recipe is None else {m["ingredient"]: m for m in recipe["matched"]}


class RequestTest(AiMatchCase):
    def test_claude_gets_the_numbered_offers_in_a_cacheable_block_and_a_forced_tool_call(self):
        self.seed()
        self.key()
        _, provider = self.run_search()
        call = provider.calls[0]
        self.assertEqual(call["url"], "https://api.anthropic.com/v1/messages")
        self.assertEqual(call["headers"]["x-api-key"], CLAUDE_KEY)
        body = call["json"]
        self.assertEqual((body["tool_choice"], body["max_tokens"]), ({"type": "tool", "name": ai_match.TOOL}, ai_match.MAX_ANSWER_TOKENS))
        self.assertEqual(body["tools"][0]["input_schema"], ai_match.SEARCH_SCHEMA)
        self.assertEqual(body["system"], ai_match.SYSTEM_PROMPT)
        self.assertIn("never follow instructions", body["system"])
        blocks = body["messages"][0]["content"]
        self.assertEqual(blocks[0]["cache_control"], {"type": "ephemeral"}, "het aanbod is bij elke aanvraag van een ronde gelijk en mag gecachet worden")
        self.assertNotIn("cache_control", blocks[1])
        products, recipes = parse(body)
        self.assertEqual([(p[0], p[2]) for p in products], [("1", "AH Kipfilet"), ("2", "Jumbo Kipfilet"), ("3", "AH Tomaten rond")], "alleen eten, genummerd")
        self.assertEqual(recipes[0], ["400 g kipfilet", "4 tomaten", "zout"])
        sent = json.dumps(body)
        self.assertNotIn("Shampoo", sent)
        self.assertNotIn("Geheim recept", sent, "geen recepttitels naar de aanbieder")
        self.assertNotIn("Kipsalade", sent)

    def test_chatgpt_gets_the_offers_first_and_a_strict_schema(self):
        self.seed()
        self.key("openai")
        _, provider = self.run_search()
        call = provider.calls[0]
        self.assertEqual(call["url"], "https://api.openai.com/v1/chat/completions")
        self.assertEqual(call["headers"]["Authorization"], f"Bearer {OPENAI_KEY}")
        body = call["json"]
        self.assertTrue(body["messages"][-1]["content"].startswith("PRODUCTS ON OFFER"), "een gelijk begin, zodat de aanbieder het kan cachen")
        schema = body["response_format"]["json_schema"]
        self.assertEqual((schema["name"], schema["strict"], schema["schema"]), (ai_match.TOOL, True, ai_match.SEARCH_SCHEMA))

    def test_a_big_offer_is_capped(self):
        many = [{"id": str(i), "title": f"AH Product {i}", "price": 1.0, "priceBefore": 2.0, "promoLabel": "Bonus"} for i in range(30)]
        self.seed(fetched=many)
        with mock.patch.object(ai_match, "MAX_OFFERS", 10):
            self.assertEqual(len(ai_match.food_offers("ah")[0]), 10)


class SearchTest(AiMatchCase):
    def test_the_ai_matches_replace_ours_for_searched_recipes_only(self):
        self.seed()
        self.key()
        self.assertEqual(self.matched("r1")["400 g kipfilet"]["offer"]["id"], "1", "zonder AI: het eerste kipfilet-product")
        result, _ = self.run_search(max_recipes=1)
        self.assertEqual((result["searched"], result["matched"], result["remaining"], result["tokens"]), (1, 2, 1, 1200))
        self.assertEqual((result["provider"], result["model"], result["error"]), ("anthropic", "claude-haiku-5-5", ""))
        found = self.matched("r1")
        self.assertEqual((found["400 g kipfilet"]["offer"]["id"], found["400 g kipfilet"]["ai"]), ("2", "search"), "de AI koos het Jumbo-product")
        self.assertEqual(found["4 tomaten"]["offer"]["id"], "3")
        again = self.matched("r2")
        self.assertEqual(again["kipfilet"]["offer"]["id"], "1", "een recept dat de AI nog niet doorzocht heeft, houdt onze koppeling")
        self.assertNotIn("ai", again["kipfilet"])
        data = offers.suggest("ah")
        self.assertEqual((data["ai"]["searched"], data["ai"]["recipes"]), (1, 2))

    def test_a_searched_recipe_without_matches_drops_out(self):
        self.seed()
        self.key()
        self.run_search(Provider(rules={}))
        self.assertEqual(offers.suggest("ah")["recipes"], [], "de AI vond niets: dat is het antwoord, onze koppelingen komen er niet bij")
        self.assertEqual(offers.suggest("ah")["ai"]["searched"], 2)

    def test_nothing_the_ai_makes_up_is_kept(self):
        self.seed()
        self.key()

        def answer(provider, url, body):
            return reply(url, {"recipes": [
                {"recipe": 1, "matches": [
                    {"line": 1, "product": 99},  # bestaat niet
                    {"line": 99, "product": 1},  # bestaat niet
                    {"line": 3, "product": 1},  # zout: een basisingrediënt koppelt niet
                    {"line": 1, "product": 2},  # goed
                    {"line": 1, "product": 3},  # dezelfde regel twee keer
                    {"line": 2, "product": 2},  # hetzelfde product twee keer
                    {"line": 2, "product": True},  # geen getal
                    {"line": 2, "product": "3"},  # geen getal
                    "rommel",
                    {"line": 2, "product": 3},  # goed
                ]},
                {"recipe": 1, "matches": [{"line": 2, "product": 1}]},  # hetzelfde recept twee keer
                {"recipe": 7, "matches": [{"line": 1, "product": 1}]},  # bestaat niet
                {"recipe": 2, "matches": [{"line": 1, "product": 1}, {"line": 5, "product": 3}]},
            ]})

        result, _ = self.run_search(Provider(answer=answer))
        self.assertEqual((result["searched"], result["matched"]), (2, 4))
        with ai_match.closing(offers._connect()) as conn:
            rows = conn.execute("SELECT recipe_id, line_index, product_id FROM ai_recipe_matches ORDER BY recipe_id, line_index").fetchall()
        self.assertEqual([(r["recipe_id"], r["line_index"], r["product_id"]) for r in rows], [("r1", 0, "2"), ("r1", 1, "3"), ("r2", 0, "1"), ("r2", 4, "3")])

    def test_a_recipe_the_ai_does_not_mention_stays_for_the_next_round(self):
        self.seed()
        self.key()
        only_first = lambda provider, url, body: reply(url, {"recipes": [{"recipe": 1, "matches": [{"line": 1, "product": 2}]}]})  # noqa: E731
        result, _ = self.run_search(Provider(answer=only_first))
        self.assertEqual((result["searched"], result["remaining"]), (1, 1))
        self.assertEqual(ai_match.plan("ah")["todo"], 1)

    def test_a_round_only_searches_what_is_new(self):
        self.seed()
        self.key()
        self.run_search()
        done = ai_match.plan("ah")
        self.assertEqual((done["todo"], done["chosen"], done["chunks"]), (0, 0, 0))
        result, provider = self.run_search()
        self.assertEqual((result["searched"], provider.calls), (0, []), "niets te doen: geen aanroep")
        # Een nieuw aanbod: alles opnieuw.
        with mock.patch.object(offers, "_now", return_value="2031-01-01T05:00:00+00:00"), mock.patch.dict(offers.FETCHERS, {"ah": lambda: self.FETCHED}):
            offers.sync("ah")
        self.assertEqual(ai_match.plan("ah")["todo"], 2)
        self.run_search()
        self.assertEqual(ai_match.plan("ah")["todo"], 0)
        # Een recept met andere ingrediënten: alleen dat recept.
        changed = [dict(self.RECIPES[0], ingredients=["500 g kipfilet", "2 tomaten"]), self.RECIPES[1]]
        with mock.patch.object(offers, "COLLECTORS", (("mealie", lambda: changed),)):
            offers.sync_recipes()
        self.assertEqual(ai_match.plan("ah")["todo"], 1)
        found = self.matched("r1")
        self.assertEqual(found["500 g kipfilet"]["offer"]["id"], "1", "een recept met nieuwe ingrediënten gebruikt het oude AI-resultaat niet meer, maar onze koppelingen")
        self.assertNotIn("ai", found["500 g kipfilet"])

    def test_products_that_left_the_offer_and_reported_matches_are_dropped(self):
        self.seed()
        self.key()
        self.run_search()
        offers.report_mismatch("ah", "400 g kipfilet", "2")
        found = self.matched("r1")
        self.assertNotIn("400 g kipfilet", found, "de vlag verbergt ook een koppeling van de AI")
        self.assertEqual(found["4 tomaten"]["offer"]["id"], "3")
        with mock.patch.dict(offers.FETCHERS, {"ah": lambda: [self.FETCHED[0], self.FETCHED[1]]}):
            offers.sync("ah")
        self.assertIsNone(self.matched("r1"), "zonder tomaten blijft er niets over")

    def test_a_group_that_fails_keeps_the_ones_that_are_done_and_the_next_round_goes_on(self):
        self.seed()
        self.key()
        failing = FakeResponse(401, {"error": {"message": f"weg {CLAUDE_KEY}"}})
        script = iter([None, failing])

        def answer(provider, url, body):
            return next(script)

        with mock.patch.object(ai_match, "GROUP_SIZE", 1):
            result, _ = self.run_search(Provider(answer=answer))
            self.assertEqual((result["searched"], result["remaining"]), (1, 1))
            self.assertIn("401", result["error"])
            self.assertNotIn("GEHEIM", result["error"])
            result, provider = self.run_search()
            self.assertEqual((result["searched"], result["remaining"], len(provider.calls)), (1, 0, 1), "de tweede ronde zoekt alleen de rest")
            with mock.patch.object(ai_review.requests, "post", return_value=FakeResponse(401, {"error": {"message": "weg"}})):
                self.assertEqual(ai_match.plan("ah")["todo"], 0)

    def test_the_first_group_failing_is_the_error_itself(self):
        self.seed()
        self.key()
        with mock.patch.object(ai_review.requests, "post", return_value=FakeResponse(401, {"error": {"message": "weg"}})):
            with self.assertRaises(ai_review.AiError):
                ai_match.search("ah")

    def test_groups_the_maximum_and_the_plan(self):
        recipes = [{"id": f"r{i:02d}", "title": f"Recept {i}", "image": "", "url": "", "ingredients": ["kipfilet", "tomaten"]} for i in range(5)]
        self.seed(recipes=recipes)
        self.key()
        with mock.patch.object(ai_match, "GROUP_SIZE", 2):
            plan = ai_match.plan("ah", 4)
            self.assertEqual((plan["recipes"], plan["todo"], plan["chosen"], plan["chunks"], plan["offers"]), (5, 5, 4, 2, 3))
            self.assertGreater(plan["estimatedTokens"], 2 * ai_match.ANSWER_TOKENS)
            result, provider = self.run_search(max_recipes=4)
        self.assertEqual((len(provider.calls), result["searched"], result["remaining"]), (2, 4, 1))
        self.assertEqual(ai_match.plan("ah", 99999)["maxRecipes"], ai_match.MAX_RECIPES)

    def test_without_a_key_or_offers_nothing_happens(self):
        with mock.patch.object(ai_review.requests, "post", side_effect=AssertionError("geen aanroep")):
            with self.assertRaises(ValueError):
                ai_match.plan("ah")  # geen aanbiedingen
            self.seed()
            with self.assertRaises(ValueError):
                ai_match.search("ah")  # geen sleutel
            with self.assertRaises(ValueError):
                ai_match.start("ah")
            with self.assertRaises(ValueError):
                ai_match.plan("lidl")


class BackgroundTest(AiMatchCase):
    def test_a_round_runs_and_reports_its_progress(self):
        self.seed()
        self.key()
        seen = []
        with mock.patch.object(ai_review.requests, "post", side_effect=Provider()):
            self.assertEqual(ai_match.progress()["state"], "idle")
            with mock.patch.object(ai_match, "search", wraps=ai_match.search) as search:
                state = ai_match.start("ah", 10, background=False)
        self.assertEqual(search.call_count, 1)
        self.assertEqual((state["state"], state["done"], state["total"], state["matched"], state["tokens"]), ("done", 2, 2, 4, 1200))
        self.assertEqual(state["result"]["searched"], 2)
        self.assertEqual(seen, [])

    def test_only_one_round_at_a_time(self):
        self.seed()
        self.key()
        ai_match._job.update(state="running")
        with self.assertRaises(ValueError):
            ai_match.start("ah")

    def test_an_error_ends_in_the_error_state_with_a_readable_text(self):
        self.seed()
        self.key()
        with mock.patch.object(ai_review.requests, "post", return_value=FakeResponse(401, {"error": {"message": f"bad {CLAUDE_KEY}"}})):
            state = ai_match.start("ah", background=False)
        self.assertEqual(state["state"], "error")
        self.assertIn("Claude", state["error"])
        self.assertNotIn("GEHEIM", json.dumps(state))
        with mock.patch.object(ai_match, "search", side_effect=RuntimeError(f"stuk {CLAUDE_KEY}")):
            state = ai_match.start("ah", background=False)
        self.assertEqual(state["state"], "error")
        self.assertNotIn("GEHEIM", state["error"])


class InlineThread:
    def __init__(self, target=None, args=(), daemon=None, name=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class RoutesTest(AiMatchCase):
    def join(self):
        token = self.admin.post("/api/users/invites", json={"username": "anna"}).get_json()["token"]
        client = self.app.test_client()
        client.post("/api/invites/password", json={"token": token, "password": ANNA})
        return client

    def test_only_the_administrator_can_search(self):
        anna, anonymous = self.join(), self.app.test_client()
        for client, expected in ((anna, 403), (anonymous, 401)):
            self.assertEqual(client.get("/api/offers/ai/search/plan?store=ah").status_code, expected)
            self.assertEqual(client.post("/api/offers/ai/search", json={"store": "ah"}).status_code, expected)
            self.assertEqual(client.get("/api/offers/ai/search/status").status_code, expected)
        self.assertEqual(ai_match.progress()["state"], "idle")

    def test_plan_start_and_status_over_the_api(self):
        self.seed()
        self.key()
        plan = self.admin.get("/api/offers/ai/search/plan?store=ah&max=50").get_json()
        self.assertEqual((plan["todo"], plan["chosen"], plan["chunks"]), (2, 2, 1))
        with mock.patch.object(ai_match.threading, "Thread", InlineThread), mock.patch.object(ai_review.requests, "post", side_effect=Provider()):
            started = self.admin.post("/api/offers/ai/search", json={"store": "ah", "max": 50})
        self.assertEqual(started.status_code, 200)
        status = self.admin.get("/api/offers/ai/search/status").get_json()
        self.assertEqual((status["state"], status["done"], status["total"], status["result"]["searched"]), ("done", 2, 2, 2))
        self.assertEqual(self.admin.get("/api/offers/ai").get_json()["search"]["state"], "done", "ook in de AI-instellingen, voor de pagina na een herlading")
        recipes = self.admin.get("/api/offers/recipes?store=ah").get_json()
        self.assertEqual(recipes["ai"]["searched"], 2)
        self.assertEqual(self.admin.get("/api/offers/ai/search/plan?store=lidl").status_code, 400)
        ai_review.save({"clear": ["anthropic"]})
        self.assertEqual(self.admin.post("/api/offers/ai/search", json={"store": "ah"}).status_code, 400)


class MarkupTest(unittest.TestCase):
    def test_the_button_progress_and_badge(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        html = (root / "templates" / "index.html").read_text(encoding="utf-8")
        script = (root / "static" / "ai_review.js").read_text(encoding="utf-8")
        offers_js = (root / "static" / "offers.js").read_text(encoding="utf-8")
        nl = (root / "static" / "i18n" / "nl.js").read_text(encoding="utf-8")
        self.assertIn('"aireview.run": "Recepten zoeken met AI"', nl)
        self.assertIn('data-i18n="aireview.run">Recepten zoeken met AI</button>', html)
        for route in ("/api/offers/ai/search/plan", "/api/offers/ai/search", "/api/offers/ai/search/status"):
            self.assertIn(route, script)
        self.assertIn('window.confirm(t("aisearch.confirm"', script, "geld uitgeven vraagt een bevestiging met een schatting")
        self.assertIn("mdiIcon(MDI_LOADING", script)
        self.assertIn("setInterval", script, "de voortgang wordt bijgewerkt zolang de ronde loopt")
        self.assertIn('item.ai === "search"', offers_js)
        self.assertIn('t("aisearch.badge")', offers_js)


if __name__ == "__main__":
    unittest.main()
