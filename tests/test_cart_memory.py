"""Wat het weekmenu per gebruiker onthoudt (mealieshopper/cart_memory.py, 4.11.0): het product dat je bij een ingrediënt koos en
wat je altijd in huis hebt. De module zelf, de routes, en de controlelijst die een onthouden product vooraan zet."""

import os
import tempfile
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import ah, auth, cart_memory, picnic, users  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402
from helpers import AppTestCase  # noqa: E402


def search_result(webshop_id, title, unit_size, price=1.5):
    return {
        "webshopId": str(webshop_id), "title": title, "brand": "AH", "unitSize": unit_size,
        "price": {"now": price, "unitSize": unit_size}, "images": [{"url": f"https://img/{webshop_id}.jpg"}],
        "isBonus": False,
    }


class MemoryCase(AppTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = self.client_for(create_app())


class KeyTest(AppTestCase):
    def test_the_key_folds_case_accents_and_spaces(self):
        cases = {"Crème  Fraîche": "creme fraiche", "  UI ": "ui", "400 g Spaghetti": "400 g spaghetti", None: "", "": ""}
        for value, expected in cases.items():
            self.assertEqual(cart_memory.key(value), expected, repr(value))


class ChoicesTest(MemoryCase):
    ITEM = {"query": "Rundergehakt", "productId": 42, "title": "AH Rundergehakt 1000 g", "unitSize": "1000 g", "image": "https://img/42.jpg"}

    async def test_a_choice_is_kept_per_user_and_store(self):
        self.assertEqual(await cart_memory.remember("owner", "ah", [self.ITEM], now="2026-11-09T10:00:00+00:00"), 1)
        self.assertEqual(
            await cart_memory.chosen("owner", "ah"),
            {"rundergehakt": {"productId": "42", "title": "AH Rundergehakt 1000 g", "unitSize": "1000 g", "image": "https://img/42.jpg", "chosenAt": "2026-11-09T10:00:00+00:00"}},
        )
        self.assertEqual(await cart_memory.chosen("owner", "picnic"), {}, "een andere winkel heeft andere producten")
        self.assertEqual(await cart_memory.chosen("u-2", "ah"), {}, "een andere gebruiker kiest zelf")
        self.assertEqual(await cart_memory.chosen("owner", "lidl"), {}, "een winkel zonder mandje onthoudt niets")

    async def test_a_new_choice_replaces_the_old_one(self):
        await cart_memory.remember("owner", "ah", [self.ITEM])
        await cart_memory.remember("owner", "ah", [{"query": " rundergehakt ", "productId": "7", "title": "AH Biologisch rundergehakt"}])
        choice = (await cart_memory.chosen("owner", "ah"))["rundergehakt"]
        self.assertEqual((choice["productId"], choice["title"], choice["unitSize"], choice["image"]), ("7", "AH Biologisch rundergehakt", None, None))

    async def test_incomplete_rows_and_odd_images_are_left_out(self):
        saved = await cart_memory.remember("owner", "ah", [
            {"query": "", "productId": 1, "title": "x"},
            {"query": "ui", "productId": None, "title": "x"},
            {"query": "ui", "productId": 1, "title": ""},
            "geen regel",
            {"query": "melk", "productId": 2, "title": "Melk", "image": "javascript:alert(1)"},
        ])
        self.assertEqual(saved, 1)
        self.assertEqual((await cart_memory.chosen("owner", "ah"))["melk"]["image"], None)
        with self.assertRaises(ValueError):
            await cart_memory.remember("owner", "lidl", [self.ITEM])

    async def test_forgetting_a_choice(self):
        await cart_memory.remember("owner", "ah", [self.ITEM])
        self.assertTrue(await cart_memory.forget("owner", "ah", "RUNDERGEHAKT"))
        self.assertFalse(await cart_memory.forget("owner", "ah", "rundergehakt"))
        self.assertEqual(await cart_memory.chosen("owner", "ah"), {})


class PantryTest(MemoryCase):
    async def test_the_pantry_is_per_user_and_sorted_by_name(self):
        await cart_memory.set_pantry("owner", "Zout", True)
        items = await cart_memory.set_pantry("owner", "olijfolie", True)
        self.assertEqual(items, [{"ingredient": "olijfolie", "name": "olijfolie"}, {"ingredient": "zout", "name": "Zout"}])
        self.assertEqual(await cart_memory.set_pantry("owner", "ZOUT", True), items, "twee keer is een keer")
        self.assertEqual(await cart_memory.pantry("u-2"), [])
        self.assertEqual(await cart_memory.set_pantry("owner", "zout", False), [{"ingredient": "olijfolie", "name": "olijfolie"}])
        with self.assertRaises(ValueError):
            await cart_memory.set_pantry("owner", "  ", True)

    async def test_a_full_pantry_refuses_more(self):
        with mock.patch.object(cart_memory, "MAX_PANTRY_ITEMS", 2):
            await cart_memory.set_pantry("owner", "zout", True)
            await cart_memory.set_pantry("owner", "peper", True)
            with self.assertRaises(ValueError):
                await cart_memory.set_pantry("owner", "olie", True)
            self.assertEqual(len(await cart_memory.set_pantry("owner", "zout", False)), 1, "weghalen kan altijd")

    async def test_a_removed_user_takes_everything_along(self):
        await cart_memory.remember("u-2", "ah", [ChoicesTest.ITEM])
        await cart_memory.set_pantry("u-2", "zout", True)
        await cart_memory.set_pantry("owner", "zout", True)
        await cart_memory.remove_user("u-2")
        self.assertEqual((await cart_memory.chosen("u-2", "ah"), await cart_memory.pantry("u-2")), ({}, []))
        self.assertEqual(len(await cart_memory.pantry("owner")), 1)

    async def test_deleting_a_user_calls_it(self):
        with mock.patch.object(users, "get_user", return_value={"id": "u-2"}), mock.patch.object(users.profile, "delete"), \
                mock.patch.object(users.oauth, "forget_user"), mock.patch.object(cart_memory, "remove_user") as remove:
            await auth.ensure_db()
            self.assertTrue(await users.delete_user("u-2"))
        remove.assert_awaited_once_with("u-2")


class RoutesTest(MemoryCase):
    async def test_the_pantry_routes(self):
        self.assertEqual((await self.client.get("/api/pantry")).get_json(), {"items": []})
        added = await self.client.post("/api/pantry", json={"name": "Zout"})
        self.assertEqual(added.get_json(), {"items": [{"ingredient": "zout", "name": "Zout"}]})
        removed = await self.client.post("/api/pantry", json={"name": "zout", "present": False})
        self.assertEqual(removed.get_json(), {"items": []})
        bad = await self.client.post("/api/pantry", json={"name": ""})
        self.assertEqual(bad.status_code, 400)

    async def test_forgetting_through_the_route(self):
        await cart_memory.remember("owner", "ah", [ChoicesTest.ITEM])
        reply = await self.client.post("/api/cart/choices/forget", json={"store": "ah", "query": "rundergehakt"})
        self.assertEqual(reply.get_json(), {"removed": True})
        self.assertEqual((await self.client.post("/api/cart/choices/forget", json={"store": "lidl", "query": "x"})).status_code, 400)

    async def test_adding_to_the_ah_list_remembers_only_what_the_user_chose(self):
        items = [
            {"query": "rundergehakt", "productId": 42, "quantity": 1, "title": "AH Rundergehakt 1000 g", "unitSize": "1000 g", "remember": True},
            {"query": "eieren", "productId": 7, "quantity": 1, "title": "AH Eieren 10 stuks"},  # het voorstel: niets te onthouden
        ]
        with mock.patch.object(ah, "add_to_shopping_list"):
            self.assertEqual((await self.client.post("/api/ah/cart", json={"items": items})).status_code, 200)
        self.assertEqual(list(await cart_memory.chosen("owner", "ah")), ["rundergehakt"])

    async def test_a_failed_add_remembers_nothing(self):
        items = [{"query": "rundergehakt", "productId": 42, "quantity": 1, "title": "AH Rundergehakt", "remember": True}]
        with mock.patch.object(ah, "add_to_shopping_list", side_effect=RuntimeError("AH is weg")):
            self.assertEqual((await self.client.post("/api/ah/cart", json={"items": items})).status_code, 424)
        self.assertEqual(await cart_memory.chosen("owner", "ah"), {})

    async def test_a_store_basket_remembers_under_its_own_store_and_skips_what_the_store_refused(self):
        items = [
            {"query": "melk", "productId": "p1", "quantity": 1, "title": "Halfvolle melk", "remember": True},
            {"query": "kaas", "productId": "p2", "quantity": 1, "title": "Jonge kaas", "remember": True},
        ]
        result = {"added": [{"productId": "p1"}], "failed": [{"productId": "p2"}], "cart": {}}
        with mock.patch.object(picnic, "add_to_cart", return_value=result):
            self.assertEqual((await self.client.post("/api/picnic/cart", json={"items": items})).status_code, 200)
        self.assertEqual(list(await cart_memory.chosen("owner", "picnic")), ["melk"])
        self.assertEqual(await cart_memory.chosen("owner", "ah"), {})

    async def test_saving_that_fails_leaves_the_basket_filled(self):
        items = [{"query": "rundergehakt", "productId": 42, "quantity": 1, "title": "AH Rundergehakt", "remember": True}]
        with mock.patch.object(ah, "add_to_shopping_list"), mock.patch.object(cart_memory, "remember", side_effect=RuntimeError("schijf vol")):
            reply = await self.client.post("/api/ah/cart", json={"items": items})
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.get_json()["added"], 1)


