"""De sleutel van een naam (`name_keys`) en het gedeelde bestand met koppelingen per winkel (`shared_links`)."""

import logging
import os
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
import test_price_import
from helpers import db, in_db

from mealieshopper import ah_orders, auth, name_keys, price_import, prices, product_links, shared_links
from mealieshopper.app import create_app
from mealieshopper.shared_links import SharedLink


def test_key_ignores_plural_and_online() -> None:
    assert name_keys.key("AH Les pain triomphe heel online") == name_keys.key("AH Les pains triomphe heel")


def test_old_names_keep_working() -> None:
    assert product_links.fold("AH Ah zachte spinazie") == name_keys.fold("AH Ah zachte spinazie") == "ah zachte spinazie"
    assert prices._near_name("ah les pains triomphe heel") == name_keys.near("ah les pains triomphe heel")


def test_dump_round_trips_sorted_one_link_per_line() -> None:
    links: list[SharedLink] = [
        SharedLink("ah stamppot", "2", "AH Stamppot", "1 kg"),
        SharedLink("ah doosjevol kersen", "1", "AH Doosje met kersen", "250 g"),
    ]
    text: str = shared_links.dump(links)
    assert [link.name for link in shared_links.parse(text)] == [
        "ah doosjevol kersen",
        "ah stamppot",
    ]
    # kop, twee koppelingen, sluiting
    assert text.count("\n") == 4 and text.endswith("\n")
    assert text.splitlines()[0] == '{"version": 1, "links": ['
    assert text.splitlines()[1] == '  {"name": "ah doosjevol kersen", "productId": "1", "title": "AH Doosje met kersen", "unitSize": "250 g"},'
    assert text.splitlines()[2].endswith('"unitSize": "1 kg"}')
    assert text.splitlines()[3] == "]}"
    assert shared_links.problems(shared_links.parse(text)) == []


def test_dump_of_nothing_is_an_empty_list() -> None:
    assert shared_links.dump([]) == '{"version": 1, "links": [\n]}\n'
    assert shared_links.parse(shared_links.dump([])) == []


def test_dump_keeps_non_ascii() -> None:
    text: str = shared_links.dump([SharedLink("ah crème", "1", "AH Crème", "1 stuk")])
    assert "Crème" in text


def test_problems_names_each_fault() -> None:
    unsorted: list[SharedLink] = [
        SharedLink("ah b", "1", "B", ""),
        SharedLink("ah a", "2", "A", ""),
    ]
    assert any("gesorteerd" in found for found in shared_links.problems(unsorted))

    twice: list[SharedLink] = [
        SharedLink("ah a", "1", "A", ""),
        SharedLink("ah a", "1", "A", ""),
    ]
    assert any("twee keer" in found for found in shared_links.problems(twice))

    same_key: list[SharedLink] = [
        SharedLink("ah les pain", "1", "A", ""),
        SharedLink("ah les pains", "2", "B", ""),
    ]
    found_same_key: list[str] = shared_links.problems(same_key)
    assert any("dezelfde sleutel" in found for found in found_same_key)

    same_key_same_product: list[SharedLink] = [
        SharedLink("ah les pain", "1", "A", ""),
        SharedLink("ah les pains", "1", "A", ""),
    ]
    assert shared_links.problems(same_key_same_product) == []

    empty_id: list[SharedLink] = [SharedLink("ah a", " ", "A", "")]
    assert any("leeg productId" in found for found in shared_links.problems(empty_id))

    unfolded: list[SharedLink] = [SharedLink("AH Crème", "1", "A", "")]
    assert any("niet gevouwen" in found for found in shared_links.problems(unfolded))


@pytest.mark.parametrize(
    "text",
    [
        '{"version": 2, "links": []}',
        '{"version": 1, "links": [{"name": "ah a", "productId": "1", "title": "A", "unitSize": "", "price": "1"}]}',
        '{"version": 1, "links": [{"name": "ah a", "productId": "1", "title": "A"}]}',
        '{"version": 1, "links": [], "extra": 1}',
        '{"version": 1}',
        '{"version": 1, "links": {}}',
        '{"version": 1, "links": [{"name": "ah a", "productId": 1, "title": "A", "unitSize": ""}]}',
        '{"version": 1, "links": ["ah a"]}',
        "[]",
        "niet json",
    ],
)
def test_parse_rejects_bad_shapes(text: str) -> None:
    with pytest.raises(ValueError):
        shared_links.parse(text)


