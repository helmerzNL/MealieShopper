"""MCP-server (Model Context Protocol) zodat Claude, ChatGPT en Copilot MealieShopper kunnen bevragen.

Streamable HTTP, zonder sessies en zonder streams: elk JSON-RPC-verzoek is een POST dat direct een
JSON-antwoord krijgt. De tools lezen (weekmenu, recepten zoeken, AH-lijstjes en -producten) en
kunnen een recept in Mealie importeren. Er is bewust geen tool die het AH-mandje vult of iets
verwijdert.

Toegang loopt via OAuth (zie oauth.py). De server staat alleen aan als de app zelf inloggen
vraagt, zodat de MCP-ingang nooit open staat op een installatie zonder beveiliging.
"""
from __future__ import annotations

import datetime
import ipaddress
import json
from typing import Any, Callable
from urllib.parse import urlsplit

from . import ah, ah_lists, ah_orders, ah_recipes, auth, mealie, server_settings

LATEST_PROTOCOL = "2025-06-18"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_RESULT_CHARS = 60_000
MAX_LIST_ITEMS = 200


def enabled() -> tuple[bool, str]:
    """Staat de MCP-server aan, en zo niet: waarom niet (een korte code voor de interface)."""
    if not server_settings.mcp_setting()[0]:
        return False, "disabled"
    if not auth.auth_required() or not auth.credentials_exist():
        return False, "no_login"
    return True, ""


class ToolError(Exception):
    """Een fout die de AI mag lezen (komt terug als resultaat met isError, niet als protocolfout)."""


# --- tools ---------------------------------------------------------------------------------------

def _monday(day: datetime.date) -> datetime.date:
    return day - datetime.timedelta(days=day.weekday())


def _parse_date(value: Any, default: datetime.date) -> datetime.date:
    if value in (None, ""):
        return default
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError:
        raise ToolError("Datum moet het formaat JJJJ-MM-DD hebben, bijvoorbeeld 2026-10-19") from None


def _text(value: Any, limit: int = 300) -> str:
    return " ".join(str(value or "").split())[:limit]


def tool_week_menu(args: dict[str, Any]) -> Any:
    start = _parse_date(args.get("start_date"), _monday(datetime.date.today()))
    days = max(1, min(14, int(args.get("days") or 7)))
    end = start + datetime.timedelta(days=days - 1)
    entries = mealie.get_meal_plan_with_recipes(start.isoformat(), end.isoformat())
    meals = []
    for entry in sorted(entries, key=lambda item: (item.get("date") or "", item.get("entryType") or "")):
        recipe = entry.get("recipe") or {}
        detail = entry.get("recipeDetail") or {}
        slug = recipe.get("slug")
        meals.append(
            {
                "date": entry.get("date"),
                "meal": entry.get("entryType"),
                "recipe": recipe.get("name") or entry.get("title") or "",
                "url": mealie.recipe_page_url(slug) if slug else None,
                "ingredients": [
                    _text(item.get("display") or item.get("note"))
                    for item in detail.get("recipeIngredient") or []
                    if item.get("display") or item.get("note")
                ],
            }
        )
    return {"from": start.isoformat(), "to": end.isoformat(), "meals": meals}


def tool_search_recipes(args: dict[str, Any]) -> Any:
    query = _text(args.get("query"), 200)
    if not query:
        raise ToolError("Geef een zoekterm op")
    data = ah.search_recipes(query, max(0, int(args.get("page") or 0)))
    return {
        "total": data["total"],
        "hasNextPage": data["hasNextPage"],
        "recipes": [
            {
                "title": card["title"],
                "url": f"https://www.ah.nl{card['webPath']}",
                "minutes": card.get("totalTime") or None,
                "servings": card.get("servings"),
            }
            for card in data["recipes"]
        ],
    }


