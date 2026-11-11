"""De regels van een zoekresultaat in de ronde van losse aankoopnamen (`loose_names`), zonder database en zonder winkel.

- `candidate`: een resultaat van `search_products` van een winkel in een vorm voor alle winkels.
- `outcome_of`: wat een zoekopdracht oplevert (`exact`, `candidates` of `missing`), met de kandidaten die blijven.
- `kept_proposal`: of een bestaand voorstel van de AI bij de nieuwe kandidaten blijft.
- `status_of`: de HTTP-status van een fout van een winkel, om bij een 403 of 429 te stoppen en te pauzeren.
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import ah, jumbo, name_keys, picnic, product_links

# Een multipack: "6-pack" in de titel of "4 x 1,5 l" in de titel of de inhoud.
MULTIPACK: re.Pattern[str] = re.compile(
    r"\b\d+\s*-\s*pack\b|\b\d+\s*x\s*\d", re.IGNORECASE
)
_STATUS_TEXT: re.Pattern[str] = re.compile(r"^(\d{3}):")


def _text(value: Any, limit: int) -> str:
    return (
        " ".join(str(value).split())[:limit]
        if isinstance(value, (str, int)) and not isinstance(value, bool)
        else ""
    )


def _price(value: Any) -> float | None:
    """De prijs in euro: een getal boven 0, bij AH in `price.now`. AH geeft 0 als hij de prijs niet weet."""
    if isinstance(value, dict):
        value = value.get("now")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return round(float(value), 2)


def _https(value: Any) -> str | None:
    """Alleen een gewoon webadres: de foto komt in een `<img>` op de pagina."""
    text: str = value.strip() if isinstance(value, str) else ""
    usable: bool = (
        text.startswith("https://")
        and len(text) <= product_links.MAX_IMAGE_CHARS
        and not any(ch.isspace() for ch in text)
    )
    return text if usable else None


def _image(store: str, product: dict[str, Any]) -> str | None:
    if store != "ah":
        return _https(product.get("image"))
    images: Any = product.get("images")
    first: Any = images[0] if isinstance(images, list) and images else None
    return _https(first.get("url")) if isinstance(first, dict) else None


def candidate(store: str, product: Any) -> dict[str, Any] | None:
    """Een zoekresultaat van een winkel als {productId, title, brand, unitSize, price, image}, of None zonder nummer of titel.

    AH (`ah._search_product_dict`) geeft `webshopId`, `price.now` en `images`. Jumbo, Picnic en Crisp geven `id`, `price` en
    `image`. Het antwoord van de winkel is een grens van het systeem: alles wordt gecontroleerd en ingekort."""
    if not isinstance(product, dict):
        return None
    product_id: str = _text(
        product.get("webshopId") if store == "ah" else product.get("id"),
        product_links.MAX_ID_CHARS,
    )
    title: str = _text(product.get("title"), product_links.MAX_NAME_CHARS)
    if (
        not product_id or product_id == "None" or not title
    ):  # "None": `ah._search_product_dict` zonder id
        return None
    return {
        "productId": product_id,
        "title": title,
        "brand": _text(product.get("brand"), product_links.MAX_NAME_CHARS) or None,
        "unitSize": _text(product.get("unitSize"), product_links.MAX_SIZE_CHARS)
        or None,
        "price": _price(product.get("price")),
        "image": _image(store, product),
    }


def candidates_of(store: str, products: list[Any], limit: int) -> list[dict[str, Any]]:
    """De bruikbare zoekresultaten (`candidate`), elk product een keer, hooguit `limit`."""
    found: dict[str, dict[str, Any]] = {}
    for product in products:
        made: dict[str, Any] | None = candidate(store, product)
        if made is not None and made["productId"] not in found:
            found[made["productId"]] = made
    return list(found.values())[:limit]


def is_multipack(*texts: Any) -> bool:
    return any(MULTIPACK.search(text) for text in texts if isinstance(text, str))


def _without_multipacks(name: str, found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Noemt de oude naam geen multipack, dan vallen de multipacks af, zolang er daarna nog een kandidaat over is."""
    if is_multipack(name):
        return found
    singles: list[dict[str, Any]] = [
        c for c in found if not is_multipack(c["title"], c["unitSize"])
    ]
    return singles or found


def outcome_of(
    key: str, name: str, found: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]]]:
    """De uitkomst van een zoekopdracht naar de losse naam `name` (sleutel `key`) en de kandidaten die blijven.

    Precies een kandidaat met dezelfde sleutel (`name_keys.key` van de titel) is `exact`. Meer met dezelfde sleutel (verschillende
    verpakkingen) zijn `candidates` met alleen die. Anders zijn alle kandidaten `candidates`, zonder multipacks
    (`_without_multipacks`). Geen kandidaten is `missing`."""
    if not found:
        return "missing", []
    same: list[dict[str, Any]] = [c for c in found if name_keys.key(c["title"]) == key]
    if len(same) == 1:
        return "exact", same
    return "candidates", same or _without_multipacks(name, found)


def kept_proposal(raw: Any, found: list[dict[str, Any]]) -> str | None:
    """Het bewaarde voorstel (JSON) als het voorgestelde product nog bij de kandidaten zit, anders None."""
    try:
        proposal: Any = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        return None
    ids: set[str] = {c["productId"] for c in found}
    return (
        raw
        if isinstance(proposal, dict) and str(proposal.get("productId")) in ids
        else None
    )


def status_of(error: Exception) -> int | None:
    """De HTTP-status van een fout van een winkel, of None.

    AH geeft `ah.AhHttpError.status`. Jumbo geeft `JumboBlocked` met de status in `where` ("403" of "429", of een tekst als er geen
    JSON terugkwam). Picnic geeft `PicnicBlockedError` bij een 403 van zijn poort. Bij elke andere status geven Jumbo, Picnic en
    Crisp een RuntimeError die met "<status>:" begint. Crisp geeft bij 401 en 403 `CrispAuthError` zonder status: dat stopt de
    ronde als gewone fout."""
    if isinstance(error, ah.AhHttpError):
        return error.status
    if isinstance(error, jumbo.JumboBlocked):
        return int(error.where) if error.where.isdigit() else None
    if isinstance(error, picnic.PicnicBlockedError):
        return 403
    match: re.Match[str] | None = (
        _STATUS_TEXT.match(str(error)) if isinstance(error, RuntimeError) else None
    )
    return int(match.group(1)) if match else None