@pytest.mark.parametrize("store", product_links.STORES)
def test_shipped_file_is_valid(store: str) -> None:
    text: str = (shared_links.DATA_DIR / f"{store}.json").read_text(encoding="utf-8")
    links: list[SharedLink] = shared_links.parse(text)
    assert shared_links.problems(links) == [] and shared_links.dump(links) == text


def test_load_keys_links_by_name_key(tmp_path, monkeypatch) -> None:
    (tmp_path / "ah.json").write_text(
        shared_links.dump([SharedLink("ah les pain triomphe heel", "7", "AH Les pains", "1 stuk")]),
        encoding="utf-8",
    )
    monkeypatch.setattr(shared_links, "DATA_DIR", tmp_path)
    monkeypatch.setattr(shared_links, "_cache", {})
    loaded: dict[str, SharedLink] = shared_links.load("ah")
    assert loaded[name_keys.key("AH Les pains triomphe heel online")].product_id == "7"


def test_broken_file_loads_as_empty(tmp_path, monkeypatch, caplog) -> None:
    (tmp_path / "ah.json").write_text("niet json", encoding="utf-8")
    monkeypatch.setattr(shared_links, "DATA_DIR", tmp_path)
    monkeypatch.setattr(shared_links, "_cache", {})
    with caplog.at_level(logging.WARNING, logger=shared_links.log.name):
        assert shared_links.load("ah") == {}
        assert shared_links.load("picnic") == {}  # ontbrekend bestand
    assert any("ah" in record.getMessage() and record.levelno == logging.WARNING for record in caplog.records)
    assert len([record for record in caplog.records if record.levelno == logging.WARNING]) == 2


def test_broken_file_with_deep_nesting_loads_as_empty(tmp_path, monkeypatch, caplog) -> None:
    (tmp_path / "ah.json").write_text("[" * 100000, encoding="utf-8")
    monkeypatch.setattr(shared_links, "DATA_DIR", tmp_path)
    monkeypatch.setattr(shared_links, "_cache", {})
    with caplog.at_level(logging.WARNING, logger=shared_links.log.name):
        assert dict(shared_links.load("ah")) == {}
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_load_result_is_read_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(shared_links, "DATA_DIR", tmp_path)
    monkeypatch.setattr(shared_links, "_cache", {})
    with pytest.raises(TypeError):
        shared_links.load("ah")["x"] = SharedLink("x", "1", "X", "")  # type: ignore[index]


def test_conflicting_file_warns_and_first_link_wins(tmp_path, monkeypatch, caplog) -> None:
    text: str = (
        '{"version": 1, "links": [\n'
        '  {"name": "ah les pain", "productId": "1", "title": "A", "unitSize": ""},\n'
        '  {"name": "ah les pains", "productId": "2", "title": "B", "unitSize": ""}\n'
        "]}\n"
    )
    (tmp_path / "ah.json").write_text(text, encoding="utf-8")
    monkeypatch.setattr(shared_links, "DATA_DIR", tmp_path)
    monkeypatch.setattr(shared_links, "_cache", {})
    with caplog.at_level(logging.WARNING, logger=shared_links.log.name):
        loaded = shared_links.load("ah")
    assert loaded[name_keys.key("ah les pain")].product_id == "1"
    warnings: list[str] = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "ah" in warnings[0] and "dezelfde sleutel" in warnings[0]