class PreviewTest(MemoryCase):
    RESULTS = {
        "rundergehakt": [search_result(1, "AH Rundergehakt 500 g", "500 g", 4.99), search_result(2, "AH Rundergehakt 1000 g", "1000 g", 8.98)],
        "AH Gemengd gehakt 500 g": [search_result(9, "AH Gemengd gehakt 500 g", "500 g", 3.99)],
    }

    async def preview(self, body, results=None):
        results = self.RESULTS if results is None else results
        with mock.patch.object(ah, "search_products", side_effect=lambda query, limit=5: results.get(query, [])):
            reply = await self.client.post("/api/ah/cart/preview", json=body)
        self.assertEqual(reply.status_code, 200)
        return reply.get_json()["items"]

    async def test_without_a_choice_nothing_changes(self):
        row = (await self.preview({"compare": True, "items": [{"query": "rundergehakt", "quantity": 500, "unit": "g"}]}))[0]
        self.assertEqual(row["candidates"][0]["productId"], "1")
        self.assertNotIn("remembered", row["candidates"][0])
        self.assertNotIn("rememberedMissing", row)

    async def test_a_remembered_product_from_the_results_comes_first(self):
        await cart_memory.remember("owner", "ah", [{"query": "Rundergehakt", "productId": "2", "title": "AH Rundergehakt 1000 g"}])
        row = (await self.preview({"compare": True, "items": [{"query": "rundergehakt", "quantity": 500, "unit": "g"}]}))[0]
        ids = [candidate["productId"] for candidate in row["candidates"]]
        self.assertEqual(ids, ["2", "1"], "je keuze eerst, het voorstel blijft als optie")
        first = row["candidates"][0]
        self.assertTrue(first["remembered"])
        self.assertEqual(first["unitPrice"], {"value": 8.98, "unit": "kg"})
        self.assertFalse(any(flag in first for flag in ("suggested", "same", "cheaper")))
        self.assertTrue(row["candidates"][1].get("suggested"))

    async def test_a_remembered_product_outside_the_results_is_searched_by_its_title(self):
        await cart_memory.remember("owner", "ah", [{"query": "rundergehakt", "productId": "9", "title": "AH Gemengd gehakt 500 g"}])
        row = (await self.preview({"compare": True, "items": [{"query": "rundergehakt", "quantity": 500, "unit": "g"}]}))[0]
        self.assertEqual(row["candidates"][0]["productId"], "9")
        self.assertTrue(row["candidates"][0]["remembered"])
        self.assertIn("1", [candidate["productId"] for candidate in row["candidates"]])

    async def test_a_product_that_is_gone_keeps_the_suggestion_and_says_so(self):
        await cart_memory.remember("owner", "ah", [{"query": "rundergehakt", "productId": "404", "title": "AH Rundergehakt mager"}])
        row = (await self.preview({"compare": True, "items": [{"query": "rundergehakt", "quantity": 500, "unit": "g"}]}))[0]
        self.assertEqual(row["candidates"][0]["productId"], "1")
        self.assertEqual(row["rememberedMissing"], "AH Rundergehakt mager")

    async def test_a_choice_at_another_store_does_not_count(self):
        await cart_memory.remember("owner", "picnic", [{"query": "rundergehakt", "productId": "2", "title": "AH Rundergehakt 1000 g"}])
        row = (await self.preview({"compare": True, "items": [{"query": "rundergehakt"}]}))[0]
        self.assertEqual(row["candidates"][0]["productId"], "1")

    async def test_reading_the_choices_that_fails_gives_the_plain_list(self):
        with mock.patch.object(cart_memory, "chosen", side_effect=RuntimeError("database op slot")):
            row = (await self.preview({"compare": True, "items": [{"query": "rundergehakt"}]}))[0]
        self.assertEqual(row["candidates"][0]["productId"], "1")
