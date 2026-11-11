"""De voorstellen van de AI bij losse aankoopnamen (`loose_names_ai`).

Geen test spreekt een echte aanbieder aan: `ai_review._call` is vervangen door `FakeAi`, die elke aanroep onthoudt.
"""

import json
import os
import tempfile
from contextlib import closing
from typing import Any
from unittest import mock

from helpers import AppTestCase, db, run

from mealieshopper import (
    ai_review,
    auth,
    loose_names,
    loose_names_ai,
    name_keys,
    price_import,
    product_links,
)

KEY: str = "sk-ant-api03-GEHEIM-0123456789abcdef"
NOW: str = "2026-11-11T01:00:00+00:00"


def candidate(
    product_id: str, title: str, unit_size: str = "500 g", price: float = 1.99
) -> dict[str, Any]:
    return {
        "productId": product_id,
        "title": title,
        "brand": "Merk",
        "unitSize": unit_size,
        "price": price,
        "image": "https://static.example/geheim.png",
    }


class FakeAi:
    """`ai_review._call`: per aanroep het volgende antwoord uit `answers` (een dict, of een fout die wordt opgeworpen)."""

    def __init__(self, answers: list[Any] | None = None) -> None:
        self.answers: list[Any] = answers or []
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        provider: str,
        key: str,
        model: str,
        system: str,
        payload: str,
        **options: Any,
    ) -> tuple[Any, int]:
        self.calls.append(
            {
                "provider": provider,
                "system": system,
                "payload": json.loads(payload),
                "options": options,
            }
        )
        answer: Any = self.answers.pop(0) if self.answers else {"choices": []}
        if isinstance(answer, Exception):
            raise answer
        return answer, 10


