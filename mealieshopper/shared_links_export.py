"""De export van je eigen koppelingen als bestand voor een pull request (`shared_links`, alleen voor de beheerder).

De app praat nooit met GitHub. Hij maakt het bestand van de draaiende versie, samengevoegd met de koppelingen die je zelf maakte of
bevestigde (`origin` `manual` of `confirmed`, nooit `exact`) voor namen uit een ingelezen bestelling, en de gebruiker stelt de wijziging
zelf voor. Per koppeling gaan alleen `name`, `productId`, `title` en `unitSize` mee.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite

from . import name_keys, price_import, product_links, shared_links
from .i18n import tr

GITHUB_FILE: str = "https://github.com/helmerzNL/MealieShopper/blob/beta/mealieshopper/shared_link_data/{store}.json"
EXPORTED_ORIGINS: tuple[str, ...] = ("manual", "confirmed")
MAX_KEYS: int = 5000


def store_of(value: Any) -> str:
    """De winkel uit een verzoek. ValueError met een leesbare melding bij een onbekende winkel."""
    store: str = str(value or "").strip().lower()
    if store not in product_links.STORES:
        raise ValueError(tr("Onbekende winkel", "Unknown store"))
    return store


def keys_of(value: Any) -> list[str]:
    """De sleutels uit een verzoek. ValueError bij iets anders dan een lijst tekst van hoogstens `MAX_KEYS` stuks."""
    if not isinstance(value, list) or not all(isinstance(key, str) for key in value):
        raise ValueError(tr("keys moet een lijst met tekst zijn", "keys must be a list of strings"))
    if len(value) > MAX_KEYS:
        raise ValueError(
            tr(
                f"Hoogstens {MAX_KEYS} sleutels tegelijk",
                f"At most {MAX_KEYS} keys at a time",
            )
        )
    return value


def _file_links(store: str) -> list[shared_links.SharedLink]:
    """De koppelingen in het bestand van de draaiende versie. Een ontbrekend bestand is leeg, een kapot bestand een ValueError:
    anders zou de export het bestand zonder de rest uitgeven."""
    path: Path = shared_links.DATA_DIR / f"{store}.json"
    try:
        text: str = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise ValueError(
            tr(
                f"Het bestand van {store} is niet te lezen: {exc}",
                f"The file of {store} cannot be read: {exc}",
            )
        ) from exc
    return shared_links.parse(text)


async def _own(store: str) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """(je eigen koppelingen op naam, de sleutels van de namen uit ingelezen bestellingen) van een winkel."""
    async with price_import._connect() as conn:
        await product_links.ensure(conn)
        links: product_links.Links = await product_links.load(conn, store)
        rows: list[aiosqlite.Row] = list(
            await conn.execute_fetchall(
                "SELECT DISTINCT title FROM imported_purchases WHERE store = ?",
                (store,),
            )
        )
    return links.names, {name_keys.key(row["title"]) for row in rows}


def _candidates(names: dict[str, dict[str, Any]], imported: set[str]) -> dict[str, list[dict[str, Any]]]:
    """Per sleutel van de naam je eigen koppelingen op naam met een herkomst van een mens, voor namen uit een bestelling."""
    found: dict[str, list[dict[str, Any]]] = {}
    for link in names.values():
        key: str = name_keys.key(link["name"])
        if link["origin"] in EXPORTED_ORIGINS and key in imported:
            found.setdefault(key, []).append(link)
    return found


def _item(key: str, link: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": key,
        "name": name_keys.fold(link["name"]),
        "productId": link["productId"],
        "title": link["title"],
        "unitSize": link["unitSize"] or "",
    }


def _first(links: list[dict[str, Any]]) -> dict[str, Any]:
    return min(links, key=lambda link: (name_keys.fold(link["name"]), link["productId"]))


async def pending(store: Any) -> dict[str, Any]:
    """Wat er te exporteren valt: `new` (nog niet in het bestand), `conflicts` (het bestand heeft een ander product voor de naam, met
    `sharedProductId` en `sharedTitle`), `ambiguous` (twee eigen namen met dezelfde sleutel en een ander product: allebei weggelaten)
    en `fileUrl`. Een koppeling die al zo in het bestand staat, telt niet mee."""
    key_store: str = store_of(store)
    in_file: dict[str, shared_links.SharedLink] = {}
    for link in _file_links(key_store):
        in_file.setdefault(name_keys.key(link.name), link)
    names, imported = await _own(key_store)
    new: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    for key, links in sorted(_candidates(names, imported).items()):
        products: list[str] = sorted({link["productId"] for link in links})
        if len(products) > 1:
            ambiguous.append(
                {
                    "key": key,
                    "names": sorted({link["name"] for link in links}),
                    "productIds": products,
                }
            )
            continue
        item: dict[str, Any] = _item(key, _first(links))
        shared: shared_links.SharedLink | None = in_file.get(key)
        if shared is None:
            new.append(item)
        elif shared.product_id != item["productId"]:
            conflicts.append(
                {
                    **item,
                    "sharedProductId": shared.product_id,
                    "sharedTitle": shared.title,
                }
            )
    return {
        "new": new,
        "conflicts": conflicts,
        "ambiguous": ambiguous,
        "fileUrl": GITHUB_FILE.format(store=key_store),
    }


async def merged(store: Any, keys: list[str]) -> str:
    """De tekst van het bestand van de draaiende versie plus de koppelingen met deze sleutels uit `pending` (`new` en `conflicts`),
    een conflict vervangt de bestaande regel. Een sleutel die niet klaarstaat telt niet mee. ValueError als het bestand dan niet
    door `shared_links.problems` komt: nooit een bestand uitgeven dat de test van het project weigert."""
    key_store: str = store_of(store)
    wanted: set[str] = set(keys_of(keys))
    page: dict[str, Any] = await pending(key_store)
    chosen: list[dict[str, Any]] = [item for item in page["new"] + page["conflicts"] if item["key"] in wanted]
    replaced: set[str] = {item["key"] for item in chosen}
    links: list[shared_links.SharedLink] = [link for link in _file_links(key_store) if name_keys.key(link.name) not in replaced]
    links += [shared_links.SharedLink(item["name"], item["productId"], item["title"], item["unitSize"]) for item in chosen]
    text: str = shared_links.dump(links)
    found: list[str] = shared_links.problems(shared_links.parse(text))
    if found:
        raise ValueError(
            tr(
                "Het bestand zou niet kloppen: ",
                "The file would not pass its own check: ",
            )
            + ", ".join(found)
        )
    return text
