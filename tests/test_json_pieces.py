"""JSON in stukken lezen (`json_pieces.py`): hetzelfde resultaat en dezelfde fouten als `json.loads`."""

import json
from typing import Any

import pytest

from mealieshopper import json_pieces

DOCUMENTS: tuple[str, ...] = (
    '{"format":"x","tables":{"a":[{"id":1,"t":"één"},{"id":2,"t":null}],"b":[]},"settings":{}}',
    ' \r\n{ "tables" : { "a" : [ {"id" : 1} , {"id":2.5e3} ] , "b" : [ ] } , "n" : [1, [2, {"x": []}]] }\t\n',
    '{"tables":{}}',
    '{"tables":[1,2,3]}',  # geen object: in een keer, en `backup.prepare` weigert het
    '{"tables":{"a":"geen lijst","b":[1,"twee",[3],{"vier":4}]}}',
    '{"a":1,"tables":{"x":[{"k":1}]},"a":2,"tables":{"y":[]}}',  # dubbele sleutels: de laatste waarde, op de eerste plek
    "{}",
    "[1, 2]",  # geen object bovenaan
    '"tekst"',
    '{"tables":{"a":[{"k":"\\u00e9\\n\\"","l":true,"m":false}]}}',
)
INVALID: tuple[str, ...] = (
    "",
    "{",
    '{"tables":{"a":[{"id":1},]}}',  # een komma voor het einde
    '{"tables":{"a":[{"id":1} {"id":2}]}}',
    '{"tables":{"a":[{"id":1}]},}',
    '{"tables":{"a":[{"id":1}]}} x',  # iets na het document
    "{tables: {}}",
    '{"tables":{"a":[NaN]}}',
    '{"tables":{"a":[{"id":1}]',
    '﻿{"tables":{}}',
)


def _reject(name: str) -> Any:
    raise ValueError(f"unsupported number {name}")


@pytest.mark.parametrize("text", DOCUMENTS)
def test_the_result_is_that_of_json_loads(text: str) -> None:
    found: Any = json_pieces.loads(text, "tables", parse_constant=_reject)
    expected: Any = json.loads(text, parse_constant=_reject)
    assert found == expected
    if isinstance(expected, dict):
        assert list(found) == list(expected)  # dezelfde volgorde van de sleutels


@pytest.mark.parametrize("text", INVALID)
def test_invalid_json_is_an_error_as_with_json_loads(text: str) -> None:
    with pytest.raises(ValueError):
        json.loads(text, parse_constant=_reject)
    with pytest.raises(ValueError):
        json_pieces.loads(text, "tables", parse_constant=_reject)


def test_the_rows_of_a_table_share_their_column_names() -> None:
    text: str = json.dumps(
        {
            "tables": {
                "a": [{"store": "ah", "price": n} for n in range(3)],
                "b": [{"store": "jumbo"}],
            }
        }
    )
    found: Any = json_pieces.loads(text, "tables")
    names: set[int] = {
        id(key) for rows in found["tables"].values() for row in rows for key in row
    }
    assert len(names) == 2  # "store" en "price", een keer elk


def test_each_row_is_its_own_call(monkeypatch: pytest.MonkeyPatch) -> None:
    # Tussen twee rijen draait Python: daar kan de event loop de GIL krijgen.
    calls: list[int] = []
    original: Any = json.JSONDecoder.raw_decode

    def counted(self: json.JSONDecoder, text: str, position: int = 0) -> Any:
        calls.append(position)
        return original(self, text, position)

    monkeypatch.setattr(json.JSONDecoder, "raw_decode", counted)
    rows: list[dict[str, int]] = [{"id": n} for n in range(50)]
    json_pieces.loads(json.dumps({"format": "x", "tables": {"a": rows}}), "tables")
    # de sleutels "format", "tables" en "a", de waarde van "format", en elke rij
    assert len(calls) == 3 + 1 + len(rows)


def test_each_gets_every_row_as_it_is_read() -> None:
    text: str = '{"tables":{"a":[{"id":1},{"id":2}],"b":[3]},"rest":[{"id":4}]}'
    found: Any = json_pieces.loads(text, "tables", each=lambda row: ("rij", row))
    assert found == {"tables": {"a": [("rij", {"id": 1}), ("rij", {"id": 2})], "b": [("rij", 3)]}, "rest": [{"id": 4}]}