@pytest.mark.parametrize(
    "order",
    [
        "mealieshopper.name_keys, mealieshopper.line_offers, mealieshopper.product_links, mealieshopper.prices",
        "mealieshopper.product_links, mealieshopper.name_keys, mealieshopper.line_offers, mealieshopper.prices",
        "mealieshopper.line_offers, mealieshopper.name_keys, mealieshopper.product_links, mealieshopper.prices",
    ],
)
def test_import_order_does_not_matter(order: str) -> None:
    root: Path = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-c", f"import {order}"],
        cwd=root,
        env={**os.environ, "MEALIESHOPPER_TESTING": "1"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


TODAY: str = "2026-10-20"
CHERRIES: SharedLink = SharedLink("ah doosjevol kersen", "1234", "AH Doosje met kersen", "250 g")


def shared(*links: SharedLink) -> Any:
    """Het gedeelde bestand van AH met deze koppelingen (de bestanden die de app meelevert zijn nog leeg)."""
    by_key: dict[str, SharedLink] = {name_keys.key(link.name): link for link in links}
    return mock.patch.object(shared_links, "load", side_effect=lambda store: by_key if store == "ah" else {})


def mail_lines(*names: str) -> list[dict[str, Any]]:
    return [{"name": name, "quantity": 1, "price": 3.0, "total": 3.0} for name in names]


class SharedListCase(test_price_import.ImportCase):
    async def seed_catalogue(self) -> None:
        await self.ah_order(101, "2026-09-01", {"productId": 1234, "title": "AH Doosje met kersen", "quantity": 1, "price": 3.0, "amount": 3.0})
        await self.ah_order(102, "2026-09-02", {"productId": 4183, "title": "AH Bananen tros", "quantity": 1, "price": 1.29, "amount": 1.29})

    async def rows(self) -> dict[str, dict[str, Any]]:
        found = await in_db(prices._connect, lambda conn: prices._receipt_rows(conn, "ah", TODAY))
        return {row.get("receipt_name") or row["title"]: row for row in found[0]}

    def receipt(self, name: str) -> None:
        with closing(db(ah_orders)) as conn, conn:
            conn.execute(
                "INSERT INTO ah_receipts (receipt_id, date_time, total, details_synced, first_seen) VALUES ('R1', '2026-09-03', 0, 1, '2026-09-03')"
            )
            conn.execute(
                "INSERT INTO ah_receipt_items (receipt_id, position, product_id, name, quantity, price, amount) VALUES ('R1', 0, '99999', ?, 1, 3.0, 3.0)",
                (name,),
            )


class SharedListTest(SharedListCase):
    async def test_own_link_beats_shared_and_shared_beats_name_match(self):
        await self.seed_catalogue()
        names = ("AH Eigen naam", "AH Doosjevol kersen", "AH Bananen tros")
        await self.run_import(test_price_import.mail_order(items=mail_lines(*names)))
        await product_links.save("ah", "AH Eigen naam", {"productId": "9", "title": "Eigen keus"})
        links = (SharedLink("ah eigen naam", "7777", "Gedeeld", ""), CHERRIES, SharedLink("ah bananen tros", "5678", "AH Bananen los", "1 kg"))
        with shared(*links):
            rows = await self.rows()
        assert (rows["AH Eigen naam"]["product_id"], rows["AH Eigen naam"].get("shared")) == ("9", None)
        cherries = rows["AH Doosjevol kersen"]
        assert (cherries["product_id"], cherries["title"], cherries["unit_size"], cherries["receipt_name"], cherries["shared"]) == (
            "1234",
            "AH Doosje met kersen",
            "250 g",
            "AH Doosjevol kersen",
            True,
        )
        assert (rows["AH Bananen tros"]["product_id"], rows["AH Bananen tros"]["shared"]) == ("5678", True)
        assert (await self.rows())["AH Bananen tros"]["product_id"] == "4183", "zonder het bestand telt de naam"

    async def test_shared_link_does_not_touch_receipt_lines(self):
        await self.seed_catalogue()
        self.receipt("AH Doosjevol kersen")
        with shared(CHERRIES):
            rows = await self.rows()
        assert rows["AH Doosjevol kersen"]["product_id"] != "1234"
        assert not rows["AH Doosjevol kersen"].get("shared")

    async def test_reject_makes_the_name_loose_again_and_survives_a_new_file(self):
        await self.seed_catalogue()
        await self.run_import(test_price_import.mail_order(items=mail_lines("AH Doosjevol kersen")))
        with shared(CHERRIES):
            assert (await self.rows())["AH Doosjevol kersen"]["product_id"] == "1234"
            assert await shared_links.reject("ah", "AH Doosjevol kersen", "1234") is True
            assert await shared_links.reject("ah", "AH  Doosjevol kersen", "1234") is False, "dezelfde sleutel, al afgewezen"
            assert (await self.rows())["AH Doosjevol kersen"]["product_id"] == "import:ah doosjevol kersen"
        with shared(SharedLink("ah doosjevol kersen", "5678", "AH Kersen", "")):
            assert (await self.rows())["AH Doosjevol kersen"]["product_id"] == "5678"
        with shared(CHERRIES):
            assert await shared_links.restore("ah", "AH Doosjevol kersen", "1234") is True
            assert await shared_links.restore("ah", "AH Doosjevol kersen", "1234") is False
            assert (await self.rows())["AH Doosjevol kersen"]["product_id"] == "1234"

    async def test_reject_changes_the_history_of_a_product(self):
        await self.seed_catalogue()
        await self.run_import(
            *(test_price_import.mail_order(number=f"N{n}", day=f"2026-09-1{n}", items=mail_lines("AH Doosjevol kersen")) for n in range(3))
        )
        with shared(CHERRIES):
            product = (await prices.history("ah", "kersen", 50, today=TODAY))["products"][0]
            assert (product["productId"], product["timesBought"]) == ("1234", 4)
            await shared_links.reject("ah", "AH Doosjevol kersen", "1234")
            product = next(p for p in (await prices.history("ah", "kersen", 50, today=TODAY))["products"] if p["productId"] == "1234")
            assert product["timesBought"] == 1

    async def test_link_names_marks_a_shared_link_and_an_own_link_wins(self):
        await self.seed_catalogue()
        await product_links.save("ah", "AH Eigen naam", {"productId": "9", "title": "Eigen keus"})
        with shared(CHERRIES, SharedLink("ah eigen naam", "7777", "Gedeeld", "")):
            found = await prices.link_names("ah", ["AH Doosjevol kersen", "AH Eigen naam"])
        assert (found["AH Doosjevol kersen"]["productId"], found["AH Doosjevol kersen"]["shared"]) == ("1234", True)
        assert found["AH Eigen naam"]["productId"] == "9" and "shared" not in found["AH Eigen naam"]

    async def test_the_imported_order_carries_shared(self):
        await self.seed_catalogue()
        await self.run_import(test_price_import.mail_order(items=mail_lines("AH Doosjevol kersen")))
        with shared(CHERRIES):
            item = (await price_import.get_order("ah", "225301"))["items"][0]
        assert (item["productId"], item["shared"]) == ("1234", True)


class RejectionRouteTest(SharedListCase):
    async def test_routes_change_and_validate(self):
        client = self.client_for(create_app())
        body = {"store": "ah", "name": "AH Doosjevol kersen", "productId": "1234"}
        with shared(CHERRIES):
            assert (await client.post("/api/shared-links/reject", json=body)).get_json() == {"changed": True}
            assert (await client.post("/api/shared-links/reject", json=body)).get_json() == {"changed": False}
            assert (await client.post("/api/shared-links/restore", json=body)).get_json() == {"changed": True}
            for bad in ({**body, "store": "aldi"}, {**body, "store": "nope"}, {**body, "name": " "}, {**body, "productId": ""}, {"store": "ah"}):
                for route in ("reject", "restore"):
                    response = await client.post(f"/api/shared-links/{route}", json=bad)
                    assert response.status_code == 400, (route, bad)
                    assert response.get_json()["error"]

    async def test_only_a_link_from_the_shared_file_can_be_rejected_or_restored(self):
        # Eindreview van 2026-11-11 (M4): elke naam en elk nummer werd bewaard, en elke rij maakt de catalogus opnieuw.
        client = self.client_for(create_app())
        with shared(CHERRIES):
            for body in (
                {"store": "ah", "name": "AH Doosjevol kersen", "productId": "9999"},
                {"store": "ah", "name": "AH Iets anders", "productId": "1234"},
                {"store": "jumbo", "name": "AH Doosjevol kersen", "productId": "1234"},
            ):
                for route in ("reject", "restore"):
                    response = await client.post(f"/api/shared-links/{route}", json=body)
                    assert response.status_code == 400, (route, body)
                    assert "gedeelde bestand" in response.get_json()["error"], (route, body)
        with closing(db(shared_links)) as conn:
            assert conn.execute("SELECT COUNT(*) FROM shared_link_rejections").fetchone()[0] == 0

    def test_everyone_may_reject(self):
        assert not auth.admin_only("/api/shared-links/reject")
        assert not auth.admin_only("/api/shared-links/restore")
