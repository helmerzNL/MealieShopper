"""De sleutel van een naam (`name_keys`) en het gedeelde bestand met koppelingen per winkel (`shared_links`)."""

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mealieshopper import name_keys, prices, product_links, shared_links
from mealieshopper.shared_links import SharedLink


def test_key_ignores_plural_and_online() -> None:
    assert name_keys.key("AH Les pain triomphe heel online") == name_keys.key(
        "AH Les pains triomphe heel"
    )


def test_old_names_keep_working() -> None:
    assert (
        product_links.fold("AH Ah zachte spinazie")
        == name_keys.fold("AH Ah zachte spinazie")
        == "ah zachte spinazie"
    )
    assert prices._near_name("ah les pains triomphe heel") == name_keys.near(
        "ah les pains triomphe heel"
    )


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
    assert (
        text.splitlines()[1]
        == '  {"name": "ah doosjevol kersen", "productId": "1", "title": "AH Doosje met kersen", "unitSize": "250 g"},'
    )
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
        shared_links.dump(
            [SharedLink("ah les pain triomphe heel", "7", "AH Les pains", "1 stuk")]
        ),
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
    assert any(
        "ah" in record.getMessage() and record.levelno == logging.WARNING
        for record in caplog.records
    )
    assert (
        len([record for record in caplog.records if record.levelno == logging.WARNING])
        == 2
    )


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