def tool_saved_recipes(args: dict[str, Any]) -> Any:
    data = ah_recipes.get()
    words = _text(args.get("query"), 200).casefold().split()
    recipes = [
        recipe for recipe in data["recipes"]
        if all(word in str(recipe.get("title") or "").casefold() for word in words)
    ]
    return {
        "total": len(recipes),
        "syncedAt": data.get("syncedAt"),
        "recipes": [{"title": r["title"], "url": r["url"]} for r in recipes[:MAX_LIST_ITEMS]],
    }


def tool_list_lists(args: dict[str, Any]) -> Any:
    data = ah_lists.merged_lists()
    return {
        "lists": [
            {
                "id": item["id"],
                "name": item["name"],
                "items": item["itemCount"],
                "synced": item["synced"],
                "syncedAt": item["syncedAt"],
            }
            for item in data["lists"]
        ],
        "ahError": data.get("ahError"),
    }


def _list_item(item: dict[str, Any]) -> dict[str, Any]:
    product = item.get("product") or {}
    return {
        "product": product.get("title") or f"Product {item.get('productId')}",
        "brand": product.get("brand"),
        "size": product.get("unitSize"),
        "price": product.get("price") or None,
        "bonus": bool(product.get("isBonus")),
        "quantity": item.get("quantity"),
        "ahQuantity": item.get("defaultQuantity", item.get("quantity")),
        "url": item.get("url") or product.get("url"),
    }


def tool_get_list(args: dict[str, Any]) -> Any:
    list_id = _text(args.get("list_id"), 100)
    if not list_id:
        raise ToolError("Geef het id van een lijstje op (zie list_ah_lists)")
    data = ah_lists.get_snapshot(list_id) or {**ah.get_favorite_list_items(list_id), "source": "ah"}
    items = data.get("items") or []
    return {
        "name": data.get("name"),
        "source": "lokale kopie" if data.get("source") == "local" else "live van AH",
        "syncedAt": data.get("syncedAt"),
        "items": [_list_item(item) for item in items[:MAX_LIST_ITEMS]],
        "total": len(items),
    }


def tool_search_list_items(args: dict[str, Any]) -> Any:
    query = _text(args.get("query"), 200)
    if not query:
        raise ToolError("Geef een zoekterm op")
    names = {item["id"]: item["name"] for item in ah_lists.list_snapshots()}
    found = ah_lists.search_items(query, limit=10)
    return {"matches": [{"list": names.get(list_id, list_id), "listId": list_id, "products": titles} for list_id, titles in found.items()]}


def tool_search_products(args: dict[str, Any]) -> Any:
    query = _text(args.get("query"), 200)
    if not query:
        raise ToolError("Geef een zoekterm op")
    limit = max(1, min(10, int(args.get("limit") or 5)))
    return {
        "products": [
            {
                "title": item["title"],
                "brand": item.get("brand"),
                "size": item.get("unitSize"),
                "price": (item.get("price") or {}).get("now"),
                "bonus": item.get("isBonus"),
                "url": ah.product_web_url(item["webshopId"]),
            }
            for item in ah.search_products(query, limit)
        ]
    }


def _private_host(hostname: str) -> bool:
    """Een adres op je eigen netwerk (Mealie zou het ophalen vanaf jouw server)."""
    host = hostname.strip("[]").rstrip(".").casefold()
    if host == "localhost" or host.endswith((".localhost", ".local", ".lan", ".internal", ".home.arpa")) or "." not in host and ":" not in host:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not address.is_global


def tool_import_recipe(args: dict[str, Any]) -> Any:
    url = _text(args.get("url"), 2000)
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ToolError("Geef een recept-URL op die met http:// of https:// begint")
    # Een assistent kan door een tekst op een webpagina op het verkeerde been worden gezet; adressen op
    # je eigen netwerk laten we daarom niet door Mealie ophalen.
    if _private_host(parts.hostname):
        raise ToolError("Dit adres staat op een eigen netwerk. Importeer alleen recepten van het internet.")
    slug = mealie.import_from_url(url)
    return {"slug": slug, "mealieUrl": mealie.recipe_page_url(slug)}


