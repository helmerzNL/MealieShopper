"""De stand per losse aankoopnaam en de zoekronde die zulke namen bij de winkel opzoekt (`loose_names`).

Geen test spreekt een echte winkel aan: elke ronde krijgt een nagemaakte zoeker (`FakeSearch`), en de pauzes (`loose_names._wait`,
`ah_orders._wait`) en de klok van de ronde (`loose_names._clock`) zijn vervangen.
"""

import asyncio
import json
import os
import string
import tempfile
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import closing
from datetime import datetime, timedelta
from typing import Any
from unittest import mock

import test_shared_links
from helpers import AppTestCase, db, run

from mealieshopper import (
    ah,
    ah_orders,
    auth,
    crisp,
    jumbo,
    loose_name_rules,
    loose_names,
    name_keys,
    picnic,
    price_import,
    prices,
    product_links,
    shared_links,
)

NOW: str = "2026-11-11T01:00:00+00:00"
TODAY: str = "2026-11-11"


def later(now: str = NOW, **delta: float) -> str:
    return (datetime.fromisoformat(now) + timedelta(**delta)).isoformat(
        timespec="seconds"
    )


def item(
    product_id: str,
    title: str,
    unit_size: str = "500 g",
    price: float = 1.99,
    image: str = "https://static.example/x.png",
) -> dict[str, Any]:
    """Een zoekresultaat in de vorm van Jumbo, Picnic en Crisp."""
    return {
        "id": product_id,
        "title": title,
        "brand": None,
        "unitSize": unit_size,
        "price": price,
        "image": image,
        "isBonus": False,
    }


def ah_item(
    webshop_id: str,
    title: str,
    unit_size: str = "500 g",
    price: float = 1.99,
    image: str = "https://static.ah.nl/x.png",
) -> dict[str, Any]:
    """Een zoekresultaat in de vorm van `ah.search_products` (`ah._search_product_dict`)."""
    return {
        "webshopId": webshop_id,
        "title": title,
        "price": {"now": price, "unitSize": unit_size},
        "images": [{"url": image, "width": 200}],
        "brand": "AH",
        "unitSize": unit_size,
        "isBonus": False,
    }