def letters(n: int) -> str:
    """Een unieke naam per getal, zonder cijfers (de sleutel van een naam vouwt cijfers en spaties niet altijd uit elkaar)."""
    return "merk " + chr(97 + n // 26) + chr(97 + n % 26) + "x"


def choice(
    number: int, pick: int, sure: bool = True, reason: str = "zelfde product"
) -> dict[str, Any]:
    return {"id": number, "choice": pick, "surePack": sure, "reason": reason}


class LooseAiCase(AppTestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(
            os.environ,
            {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"},
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
            os.environ.pop(name, None)
        run(auth.init_db())
        self.orders: int = 0

    def name(
        self,
        title: str,
        cands: list[dict[str, Any]] | None = None,
        times: int = 1,
        outcome: str = "candidates",
        proposal: dict[str, Any] | None = None,
        store: str = "jumbo",
        paid: float | None = 2.0,
    ) -> None:
        """Een losse naam met `times` aankopen en een rij in `loose_names`."""
        with closing(db(price_import)) as conn, conn:
            for n in range(times):
                self.orders += 1
                conn.execute(
                    "INSERT INTO imported_purchases (store, order_ref, position, day, title, quantity, paid, regular, order_total, imported_at) "
                    "VALUES (?, ?, 0, ?, ?, 1, ?, NULL, 10, 'x')",
                    (store, f"M{self.orders}", f"{2022 + n}-03-14", title, paid),
                )
        with closing(db(loose_names)) as conn, conn:
            conn.execute(
                "INSERT INTO loose_names (store, key, name, checked_at, outcome, candidates, proposal) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    store,
                    name_keys.key(title),
                    title,
                    NOW,
                    outcome,
                    json.dumps(
                        cands or [candidate("p1", "Eerste"), candidate("p2", "Tweede")]
                    ),
                    json.dumps(proposal) if proposal else None,
                ),
            )

    def proposal(self, title: str, store: str = "jumbo") -> dict[str, Any] | None:
        with closing(db(loose_names)) as conn:
            row = conn.execute(
                "SELECT proposal FROM loose_names WHERE store = ? AND key = ?",
                (store, name_keys.key(title)),
            ).fetchone()
        return json.loads(row["proposal"]) if row and row["proposal"] else None

    async def key(self) -> None:
        await ai_review.save({"keys": {"anthropic": KEY}})

    async def propose(
        self, ai: FakeAi, store: str = "jumbo", limit: int | None = None
    ) -> dict[str, int]:
        with mock.patch.object(ai_review, "_call", ai):
            return await loose_names_ai.propose(store, limit)


class ProposeTest(LooseAiCase):
    async def test_valid_choice_is_stored_as_proposal_with_sure_pack_and_reason(
        self,
    ) -> None:
        await self.key()
        self.name("pindakaas")
        ai: FakeAi = FakeAi(
            [{"choices": [choice(1, 2, sure=False, reason="zelfde merk")]}]
        )
        result: dict[str, int] = await self.propose(ai)
        self.assertEqual(
            self.proposal("pindakaas"),
            {"productId": "p2", "surePack": False, "reason": "zelfde merk"},
        )
        self.assertEqual(
            (result["asked"], result["proposed"], result["none"], result["tokens"]),
            (1, 1, 0, 10),
        )
        self.assertEqual(
            ai.calls[0]["options"],
            {"schema": loose_names_ai.SCHEMA, "tool": "report_choices"},
        )
        self.assertEqual(ai.calls[0]["system"], loose_names_ai.SYSTEM_PROMPT)

    async def test_choice_zero_is_stored_so_the_name_is_not_asked_again(self) -> None:
        await self.key()
        self.name("pindakaas")
        first: FakeAi = FakeAi(
            [{"choices": [choice(1, 0, sure=True, reason="geen gelijke")]}]
        )
        result: dict[str, int] = await self.propose(first)
        self.assertEqual(
            self.proposal("pindakaas"),
            {"productId": None, "surePack": False, "reason": "geen gelijke"},
        )
        self.assertEqual((result["proposed"], result["none"]), (0, 1))
        second: FakeAi = FakeAi()
        self.assertEqual((await self.propose(second))["asked"], 0)
        self.assertEqual(second.calls, [])

    async def test_unknown_id_out_of_range_choice_and_wrong_types_are_ignored(
        self,
    ) -> None:
        await self.key()
        self.name("pindakaas", times=2)
        self.name("hagelslag")
        bad: list[Any] = [
            choice(9, 1),
            choice(0, 1),
            choice(1, 3),
            choice(1, -1),
            {"id": "1", "choice": 1, "surePack": True, "reason": "x"},
            {"id": 1, "choice": True, "surePack": True, "reason": "x"},
            {"id": 1, "choice": 1.0, "surePack": True, "reason": "x"},
            {"id": 1, "choice": 1, "surePack": "ja", "reason": "x"},
            {"id": 1, "choice": 1, "surePack": True, "reason": 5},
            {"id": 1, "choice": 1, "surePack": True},
            "tekst",
            None,
        ]
        result: dict[str, int] = await self.propose(
            FakeAi([{"choices": bad + [choice(2, 1)]}])
        )
        self.assertIsNone(self.proposal("pindakaas"))
        self.assertEqual(self.proposal("hagelslag")["productId"], "p1")
        self.assertEqual((result["asked"], result["proposed"]), (2, 1))

    async def test_non_dict_answer_stores_nothing(self) -> None:
        await self.key()
        self.name("pindakaas")
        for answer in (
            None,
            "tekst",
            [choice(1, 1)],
            {"choices": "nee"},
            {"other": []},
        ):
            with self.subTest(answer=answer):
                result: dict[str, int] = await self.propose(FakeAi([answer]))
                self.assertIsNone(self.proposal("pindakaas"))
                self.assertEqual(
                    (result["asked"], result["proposed"], result["none"]), (1, 0, 0)
                )

    async def test_propose_never_writes_product_links(self) -> None:
        await self.key()
        self.name("pindakaas")
        await self.propose(FakeAi([{"choices": [choice(1, 1)]}]))
        with closing(db(product_links)) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM product_links").fetchone()[0], 0
            )

    async def test_names_go_in_groups_of_20(self) -> None:
        await self.key()
        for n in range(45):
            self.name(letters(n), times=45 - n)
        ai: FakeAi = FakeAi()
        result: dict[str, int] = await self.propose(ai)
        sizes: list[int] = [len(call["payload"]["items"]) for call in ai.calls]
        self.assertEqual(sizes, [20, 20, 5])
        self.assertEqual(result["asked"], 45)
        self.assertEqual(
            ai.calls[0]["payload"]["items"][0]["name"],
            letters(0),
            "de vaakst gekochte eerst",
        )
        self.assertEqual(
            [i["id"] for i in ai.calls[2]["payload"]["items"]], [1, 2, 3, 4, 5]
        )

    async def test_limit_asks_at_most_that_many_names(self) -> None:
        await self.key()
        self.name("weinig", times=1)
        self.name("veel", times=3)
        self.name("middel", times=2)
        ai: FakeAi = FakeAi()
        await self.propose(ai, limit=2)
        self.assertEqual(
            [i["name"] for i in ai.calls[0]["payload"]["items"]], ["veel", "middel"]
        )

    async def test_only_open_names_with_candidates_and_no_proposal_are_asked(
        self,
    ) -> None:
        await self.key()
        self.name("open")
        self.name(
            "heeft voorstel",
            proposal={"productId": "p1", "surePack": True, "reason": "x"},
        )
        self.name("niet gevonden", cands=[], outcome="missing")
        self.name("afgewezen", outcome="rejected")
        self.name("andere winkel", store="picnic")
        ai: FakeAi = FakeAi()
        result: dict[str, int] = await self.propose(ai)
        self.assertEqual([i["name"] for i in ai.calls[0]["payload"]["items"]], ["open"])
        self.assertEqual(result["asked"], 1)

    async def test_reason_is_cut_to_120_chars_and_stripped_of_newlines(self) -> None:
        await self.key()
        self.name("pindakaas")
        long: str = "  woord\n\n<b>vet</b>\t" + "x " * 100
        await self.propose(FakeAi([{"choices": [choice(1, 1, reason=long)]}]))
        reason: str = self.proposal("pindakaas")["reason"]
        self.assertLessEqual(len(reason), loose_names_ai.MAX_REASON_CHARS)
        self.assertEqual(len(reason), 120)
        self.assertTrue(reason.startswith("woord <b>vet</b> x x"))
        self.assertNotRegex(reason, r"[\n\t]|  ")

    async def test_ai_error_keeps_earlier_groups(self) -> None:
        await self.key()
        for n in range(25):
            self.name(letters(n), times=25 - n)
        ai: FakeAi = FakeAi(
            [
                {"choices": [choice(n, 1) for n in range(1, 21)]},
                ai_review.AiError("de aanbieder is stuk"),
            ]
        )
        with self.assertRaisesRegex(ai_review.AiError, "stuk"):
            await self.propose(ai)
        self.assertEqual(len(ai.calls), 2)
        self.assertEqual(self.proposal(letters(0))["productId"], "p1")
        self.assertEqual(self.proposal(letters(19))["productId"], "p1")
        self.assertIsNone(self.proposal(letters(20)))

    async def test_without_an_ai_it_raises_a_readable_error(self) -> None:
        self.name("pindakaas")
        ai: FakeAi = FakeAi()
        with self.assertRaises(ai_review.AiError) as raised:
            await self.propose(ai)
        self.assertIn("API", str(raised.exception))
        self.assertEqual(ai.calls, [])

    async def test_a_name_confirmed_while_the_ai_answers_keeps_no_proposal(
        self,
    ) -> None:
        await self.key()
        self.name("pindakaas")

        async def reject_meanwhile(*args: Any, **kwargs: Any) -> tuple[Any, int]:
            await loose_names.reject("jumbo", name_keys.key("pindakaas"))
            return {"choices": [choice(1, 1)]}, 5

        with mock.patch.object(ai_review, "_call", reject_meanwhile):
            await loose_names_ai.propose("jumbo")
        self.assertIsNone(self.proposal("pindakaas"))

    async def test_payload_has_no_field_beyond_the_listed_ones(self) -> None:
        await self.key()
        self.name("pindakaas", times=3, paid=2.5)
        self.name("zonder prijs", paid=None)
        ai: FakeAi = FakeAi()
        await self.propose(ai)
        items: list[dict[str, Any]] = {
            i["name"]: i for i in ai.calls[0]["payload"]["items"]
        }
        for item in items.values():
            self.assertEqual(
                set(item),
                {"id", "name", "timesBought", "paidEach", "years", "candidates"},
            )
            for cand in item["candidates"]:
                self.assertEqual(set(cand), {"n", "title", "brand", "size", "price"})
        first: dict[str, Any] = items["pindakaas"]
        self.assertEqual(
            (first["timesBought"], first["paidEach"], first["years"]),
            (3, [2.5, 2.5], "2022-2024"),
        )
        self.assertEqual(
            first["candidates"][1],
            {
                "n": 2,
                "title": "Tweede",
                "brand": "Merk",
                "size": "500 g",
                "price": 1.99,
            },
        )
        self.assertIsNone(items["zonder prijs"]["paidEach"])
        raw: str = json.dumps(ai.calls[0]["payload"])
        for secret in (
            "geheim.png",
            "p1",
            "p2",
            "2022-03",
            name_keys.key("pindakaas") + "x",
            "jumbo",
        ):
            self.assertNotIn(secret, raw)


class NightlySettingTest(LooseAiCase):
    async def test_loose_nightly_is_off_by_default_and_saved_through_ai_review_save(
        self,
    ) -> None:
        self.assertIs(await ai_review.loose_nightly(), False)
        self.assertIs((await ai_review.status())["looseNightly"], False)
        await ai_review.save({"keys": {"anthropic": KEY}})
        self.assertIs(
            await ai_review.loose_nightly(), False, "een sleutel zet hem niet aan"
        )
        self.assertIs(
            (await ai_review.save({"looseNightly": True}))["looseNightly"], True
        )
        self.assertIs(await ai_review.loose_nightly(), True)
        await ai_review.save({"models": {}})
        self.assertIs(
            await ai_review.loose_nightly(), True, "een andere opslag laat hem staan"
        )
        self.assertIs(
            (await ai_review.save({"looseNightly": False}))["looseNightly"], False
        )
        await ai_review.save({"looseNightly": "true"})
        self.assertIs(
            await ai_review.loose_nightly(), False, "alleen een echte true zet hem aan"
        )
