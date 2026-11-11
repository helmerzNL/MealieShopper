"""De losse aankoopnamen per winkel en de ronde die ze bij de winkel opzoekt.

Een losse naam is een regel uit een bevestigingsmail die na je eigen koppeling, het gedeelde bestand en de naamherkenning nog bij
geen product hoort (`prices.loose_import_names`). De ronde zoekt zo'n naam op met `search_products(naam, CANDIDATES)` van de
winkel en bewaart de uitkomst per sleutel (`name_keys.key`) in `loose_names`. De regels van een zoekresultaat staan in
`loose_name_rules`:

- `exact`: precies een resultaat heeft dezelfde sleutel en is letterlijk dezelfde naam, met dezelfde getallen en verpakking
  (`loose_name_rules.outcome_of`). Dat wordt meteen een koppeling met herkomst `exact` (`product_links.save`). Haalt de gebruiker
  die koppeling weg, dan is de naam weer los en een kaart zoals bij `candidates`, en maakt de ronde hem niet opnieuw.
- `candidates`: de resultaten om uit te kiezen, als kaart op de pagina. Bevestigen (`confirm`) maakt een koppeling met herkomst
  `confirmed` en haalt de rij weg: de koppeling staat dan in `product_links`.
- `missing`: de winkel vond niets.
- `rejected`: "Geen van deze" (`reject`). Zo'n naam zoekt de ronde nooit meer op, tot `restore` hem terugzet.

De rem van de winkel. Bij AH gaat elke zoekopdracht door `ah_orders.polite` (het tempo en het uurbudget van het koppelen van de
bonnen) en geldt de pauze van AH (`ah_orders.paused_until`, `ah_orders.pause`). Bij de andere winkels houdt deze module hetzelfde
tempo (`PAUSE_SECONDS`) en een eigen pauze van `BACKOFF_HOURS` na een 403 of 429 (de instelling `loose_names_paused_until_<winkel>`).
De ronde stopt bij de eerste fout van de winkel, kijkt voor elke zoekopdracht of er intussen een pauze geldt, en loopt hooguit
`ROUND_DEADLINE_SECONDS`. Per winkel loopt er een ronde tegelijk.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite

from . import ah, ah_orders, ai_review, auth, crisp, jumbo, picnic, prices, product_links, purchase_sync, tasks
from .i18n import tr
from .loose_name_rules import candidates_of, kept_proposal, outcome_of, status_of

log: logging.Logger = logging.getLogger(__name__)

SEARCHES_PER_ROUND: int = 100
CANDIDATES: int = 10
RECHECK_DAYS: int = 90
PAUSE_SECONDS: float = 1.5
BACKOFF_HOURS: int = 12
BUDGET_WAIT_SECONDS: int = 600
ROUND_DEADLINE_SECONDS: int = 6 * 3600

Search = Callable[[str, int], Awaitable[list[dict[str, Any]]]]
STORES: tuple[str, ...] = product_links.STORES
BLOCKED_STATUSES: frozenset[int] = frozenset(
    {403, 429}
)  # een firewall of te veel verzoeken: niet aandringen
SETTING_PAUSED_PREFIX: str = "loose_names_paused_until_"  # plus de winkel, niet bij AH (die heeft `ah_orders.pause`)
MAX_KEY_CHARS: int = 400
PAGE_SIZE: int = 30
MAX_PAGE_SIZE: int = 100
SETTING_LAST_ROUND: str = "loose_names_last_round"
TASK_NAME: str = "loose-names"
# De uitkomsten van een losse naam die als kaart op de pagina staan. Een losse naam met een rij `exact` had een directe
# koppeling die de gebruiker weghaalde (Ontkoppelen): de rij houdt de kandidaat, dus het wordt een kaart (`_settle`).
CARD_OUTCOMES: frozenset[str] = frozenset({"candidates", "exact"})

SCHEMA: str = """
-- De stand per losse aankoopnaam (loose_names.py): de sleutel van de naam (`name_keys.key`), de schrijfwijze die het vaakst in de
-- mail stond, wanneer de winkel hem voor het laatst opzocht, de uitkomst (`candidates`, `exact`, `missing` of `rejected`), de
-- kandidaten als JSON-lijst van {productId, title, brand, unitSize, price, image} en het voorstel van de AI als JSON
-- {productId, surePack, reason} of NULL.
CREATE TABLE IF NOT EXISTS loose_names (
  store TEXT NOT NULL,
  key TEXT NOT NULL,
  name TEXT NOT NULL,
  checked_at TEXT NOT NULL,
  outcome TEXT NOT NULL,
  candidates TEXT NOT NULL DEFAULT '[]',
  proposal TEXT,
  PRIMARY KEY (store, key)
);
"""

# Een koppeling van een mens (`manual` of `confirmed`) op een van de schrijfwijzen van een naam. `{marks}` is een `?` per
# schrijfwijze (`product_links.fold`), na de winkel.
_HUMAN_LINK_SQL: str = (
    "SELECT 1 FROM product_links WHERE store = ? AND kind = 'name' AND origin IN ('manual', 'confirmed') AND key IN ({marks})"
)

# Een nieuwe uitkomst van de ronde, in een opdracht: een afwijzing (`reject`) of een bevestiging (`confirm`) die de gebruiker gaf
# terwijl de ronde de naam opzocht, blijft staan. Parameters: de zeven kolommen, dan de winkel en de schrijfwijzen van `{marks}`.
_UPSERT_SQL: str = f"""
INSERT INTO loose_names (store, key, name, checked_at, outcome, candidates, proposal) SELECT ?, ?, ?, ?, ?, ?, ?
WHERE NOT EXISTS ({_HUMAN_LINK_SQL})
ON CONFLICT (store, key) DO UPDATE SET name = excluded.name, checked_at = excluded.checked_at, outcome = excluded.outcome,
  candidates = excluded.candidates, proposal = excluded.proposal