class FakeSearch:
    """`search_products(query, limit)` van een winkel: per zoekterm een lijst resultaten of een fout. Onthoudt elke aanroep."""

    def __init__(
        self,
        answers: dict[str, Any] | None = None,
        before: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.answers: dict[str, Any] = answers or {}
        self.before: Callable[[str], Awaitable[None]] | None = before
        self.calls: list[tuple[str, int]] = []

    async def __call__(self, query: str, limit: int) -> list[dict[str, Any]]:
        self.calls.append((query, limit))
        if self.before is not None:
            await self.before(query)
        answer: Any = self.answers.get(query, [])
        if isinstance(answer, Exception):
            raise answer
        return answer

    @property
    def queries(self) -> list[str]:
        return [query for query, _ in self.calls]


class LooseNamesCase(AppTestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(
            os.environ,
            {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"},
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        run(auth.init_db())
        self.orders: int = 0
        self.clock: float = 0.0
        self.waits: list[float] = []

        async def wait(seconds: float) -> None:
            self.waits.append(seconds)
            self.clock += seconds

        async def ah_wait(
            seconds: float,
        ) -> None:  # het tempo van AH zelf (`ah_orders._polite`): niet echt wachten
            return None

        for patch in (
            mock.patch.object(loose_names, "_wait", wait),
            mock.patch.object(loose_names, "_clock", lambda: self.clock),
            mock.patch.object(ah_orders, "_wait", ah_wait),
            mock.patch.dict(
                ah_orders._PACE, {"last": float("-inf"), "recent": deque()}
            ),
            mock.patch.object(shared_links, "load", return_value={}),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def bought(
        self,
        title: str,
        times: int = 1,
        store: str = "jumbo",
        paid: float | None = 2.0,
        year: int = 2022,
    ) -> None:
        """`times` ingelezen aankopen van `title` uit bevestigingsmails, elk in een eigen bestelling."""
        with closing(db(price_import)) as conn, conn:
            for n in range(times):
                self.orders += 1
                conn.execute(
                    "INSERT INTO imported_purchases (store, order_ref, position, day, title, quantity, paid, regular, order_total, imported_at) "
                    "VALUES (?, ?, 0, ?, ?, 1, ?, NULL, 10, 'x')",
                    (store, f"M{self.orders}", f"{year + n}-03-14", title, paid),
                )

    def state(
        self,
        store: str,
        name: str,
        checked_at: str,
        outcome: str,
        candidates: list[dict[str, Any]] | None = None,
        proposal: dict[str, Any] | None = None,
    ) -> None:
        """Een rij in `loose_names`, zoals een eerdere ronde hem achterliet."""
        with closing(db(loose_names)) as conn, conn:
            conn.execute(
                "INSERT INTO loose_names (store, key, name, checked_at, outcome, candidates, proposal) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    store,
                    name_keys.key(name),
                    name,
                    checked_at,
                    outcome,
                    json.dumps(candidates or []),
                    json.dumps(proposal) if proposal else None,
                ),
            )

    def row(self, store: str, name: str) -> dict[str, Any] | None:
        with closing(db(loose_names)) as conn:
            found = conn.execute(
                "SELECT * FROM loose_names WHERE store = ? AND key = ?",
                (store, name_keys.key(name)),
            ).fetchone()
        if found is None:
            return None
        return {
            **dict(found),
            "candidates": json.loads(found["candidates"]),
            "proposal": json.loads(found["proposal"]) if found["proposal"] else None,
        }

    async def history_ids(self, store: str, query: str) -> list[str]:
        return [
            product["productId"]
            for product in (await prices.history(store, query, 50, today=TODAY))[
                "products"
            ]
        ]


class ResultRulesTest(LooseNamesCase):
    async def test_exact_single_hit_links_with_origin_exact(self) -> None:
        self.bought("Jumbo Pindakaas naturel", 2)
        search = FakeSearch(
            {
                "Jumbo Pindakaas naturel": [
                    item("1", "Jumbo Pindakaas Naturel", "350 g"),
                    item("2", "Jumbo Pindakaas met stukjes"),
                ]
            }
        )
        self.assertEqual(
            await self.history_ids("jumbo", "pindakaas"),
            ["import:jumbo pindakaas naturel"],
        )
        result = await loose_names.run_round("jumbo", NOW, search=search)
        self.assertEqual(
            result,
            {"searches": 1, "exact": 1, "candidates": 0, "missing": 0, "stopped": None},
        )
        self.assertEqual(
            search.calls, [("Jumbo Pindakaas naturel", loose_names.CANDIDATES)]
        )
        links = (await product_links.list_links("jumbo"))["links"]
        self.assertEqual(
            [
                (link["productId"], link["title"], link["unitSize"], link["origin"])
                for link in links
            ],
            [("1", "Jumbo Pindakaas Naturel", "350 g", "exact")],
        )
        self.assertEqual(
            self.row("jumbo", "Jumbo Pindakaas naturel")["outcome"], "exact"
        )
        self.assertEqual(
            await self.history_ids("jumbo", "pindakaas"),
            ["1"],
            "de catalogus ziet de koppeling meteen",
        )
        self.assertEqual(
            await loose_names.due("jumbo", later(days=200), 100),
            [],
            "de naam is niet meer los",
        )

    async def test_exact_hit_never_replaces_a_link_of_a_person(self) -> None:
        self.bought("Jumbo Pindakaas naturel")
        search = FakeSearch(
            {"Jumbo Pindakaas naturel": [item("1", "Jumbo Pindakaas Naturel")]}
        )
        with mock.patch.object(
            prices,
            "loose_import_names",
            return_value=[
                {
                    "key": name_keys.key("Jumbo Pindakaas naturel"),
                    "name": "Jumbo Pindakaas naturel",
                    "bought": 1,
                    "firstYear": 2022,
                    "lastYear": 2022,
                    "paidMin": 2.0,
                    "paidMax": 2.0,
                    "spellings": ["Jumbo Pindakaas naturel"],
                }
            ],
        ):
            await product_links.save(
                "jumbo",
                "Jumbo Pindakaas naturel",
                {"productId": "9", "title": "Eigen keus"},
            )
            await loose_names.run_round("jumbo", NOW, search=search)
        links = (await product_links.list_links("jumbo"))["links"]
        self.assertEqual(
            [(link["productId"], link["origin"]) for link in links], [("9", "manual")]
        )

    async def test_exact_hits_in_two_packs_become_candidates_only_those(self) -> None:
        self.bought("Jumbo Pindakaas naturel")
        search = FakeSearch(
            {
                "Jumbo Pindakaas naturel": [
                    item("1", "Jumbo Pindakaas naturel", "350 g"),
                    item("2", "Jumbo Pindakaas naturel", "600 g"),
                    item("3", "Jumbo Hagelslag"),
                ]
            }
        )
        result = await loose_names.run_round("jumbo", NOW, search=search)
        self.assertEqual((result["exact"], result["candidates"]), (0, 1))
        state = self.row("jumbo", "Jumbo Pindakaas naturel")
        self.assertEqual(
            (state["outcome"], [c["productId"] for c in state["candidates"]]),
            ("candidates", ["1", "2"]),
        )
        self.assertEqual(
            (await product_links.list_links("jumbo"))["links"],
            [],
            "geen koppeling zonder een mens",
        )

    async def test_renamed_name_keeps_up_to_ten_candidates_with_price_and_image(
        self,
    ) -> None:
        self.bought("AH Les pain triomphe heel online", store="ah")
        results = [
            ah_item(str(n), f"AH Pain triomphe soort {letter}", price=2.5 + n)
            for n, letter in enumerate(string.ascii_lowercase[:12], 1)
        ]
        results[1]["images"] = [
            {"url": "http://static.ah.nl/plain.png"}
        ]  # geen https: geen foto
        results.insert(
            0, {**ah_item("x", "Zonder nummer"), "webshopId": "None"}
        )  # wat `ah._search_product_dict` zonder id geeft
        search = FakeSearch({"AH Les pain triomphe heel online": results})
        await loose_names.run_round("ah", NOW, search=search)
        self.assertEqual(search.calls, [("AH Les pain triomphe heel online", 10)])
        state = self.row("ah", "AH Les pain triomphe heel online")
        self.assertEqual(len(state["candidates"]), loose_names.CANDIDATES)
        self.assertEqual(
            state["candidates"][0],
            {
                "productId": "1",
                "title": "AH Pain triomphe soort a",
                "brand": "AH",
                "unitSize": "500 g",
                "price": 3.5,
                "image": "https://static.ah.nl/x.png",
            },
        )
        self.assertIsNone(state["candidates"][1]["image"])
        self.assertEqual(
            [c["productId"] for c in state["candidates"]],
            [str(n) for n in range(1, 11)],
        )

    def test_candidate_reads_every_shop_and_drops_what_is_unusable(self) -> None:
        self.assertEqual(
            loose_name_rules.candidate(
                "picnic",
                {
                    "id": "s1",
                    "title": " Melk ",
                    "unitSize": "1 l",
                    "price": 1.2,
                    "image": "https://img/x",
                },
            ),
            {
                "productId": "s1",
                "title": "Melk",
                "brand": None,
                "unitSize": "1 l",
                "price": 1.2,
                "image": "https://img/x",
            },
        )
        self.assertEqual(
            loose_name_rules.candidate("ah", ah_item("4183", "AH Bananen", price=0))[
                "price"
            ],
            None,
            "AH zonder prijs geeft 0",
        )
        for bad in (
            {"id": "", "title": "Melk"},
            {"id": "1", "title": ""},
            {"id": "1"},
            "geen dict",
            {"id": "None", "title": "x"},
        ):
            self.assertIsNone(loose_name_rules.candidate("jumbo", bad))
        self.assertIsNone(
            loose_name_rules.candidate(
                "jumbo", item("1", "Melk", image="javascript:alert(1)")
            )["image"]
        )
        self.assertIsNone(
            loose_name_rules.candidate("jumbo", {**item("1", "Melk"), "price": True})[
                "price"
            ]
        )

    async def test_multipacks_drop_unless_the_old_name_is_one_or_nothing_is_left(
        self,
    ) -> None:
        self.bought("Jumbo Cola zero", 3)
        self.bought("Jumbo Cola 6-pack", 2)
        self.bought("Jumbo Ice tea")
        search = FakeSearch(
            {
                "Jumbo Cola zero": [
                    item("1", "Jumbo Cola zero fles", "1,5 l"),
                    item("2", "Jumbo Cola zero 6-pack"),
                    item("3", "Coca-Cola zero", "4 x 1,5 l"),
                ],
                "Jumbo Cola 6-pack": [
                    item("4", "Jumbo Cola regular", "1,5 l"),
                    item("5", "Jumbo Cola regular 6-pack"),
                ],
                "Jumbo Ice tea": [
                    item("6", "Jumbo Ice tea green 6-pack"),
                    item("7", "Lipton Ice tea", "6 x 33 cl"),
                ],
            }
        )
        await loose_names.run_round("jumbo", NOW, search=search)
        ids = {
            name: [c["productId"] for c in self.row("jumbo", name)["candidates"]]
            for name in ("Jumbo Cola zero", "Jumbo Cola 6-pack", "Jumbo Ice tea")
        }
        self.assertEqual(
            ids,
            {
                "Jumbo Cola zero": ["1"],
                "Jumbo Cola 6-pack": ["4", "5"],
                "Jumbo Ice tea": ["6", "7"],
            },
        )

    async def test_no_results_is_missing(self) -> None:
        self.bought("Jumbo Iets dat weg is", 2)
        result = await loose_names.run_round(
            "jumbo",
            NOW,
            search=FakeSearch(
                {"Jumbo Iets dat weg is": [{"id": "", "title": "kapot"}]}
            ),
        )
        self.assertEqual((result["missing"], result["searches"]), (1, 1))
        state = self.row("jumbo", "Jumbo Iets dat weg is")
        self.assertEqual(
            (state["outcome"], state["candidates"], state["checked_at"]),
            ("missing", [], NOW),
        )

    async def test_new_candidates_drop_a_proposal_for_a_product_that_is_gone(
        self,
    ) -> None:
        self.bought("Jumbo Appelsap", 2)
        self.bought("Jumbo Perensap")
        old = later(days=-100)
        self.state(
            "jumbo",
            "Jumbo Appelsap",
            old,
            "candidates",
            [item("1", "Jumbo Appel sap")],
            {"productId": "1", "surePack": True, "reason": "zelfde"},
        )
        self.state(
            "jumbo",
            "Jumbo Perensap",
            old,
            "candidates",
            [item("9", "Jumbo Peer sap")],
            {"productId": "9", "surePack": False, "reason": "lijkt"},
        )
        search = FakeSearch(
            {
                "Jumbo Appelsap": [
                    item("2", "Jumbo Appelsap troebel"),
                    item("1", "Jumbo Appel sap"),
                ],
                "Jumbo Perensap": [item("8", "Jumbo Perensap helder")],
            }
        )
        await loose_names.run_round("jumbo", NOW, search=search)
        self.assertEqual(
            self.row("jumbo", "Jumbo Appelsap")["proposal"],
            {"productId": "1", "surePack": True, "reason": "zelfde"},
        )
        self.assertIsNone(self.row("jumbo", "Jumbo Perensap")["proposal"])
        self.assertEqual(self.row("jumbo", "Jumbo Perensap")["checked_at"], NOW)


class DueTest(LooseNamesCase):
    async def test_loose_import_names_groups_spellings_most_bought_first(self) -> None:
        self.bought(
            "AH Les pain triomphe heel online", 2, store="ah", paid=1.5, year=2021
        )
        self.bought("AH  Les pains triomphe heel", 1, store="ah", paid=1.9, year=2024)
        self.bought("AH Hagelslag", 1, store="ah", paid=None)
        found = await prices.loose_import_names("ah")
        self.assertEqual(
            found,
            [
                {
                    "key": name_keys.key("AH Les pains triomphe heel"),
                    "name": "AH Les pain triomphe heel online",
                    "bought": 3,
                    "firstYear": 2021,
                    "lastYear": 2024,
                    "paidMin": 1.5,
                    "paidMax": 1.9,
                    "spellings": [
                        "AH Les pain triomphe heel online",
                        "AH Les pains triomphe heel",
                    ],
                },
                {
                    "key": name_keys.key("AH Hagelslag"),
                    "name": "AH Hagelslag",
                    "bought": 1,
                    "firstYear": 2022,
                    "lastYear": 2022,
                    "paidMin": None,
                    "paidMax": None,
                    "spellings": ["AH Hagelslag"],
                },
            ],
        )

    async def test_round_stops_at_100_searches_most_bought_first(self) -> None:
        self.bought("Jumbo Vaak gekocht", 5)
        names = [
            f"Jumbo artikel q{a}{b}"
            for a in string.ascii_lowercase[:10]
            for b in string.ascii_lowercase[:10]
        ][:99]
        for name in names:
            self.bought(name, 2)
        self.bought("Jumbo Zelden gekocht", 1)
        search = FakeSearch()
        result = await loose_names.run_round("jumbo", NOW, search=search)
        self.assertEqual(
            (result["searches"], result["missing"], result["stopped"]), (100, 100, None)
        )
        self.assertEqual(search.queries[0], "Jumbo Vaak gekocht")
        self.assertEqual(sorted(search.queries[1:]), sorted(names))
        self.assertNotIn("Jumbo Zelden gekocht", search.queries)
        self.assertEqual(
            self.waits,
            [loose_names.PAUSE_SECONDS] * 99,
            "het tempo tussen twee zoekopdrachten",
        )
        self.assertEqual(
            [entry["name"] for entry in await loose_names.due("jumbo", NOW, 100)],
            ["Jumbo Zelden gekocht"],
        )

    async def test_name_searched_89_days_ago_is_skipped_and_91_days_ago_is_due(
        self,
    ) -> None:
        self.bought("Jumbo Recent opgezocht", 3)
        self.bought("Jumbo Lang geleden opgezocht", 2)
        self.state("jumbo", "Jumbo Recent opgezocht", later(days=-89), "missing")
        self.state("jumbo", "Jumbo Lang geleden opgezocht", later(days=-91), "missing")
        self.assertEqual(
            [entry["name"] for entry in await loose_names.due("jumbo", NOW, 100)],
            ["Jumbo Lang geleden opgezocht"],
        )
        self.assertEqual(await loose_names.due("jumbo", NOW, 0), [])

    async def test_rejected_name_is_never_due(self) -> None:
        self.bought("Jumbo Afgewezen", 3)
        self.state(
            "jumbo",
            "Jumbo Afgewezen",
            later(days=-400),
            "rejected",
            [item("1", "Iets anders")],
        )
        self.assertEqual(await loose_names.due("jumbo", NOW, 100), [])
        search = FakeSearch()
        await loose_names.run_round("jumbo", NOW, search=search)
        self.assertEqual(search.calls, [])

    async def test_name_in_the_shared_list_is_not_searched(self) -> None:
        self.bought("AH Doosjevol kersen", 3, store="ah")
        self.bought("AH Bananen tros", 1, store="ah")
        search = FakeSearch()
        with test_shared_links.shared(test_shared_links.CHERRIES):
            result = await loose_names.run_round("ah", NOW, search=search)
        self.assertEqual(search.queries, ["AH Bananen tros"])
        self.assertEqual(result["searches"], 1)


class BrakeTest(LooseNamesCase):
    async def test_403_stops_the_round_pauses_12_hours_and_keeps_the_name_unchecked(
        self,
    ) -> None:
        self.bought("Jumbo Eerste naam", 3)
        self.bought("Jumbo Tweede naam", 2)
        self.bought("Jumbo Derde naam", 1)
        old = later(days=-100)
        self.state(
            "jumbo", "Jumbo Tweede naam", old, "candidates", [item("1", "Jumbo Iets")]
        )
        search = FakeSearch(
            {
                "Jumbo Eerste naam": [item("2", "Jumbo Eerste ding")],
                "Jumbo Tweede naam": jumbo.JumboBlocked("geweigerd", "403"),
            }
        )
        result = await loose_names.run_round("jumbo", NOW, search=search)
        self.assertEqual(
            result,
            {
                "searches": 2,
                "exact": 0,
                "candidates": 1,
                "missing": 0,
                "stopped": "blocked",
            },
        )
        self.assertEqual(
            search.queries,
            ["Jumbo Eerste naam", "Jumbo Tweede naam"],
            "na de weigering geen zoekopdracht meer",
        )
        until = later(hours=loose_names.BACKOFF_HOURS)
        self.assertEqual(
            await auth.get_setting("loose_names_paused_until_jumbo"), until
        )
        self.assertEqual(await loose_names.paused_until("jumbo", NOW), until)
        self.assertIsNone(await loose_names.paused_until("jumbo", until))
        self.assertEqual(
            await auth.get_setting(ah_orders.SETTING_RECEIPT_PRODUCTS_PAUSED),
            "",
            "de pauze van AH blijft er buiten",
        )
        state = self.row("jumbo", "Jumbo Tweede naam")
        self.assertEqual(
            (state["checked_at"], state["outcome"], state["candidates"][0]["id"]),
            (old, "candidates", "1"),
            "de oude stand",
        )
        again = await loose_names.run_round("jumbo", later(hours=1), search=search)
        self.assertEqual((again["searches"], again["stopped"]), (0, "blocked"))
        self.assertEqual(len(search.calls), 2)
        self.assertEqual(
            [entry["name"] for entry in await loose_names.due("jumbo", NOW, 100)],
            ["Jumbo Tweede naam", "Jumbo Derde naam"],
        )

    async def test_ah_403_sets_the_existing_receipt_pause(self) -> None:
        self.bought("AH Eerste naam", 2, store="ah")
        self.bought("AH Tweede naam", 1, store="ah")
        search = FakeSearch(
            {
                "AH Eerste naam": ah.AhHttpError(
                    "AH productzoekopdracht mislukt (403)", 403
                )
            }
        )
        result = await loose_names.run_round("ah", NOW, search=search)
        self.assertEqual((result["searches"], result["stopped"]), (1, "blocked"))
        until = later(hours=ah_orders.RECEIPT_PRODUCTS_BACKOFF_HOURS)
        self.assertEqual(await auth.get_setting("receipt_products_paused_until"), until)
        self.assertEqual(
            await auth.get_setting(ah_orders.SETTING_RECEIPT_PRODUCTS_PAUSED_REASON),
            f"403@{NOW}",
        )
        self.assertEqual(
            await auth.get_setting("loose_names_paused_until_ah"),
            "",
            "geen tweede pauze bij AH",
        )
        self.assertEqual(search.queries, ["AH Eerste naam"])
        self.assertEqual(await loose_names.paused_until("ah", NOW), until)
        self.assertIsNone(self.row("ah", "AH Eerste naam"))

    async def test_ah_paused_until_agrees_with_the_receipt_linking_and_its_override(
        self,
    ) -> None:
        self.assertIsNone(await ah_orders.paused_until(NOW))
        until = await ah_orders.pause(NOW, 429)
        self.assertEqual(until, later(hours=12))
        self.assertEqual(
            await ah_orders.paused_until(NOW),
            (await ah_orders.linking_status(NOW))["pausedUntil"],
        )
        self.assertIsNone(
            await ah_orders.paused_until(until), "een verlopen pauze telt niet"
        )
        # De knop van de beheerder (`run_linking(force=True)`) maakt de instelling leeg.
        await auth.set_setting(ah_orders.SETTING_RECEIPT_PRODUCTS_PAUSED, "")
        self.assertIsNone(await ah_orders.paused_until(NOW))
        self.assertIs(ah_orders.HourlyBudgetSpent, ah_orders._HourlyBudgetSpent)

    def test_status_of_reads_every_shop_error(self) -> None:
        cases: list[tuple[Exception, int | None]] = [
            (ah.AhHttpError("AH productzoekopdracht mislukt (429)", 429), 429),
            (jumbo.JumboBlocked("geweigerd", "403"), 403),
            (jumbo.JumboBlocked("geen JSON", "geen JSON terug / no JSON back"), None),
            (picnic.PicnicBlockedError("poort"), 403),
            (crisp.CrispAuthError("Crisp weigerde de sessie"), 403),
            (RuntimeError("429: Too Many Requests"), 429),
            (RuntimeError("Crisp weigerde de sessie"), None),
            (ValueError("iets anders"), None),
        ]
        for error, status in cases:
            self.assertEqual(loose_name_rules.status_of(error), status, repr(error))

    async def test_other_error_stops_without_pause(self) -> None:
        self.bought("Picnic Eerste naam", 2, store="picnic")
        self.bought("Picnic Tweede naam", 1, store="picnic")
        search = FakeSearch({"Picnic Eerste naam": RuntimeError("500: kapot")})
        result = await loose_names.run_round("picnic", NOW, search=search)
        self.assertEqual((result["searches"], result["stopped"]), (1, "error"))
        self.assertEqual(search.queries, ["Picnic Eerste naam"])
        self.assertIsNone(await loose_names.paused_until("picnic", NOW))
        self.assertIsNone(self.row("picnic", "Picnic Eerste naam"))
        result = await loose_names.run_round("picnic", NOW, search=FakeSearch())
        self.assertEqual(
            (result["searches"], result["stopped"]),
            (2, None),
            "de volgende ronde probeert het gewoon weer",
        )

    async def test_a_pause_set_during_the_round_stops_it(self) -> None:
        # Een andere ronde (de bonnen bij AH, of een tweede proces) kreeg een 403 terwijl deze liep: de pauze geldt meteen.
        self.bought("AH Eerste naam", 2, store="ah")
        self.bought("AH Tweede naam", 1, store="ah")

        async def blocked_elsewhere(query: str) -> None:
            await ah_orders.pause(NOW, 403)

        search = FakeSearch(before=blocked_elsewhere)
        result = await loose_names.run_round("ah", NOW, search=search)
        self.assertEqual((result["searches"], result["stopped"]), (1, "blocked"))
        self.assertEqual(search.queries, ["AH Eerste naam"])

    async def test_spent_hourly_budget_waits_and_continues_with_the_same_name(
        self,
    ) -> None:
        self.bought("AH Eerste naam", 2, store="ah")
        self.bought("AH Tweede naam", 1, store="ah")
        refusals: list[int] = [2]
        polite = ah_orders.polite

        async def busy(call: Callable[[], Awaitable[Any]]) -> Any:
            if refusals[0]:
                refusals[0] -= 1
                raise ah_orders.HourlyBudgetSpent()
            return await polite(call)

        search = FakeSearch()
        with mock.patch.object(ah_orders, "polite", busy):
            result = await loose_names.run_round("ah", NOW, search=search)
        self.assertEqual(
            result,
            {"searches": 2, "exact": 0, "candidates": 0, "missing": 2, "stopped": None},
        )
        self.assertEqual(search.queries, ["AH Eerste naam", "AH Tweede naam"])
        self.assertEqual(self.waits, [loose_names.BUDGET_WAIT_SECONDS] * 2)

    async def test_spent_budget_past_the_deadline_stops_with_deadline(self) -> None:
        self.bought("AH Eerste naam", store="ah")

        async def spent(call: Callable[[], Awaitable[Any]]) -> Any:
            raise ah_orders.HourlyBudgetSpent()

        search = FakeSearch()
        with mock.patch.object(ah_orders, "polite", spent):
            result = await loose_names.run_round("ah", NOW, search=search)
        self.assertEqual((result["searches"], result["stopped"]), (0, "deadline"))
        self.assertEqual(search.calls, [])
        self.assertGreater(self.clock, loose_names.ROUND_DEADLINE_SECONDS)
        self.assertLessEqual(
            self.clock,
            loose_names.ROUND_DEADLINE_SECONDS + loose_names.BUDGET_WAIT_SECONDS,
        )

    async def test_paused_store_does_no_searches(self) -> None:
        self.bought("Picnic Naam", store="picnic")
        self.bought("AH Naam", store="ah")
        await auth.set_setting("loose_names_paused_until_picnic", later(hours=1))
        await ah_orders.pause(NOW, 429)
        search = FakeSearch()
        for store in ("picnic", "ah"):
            self.assertEqual(
                await loose_names.run_round(store, NOW, search=search),
                {
                    "searches": 0,
                    "exact": 0,
                    "candidates": 0,
                    "missing": 0,
                    "stopped": "blocked",
                },
            )
        self.assertEqual(search.calls, [])
        self.assertEqual(
            (await loose_names.run_round("picnic", later(hours=2), search=search))[
                "searches"
            ],
            1,
            "na de pauze weer",
        )

    async def test_ah_searches_go_through_ah_orders_polite(self) -> None:
        self.bought("AH Eerste naam", 2, store="ah")
        self.bought("AH Tweede naam", 1, store="ah")
        self.bought("Jumbo Naam", store="jumbo")
        calls: list[int] = []
        polite = ah_orders.polite

        async def counted(call: Callable[[], Awaitable[Any]]) -> Any:
            calls.append(1)
            return await polite(call)

        search = FakeSearch()
        with mock.patch.object(ah_orders, "polite", counted):
            await loose_names.run_round("ah", NOW, search=search)
            self.assertEqual((len(calls), len(search.calls)), (2, 2))
            self.assertEqual(
                len(ah_orders._PACE["recent"]),
                2,
                "ze tellen mee in het uurbudget van AH",
            )
            self.assertEqual(self.waits, [], "bij AH is het tempo dat van ah_orders")
            await loose_names.run_round("jumbo", NOW, search=search)
        self.assertEqual(
            (len(calls), len(search.calls)), (2, 3), "Jumbo gaat er niet door"
        )

    async def test_a_second_round_for_the_same_store_returns_running(self) -> None:
        self.bought("Jumbo Eerste naam", 2)
        self.bought("Picnic Naam", store="picnic")
        started, release = asyncio.Event(), asyncio.Event()

        async def hold(query: str) -> None:
            started.set()
            await release.wait()

        first = asyncio.create_task(
            loose_names.run_round("jumbo", NOW, search=FakeSearch(before=hold))
        )
        await asyncio.wait_for(started.wait(), 5)
        second = FakeSearch()
        self.assertEqual(
            await loose_names.run_round("jumbo", NOW, search=second),
            {
                "searches": 0,
                "exact": 0,
                "candidates": 0,
                "missing": 0,
                "stopped": "running",
            },
        )
        self.assertEqual(second.calls, [])
        self.assertTrue((await loose_names.page("jumbo"))["running"])
        self.assertEqual(
            (await loose_names.run_round("picnic", NOW, search=second))["searches"],
            1,
            "een andere winkel mag wel",
        )
        release.set()
        self.assertEqual((await first)["searches"], 1)
        self.assertFalse((await loose_names.page("jumbo"))["running"])


class ChoiceTest(LooseNamesCase):
    async def test_confirm_links_with_origin_confirmed_and_removes_the_card(
        self,
    ) -> None:
        self.bought("AH Les pain triomphe heel online", 2, store="ah")
        self.bought("AH Les pains triomphe heel", 1, store="ah")
        search = FakeSearch(
            {
                "AH Les pain triomphe heel online": [
                    ah_item("1", "AH Pain triomphe wit"),
                    ah_item("2", "AH Pain triomphe bruin", "400 g"),
                ]
            }
        )
        await loose_names.run_round("ah", NOW, search=search)
        card = (await loose_names.page("ah"))["cards"][0]
        self.assertEqual(
            (card["name"], card["bought"]), ("AH Les pain triomphe heel online", 3)
        )
        link = await loose_names.confirm("ah", card["key"], "2")
        self.assertEqual(
            (link["productId"], link["title"], link["unitSize"], link["origin"]),
            ("2", "AH Pain triomphe bruin", "400 g", "confirmed"),
        )
        links = (await product_links.list_links("ah"))["links"]
        self.assertEqual(
            sorted((link["name"], link["origin"]) for link in links),
            [
                ("AH Les pain triomphe heel online", "confirmed"),
                ("AH Les pains triomphe heel", "confirmed"),
            ],
            "elke schrijfwijze",
        )
        page = await loose_names.page("ah")
        self.assertEqual((page["cards"], page["total"], page["toSearch"]), ([], 0, 0))
        self.assertEqual(
            await self.history_ids("ah", "triomphe"),
            ["2"],
            "de catalogus ziet de koppeling meteen",
        )
        with self.assertRaises(ValueError):
            await loose_names.confirm("ah", card["key"], "2")

    async def test_confirm_and_reject_validate_their_input(self) -> None:
        for store, key in (
            ("aldi", "~x"),
            ("nope", "~x"),
            ("ah", ""),
            ("ah", "x" * 500),
            ("ah", None),
        ):
            with self.assertRaises(ValueError):
                await loose_names.confirm(store, key, "1")
            with self.assertRaises(ValueError):
                await loose_names.reject(store, key)
        with self.assertRaises(ValueError):
            await loose_names.confirm("ah", "~geen kaart", "1")
        self.assertFalse(await loose_names.reject("ah", "~geen kaart"))
        with self.assertRaises(ValueError):
            await loose_names.page("lidl")

    async def test_reject_and_restore(self) -> None:
        self.bought("Jumbo Oude naam", 2)
        await loose_names.run_round(
            "jumbo",
            NOW,
            search=FakeSearch({"Jumbo Oude naam": [item("1", "Jumbo Iets anders")]}),
        )
        key = name_keys.key("Jumbo Oude naam")
        self.assertTrue(await loose_names.reject("jumbo", key))
        self.assertFalse(await loose_names.reject("jumbo", key), "al afgewezen")
        page = await loose_names.page("jumbo")
        self.assertEqual(
            (page["cards"], page["rejected"]),
            ([], [{"key": key, "name": "Jumbo Oude naam", "bought": 2}]),
        )
        self.assertEqual(await loose_names.due("jumbo", later(days=365), 100), [])
        self.assertTrue(await loose_names.restore("jumbo", key))
        self.assertFalse(await loose_names.restore("jumbo", key), "niet meer afgewezen")
        page = await loose_names.page("jumbo")
        self.assertEqual(
            ([card["key"] for card in page["cards"]], page["rejected"]), ([key], [])
        )

    async def test_a_round_never_overwrites_a_rejection_it_did_not_see(self) -> None:
        self.bought("Jumbo Oude naam", 2)
        key = name_keys.key("Jumbo Oude naam")
        self.state(
            "jumbo",
            "Jumbo Oude naam",
            later(days=-100),
            "candidates",
            [item("1", "Jumbo Iets")],
        )

        async def rejected_meanwhile(query: str) -> None:
            await loose_names.reject("jumbo", key)

        await loose_names.run_round(
            "jumbo",
            NOW,
            search=FakeSearch(
                {"Jumbo Oude naam": [item("2", "Jumbo Nieuw")]},
                before=rejected_meanwhile,
            ),
        )
        self.assertEqual(self.row("jumbo", "Jumbo Oude naam")["outcome"], "rejected")

    async def test_page_counts_cards_to_search_missing_and_rejected(self) -> None:
        self.bought("Jumbo Met kaart", 5, paid=1.25, year=2020)
        self.bought("Jumbo Niet gevonden", 4)
        self.bought("Jumbo Afgewezen", 3)
        self.bought("Jumbo Nog niet opgezocht", 1)
        search = FakeSearch(
            {
                "Jumbo Met kaart": [item("1", "Jumbo Kaart een", price=1.5)],
                "Jumbo Afgewezen": [item("2", "Jumbo Iets anders")],
            }
        )
        result = await loose_names.run_round("jumbo", search=search, searches=3)
        self.assertEqual(result["searches"], 3)
        await loose_names.reject("jumbo", name_keys.key("Jumbo Afgewezen"))
        page = await loose_names.page("jumbo")
        self.assertEqual(
            page,
            {
                "cards": [
                    {
                        "key": name_keys.key("Jumbo Met kaart"),
                        "name": "Jumbo Met kaart",
                        "bought": 5,
                        "firstYear": 2020,
                        "lastYear": 2024,
                        "paidMin": 1.25,
                        "paidMax": 1.25,
                        "proposal": None,
                        "candidates": [
                            {
                                "productId": "1",
                                "title": "Jumbo Kaart een",
                                "brand": None,
                                "unitSize": "500 g",
                                "price": 1.5,
                                "image": "https://static.example/x.png",
                            }
                        ],
                    }
                ],
                "total": 1,
                "toSearch": 1,
                "missing": [
                    {
                        "key": name_keys.key("Jumbo Niet gevonden"),
                        "name": "Jumbo Niet gevonden",
                        "bought": 4,
                    }
                ],
                "rejected": [
                    {
                        "key": name_keys.key("Jumbo Afgewezen"),
                        "name": "Jumbo Afgewezen",
                        "bought": 3,
                    }
                ],
                "pausedUntil": None,
                "running": False,
            },
        )
        self.assertEqual(
            (
                (await loose_names.page("jumbo", offset=1))["cards"],
                (await loose_names.page("jumbo", offset=1))["total"],
            ),
            ([], 1),
        )
        self.assertEqual(
            len((await loose_names.page("jumbo", offset="x", limit="nope"))["cards"]),
            1,
            "een kapotte invoer valt terug",
        )

    async def test_page_shows_the_pause(self) -> None:
        await auth.set_setting(
            "loose_names_paused_until_crisp", "2999-01-01T00:00:00+00:00"
        )
        self.assertEqual(
            (await loose_names.page("crisp"))["pausedUntil"],
            "2999-01-01T00:00:00+00:00",
        )


class RaceTest(LooseNamesCase):
    """Wat er tijdens een zoekopdracht gebeurt: een andere zoeker bij AH krijgt een 403, of de gebruiker kiest al."""

    async def test_a_403_of_another_ah_caller_while_queued_sends_nothing(self) -> None:
        # De bonnenronde (of het zoeken in de Prijsgeschiedenis) houdt het slot van `ah_orders.polite` en krijgt een 403, terwijl
        # de ronde van losse namen erachter wacht: daarna mag er geen verzoek meer naar AH.
        self.bought("AH Eerste naam", 2, store="ah")
        old: str = later(days=-100)
        self.state("ah", "AH Eerste naam", old, "candidates", [item("1", "AH Iets")])
        inside, release = asyncio.Event(), asyncio.Event()

        async def other_caller() -> None:
            async def call() -> Any:
                inside.set()
                await release.wait()
                raise ah.AhHttpError("AH productzoekopdracht mislukt (403)", 403)

            try:
                await ah_orders.polite(call)
            except ah.AhHttpError as error:
                await ah_orders.pause(NOW, error.status)

        other: asyncio.Task[None] = asyncio.create_task(other_caller())
        await asyncio.wait_for(inside.wait(), 5)
        queued: asyncio.Event = asyncio.Event()
        polite = ah_orders.polite

        async def queue(call: Callable[[], Awaitable[Any]]) -> Any:
            queued.set()  # de ronde keek al naar de pauze en wacht nu op het slot van de ander
            return await polite(call)

        async def pace(seconds: float) -> None:
            # Het tempo van `ah_orders._polite` echt laten lopen (hooguit een seconde): in die 1,5 seconde onder het slot zet de
            # ander zijn pauze, want `ah_orders` pauzeert pas nadat het slot vrij is.
            await asyncio.sleep(min(seconds, 1.0))

        search = FakeSearch()
        with mock.patch.object(ah_orders, "polite", queue), mock.patch.object(ah_orders, "_wait", pace):
            loose: asyncio.Task[dict[str, Any]] = asyncio.create_task(loose_names.run_round("ah", NOW, search=search))
            await asyncio.wait_for(queued.wait(), 5)
            self.assertFalse(loose.done())
            release.set()
            await other
            result: dict[str, Any] = await asyncio.wait_for(loose, 5)
        self.assertEqual(search.calls, [], "geen verzoek na de 403 van de ander")
        self.assertEqual((result["searches"], result["stopped"]), (0, "blocked"))
        state = self.row("ah", "AH Eerste naam")
        self.assertEqual((state["checked_at"], state["outcome"]), (old, "candidates"), "de oude stand")

    async def test_a_rejection_during_the_search_gets_no_exact_link(self) -> None:
        self.bought("Jumbo Pindakaas naturel", 2)
        self.state("jumbo", "Jumbo Pindakaas naturel", later(days=-100), "candidates", [item("1", "Jumbo Iets")])
        key: str = name_keys.key("Jumbo Pindakaas naturel")

        async def rejected_meanwhile(query: str) -> None:
            await loose_names.reject("jumbo", key)

        search = FakeSearch({"Jumbo Pindakaas naturel": [item("1", "Jumbo Pindakaas Naturel")]}, before=rejected_meanwhile)
        result: dict[str, Any] = await loose_names.run_round("jumbo", NOW, search=search)
        self.assertEqual((result["searches"], result["exact"]), (1, 0))
        self.assertEqual((await product_links.list_links("jumbo"))["links"], [], "afgewezen: geen koppeling")
        self.assertEqual(self.row("jumbo", "Jumbo Pindakaas naturel")["outcome"], "rejected")

    async def test_a_confirm_during_the_search_leaves_no_row(self) -> None:
        self.bought("Jumbo Oude naam", 2)
        chosen: dict[str, Any] | None = loose_name_rules.candidate("jumbo", item("1", "Jumbo Nieuwe naam"))
        self.state("jumbo", "Jumbo Oude naam", later(days=-100), "candidates", [chosen])
        key: str = name_keys.key("Jumbo Oude naam")

        async def confirmed_meanwhile(query: str) -> None:
            await loose_names.confirm("jumbo", key, "1")

        search = FakeSearch({"Jumbo Oude naam": [item("2", "Jumbo Iets anders")]}, before=confirmed_meanwhile)
        result: dict[str, Any] = await loose_names.run_round("jumbo", NOW, search=search)
        self.assertEqual((result["searches"], result["candidates"]), (1, 0))
        self.assertIsNone(self.row("jumbo", "Jumbo Oude naam"), "geen verouderde kaart")
        links = (await product_links.list_links("jumbo"))["links"]
        self.assertEqual([(link["productId"], link["origin"]) for link in links], [("1", "confirmed")])
        self.assertEqual((await loose_names.page("jumbo"))["cards"], [])

    async def test_crisp_refusing_the_session_pauses_crisp(self) -> None:
        self.bought("Crisp Eerste naam", 2, store="crisp")
        self.bought("Crisp Tweede naam", 1, store="crisp")
        search = FakeSearch({"Crisp Eerste naam": crisp.CrispAuthError("Crisp weigerde de sessie")})
        result: dict[str, Any] = await loose_names.run_round("crisp", NOW, search=search)
        self.assertEqual((result["searches"], result["stopped"]), (1, "blocked"))
        self.assertEqual(search.queries, ["Crisp Eerste naam"])
        self.assertEqual(await loose_names.paused_until("crisp", NOW), later(hours=loose_names.BACKOFF_HOURS))