def _copy_note(synced: str | None) -> str:
    return f"Uit de lokale kopie van {synced[:16].replace('T', ' ')} (UTC)." if synced else "Nog niet gesynchroniseerd: de kopie is leeg. De eigenaar synchroniseert onder AH Items > Bestellingen."


def tool_basket(args: dict[str, Any]) -> Any:
    basket = ah_orders.get_basket()
    return {"note": _copy_note(basket["syncedAt"]) + " Dit is het bestelmandje (de actieve bestelling bij AH), niet de boodschappenlijst.", **basket}


def tool_list_orders(args: dict[str, Any]) -> Any:
    data = ah_orders.list_orders()
    limit = max(1, min(MAX_LIST_ITEMS, int(args.get("limit") or 50)))
    orders = data["orders"]
    return {"note": _copy_note(data["syncedAt"]), "total": len(orders), "orders": orders[:limit]}


def tool_get_order(args: dict[str, Any]) -> Any:
    try:
        order_id = int(args.get("order_id"))
    except (TypeError, ValueError):
        raise ToolError("Geef het nummer van een bestelling op (zie list_ah_orders)") from None
    order = ah_orders.get_order(order_id)
    if not order:
        raise ToolError("Deze bestelling staat niet in de lokale kopie")
    return order


def tool_list_receipts(args: dict[str, Any]) -> Any:
    data = ah_orders.list_receipts()
    limit = max(1, min(MAX_LIST_ITEMS, int(args.get("limit") or 50)))
    return {"note": _copy_note(data["syncedAt"]), "total": len(data["receipts"]), "receipts": data["receipts"][:limit]}


def tool_get_receipt(args: dict[str, Any]) -> Any:
    receipt = ah_orders.get_receipt(_text(args.get("receipt_id"), 100))
    if not receipt:
        raise ToolError("Deze kassabon staat niet in de lokale kopie (zie list_ah_receipts)")
    return receipt


def tool_search_purchases(args: dict[str, Any]) -> Any:
    query = _text(args.get("query"), 200)
    if not query:
        raise ToolError("Geef een zoekterm op")
    return ah_orders.search_purchases(query, limit=max(1, min(MAX_LIST_ITEMS, int(args.get("limit") or 50))))


def _schema(properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties or {}, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}