WHERE loose_names.outcome != 'rejected'
"""


class _Paused(Exception):
    """De winkel ging op pauze terwijl de zoekopdracht op zijn beurt wachtte (`_unless_paused`)."""


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    """Een verbinding met de tabel. Sluit zonder commit, zoals `auth.connect`."""
    await auth.ensure_db()
    async with auth.connect() as conn:
        await auth.ensure_schema(conn, "loose_names", SCHEMA)
        yield await product_links.ensure(conn)  # `_HUMAN_LINK_SQL` leest de koppelingen


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


async def _wait(seconds: float) -> None:
    """De pauze tussen twee zoekopdrachten (niet bij AH) en het wachten op het uurbudget van AH. Een eigen functie, zodat een test
    hem vervangt zonder `asyncio.sleep` voor het hele proces te vervangen."""
    await asyncio.sleep(seconds)


def _clock() -> float:
    """De klok van de tijdslimiet van een ronde. Een eigen functie, zodat een test de tijd laat verstrijken."""
    return time.monotonic()


# --- een ronde per winkel tegelijk -----------------------------------------------------------------------------------------
# Een `asyncio.Lock` hoort bij de event loop waarin hij voor het eerst wacht. In de app is er een loop per proces, de tests
# draaien elk een eigen loop (zoals `ah_orders._lock`).
_LOCKS: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[str, asyncio.Lock]
] = weakref.WeakKeyDictionary()


def _lock(store: str) -> asyncio.Lock:
    """Het slot van de ronde van `store` in de lopende event loop."""
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    locks: dict[str, asyncio.Lock] = _LOCKS.setdefault(loop, {})
    return locks.setdefault(store, asyncio.Lock())


def running(store: str | None = None) -> bool:
    """Of er nu een ronde loopt voor `store`, of zonder winkel of de taak `loose-names` (`start`) loopt."""
    if store is not None:
        return _lock(store).locked()
    task: asyncio.Task[Any] | None = tasks.running().get(TASK_NAME)
    return task is not None and not task.done()


# --- invoer van buiten ------------------------------------------------------------------------------------------------------


def _store(value: Any) -> str:
    store: str = str(value or "").strip().lower()
    if store not in STORES:
        raise ValueError(tr("Onbekende winkel", "Unknown store"))
    return store


def _identity(store: Any, key: Any) -> tuple[str, str]:
    """(winkel, sleutel) uit een verzoek. ValueError met een leesbare melding bij een ongeldige invoer."""
    text: str = str(key or "").strip() if isinstance(key, str) else ""
    if not text or len(text) > MAX_KEY_CHARS:
        raise ValueError(tr("Een naam is nodig", "A name is required"))
    return _store(store), text


def _bounded(value: Any, default: int, low: int, high: int) -> int:
    """Een geheel getal tussen `low` en `high`, of `default` als `value` er geen is."""
    try:
        number: int = int(value) if not isinstance(value, bool) else default
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


# --- de pauze ---------------------------------------------------------------------------------------------------------------


async def paused_until(store: str, now: str) -> str | None:
    """Tot wanneer het zoeken bij `store` stilligt na een 403 of 429, of None. Bij AH de pauze van het koppelen van de bonnen."""
    if store == "ah":
        return await ah_orders.paused_until(now)
    until: str = await auth.get_setting(SETTING_PAUSED_PREFIX + store, "")
    return until if until and until > now else None


async def _pause(store: str, now: str, status: int) -> str:
    """Zet het zoeken bij `store` `BACKOFF_HOURS` uur stil. Bij AH de bestaande pauze (`ah_orders.pause`). Geeft het einde."""
    if store == "ah":
        return await ah_orders.pause(now, status)
    until: str = (
        datetime.fromisoformat(now) + timedelta(hours=BACKOFF_HOURS)
    ).isoformat(timespec="seconds")
    await auth.set_setting(SETTING_PAUSED_PREFIX + store, until)
    return until


# --- de stand -----------------------------------------------------------------------------------------------------------------


async def _states(store: str) -> dict[str, aiosqlite.Row]:
    async with _connect() as conn:
        rows: list[aiosqlite.Row] = list(
            await conn.execute_fetchall("SELECT * FROM loose_names WHERE store = ?", (store,))
        )
    return {row["key"]: row for row in rows}


def _json(value: Any, kind: type) -> Any:
    """Een JSON-kolom: de lijst of het object, of None als het iets anders is (een back-up is een bestand van buiten)."""
    try:
        found: Any = json.loads(value) if isinstance(value, str) else None
    except ValueError:
        return None
    return found if isinstance(found, kind) else None


def _due(
    loose: list[dict[str, Any]], states: dict[str, aiosqlite.Row], now: str
) -> list[dict[str, Any]]:
    """De losse namen die de ronde nu mag opzoeken: niet afgewezen en niet korter dan `RECHECK_DAYS` geleden opgezocht."""
    since: str = (datetime.fromisoformat(now) - timedelta(days=RECHECK_DAYS)).isoformat(
        timespec="seconds"
    )
    found: list[dict[str, Any]] = []
    for entry in loose:
        state: aiosqlite.Row | None = states.get(entry["key"])
        if state is None or (
            state["outcome"] != "rejected" and state["checked_at"] <= since
        ):
            found.append(entry)
    return found


async def due(store: str, now: str, limit: int) -> list[dict[str, Any]]:
    """Hooguit `limit` losse namen die de ronde nu mag opzoeken, de vaakst gekochte eerst (`prices.loose_import_names`)."""
    loose: list[dict[str, Any]] = await prices.loose_import_names(store)
    return _due(loose, await _states(store), now)[: max(0, limit)]


def _folded(entry: dict[str, Any]) -> list[str]:
    """De schrijfwijzen van een losse naam zoals een koppeling op naam ze bewaart (`product_links.fold`)."""
    return [product_links.fold(spelling) for spelling in entry["spellings"]]


async def _open_state(
    store: str, entry: dict[str, Any]
) -> tuple[bool, str | None, str | None]:
    """(staat de naam nog open, de bewaarde uitkomst, het bewaarde voorstel). Dicht is een naam die de gebruiker afwees of zelf
    koppelde terwijl de ronde hem opzocht: die krijgt geen koppeling en geen nieuwe stand."""
    folded: list[str] = _folded(entry)
    async with _connect() as conn:
        row: aiosqlite.Row | None = await auth.fetchone(
            conn, "SELECT outcome, proposal FROM loose_names WHERE store = ? AND key = ?", (store, entry["key"])
        )
        human: aiosqlite.Row | None = await auth.fetchone(
            conn, _HUMAN_LINK_SQL.format(marks=",".join("?" * len(folded))), (store, *folded)
        )
    rejected: bool = row is not None and row["outcome"] == "rejected"
    return (
        not rejected and human is None,
        row["outcome"] if row is not None else None,
        row["proposal"] if row is not None else None,
    )


async def _settle(store: str, entry: dict[str, Any], products: list[Any], now: str) -> str | None:
    """Bewaar de uitkomst van een zoekopdracht voor `entry` en geef hem terug, of None als de naam intussen dicht is
    (`_open_state`). Een directe koppeling geldt voor elke schrijfwijze. Was de naam al `exact` en is hij toch weer los, dan haalde
    de gebruiker die koppeling weg: dan wordt het een kaart (`candidates`) en geen nieuwe koppeling."""
    outcome: str
    kept: list[dict[str, Any]]
    outcome, kept = outcome_of(entry["key"], entry["name"], candidates_of(store, products, CANDIDATES))
    still_open: bool
    old_outcome: str | None
    old_proposal: str | None
    still_open, old_outcome, old_proposal = await _open_state(store, entry)
    if not still_open:
        return None
    if outcome == "exact" and old_outcome == "exact":
        outcome = "candidates"
    if outcome == "exact":
        for spelling in entry["spellings"]:
            await product_links.save(store, spelling, kept[0], now=now, origin="exact")
    folded: list[str] = _folded(entry)
    values: tuple[Any, ...] = (
        store, entry["key"], entry["name"], now, outcome, json.dumps(kept, ensure_ascii=False), kept_proposal(old_proposal, kept),
        store, *folded,
    )
    async with _connect() as conn:
        await conn.execute(_UPSERT_SQL.format(marks=",".join("?" * len(folded))), values)
        await conn.commit()
    return outcome


# --- de ronde -------------------------------------------------------------------------------------------------------------------


async def _ah_search(name: str, limit: int) -> list[dict[str, Any]]:
    """`ah.search_products` zonder de resultaten zonder eigen titel: hetzelfde verzoek en dezelfde volgorde. `ah._search_product_dict`
    geeft zo'n resultaat de zoekterm als titel, en dan heeft het dezelfde sleutel als de losse naam: een directe koppeling op niets."""
    found: Any = await ah._search_json(name, max(limit, ah.SEARCH_WINDOW))
    ranked: list[dict[str, Any]] = ah.rank_search_results(name, found.get("products") or [])
    titled: list[dict[str, Any]] = [item for item in ranked if item.get("title")]
    return [ah._search_product_dict(item, name) for item in titled[: max(1, limit)]]


