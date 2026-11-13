"""De gewone prijs per week van de AH-producten die je kocht, van ahprijsindex.nl.

De app kent de gewone prijs van een product alleen van de dagen waarop ze het zelf zag: in een bestelling, op een bon of bij een
zoekopdracht. Van de meeste producten is dat te weinig voor een oordeel bij een aanbieding. ahprijsindex.nl houdt van elk product
van AH per week de gewone prijs bij, sinds juli 2022, en zet die reeks in de productpagina zelf (`/product/<nummer>`, het nummer
van de webwinkel). Een pagina is dus de hele geschiedenis van een product.

De ronde (`run_round`) vraagt die pagina voor elk product dat je minstens een keer kocht, en schrijft de weken in
`price_observations` met de bron `ahprijsindex`, op de maandag van de week. Zo tellen ze mee in de prijsgeschiedenis en in het
oordeel bij een aanbieding, net als een prijs die de app zelf mat. Een prijs die de app zelf mat op dezelfde dag blijft staan.

De site vraagt in haar robots.txt tien seconden tussen twee verzoeken en verbiedt `/api/`. De ronde houdt zich daaraan: ze vraagt
alleen productpagina's, wacht `GAP_SECONDS` tussen twee verzoeken, noemt zichzelf in de User-Agent en stopt bij de eerste
weigering (403, 429 of een fout van de server). Een product komt pas na `REFRESH_DAYS` opnieuw aan de beurt.
`MEALIESHOPPER_PRICE_INDEX=false` zet de ronde uit.

De eerste keer met de hand: `python -m mealieshopper.ah_price_index_run`. Daarna start de dagelijkse run haar (`offers_schedule`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from os import environ
from typing import Any, NamedTuple

import aiosqlite
import httpx

from . import about, auth, http_client, prices, tasks

log: logging.Logger = logging.getLogger(__name__)

STORE: str = "ah"
SOURCE: str = prices.PRICE_INDEX
BASE_URL: str = "https://www.ahprijsindex.nl"
CLIENT: str = "ahprijsindex"
TASK_NAME: str = "ah-price-index"
SETTING_LAST_ROUND: str = "AH_PRICE_INDEX_LAST_ROUND"

GAP_SECONDS: float = 10.0  # Crawl-delay in de robots.txt van de site
REFRESH_DAYS: int = (
    14  # een week is zeven dagen en het oordeel wil een prijs van de laatste dertig
)
MISSING_DAYS: int = 30  # een product dat de site niet kent
ERROR_DAYS: int = 1  # een pagina die niet te lezen was
ROUND_LIMIT: int = 500  # hooguit zoveel verzoeken per ronde, bijna anderhalf uur
PAGE_ERRORS_IN_A_ROW: int = (
    3  # zoveel onleesbare pagina's achter elkaar: de site is veranderd, de ronde stopt
)

SCHEMA: str = """
-- De stand per product van de ronde langs ahprijsindex.nl: wanneer het is gevraagd en wat eruit kwam (`ok`, `missing` als de
-- site het product niet kent, `error` als de pagina niet te lezen was).
CREATE TABLE IF NOT EXISTS ah_price_index (
  product_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  checked_at TEXT NOT NULL,
  weeks INTEGER NOT NULL DEFAULT 0
);
"""

_WAIT_DAYS: dict[str, int] = {"ok": REFRESH_DAYS, "missing": MISSING_DAYS}
_DATA: re.Pattern[str] = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)
# Een prijs van de index vervangt alleen een eerdere prijs van de index, en alleen als hij anders is. Wat de app zelf mat, blijft.
_SAVE_SQL: str = """
INSERT INTO price_observations (store, product_id, day, source, title, brand, unit_size, image, url, price, seen_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
ON CONFLICT (store, product_id, day) DO UPDATE SET
  title = excluded.title, brand = excluded.brand, unit_size = excluded.unit_size, image = excluded.image,
  price = excluded.price, seen_at = excluded.seen_at
WHERE price_observations.source = excluded.source AND price_observations.price != excluded.price
"""
_MARK_SQL: str = """
INSERT INTO ah_price_index (product_id, status, checked_at, weeks) VALUES (?, ?, ?, ?)
ON CONFLICT (product_id) DO UPDATE SET status = excluded.status, checked_at = excluded.checked_at, weeks = excluded.weeks
"""


class PageError(Exception):
    """De pagina heeft niet de vorm die we kennen."""


class Refused(Exception):
    """De site gaf geen pagina en geen 404: de ronde stopt."""


class Series(NamedTuple):
    product_id: str
    title: str
    brand: str | None
    unit_size: str | None
    image: str | None
    weeks: tuple[
        tuple[str, float], ...
    ]  # (maandag van de week, gewone prijs), de oudste eerst


def enabled() -> bool:
    return environ.get("MEALIESHOPPER_PRICE_INDEX", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def user_agent() -> str:
    """Wie er vraagt en waar de beheerder van de site meer vindt."""
    return f"MealieShopper/{about.version()} (+{about.REPO_URL})"


def page_url(product_id: str) -> str:
    return f"{BASE_URL}/product/{product_id}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    """Een verbinding met het schema van de prijzen en van de stand. Sluit zonder commit."""
    async with prices._connect() as conn:
        await auth.ensure_schema(conn, "ah_price_index", SCHEMA)
        yield conn


# --- de pagina lezen ----------------------------------------------------------------------------------------------------


def _text(value: Any) -> str | None:
    text: str = (
        str(value).strip()
        if isinstance(value, (str, int)) and not isinstance(value, bool)
        else ""
    )
    return text or None


def _whole(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _price(value: Any) -> float | None:
    try:
        price: float = float(value)
    except (TypeError, ValueError):
        return None
    return round(price, 2) if math.isfinite(price) and price > 0 else None


def _week(row: Any) -> tuple[str, float] | None:
    """Een week van de pagina als (maandag, prijs). None voor een week die de site zelf schatte (`is_interpolated`), een week
    zonder bruikbare prijs en een week die niet bestaat."""
    if not isinstance(row, dict) or row.get("is_interpolated"):
        return None
    year, week, price = (
        _whole(row.get("isoyear")),
        _whole(row.get("isoweek")),
        _price(row.get("price")),
    )
    if year is None or week is None or price is None:
        return None
    try:
        return date.fromisocalendar(year, week, 1).isoformat(), price
    except ValueError:
        return None


def _page_props(html: str) -> dict[str, Any]:
    match: re.Match[str] | None = _DATA.search(html)
    if match is None:
        raise PageError("geen gegevens in de pagina")
    try:
        data: Any = json.loads(match.group(1))
    except ValueError as exc:
        raise PageError("de gegevens in de pagina zijn geen JSON") from exc
    props: Any = data.get("props") if isinstance(data, dict) else None
    page_props: Any = props.get("pageProps") if isinstance(props, dict) else None
    if not isinstance(page_props, dict):
        raise PageError("geen pageProps in de pagina")
    return page_props


def parse(html: str) -> Series:
    """De reeks van een productpagina. PageError als de pagina een andere vorm heeft dan we kennen.

    De inhoud (`unit_size`) staat er alleen bij als het product nooit van inhoud veranderde (`sizeHistory` leeg): anders hoort de
    prijs van een oudere week bij een andere inhoud dan de pagina nu noemt."""
    props: dict[str, Any] = _page_props(html)
    info: Any = props.get("productInfo")
    rows: Any = props.get("prices")
    if not isinstance(info, dict) or not isinstance(rows, list):
        raise PageError("geen product of geen prijzen in de pagina")
    product_id, title = _text(info.get("product_id")), _text(info.get("product_title"))
    if product_id is None or title is None:
        raise PageError("een product zonder nummer of naam")
    weeks: dict[str, float] = dict(
        found for found in map(_week, rows) if found is not None
    )
    same_size: bool = not props.get("sizeHistory")
    return Series(
        product_id,
        title,
        _text(info.get("product_brand")),
        _text(info.get("product_size")) if same_size else None,
        _text(info.get("product_imgurl")),
        tuple(sorted(weeks.items())),
    )


async def fetch(product_id: str) -> Series | None:
    """De reeks van een product, of None als de site het niet kent (404). Refused bij elk ander antwoord dan een pagina,
    PageError bij een pagina die niet te lezen is of van een ander product is, httpx.HTTPError als de site niet bereikbaar is."""
    client: httpx.AsyncClient = http_client.client(
        CLIENT, headers={"User-Agent": user_agent()}
    )
    response: httpx.Response = await client.get(page_url(product_id))
    if response.status_code in (404, 410):
        return None
    if response.status_code != 200:
        raise Refused(f"HTTP {response.status_code}")
    series: Series = parse(response.text)
    if series.product_id != product_id:
        raise PageError(f"de pagina van {product_id} gaat over {series.product_id}")
    return series


# --- bewaren en de stand ------------------------------------------------------------------------------------------------


async def _save(conn: aiosqlite.Connection, series: Series, stamp: str) -> int:
    """Schrijft de weken tot en met vandaag en geeft hun aantal. Een week die nog moet beginnen heeft nog geen prijs."""
    today: str = stamp[:10]
    rows: list[tuple[Any, ...]] = [
        (
            STORE,
            series.product_id,
            day,
            SOURCE,
            series.title,
            series.brand,
            series.unit_size,
            series.image,
            price,
            stamp,
        )
        for day, price in series.weeks
        if day <= today
    ]
    await conn.executemany(_SAVE_SQL, rows)
    return len(rows)


async def _mark(
    product_id: str, status: str, stamp: str, series: Series | None = None
) -> int:
    """Bewaart de uitkomst van een product, met zijn weken als die er zijn. Geeft het aantal weken."""
    async with _connect() as conn:
        weeks: int = await _save(conn, series, stamp) if series is not None else 0
        await conn.execute(_MARK_SQL, (product_id, status, stamp, weeks))
        await conn.commit()
    return weeks


def _is_due(entry: tuple[str, str] | None, today: str) -> bool:
    """Of een product aan de beurt is: nooit gevraagd, of lang genoeg geleden voor zijn laatste uitkomst (`_WAIT_DAYS`)."""
    if entry is None:
        return True
    status, checked = entry
    wait: int = _WAIT_DAYS.get(status, ERROR_DAYS)
    return checked <= (date.fromisoformat(today) - timedelta(days=wait)).isoformat()


async def _due(today: str) -> list[str]:
    """De gekochte producten die aan de beurt zijn: eerst wat nog nooit is gevraagd, dan het langst geleden. Bij gelijke stand
    gaat een product uit de aanbiedingen van nu voor: daar staat het oordeel nu op het scherm."""
    bought: set[str] = {
        product_id
        for product_id in await prices.bought_ids(STORE, today)
        if product_id.isdigit()
    }
    if not bought:
        return []
    async with _connect() as conn:
        state: dict[str, tuple[str, str]] = {
            row["product_id"]: (row["status"], str(row["checked_at"])[:10])
            for row in await conn.execute_fetchall(
                "SELECT product_id, status, checked_at FROM ah_price_index"
            )
        }
        on_offer: set[str] = {
            str(row["product_id"])
            for row in await conn.execute_fetchall(
                "SELECT DISTINCT product_id FROM offers WHERE store = ?", (STORE,)
            )
        }
    waiting: list[str] = [
        product_id for product_id in bought if _is_due(state.get(product_id), today)
    ]
    return sorted(
        waiting,
        key=lambda product_id: (
            state.get(product_id, ("", ""))[1],
            product_id not in on_offer,
            int(product_id),
        ),
    )


# --- de ronde -----------------------------------------------------------------------------------------------------------


async def _one(product_id: str, stamp: str, result: dict[str, Any]) -> bool:
    """Vraagt een product en telt de uitkomst bij `result`. Geeft False als de pagina niet te lezen was. Een weigering of een
    netwerkfout gaat door naar de ronde: het product blijft dan zonder stand en komt de volgende keer terug."""
    try:
        series: Series | None = await fetch(product_id)
    except PageError as exc:
        log.warning(
            "ahprijsindex: de pagina van product %s is niet te lezen: %s",
            product_id,
            exc,
        )
        await _mark(product_id, "error", stamp)
        result["checked"] += 1
        result["failed"] += 1
        return False
    result["prices"] += await _mark(
        product_id, "ok" if series is not None else "missing", stamp, series
    )
    result["checked"] += 1
    result["missing"] += series is None
    return True


async def _walk(waiting: list[str], stamp: str, result: dict[str, Any]) -> str | None:
    """Loopt de producten af, met `GAP_SECONDS` tussen twee verzoeken. Geeft waarom de ronde stopte, of None als ze klaar is."""
    unreadable: int = 0
    for number, product_id in enumerate(waiting):
        if number:
            await _sleep(GAP_SECONDS)
        try:
            unreadable = 0 if await _one(product_id, stamp, result) else unreadable + 1
        except Refused as exc:
            return str(exc)
        except httpx.HTTPError as exc:
            return type(exc).__name__
        if unreadable >= PAGE_ERRORS_IN_A_ROW:
            return "page"
    return None


async def run_round(
    limit: int = ROUND_LIMIT, now: str | None = None
) -> dict[str, Any] | None:
    """Een ronde langs ahprijsindex.nl voor de gekochte producten die aan de beurt zijn, hooguit `limit` verzoeken.

    Geeft `{checked, prices, missing, failed, stopped, left}`: hoeveel producten een antwoord kregen, hoeveel weekprijzen dat gaf,
    hoeveel producten de site niet kent, hoeveel pagina's niet te lezen waren, waarom de ronde eerder stopte (`HTTP 429`, de naam
    van een netwerkfout, `page` bij drie onleesbare pagina's achter elkaar, anders None) en hoeveel producten er nog wachten.
    None als de ronde uit staat of niets te doen had."""
    if not enabled():
        return None
    stamp: str = now or _now()
    waiting: list[str] = await _due(stamp[:10])
    if not waiting:
        return None
    result: dict[str, Any] = {
        "checked": 0,
        "prices": 0,
        "missing": 0,
        "failed": 0,
        "stopped": None,
        "left": 0,
    }
    result["stopped"] = await _walk(waiting[: max(limit, 0)], stamp, result)
    result["left"] = len(waiting) - result["checked"]
    if result["stopped"] is not None:
        log.warning(
            "ahprijsindex: de ronde stopte (%s) na %d producten, %d wachten nog",
            result["stopped"],
            result["checked"],
            result["left"],
        )
    else:
        log.info(
            "ahprijsindex: %d producten, %d weekprijzen, %d wachten nog",
            result["checked"],
            result["prices"],
            result["left"],
        )
    await auth.set_setting(SETTING_LAST_ROUND, json.dumps({"at": stamp, **result}))
    return result


async def last_round() -> dict[str, Any] | None:
    """De uitkomst van de laatste ronde die iets te doen had, met `at`, of None."""
    try:
        found: Any = json.loads(
            await auth.get_setting(SETTING_LAST_ROUND, "") or "null"
        )
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


def running() -> bool:
    task: asyncio.Task[Any] | None = tasks.running().get(TASK_NAME)
    return task is not None and not task.done()


def start() -> bool:
    """Start de ronde in een eigen task en wacht er niet op. False als ze uit staat of al loopt."""
    if not enabled() or running():
        return False
    tasks.spawn(TASK_NAME, run_round())
    return True