TOOLS: dict[str, dict[str, Any]] = {
    "get_week_menu": {
        "description": "Het weekmenu uit Mealie: per dag en maaltijd het recept, de link en de ingrediënten. Standaard de huidige week (maandag t/m zondag).",
        "inputSchema": _schema(
            {
                "start_date": {"type": "string", "description": "Eerste dag, JJJJ-MM-DD. Standaard de maandag van deze week."},
                "days": {"type": "integer", "minimum": 1, "maximum": 14, "description": "Aantal dagen, standaard 7."},
            }
        ),
        "annotations": {**READ_ONLY, "title": "Weekmenu"},
        "handler": tool_week_menu,
    },
    "search_allerhande_recipes": {
        "description": "Zoek recepten bij Allerhande (Albert Heijn). Geeft titel, link, bereidingstijd en aantal personen. Importeren in Mealie gaat met import_recipe_to_mealie.",
        "inputSchema": _schema(
            {"query": {"type": "string", "description": "Zoekterm, bijvoorbeeld 'pasta pesto'."}, "page": {"type": "integer", "minimum": 0, "description": "Pagina, beginnend bij 0."}},
            ["query"],
        ),
        "annotations": {**READ_ONLY, "title": "Allerhande-recepten zoeken"},
        "handler": tool_search_recipes,
    },
    "list_saved_ah_recipes": {
        "description": "De recepten die de eigenaar bij Albert Heijn heeft bewaard, uit de lokale kopie. Optioneel gefilterd op woorden in de titel.",
        "inputSchema": _schema({"query": {"type": "string", "description": "Woorden die in de titel moeten staan."}}),
        "annotations": {**READ_ONLY, "title": "Bewaarde AH-recepten"},
        "handler": tool_saved_recipes,
    },
    "list_ah_lists": {
        "description": "De favoriete AH-lijstjes van de eigenaar, met id, naam en aantal producten. Gebruik het id met get_ah_list.",
        "inputSchema": _schema(),
        "annotations": {**READ_ONLY, "title": "AH-lijstjes"},
        "handler": tool_list_lists,
    },
    "get_ah_list": {
        "description": "De producten in een AH-lijstje, met aantal (en het aantal zoals AH het geeft als de eigenaar dat heeft aangepast), prijs en bonus.",
        "inputSchema": _schema({"list_id": {"type": "string", "description": "Het id uit list_ah_lists."}}, ["list_id"]),
        "annotations": {**READ_ONLY, "title": "AH-lijstje bekijken"},
        "handler": tool_get_list,
    },
    "search_ah_list_items": {
        "description": "In welke gesynchroniseerde AH-lijstjes staat een product? Zoekt op woorden in productnaam of merk.",
        "inputSchema": _schema({"query": {"type": "string", "description": "Bijvoorbeeld 'melk'."}}, ["query"]),
        "annotations": {**READ_ONLY, "title": "Product in lijstjes zoeken"},
        "handler": tool_search_list_items,
    },
    "search_ah_products": {
        "description": "Zoek producten in het assortiment van Albert Heijn: naam, merk, inhoud, prijs en bonus.",
        "inputSchema": _schema(
            {"query": {"type": "string", "description": "Productnaam."}, "limit": {"type": "integer", "minimum": 1, "maximum": 10, "description": "Aantal resultaten, standaard 5."}},
            ["query"],
        ),
        "annotations": {**READ_ONLY, "title": "AH-producten zoeken"},
        "handler": tool_search_products,
    },
    "get_ah_basket": {
        "description": "Het bestelmandje: de actieve bestelling bij Albert Heijn (producten, aantallen, totaal, gekozen bezorgmoment). Uit de lokale kopie, met het tijdstip van de laatste synchronisatie. Niet de boodschappenlijst.",
        "inputSchema": _schema(),
        "annotations": {**READ_ONLY, "openWorldHint": False, "title": "Bestelmandje"},
        "handler": tool_basket,
    },
    "list_ah_orders": {
        "description": "AH-bestellingen uit de lokale kopie, open bestellingen eerst en daarna de afgeleverde, met datum, bezorgvenster en totaal. Gebruik het nummer met get_ah_order.",
        "inputSchema": _schema({"limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIST_ITEMS, "description": "Aantal bestellingen, standaard 50."}}),
        "annotations": {**READ_ONLY, "openWorldHint": False, "title": "AH-bestellingen"},
        "handler": tool_list_orders,
    },
    "get_ah_order": {
        "description": "De producten van één AH-bestelling (naam, merk, aantal, prijs, bonus, categorie), uit de lokale kopie.",
        "inputSchema": _schema({"order_id": {"type": "integer", "description": "Het nummer uit list_ah_orders."}}, ["order_id"]),
        "annotations": {**READ_ONLY, "openWorldHint": False, "title": "AH-bestelling bekijken"},
        "handler": tool_get_order,
    },
    "list_ah_receipts": {
        "description": "Kassabonnen van aankopen in de winkel, nieuwste eerst, uit de lokale kopie. Gebruik het id met get_ah_receipt.",
        "inputSchema": _schema({"limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIST_ITEMS, "description": "Aantal bonnen, standaard 50."}}),
        "annotations": {**READ_ONLY, "openWorldHint": False, "title": "Kassabonnen"},
        "handler": tool_list_receipts,
    },
    "get_ah_receipt": {
        "description": "De regels, kortingen en betalingen van één kassabon, uit de lokale kopie.",
        "inputSchema": _schema({"receipt_id": {"type": "string", "description": "Het id uit list_ah_receipts."}}, ["receipt_id"]),
        "annotations": {**READ_ONLY, "openWorldHint": False, "title": "Kassabon bekijken"},
        "handler": tool_get_receipt,
    },
    "search_ah_purchases": {
        "description": "Waar en wanneer kocht de eigenaar een product? Zoekt in alle bewaarde bestellingen en kassabonnen op woorden in de productnaam. Geeft per product de laatste keer, hoe vaak en hoeveel, en de losse aankopen.",
        "inputSchema": _schema(
            {"query": {"type": "string", "description": "Bijvoorbeeld 'koffie'."}, "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIST_ITEMS, "description": "Aantal losse aankopen, standaard 50."}},
            ["query"],
        ),
        "annotations": {**READ_ONLY, "openWorldHint": False, "title": "Aankopen doorzoeken"},
        "handler": tool_search_purchases,
    },
    "import_recipe_to_mealie": {
        "description": "Importeer een recept van een webpagina (ook Allerhande) in Mealie. Maakt een nieuw recept aan; er wordt niets overschreven of verwijderd.",
        "inputSchema": _schema({"url": {"type": "string", "description": "De volledige URL van het recept."}}, ["url"]),
        "annotations": {"title": "Recept importeren in Mealie", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
        "handler": tool_import_recipe,
    },
}


def tool_descriptors() -> list[dict[str, Any]]:
    return [
        {"name": name, "description": tool["description"], "inputSchema": tool["inputSchema"], "annotations": tool["annotations"]}
        for name, tool in TOOLS.items()
    ]


def call_tool(name: str, arguments: Any) -> dict[str, Any]:
    tool = TOOLS.get(name)
    if not tool:
        raise KeyError(name)
    handler: Callable[[dict[str, Any]], Any] = tool["handler"]
    try:
        if not isinstance(arguments, dict):
            raise ToolError("arguments moet een object zijn")
        text = json.dumps(handler(arguments), ensure_ascii=False, indent=1, default=str)
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + "\n… (ingekort)"
        return {"content": [{"type": "text", "text": text}], "isError": False}
    except ToolError as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    except (TypeError, ValueError) as exc:
        return {"content": [{"type": "text", "text": f"Ongeldige invoer: {exc}"}], "isError": True}
    except Exception as exc:  # noqa: BLE001 - Mealie en AH zijn externe diensten; de AI mag de fout lezen
        return {"content": [{"type": "text", "text": _text(exc, 500) or "Er ging iets mis"}], "isError": True}


# --- JSON-RPC ------------------------------------------------------------------------------------

def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle_message(message: Any) -> dict[str, Any] | None:
    """Verwerk één JSON-RPC-bericht. None voor een notificatie (geen antwoord)."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, -32600, "Ongeldig JSON-RPC-verzoek")
    method = message.get("method")
    request_id = message.get("id")
    if not isinstance(method, str):
        return None if "result" in message or "error" in message else _error(request_id, -32600, "method ontbreekt")
    if "id" not in message:
        return None  # notificatie, zoals notifications/initialized
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _error(request_id, -32602, "params moet een object zijn")

    if method == "initialize":
        asked = params.get("protocolVersion")
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": asked if asked in SUPPORTED_PROTOCOLS else LATEST_PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "mealieshopper", "title": "MealieShopper", "version": _version()},
                "instructions": (
                    "MealieShopper koppelt Mealie aan Albert Heijn. Gebruik de tools om het weekmenu, "
                    "recepten, AH-lijstjes, AH-producten, het bestelmandje, bestellingen en kassabonnen te bekijken "
                    "(die laatste drie komen uit een lokale kopie met een tijdstip) en om een recept in Mealie te importeren. "
                    "Het AH-mandje vullen kan niet via deze server."
                ),
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tool_descriptors()}}
    if method == "tools/call":
        try:
            result = call_tool(str(params.get("name") or ""), params.get("arguments") or {})
        except KeyError:
            return _error(request_id, -32602, f"Onbekende tool: {params.get('name')}")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    return _error(request_id, -32601, f"Onbekende methode: {method}")


def _version() -> str:
    from . import about

    return about.build_info()["version"]