def _default_search(store: str) -> Search:
    return {
        "ah": _ah_search,
        "jumbo": jumbo.search_products,
        "picnic": picnic.search_products,
        "crisp": crisp.search_products,
    }[store]


async def _unless_paused(store: str, search: Search, name: str, now: str) -> list[Any]:
    """De zoekopdracht zelf, tenzij de winkel intussen op pauze staat (`_Paused`). Bij AH loopt dit onder het slot van
    `ah_orders.polite`: kreeg een andere zoeker bij AH een 403 terwijl deze op zijn beurt wachtte, dan gaat er niets meer uit."""
    if await paused_until(store, now):
        raise _Paused()
    return await search(name, CANDIDATES)


async def _search(store: str, search: Search, name: str, now: str) -> list[Any]:
    """Een zoekopdracht, bij AH binnen het tempo en het uurbudget van `ah_orders.polite`. Een antwoord dat geen lijst is, is een fout."""
    if store == "ah":
        found: Any = await ah_orders.polite(lambda: _unless_paused(store, search, name, now))
    else:
        found = await _unless_paused(store, search, name, now)
    if not isinstance(found, list):
        raise TypeError(
            f"search_products gaf {type(found).__name__} in plaats van een lijst"
        )
    return found


async def _refused(store: str, error: Exception, now: str) -> str:
    """Hoe de ronde stopt na een fout van de winkel: `blocked` na een 403 of 429 (met de pauze), anders `error`."""
    status: int | None = status_of(error)
    if status in BLOCKED_STATUSES:
        until: str = await _pause(store, now, status)
        log.warning(
            "Losse aankoopnamen: %s weigert (%s), de ronde stopt en het zoeken wacht tot %s",
            store,
            status,
            until,
        )
        return "blocked"
    log.warning(
        "Losse aankoopnamen: zoeken bij %s mislukte, de ronde stopt",
        store,
        exc_info=error,
    )
    return "error"


