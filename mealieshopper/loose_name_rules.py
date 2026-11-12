"""De regels van een zoekresultaat in de ronde van losse aankoopnamen (`loose_names`), zonder database en zonder winkel.

- `candidate`: een resultaat van `search_products` van een winkel in een vorm voor alle winkels.
- `outcome_of`: wat een zoekopdracht oplevert (`exact`, `candidates` of `missing`), met de kandidaten die blijven. `exact` is
  alleen letterlijk hetzelfde product (`_literal`): dezelfde sleutel, dezelfde getallen en dezelfde soort verpakking.
- `literal_for_all`: of een resultaat letterlijk elke schrijfwijze van een losse naam is, voor een koppeling zonder klik.
- `kept_proposal`: of een bestaand voorstel van de AI bij de nieuwe kandidaten blijft.
- `status_of`: de HTTP-status van een fout van een winkel, om bij een 403 of 429 te stoppen en te pauzeren.
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import ah, crisp, jumbo, name_keys, picnic, product_links

# Een multipack: "6-pack" in de titel of "4 x 1,5 l" in de titel of de inhoud.
MULTIPACK: re.Pattern[str] = re.compile(
    r"\b\d+\s*-\s*pack\b|\b\d+\s*x\s*\d", re.IGNORECASE
)
_STATUS_TEXT: re.Pattern[str] = re.compile(r"^(\d{3}):")
_DIGITS: re.Pattern[str] = re.compile(r"\d+")


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


def _digits(text: str) -> list[str]:
    """De getallen van een gevouwen naam op volgorde: "Coca-Cola Zero sugar 4 x 1,5 L" geeft ["4", "1", "5"]."""
    return _DIGITS.findall(name_keys.fold(text))


def _literal(name: str, found: dict[str, Any]) -> bool:
    """Of een kandidaat met dezelfde sleutel letterlijk de losse naam `name` is. De sleutel laat getallen weg ("Melk 1 L" en
    "Melk 2 L" hebben dezelfde), dus ook de getallen van de naam en de titel moeten gelijk zijn, en de naam en de kandidaat (titel
    en inhoud) zijn allebei een multipack of allebei niet: bij AH staat "6 x 0,33 l" vaak alleen in de inhoud."""
    return _digits(name) == _digits(found["title"]) and is_multipack(name) == is_multipack(
        found["title"], found["unitSize"]
    )


def literal_for_all(spellings: list[str], found: dict[str, Any]) -> bool:
    """Of de kandidaat `found` letterlijk elke schrijfwijze van een losse naam is (`_literal`). De schrijfwijzen horen bij een naam
    door hun sleutel, en die laat getallen weg: "Melk 1 L" en "Melk 1,5 L" zijn een losse naam, maar een koppeling geldt voor
    elke schrijfwijze, dus alleen als het resultaat bij allemaal letterlijk klopt."""
    return all(_literal(spelling, found) for spelling in spellings)


def outcome_of(
    key: str, name: str, found: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]]]:
    """De uitkomst van een zoekopdracht naar de losse naam `name` (sleutel `key`) en de kandidaten die blijven.

    Precies een kandidaat met dezelfde sleutel (`name_keys.key` van de titel) die letterlijk de naam is (`_literal`) is `exact`.
    Een kandidaat met dezelfde sleutel die dat niet is, of meer met dezelfde sleutel (verschillende verpakkingen), zijn
    `candidates` met alleen die. Anders zijn alle kandidaten `candidates`, zonder multipacks (`_without_multipacks`). Geen
    kandidaten is `missing`."""
    if not found:
        return "missing", []
    same: list[dict[str, Any]] = [c for c in found if name_keys.key(c["title"]) == key]
    if len(same) == 1 and _literal(name, same[0]):
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
    Crisp een RuntimeError die met "<status>:" begint. Crisp geeft bij 401 en 403 `CrispAuthError` zonder status: dat telt als
    403, dus ook Crisp krijgt de pauze."""
    if isinstance(error, ah.AhHttpError):
        return error.status
    if isinstance(error, jumbo.JumboBlocked):
        return int(error.where) if error.where.isdigit() else None
    if isinstance(error, (picnic.PicnicBlockedError, crisp.CrispAuthError)):
        return 403  # Crisp geeft bij 401 en 403 dezelfde fout: liever 12 uur te lang wachten dan aandringen
    match: re.Match[str] | None = (
        _STATUS_TEXT.match(str(error)) if isinstance(error, RuntimeError) else None
    )
    return int(match.group(1)) if match else None
