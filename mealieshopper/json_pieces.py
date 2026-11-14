"""JSON lezen in stukken, voor een groot bestand dat een thread leest terwijl de event loop doorgaat.

`json.loads` is een aanroep in C die de GIL vasthoudt tot het hele document gelezen is. Een thread helpt dan niet: de loop
wacht zo lang op de GIL. Bij een back-up op de schaal van productie (150 MB) is dat bijna 400 ms in een keer. `loads` geeft
hetzelfde resultaat als `json.loads`, maar leest de grote lijsten van rijen (`tables` in een back-up: een object van
tabelnaam naar een lijst van rijen) een rij per aanroep (`JSONDecoder.raw_decode`). Tussen twee rijen draait Python, en daar
kan de loop de GIL krijgen. De rest van het document gaat zoals in `json.loads`.

Het formaat verandert niet: elk geldig JSON-document wordt gelezen zoals `json.loads` het leest, ook een back-up van een oudere
versie of een bestand met andere witruimte. Een document dat er anders uitziet dan verwacht (geen object, of `tables` geen
object van lijsten) gaat dat onderdeel in een keer, zoals voorheen. Ongeldige JSON geeft een `json.JSONDecodeError`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any, Protocol

_SPACE: re.Pattern[str] = re.compile(r"[ \t\n\r]*")  # de witruimte van JSON (RFC 8259), dezelfde als in `json.decoder`

Value = Callable[[str, int], tuple[Any, int]]


def _skip(text: str, position: int) -> int:
    match: re.Match[str] | None = _SPACE.match(text, position)
    return match.end() if match is not None else position


def _expect(text: str, position: int, character: str) -> int:
    """De positie na `character`, na eventuele witruimte. Een ander teken is ongeldige JSON."""
    position = _skip(text, position)
    if not text.startswith(character, position):
        raise json.JSONDecodeError(f"Expecting {character!r}", text, position)
    return position + 1


def _object(text: str, position: int, decoder: json.JSONDecoder, value: Value) -> tuple[dict[str, Any], int]:
    """Een object vanaf `position` (de `{`), met elke waarde uit `value(sleutel, positie)`. Een sleutel die twee keer staat,
    krijgt de laatste waarde op de plek van de eerste, zoals bij `json.loads`."""
    found: dict[str, Any] = {}
    position = _skip(text, position + 1)
    if text.startswith("}", position):
        return found, position + 1
    while True:
        if not text.startswith('"', position):
            raise json.JSONDecodeError("Expecting property name enclosed in double quotes", text, position)
        key: str
        key, position = decoder.raw_decode(text, position)
        position = _skip(text, _expect(text, position, ":"))
        found[key], position = value(key, position)
        position = _skip(text, position)
        if text.startswith("}", position):
            return found, position + 1
        position = _skip(text, _expect(text, position, ","))


class Rows(Protocol):
    """Waar de rijen van een lijst in komen: een `list`, of iets anders met `append` (`backup.Rows`)."""

    def append(self, row: Any, /) -> None: ...


def _shared(keys: dict[str, str]) -> Callable[[Any], Any]:
    """Wat er met elke rij gebeurt: een dict krijgt de sleutels uit `keys`, een keer per tekst (zo deelt een tabel van
    honderdduizenden rijen zijn kolomnamen, zoals in een enkele aanroep van `json.loads`)."""

    def finish(row: Any) -> Any:
        if isinstance(row, dict):
            row = {keys.setdefault(key, key): cell for key, cell in row.items()}
        return row

    return finish


def _rows(text: str, position: int, decoder: json.JSONDecoder, finish: Callable[[Any], Any], rows: Callable[[], Rows]) -> tuple[Any, int]:
    """Een lijst van rijen, een rij per aanroep van `raw_decode`, elke rij door `finish` in een nieuwe `rows()`. Iets anders dan
    een lijst gaat in een keer."""
    if not text.startswith("[", position):
        return decoder.raw_decode(text, position)
    found: Rows = rows()
    position = _skip(text, position + 1)
    if text.startswith("]", position):
        return found, position + 1
    while True:
        row: Any
        row, position = decoder.raw_decode(text, position)
        found.append(finish(row))
        position = _skip(text, position)
        if text.startswith("]", position):
            return found, position + 1
        position = _skip(text, _expect(text, position, ","))


def _tables(text: str, position: int, decoder: json.JSONDecoder, finish: Callable[[Any], Any], rows: Callable[[], Rows]) -> tuple[Any, int]:
    """Het object van tabelnaam naar rijen. Iets anders dan een object gaat in een keer."""
    if not text.startswith("{", position):
        return decoder.raw_decode(text, position)
    return _object(text, position, decoder, lambda key, at: _rows(text, at, decoder, finish, rows))


def loads(text: str, split: str, parse_constant: Callable[[str], Any] | None = None, rows: Callable[[], Rows] = list) -> Any:
    """`json.loads(text, parse_constant=parse_constant)`, met de waarde van de sleutel `split` van het object bovenaan (een object
    van lijsten) een element per aanroep. Elke lijst daarin wordt een `rows()`, met elk element erin zodra het gelezen is: zo
    hoeft de aanroeper de rijen niet allemaal tegelijk als dict te hebben (`backup.Rows`). Met een `list` (standaard) is het
    resultaat dat van `json.loads`."""
    decoder: json.JSONDecoder = json.JSONDecoder(parse_constant=parse_constant)
    position: int = _skip(text, 0)
    if not text.startswith("{", position):
        return decoder.decode(text)
    finish: Callable[[Any], Any] = _shared({})

    def value(key: str, at: int) -> tuple[Any, int]:
        return _tables(text, at, decoder, finish, rows) if key == split else decoder.raw_decode(text, at)

    found: dict[str, Any]
    found, position = _object(text, position, decoder, value)
    position = _skip(text, position)
    if position != len(text):
        raise json.JSONDecodeError("Extra data", text, position)
    return found