async def _look_up(
    store: str,
    entry: dict[str, Any],
    fixed: str | None,
    search: Search,
    result: dict[str, Any],
    deadline: float,
) -> str | None:
    """Zoek een naam op en bewaar de uitkomst. Geeft waarom de ronde stopt, of None. Is het uurbudget van AH op, dan wacht het
    `BUDGET_WAIT_SECONDS` en probeert het dezelfde naam opnieuw, tot de tijdslimiet. Een fout laat de stand van de naam staan."""
    while True:
        if _clock() > deadline:
            return "deadline"
        if result["searches"] and store != "ah":
            await _wait(PAUSE_SECONDS)
        now: str = fixed or _now()
        if await paused_until(store, now):
            return "blocked"
        try:
            products: list[Any] = await _search(store, search, entry["name"], now)
        except _Paused:  # er ging geen verzoek uit
            return "blocked"
        except ah_orders.HourlyBudgetSpent:
            await _wait(BUDGET_WAIT_SECONDS)
            continue
        except Exception as error:  # noqa: BLE001 - elke fout van de winkel stopt de ronde, zie `_refused`
            result["searches"] += 1
            return await _refused(store, error, now)
        result["searches"] += 1
        outcome: str | None = await _settle(store, entry, products, now)
        if outcome is not None:
            result[outcome] += 1
        return None


def _counts(stopped: str | None = None) -> dict[str, Any]:
    """De uitkomst van een ronde die nog niets deed: {searches, exact, candidates, missing, stopped}."""
    return {"searches": 0, "exact": 0, "candidates": 0, "missing": 0, "stopped": stopped}


async def _round(
    store: str, fixed: str | None, searches: int, search: Search
) -> dict[str, Any]:
    result: dict[str, Any] = _counts()
    start: str = fixed or _now()
    if await paused_until(store, start):
        return {**result, "stopped": "blocked"}
    deadline: float = _clock() + ROUND_DEADLINE_SECONDS
    for entry in await due(store, start, searches):
        stopped: str | None = await _look_up(
            store, entry, fixed, search, result, deadline
        )
        if stopped:
            return {**result, "stopped": stopped}
    return result


async def run_round(
    store: str,
    now: str | None = None,
    *,
    searches: int = SEARCHES_PER_ROUND,
    search: Search | None = None,
) -> dict[str, Any]:
    """Zoek hooguit `searches` losse namen van `store` op bij de winkel, de vaakst gekochte eerst, en bewaar per naam de uitkomst.

    Geeft {searches, exact, candidates, missing, stopped}. `stopped` is None (alles gedaan), `blocked` (de winkel is gepauzeerd, of
    weigerde met een 403 of 429 en is nu gepauzeerd), `error` (een andere fout van de winkel), `deadline` (de tijdslimiet) of
    `running` (er liep al een ronde voor deze winkel). Met `now` geldt dat tijdstip voor alles, anders de klok bij elke zoekopdracht,
    zodat een pauze na uren nog 12 uur duurt. `search` vervangt `search_products` van de winkel (de tests)."""
    key_store: str = _store(store)
    lock: asyncio.Lock = _lock(key_store)
    if lock.locked():
        return _counts("running")
    async with lock:
        return await _round(
            key_store, now, max(0, int(searches)), search or _default_search(key_store)
        )


# --- de keuze van de gebruiker -------------------------------------------------------------------------------------------------


async def confirm(store: Any, key: Any, product_id: Any) -> dict[str, Any]:
    """Koppel een losse naam met een kaart aan een product, voor elke schrijfwijze, met herkomst `confirmed`. Geeft de koppeling
    (`product_links.link_json`) en haalt de rij weg. ValueError als de naam geen kaart (meer) heeft of de invoer niet klopt."""
    key_store: str
    text: str
    key_store, text = _identity(store, key)
    chosen_id: str = " ".join(str(product_id or "").split())[
        : product_links.MAX_ID_CHARS
    ]
    entry: dict[str, Any] | None = next(
        (e for e in await prices.loose_import_names(key_store) if e["key"] == text),
        None,
    )
    state: aiosqlite.Row | None = (await _states(key_store)).get(text)
    if not chosen_id:
        raise ValueError(tr("Kies een product om te koppelen", "Choose a product to link"))
    if entry is None or state is None or state["outcome"] not in CARD_OUTCOMES:
        raise ValueError(
            tr(
                "Deze naam heeft geen voorstellen meer. Laad de pagina opnieuw.",
                "This name has no suggestions any more. Reload the page.",
            )
        )
    found: list[dict[str, Any]] = _json(state["candidates"], list) or []
    chosen: dict[str, Any] | None = next(
        (c for c in found if isinstance(c, dict) and c.get("productId") == chosen_id),
        None,
    )
    if chosen is None:
        raise ValueError(
            tr(
                "Dit product staat niet meer bij de voorstellen. Laad de pagina opnieuw.",
                "This product is no longer among the suggestions. Reload the page.",
            )
        )
    link: dict[str, Any] = {}
    for spelling in entry["spellings"]:
        link = await product_links.save(key_store, spelling, chosen, origin="confirmed")
    async with _connect() as conn:
        await conn.execute(
            "DELETE FROM loose_names WHERE store = ? AND key = ?", (key_store, text)
        )
        await conn.commit()
    return link


async def _set_outcome(store: Any, key: Any, sql: str) -> bool:
    key_store: str
    text: str
    key_store, text = _identity(store, key)
    async with _connect() as conn:
        cursor: aiosqlite.Cursor = await conn.execute(sql, (key_store, text))
        await conn.commit()
    return cursor.rowcount > 0


async def reject(store: Any, key: Any) -> bool:
    """Wijs een losse naam af (de knop "Geen van deze"): hij komt niet meer op de pagina en de ronde zoekt hem niet meer op. Geeft of
    er iets veranderde."""
    return await _set_outcome(
        store,
        key,
        "UPDATE loose_names SET outcome = 'rejected' WHERE store = ? AND key = ? AND outcome != 'rejected'",
    )


async def restore(store: Any, key: Any) -> bool:
    """Draai een afwijzing terug: met bewaarde kandidaten komt de kaart terug, anders staat de naam bij niet gevonden. Geeft of er
    iets veranderde."""
    return await _set_outcome(
        store,
        key,
        "UPDATE loose_names SET outcome = CASE WHEN candidates NOT IN ('', '[]') THEN 'candidates' ELSE 'missing' END "
        "WHERE store = ? AND key = ? AND outcome = 'rejected'",
    )


# --- de pagina -------------------------------------------------------------------------------------------------------------------


def _card(entry: dict[str, Any], state: aiosqlite.Row) -> dict[str, Any]:
    fields: tuple[str, ...] = (
        "key",
        "name",
        "bought",
        "firstYear",
        "lastYear",
        "paidMin",
        "paidMax",
    )
    return {
        **{field: entry[field] for field in fields},
        "candidates": _json(state["candidates"], list) or [],
        "proposal": _json(state["proposal"], dict),
    }


def _listed(
    loose: list[dict[str, Any]], states: dict[str, aiosqlite.Row], outcome: str
) -> list[dict[str, Any]]:
    return [
        {"key": e["key"], "name": e["name"], "bought": e["bought"]}
        for e in loose
        if e["key"] in states and states[e["key"]]["outcome"] == outcome
    ]


async def page(store: Any, offset: int = 0, limit: int = PAGE_SIZE) -> dict[str, Any]:
    """De pagina "Losse aankopen" van een winkel: de kaarten (de losse namen met kandidaten, de vaakst gekochte eerst, vanaf
    `offset` hooguit `limit`), hoeveel kaarten er zijn (`total`), hoeveel namen de ronde nu zou opzoeken (`toSearch`), de namen
    die de winkel niet vond (`missing`) en die je afwees (`rejected`), de pauze van de winkel en of er een ronde loopt."""
    key_store: str = _store(store)
    start: int = _bounded(offset, 0, 0, 1_000_000)
    size: int = _bounded(limit, PAGE_SIZE, 1, MAX_PAGE_SIZE)
    now: str = _now()
    loose: list[dict[str, Any]] = await prices.loose_import_names(key_store)
    states: dict[str, aiosqlite.Row] = await _states(key_store)
    cards: list[dict[str, Any]] = [
        _card(e, states[e["key"]])
        for e in loose
        if e["key"] in states and states[e["key"]]["outcome"] in CARD_OUTCOMES
    ]
    return {
        "cards": cards[start : start + size],
        "total": len(cards),
        "toSearch": len(_due(loose, states, now)),
        "missing": _listed(loose, states, "missing"),
        "rejected": _listed(loose, states, "rejected"),
        "pausedUntil": await paused_until(key_store, now),
        "running": running(key_store),
    }


# --- de planning: 's nachts en op verzoek ---------------------------------------------------------------------------------------


async def _linked_stores() -> list[str]:
    """De winkels van `product_links.STORES` die gekoppeld zijn, met dezelfde controle als de nachtelijke synchronisatie."""
    return [
        s.name
        for s in purchase_sync._STORES
        if s.name in STORES and await purchase_sync._is_linked(s)
    ]


async def _round_of(store: str, now: str | None) -> dict[str, Any]:
    try:
        return await run_round(store, now)
    except Exception as exc:  # noqa: BLE001 - een winkel die faalt, houdt de volgende niet tegen
        log.warning("Ronde voor losse namen van %s mislukt: %s", store, exc, exc_info=True)
        return {**_counts("error"), "error": str(exc) or type(exc).__name__}


async def _proposals_of(store: str) -> dict[str, Any] | None:
    """De voorstellen van de AI na de ronde, alleen met een AI en de instelling voor 's nachts aan. Een fout komt in de uitkomst."""
    if not (await ai_review.configured() and await ai_review.loose_nightly()):
        return None
    from . import loose_names_ai  # pas hier: loose_names_ai importeert deze module

    try:
        return dict(await loose_names_ai.propose(store))
    except Exception as exc:  # noqa: BLE001 - de AI is een extraatje: de ronde zelf is al gelukt
        log.warning("Voorstellen van de AI voor losse namen van %s mislukt: %s", store, exc)
        return {"error": str(exc) or type(exc).__name__}


async def run_all_stores(
    trigger: str, now: str | None = None, only: str | None = None
) -> dict[str, Any]:
    """Loop de ronde voor elke gekoppelde winkel (of alleen `only`), na elkaar, daarna de voorstellen van de AI als die aan staan.
    De uitkomst, {at, trigger, stores: {winkel: ronde plus eventueel "ai"}}, gaat naar de instelling `loose_names_last_round`."""
    names: list[str] = [_store(only)] if only else await _linked_stores()
    stores: dict[str, Any] = {}
    for name in names:
        stores[name] = await _round_of(name, now)
        proposals: dict[str, Any] | None = await _proposals_of(name)
        if proposals is not None:
            stores[name]["ai"] = proposals
    result: dict[str, Any] = {"at": now or _now(), "trigger": trigger, "stores": stores}
    await auth.set_setting(SETTING_LAST_ROUND, json.dumps(result))
    return result


async def last_round() -> dict[str, Any] | None:
    """De uitkomst van de laatste `run_all_stores`, of None als die er niet is of niet te lezen is."""
    return _json(await auth.get_setting(SETTING_LAST_ROUND, ""), dict)


def start(store: str | None, trigger: str) -> bool:
    """Start de taak `loose-names` (een winkel, of alle gekoppelde winkels bij None) en wacht er niet op. Geeft False als de taak al
    loopt: er is er hooguit een tegelijk. ValueError bij een onbekende winkel."""
    only: str | None = None if store is None else _store(store)
    if running():
        return False
    tasks.spawn(TASK_NAME, run_all_stores(trigger, only=only))
    return True


def busy_reason(store: str) -> str:
    """Waarom `start` voor `store` niets startte, als leesbare melding voor de knop Nu ophalen. De taak `loose-names` loopt dan
    nog: voor deze winkel (zijn slot is bezet), of voor een andere winkel of in de AI-stap na de ronde."""
    if running(store):
        return tr("Er loopt al een ronde voor deze winkel.", "A round for this store is already running.")
    return tr(
        "Er loopt al een ronde voor een andere winkel, of de AI kijkt nog naar de vorige ronde. Probeer het straks opnieuw.",
        "A round for another store is still running, or the AI is still looking at the last round. Try again later.",
    )


async def overview(store: Any, offset: int = 0, limit: int = PAGE_SIZE) -> dict[str, Any]:
    """`page` plus wat de pagina nodig heeft om de knoppen te kiezen: of er een AI is (`ai.configured`) en of die 's nachts voorstelt
    (`ai.nightly`), en de uitkomst van de laatste ronde (`lastRound`)."""
    return {
        **await page(store, offset, limit),
        "ai": {
            "configured": await ai_review.configured(),
            "nightly": await ai_review.loose_nightly(),
        },
        "lastRound": await last_round(),
    }
