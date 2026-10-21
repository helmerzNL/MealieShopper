import mimetypes
from concurrent.futures import ThreadPoolExecutor
from os import environ
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
import sqlite3

import requests
from flask import Flask, Response, jsonify, redirect, render_template, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

from . import about, ah, ah_explore, audit, ah_lists, ah_orders, ah_recipes, auth_password, jumbo, jumbo_explore, jumbo_lists, jumbo_orders, jumbo_recipes, mcp_http, mealie, mealie_images, onboarding, picnic, picnic_explore, picnic_orders, picnic_recipes, server_settings, updates, users
from . import auth
from .i18n import tr


# Python kent .webmanifest niet overal; browsers verwachten application/manifest+json.
mimetypes.add_type("application/manifest+json", ".webmanifest")


def create_app() -> Flask:
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.config["ASSET_VERSION"] = environ.get("MEALIESHOPPER_ASSET_VERSION", "20261023-17")
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
    audit.install()  # legt het verkeer met AH, Picnic en Jumbo vast (zie audit.py)
    audit.register_routes(app)
    auth.register_routes(app)
    auth_password.register_routes(app)
    onboarding.register_routes(app)

    try:
        removed = auth.purge_obsolete_secrets()
        if removed:
            app.logger.info("Opgeslagen gegevens van verwijderde koppelingen gewist (%d)", removed)
    except (sqlite3.Error, OSError):
        # Een onbereikbare data-map meldt zich bij het eerste gebruik; opstarten blijft mogelijk.
        app.logger.warning("Kon oude koppelingsgegevens niet opruimen", exc_info=True)

    # Fouten van externe diensten (AH, Mealie) geven we terug als 424 in
    # plaats van 502: reverse proxies (Cloudflare, SWAG, NPM) vervangen een 502
    # door hun eigen HTML-pagina, waardoor de echte foutmelding verdwijnt.

    public_base_url = server_settings.public_base_url  # in de app in te stellen, zie server_settings.py

    def ah_callback_url() -> str:
        return f"{public_base_url()}/api/ah/auth/callback"

    def ah_proxy_base_url() -> str:
        return f"{public_base_url()}/api/ah/auth/proxy"

    server_settings.register_routes(app)
    users.register_routes(app, public_base_url)
    mcp_http.register_routes(app, public_base_url)

    @app.errorhandler(sqlite3.Error)
    @app.errorhandler(OSError)
    def database_error(exc):
        app.logger.exception("Storage error")
        message = (
            "Auth database is niet bereikbaar. Controleer MEALIESHOPPER_DATA_DIR "
            "en de /data volume mount/rechten."
        )
        if request.path.startswith("/api/"):
            return jsonify({"error": message, "detail": str(exc)}), 503
        return message, 503

    @app.before_request
    def require_passkey_auth():
        blocked = auth.require_auth_for_request()
        if blocked:
            payload, status = blocked
            return jsonify(payload), status

    @app.get("/")
    def index():
        return render_template("index.html", public_base_url=public_base_url())

    @app.get("/favicon.ico")
    def favicon():
        # Browsers vragen /favicon.ico op zonder <link>; dit blijft zonder inloggen bereikbaar.
        return send_from_directory(
            Path(app.static_folder) / "brand", "favicon.ico", mimetype="image/x-icon"
        )

    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True})

    @app.get("/api/about")
    def about_info():
        return jsonify(about.about())

    # Alleen de beheerder: /api/update staat in auth.ADMIN_PREFIXES.
    @app.get("/api/update")
    def update_status():
        return jsonify(updates.status())

    @app.post("/api/update/check")
    def update_check():
        return jsonify(updates.status(force=True))

    @app.get("/api/mealplan")
    def mealplan():
        start = request.args.get("start")
        end = request.args.get("end")
        if not start or not end:
            return jsonify({"error": "start en end parameters zijn verplicht"}), 400

        try:
            return jsonify(mealie.get_meal_plan_with_recipes(start, end))
        except Exception as exc:
            app.logger.exception("Mealplan error")
            return jsonify({"error": str(exc) or "Fout bij ophalen weekmenu"}), 424

    @app.get("/api/ah/search")
    def ah_search():
        query = (request.args.get("q") or "").strip()
        page = int(request.args.get("page") or 0)
        if not query:
            return jsonify({"error": "Zoekterm ontbreekt"}), 400

        try:
            return jsonify(ah.search_recipes(query, page))
        except Exception as exc:
            app.logger.exception("AH search error")
            return jsonify({"error": str(exc) or "Zoeken mislukt"}), 424

    @app.get("/api/ah/recipes/saved")
    def ah_saved_recipes():
        # Uit de lokale kopie; ?refresh=1 haalt ze opnieuw bij AH op, ?cached=1 vraagt nooit aan AH.
        def flag(name: str) -> bool:
            return (request.args.get(name) or "").strip().lower() in {"1", "true", "yes"}

        try:
            return jsonify(ah_recipes.get(force=flag("refresh"), fetch_if_missing=not flag("cached")))
        except Exception as exc:
            app.logger.exception("AH saved recipes error")
            return jsonify({"error": str(exc) or "Bewaarde recepten ophalen mislukt"}), 424

    @app.get("/api/mealie/recipes/<recipe_id>/image")
    def mealie_recipe_image(recipe_id: str):
        if not mealie_images.valid_recipe_id(recipe_id):
            return jsonify({"error": "Ongeldig recept"}), 400
        version = (request.args.get("v") or "")[:64]
        try:
            content_type, data = mealie_images.get_image(
                recipe_id, request.args.get("size") or mealie_images.DEFAULT_SIZE, version
            )
        except mealie_images.ImageNotFound:
            return jsonify({"error": "Dit recept heeft geen afbeelding"}), 404
        except Exception as exc:
            app.logger.exception("Mealie image error")
            return jsonify({"error": str(exc) or "Afbeelding ophalen mislukt"}), 424
        response = Response(data, mimetype=content_type)
        # Met ?v= (de afbeeldingsversie van Mealie) verandert het adres als het plaatje verandert.
        response.headers["Cache-Control"] = (
            "private, max-age=31536000, immutable" if version else "private, max-age=3600"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/api/ah/auth/status")
    def ah_auth_status():
        try:
            verify = (request.args.get("verify") or "").strip().lower() in {"1", "true", "yes"}
            return jsonify(ah.auth_status(verify=verify))
        except Exception as exc:
            app.logger.exception("AH auth status error")
            return jsonify({"connected": False, "error": str(exc)}), 424

    @app.get("/api/ah/favorite-lists")
    def ah_favorite_lists():
        try:
            return jsonify({"lists": ah.get_favorite_lists()})
        except Exception as exc:
            app.logger.exception("AH favorite lists error")
            return jsonify({"error": str(exc) or "AH lijstjes ophalen mislukt"}), 424

    @app.get("/api/ah/favorite-lists/<list_id>/items")
    def ah_favorite_list_items(list_id: str):
        try:
            return jsonify(ah.get_favorite_list_items(list_id))
        except Exception as exc:
            app.logger.exception("AH favorite list items error")
            return jsonify({"error": str(exc) or "AH lijstinhoud ophalen mislukt"}), 424

    @app.get("/api/ah/lists")
    def ah_lists_overview():
        # Altijd 200: lokale kopieen blijven zichtbaar ook als AH niet bereikbaar is.
        return jsonify(ah_lists.merged_lists())

    @app.get("/api/ah/lists/search")
    def ah_lists_search():
        # Alleen de lokale kopieen: snel genoeg om bij elke toetsaanslag te vragen.
        query = (request.args.get("q") or "").strip()
        return jsonify({"query": query, "items": ah_lists.search_items(query)})

    @app.get("/api/ah/lists/<list_id>")
    def ah_list_detail(list_id: str):
        snapshot = ah_lists.get_snapshot(list_id)
        if snapshot:
            return jsonify(snapshot)
        try:
            return jsonify({**ah.get_favorite_list_items(list_id), "source": "ah"})
        except Exception as exc:
            app.logger.exception("AH list detail error")
            return jsonify({"error": str(exc) or "AH lijstinhoud ophalen mislukt"}), 424

    @app.post("/api/ah/lists/<list_id>/sync")
    def ah_list_sync(list_id: str):
        try:
            return jsonify(ah_lists.sync_list(list_id))
        except Exception as exc:
            app.logger.exception("AH list sync error")
            return jsonify({"error": str(exc) or "Synchroniseren mislukt"}), 424

    @app.patch("/api/ah/lists/<list_id>/items/<int:position>")
    def ah_list_item_quantity(list_id: str, position: int):
        body = request.get_json(silent=True) or {}
        try:
            snapshot = ah_lists.set_quantity(list_id, position, body.get("quantity"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if not snapshot:
            return jsonify({"error": tr("Regel niet gevonden. Synchroniseer het lijstje eerst.", "Line not found. Sync the list first.")}), 404
        return jsonify(snapshot)

    @app.post("/api/ah/lists/<list_id>/reset")
    def ah_list_reset(list_id: str):
        snapshot = ah_lists.reset_quantities(list_id)
        if not snapshot:
            return jsonify({"error": tr("Dit lijstje is niet gesynchroniseerd.", "This list is not synced.")}), 404
        return jsonify(snapshot)

    @app.post("/api/ah/lists/<list_id>/cart")
    def ah_list_to_cart(list_id: str):
        """Zet het hele lijstje met een druk op de knop in het AH-mandje, met de aantallen zoals ze nu staan."""
        snapshot = ah_lists.get_snapshot(list_id)
        try:
            if not snapshot:
                snapshot = ah.get_favorite_list_items(list_id)
            items = ah_lists.cart_items_from(snapshot)
            if not items:
                return jsonify({"error": tr("Dit lijstje heeft geen producten.", "This list has no products.")}), 400
            ah.add_to_shopping_list(items)
        except Exception as exc:  # noqa: BLE001 - AH is onofficieel; geef de fout door
            app.logger.exception("AH list to cart error")
            return jsonify({"error": str(exc) or tr("Toevoegen aan het mandje mislukt", "Adding to the basket failed")}), 424
        return jsonify(
            {
                "added": len(items),
                "units": sum(item["quantity"] for item in items),
                "name": snapshot.get("name"),
            }
        )

    # --- bestelmandje, bestellingen en kassabonnen: lezen uit de lokale kopie, synchroniseren bij AH ----------

    def ah_sync(action, error_text: str):
        try:
            return jsonify(action())
        except Exception as exc:  # noqa: BLE001 - AH is onofficieel; toon de fout, de bewaarde kopie blijft staan
            app.logger.exception("AH order sync error")
            return jsonify({"error": str(exc) or error_text}), 424

    @app.get("/api/ah/basket")
    def ah_basket():
        return jsonify(ah_orders.get_basket())

    @app.post("/api/ah/basket/sync")
    def ah_basket_sync():
        return ah_sync(ah_orders.sync_basket, tr("Mandje ophalen mislukt", "Fetching the basket failed"))

    @app.get("/api/ah/orders")
    def ah_orders_list():
        return jsonify(ah_orders.list_orders())

    @app.get("/api/ah/orders/<int:order_id>")
    def ah_order_detail(order_id: int):
        order = ah_orders.get_order(order_id)
        if not order:
            return jsonify({"error": tr("Bestelling niet gevonden.", "Order not found.")}), 404
        return jsonify(order)

    @app.post("/api/ah/orders/sync")
    def ah_orders_sync():
        return ah_sync(ah_orders.sync_orders, tr("Bestellingen ophalen mislukt", "Fetching the orders failed"))

    @app.get("/api/ah/receipts")
    def ah_receipts_list():
        return jsonify(ah_orders.list_receipts())

    @app.get("/api/ah/receipts/<receipt_id>")
    def ah_receipt_detail(receipt_id: str):
        receipt = ah_orders.get_receipt(receipt_id)
        if not receipt:
            return jsonify({"error": tr("Kassabon niet gevonden.", "Receipt not found.")}), 404
        return jsonify(receipt)

    @app.post("/api/ah/receipts/sync")
    def ah_receipts_sync():
        return ah_sync(ah_orders.sync_receipts, tr("Kassabonnen ophalen mislukt", "Fetching the receipts failed"))

    @app.get("/api/ah/receipts/search")
    def ah_receipts_search():
        return jsonify(ah_orders.search_receipts(request.args.get("q") or ""))

    @app.get("/api/ah/purchases/search")
    def ah_purchases_search():
        return jsonify(ah_orders.search_purchases(request.args.get("q") or ""))

    @app.post("/api/ah/explore")
    def ah_explore_routes():
        # Alleen de beheerder (auth.ADMIN_PATHS): roept leesroutes aan en meldt de vorm van de antwoorden.
        return jsonify(ah_explore.explore())

    @app.get("/api/mealie/status")
    def mealie_status():
        verify = (request.args.get("verify") or "").strip().lower() in {"1", "true", "yes"}
        return jsonify(mealie.status(verify=verify))

    @app.post("/api/mealie/auth")
    def mealie_auth():
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(mealie.link(body.get("url"), body.get("token")))
        except (ValueError, RuntimeError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/mealie/auth/logout")
    def mealie_auth_logout():
        return jsonify(mealie.clear())

    @app.post("/api/mealie/import")
    def import_recipe():
        body = request.get_json(silent=True) or {}
        url = (body.get("url") or "").strip()
        if not url:
            return jsonify({"error": "URL ontbreekt"}), 400

        try:
            slug = mealie.import_from_url(url)
            return jsonify({"slug": slug, "mealieUrl": mealie.recipe_page_url(slug)})
        except Exception as exc:
            app.logger.exception("Mealie import error")
            return jsonify({"error": str(exc) or "Import mislukt"}), 424

    def cart_candidate(product: dict[str, Any], quantity: Any, unit: Any) -> dict[str, Any]:
        packs, doubt = ah.assess_packs(quantity, unit, product.get("unitSize"))
        images = product.get("images") or []
        return {
            "productId": product["webshopId"],
            "title": product["title"],
            "brand": product.get("brand"),
            "unitSize": product.get("unitSize"),
            "price": product["price"]["now"],
            "image": images[0].get("url") if images and isinstance(images[0], dict) else None,
            "isBonus": bool(product.get("isBonus")),
            "url": ah.product_web_url(product["webshopId"]),
            "packs": packs,
            "doubt": doubt,
        }

    def cart_preview(search: Any, make_candidate: Any) -> Any:
        """Zoek per ingredient de beste producten en reken het aantal verpakkingen uit.

        Een regel met `doubt` is er niet zeker van; de gebruiker kiest dan zelf. Gedeeld door
        alle supermarkten: `search(query, limit)` en `make_candidate(product, quantity, unit)`
        zijn de enige winkelspecifieke delen.
        """
        body = request.get_json(silent=True) or {}
        items = body.get("items") or []
        if not items:
            return jsonify({"error": "Geen ingredienten opgegeven"}), 400

        def resolve(item: dict[str, Any]) -> dict[str, Any]:
            query = str(item.get("query") or "")
            result = {
                "query": query,
                "display": item.get("display") or query,
                "quantity": item.get("quantity"),
                "unit": item.get("unit"),
                "candidates": [],
            }
            try:
                products = search(query, 5)
            except Exception as exc:  # noqa: BLE001 - een mislukte zoekopdracht geldt alleen voor deze regel
                result["error"] = str(exc)
                return result
            result["candidates"] = [
                make_candidate(product, item.get("quantity"), item.get("unit")) for product in products
            ]
            return result

        with ThreadPoolExecutor(max_workers=6) as executor:
            resolved = list(executor.map(audit.propagate(resolve), items))
        return jsonify({"items": resolved})

    @app.post("/api/ah/cart/preview")
    def ah_cart_preview():
        return cart_preview(ah.search_products, cart_candidate)

    @app.post("/api/ah/cart")
    def ah_cart():
        body = request.get_json(silent=True) or {}
        items = body.get("items") or []
        if not items:
            return jsonify({"error": "Geen ingredienten opgegeven"}), 400

        matched = []
        diagnostics = []
        for item in items:
            query = item.get("query", "")
            try:
                if item.get("productId"):
                    # Door de gebruiker bevestigd (of aangepast) in de controlelijst.
                    matched.append(
                        {
                            "query": query,
                            "productId": item["productId"],
                            "quantity": ah.normalize_quantity(item.get("quantity")),
                            "title": item.get("title") or query,
                            "price": item.get("price") or 0,
                            "unitSize": item.get("unitSize"),
                            "image": item.get("image"),
                        }
                    )
                    diagnostics.append({"query": query, "status": "gevonden"})
                    continue
                product = ah.search_product(query)
                if product:
                    matched.append(
                        {
                            "query": query,
                            "productId": product["webshopId"],
                            "quantity": ah.packs_needed(
                                item.get("quantity"), item.get("unit"), product.get("unitSize")
                            ),
                            "title": product["title"],
                            "price": product["price"]["now"],
                            "unitSize": product.get("unitSize") or product["price"].get("unitSize"),
                            "image": (product.get("images") or [{"url": None}])[0].get("url"),
                        }
                    )
                    diagnostics.append({"query": query, "status": "gevonden"})
                else:
                    diagnostics.append({"query": query, "status": "niet gevonden"})
            except Exception as exc:
                diagnostics.append(
                    {"query": item.get("query", "onbekend"), "status": "fout", "error": str(exc)}
                )

        if not matched:
            return (
                jsonify(
                    {
                        "error": "Geen producten gevonden voor de opgegeven ingredienten",
                        "diagnostics": diagnostics,
                    }
                ),
                404,
            )

        try:
            ah.add_to_shopping_list(
                [{"productId": item["productId"], "quantity": item["quantity"]} for item in matched]
            )
        except Exception as exc:
            app.logger.exception("AH cart error")
            return jsonify({"error": str(exc)}), 424

        skipped = [item for item in diagnostics if item["status"] != "gevonden"]
        return jsonify(
            {
                "added": len(matched),
                "skipped": len(skipped),
                "skippedItems": [item["query"] for item in skipped],
                "items": [
                    {
                        "query": item["query"],
                        "quantity": item["quantity"],
                        "product": {
                            "title": item["title"],
                            "price": item["price"],
                            "unitSize": item["unitSize"],
                            "image": item["image"],
                        },
                    }
                    for item in matched
                ],
            }
        )

    # --- Winkels die direct in het echte mandje schrijven (Picnic, Jumbo) ---------------------
    # Elke winkel is een module met search_product, candidate en add_to_cart (zie picnic.py, jumbo.py).

    def store_error(label: str, exc: Exception, *user_errors: type) -> Any:
        """Fouten van de gebruiker (400) apart van fouten van de winkel zelf (424).

        De 400-fouten (ValueError en de `user_errors`) dragen een tekst die wij zelf schreven, zoals
        "controleer e-mailadres en wachtwoord"; die tonen we. De winkelmodules zetten het ruwe antwoord
        van de winkel nooit in zulke fouten. Bij een 424 kan het antwoord van de winkel erin zitten:
        die gaat naar het logboek en de gebruiker krijgt alleen een algemene melding.
        """
        if isinstance(exc, (ValueError, *user_errors)):
            return jsonify({"error": str(exc) or f"{label} aanvraag ongeldig"}), 400
        app.logger.exception("%s error", label)
        return jsonify({"error": f"{label} fout"}), 424

    def store_cart(store: Any, error: Any, auth_error: type) -> Any:
        """Zoek (indien nodig) de producten en zet ze in het mandje van de winkel; nooit een bestelling."""
        body = request.get_json(silent=True) or {}
        items = body.get("items") or []
        if not items:
            return jsonify({"error": "Geen ingredienten opgegeven"}), 400

        matched: list[dict[str, Any]] = []
        skipped: list[str] = []
        for item in items:
            query = item.get("query", "")
            try:
                if item.get("productId"):
                    # Door de gebruiker bevestigd (of aangepast) in de controlelijst.
                    matched.append({"productId": item["productId"], "quantity": item.get("quantity"), "query": query})
                    continue
                product = store.search_product(query)
            except Exception as exc:  # noqa: BLE001
                if isinstance(exc, auth_error):
                    return error(exc)
                skipped.append(query or "onbekend")
                continue
            if not product:
                skipped.append(query or "onbekend")
                continue
            found = store.candidate(product, item.get("quantity"), item.get("unit"))
            matched.append({"productId": found["productId"], "quantity": found["packs"], "query": query})

        if not matched:
            return jsonify({"error": "Geen producten gevonden voor de opgegeven ingredienten", "skippedItems": skipped}), 404
        try:
            result = store.add_to_cart(matched)
        except Exception as exc:  # noqa: BLE001
            return error(exc)
        failed_ids = {entry["productId"] for entry in result["failed"]}
        skipped += [entry["query"] for entry in matched if entry["productId"] in failed_ids]
        return jsonify(
            {
                "added": len(result["added"]),
                "skipped": len(skipped),
                "skippedItems": skipped,
                "failed": result["failed"],
                "cart": result["cart"],
            }
        )

    # --- Picnic -------------------------------------------------------------------------
    # Zelfde opzet als AH, met twee verschillen: inloggen heeft een tweestapscode, en de producten
    # gaan direct in het echte Picnic-winkelmandje (er is geen apart boodschappenlijstje).

    def picnic_error(exc: Exception) -> Any:
        return store_error("Picnic", exc, picnic.PicnicAuthError, picnic.Picnic2FAError)

    @app.get("/api/picnic/auth/status")
    def picnic_auth_status():
        verify = (request.args.get("verify") or "").strip().lower() in {"1", "true", "yes"}
        return jsonify(picnic.status(verify=verify))

    @app.post("/api/picnic/auth")
    def picnic_auth():
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(picnic.login(body.get("email"), body.get("password") or ""))
        except Exception as exc:  # noqa: BLE001
            return picnic_error(exc)

    @app.post("/api/picnic/auth/2fa/request")
    def picnic_auth_2fa_request():
        body = request.get_json(silent=True) or {}
        try:
            picnic.request_2fa_code(body.get("channel") or "SMS")
            return jsonify({"requested": True})
        except Exception as exc:  # noqa: BLE001
            return picnic_error(exc)

    @app.post("/api/picnic/auth/2fa/verify")
    def picnic_auth_2fa_verify():
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(picnic.verify_2fa(body.get("code")))
        except Exception as exc:  # noqa: BLE001
            return picnic_error(exc)

    @app.post("/api/picnic/auth/logout")
    def picnic_auth_logout():
        return jsonify(picnic.unlink())

    @app.get("/api/picnic/search")
    def picnic_search():
        query = (request.args.get("q") or "").strip()
        if not query:
            return jsonify({"error": "Zoekterm ontbreekt"}), 400
        try:
            return jsonify({"products": picnic.search_products(query, 10)})
        except Exception as exc:  # noqa: BLE001
            return picnic_error(exc)

    @app.post("/api/picnic/cart/preview")
    def picnic_cart_preview():
        return cart_preview(picnic.search_products, picnic.candidate)

    @app.get("/api/picnic/cart")
    def picnic_cart_get():
        try:
            return jsonify(picnic.get_cart())
        except Exception as exc:  # noqa: BLE001
            return picnic_error(exc)

    @app.post("/api/picnic/cart")
    def picnic_cart():
        return store_cart(picnic, picnic_error, picnic.PicnicAuthError)

    @app.get("/api/picnic/deliveries")
    def picnic_deliveries():
        current = (request.args.get("current") or "").strip().lower() in {"1", "true", "yes"}
        try:
            return jsonify({"deliveries": picnic.get_deliveries(current_only=current)})
        except Exception as exc:  # noqa: BLE001
            return picnic_error(exc)

    # Afgeleverde bestellingen: lezen uit de lokale kopie, synchroniseren bij Picnic. De ids zijn tekst (leveringsnummers).

    @app.get("/api/picnic/orders")
    def picnic_orders_list():
        return jsonify(picnic_orders.list_orders())

    @app.get("/api/picnic/orders/<order_id>")
    def picnic_order_detail(order_id: str):
        order = picnic_orders.get_order(order_id)
        if not order:
            return jsonify({"error": tr("Bestelling niet gevonden.", "Order not found.")}), 404
        return jsonify(order)

    @app.post("/api/picnic/orders/sync")
    def picnic_orders_sync():
        try:
            return jsonify(picnic_orders.sync_orders())
        except Exception as exc:  # noqa: BLE001 - de bewaarde kopie blijft staan
            return picnic_error(exc)

    @app.get("/api/picnic/deliveries/<delivery_id>")
    def picnic_delivery(delivery_id: str):
        # Alleen lezen: één levering met haar producten.
        try:
            return jsonify(picnic.get_delivery(delivery_id))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001
            return picnic_error(exc)

    @app.put("/api/picnic/cart/items/<product_id>")
    def picnic_cart_set_quantity(product_id: str):
        # Een exact aantal; 0 haalt het product uit het mandje. Plaatst nooit een bestelling.
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(picnic.set_quantity(product_id, body.get("quantity")))
        except Exception as exc:  # noqa: BLE001
            return picnic_error(exc)

    @app.post("/api/picnic/cart/clear")
    def picnic_cart_clear():
        # Het hele mandje leegmaken vraagt om een expliciete bevestiging, ook van andere aanroepers.
        body = request.get_json(silent=True) or {}
        if body.get("confirm") is not True:
            return jsonify({"error": "Bevestiging ontbreekt"}), 400
        try:
            return jsonify(picnic.clear_cart())
        except Exception as exc:  # noqa: BLE001
            return picnic_error(exc)

    @app.get("/api/picnic/favorites")
    def picnic_favorites():
        # Alleen lezen: de favoriete producten uit de Favorieten-tab van de Picnic-app. De producten worden lui
        # gecontroleerd: met `budget` hooguit zoveel nieuwe per aanroep (`pending` zegt hoeveel er nog komen),
        # met `refresh=1` vergeet de server eerst wat hij wist.
        budget = request.args.get("budget", type=int)
        try:
            return jsonify(picnic.get_favorites(
                budget=max(1, min(budget, 30)) if budget else None,
                refresh=request.args.get("refresh") == "1",
            ))
        except Exception as exc:  # noqa: BLE001
            return picnic_error(exc)

    @app.get("/api/picnic/recipes")
    def picnic_recipe_list():
        # Alleen lezen: de recepten van Picnic (opgeslagen of het kookboek), met id en naam.
        source = (request.args.get("source") or "saved").strip().lower()
        query = (request.args.get("q") or "").strip()
        try:
            if query:  # zoeken in je bewaarde recepten en het kookboek, voor Recepten zoeken
                return jsonify({"source": "search", **picnic_recipes.search(query)})
            return jsonify({"source": source, "recipes": picnic_recipes.get_recipes(source)})
        except Exception as exc:  # noqa: BLE001
            return picnic_error(exc)

    @app.get("/api/picnic/recipes/<recipe_id>")
    def picnic_recipe_detail(recipe_id: str):
        # Alleen lezen: één recept met ingrediënten en bereiding.
        try:
            return jsonify(picnic_recipes.get_recipe(recipe_id))
        except Exception as exc:  # noqa: BLE001
            return picnic_error(exc)

    @app.post("/api/picnic/recipes/<recipe_id>/import")
    def picnic_recipe_import(recipe_id: str):
        # Zet het recept in Mealie, als schema.org Recipe (Picnic-recepten hebben geen webpagina).
        try:
            mealie.ensure_configured()
        except RuntimeError as exc:  # Mealie is niet gekoppeld: de tekst is van onszelf en zegt wat te doen
            return jsonify({"error": str(exc)}), 400
        try:
            recipe = picnic_recipes.get_recipe(recipe_id)
            slug = mealie.import_via_jsonld(picnic_recipes.source_url(recipe_id), picnic_recipes.jsonld(recipe))
            return jsonify({"slug": slug, "mealieUrl": mealie.recipe_page_url(slug)})
        except Exception as exc:  # noqa: BLE001
            app.logger.exception("Picnic recipe import error")
            if isinstance(exc, (ValueError, picnic.PicnicAuthError)):
                return picnic_error(exc)
            # De melding van Mealie of Picnic kan het antwoord van de dienst bevatten: alleen in het logboek.
            return jsonify({"error": "Importeren in Mealie mislukt"}), 424

    @app.get("/api/picnic/slots")
    def picnic_slots():
        # Alleen lezen: een bezorgmoment kiezen doe je in de Picnic-app.
        try:
            limit = max(1, min(30, int(request.args.get("limit") or 8)))
            return jsonify({"slots": picnic.get_delivery_slots(limit)})
        except Exception as exc:  # noqa: BLE001
            return picnic_error(exc)

    @app.post("/api/picnic/explore")
    def picnic_explore_routes():
        # Alleen de beheerder (auth.ADMIN_PATHS): roept leesroutes aan en meldt de vorm van de antwoorden.
        return jsonify(picnic_explore.explore())

    # --- Jumbo --------------------------------------------------------------------------
    # Koppelen werkt zoals bij AH (de beheerder plakt een regel uit de browserconsole); de producten
    # gaan, net als bij Picnic, direct in het echte mandje.

    def jumbo_error(exc: Exception) -> Any:
        return store_error("Jumbo", exc, jumbo.JumboAuthError)

    @app.get("/api/jumbo/auth/status")
    def jumbo_auth_status():
        verify = (request.args.get("verify") or "").strip().lower() in {"1", "true", "yes"}
        return jsonify(jumbo.status(verify=verify))

    @app.get("/api/jumbo/auth/start")
    def jumbo_auth_start():
        return redirect(jumbo.start_login(), code=302)

    @app.post("/api/jumbo/auth")
    def jumbo_auth():
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(jumbo.link_account(body.get("code") or body.get("token") or ""))
        except Exception as exc:  # noqa: BLE001
            return jumbo_error(exc)

    @app.post("/api/jumbo/auth/logout")
    def jumbo_auth_logout():
        return jsonify(jumbo.unlink())

    @app.get("/api/jumbo/search")
    def jumbo_search():
        query = (request.args.get("q") or "").strip()
        if not query:
            return jsonify({"error": "Zoekterm ontbreekt"}), 400
        try:
            return jsonify({"products": jumbo.search_products(query, 10)})
        except Exception as exc:  # noqa: BLE001
            return jumbo_error(exc)

    @app.post("/api/jumbo/cart/preview")
    def jumbo_cart_preview():
        return cart_preview(jumbo.search_products, jumbo.candidate)

    @app.get("/api/jumbo/cart")
    def jumbo_cart_get():
        try:
            return jsonify(jumbo.get_cart())
        except Exception as exc:  # noqa: BLE001
            return jumbo_error(exc)

    @app.post("/api/jumbo/cart")
    def jumbo_cart():
        return store_cart(jumbo, jumbo_error, jumbo.JumboAuthError)

    # Het Jumbo-lijstje: één lijstje, bewaard als lokale kopie en in dezelfde vorm als de AH-lijstjes.

    @app.get("/api/jumbo/lists")
    def jumbo_lists_overview():
        # Altijd 200: de lokale kopie blijft zichtbaar ook als Jumbo niet bereikbaar is.
        return jsonify(jumbo_lists.merged_lists())

    @app.get("/api/jumbo/lists/search")
    def jumbo_lists_search():
        query = (request.args.get("q") or "").strip()
        return jsonify({"query": query, "items": jumbo_lists.search_items(query)})

    @app.get("/api/jumbo/lists/<list_id>")
    def jumbo_list_detail(list_id: str):
        snapshot = jumbo_lists.get_snapshot(list_id)
        if snapshot:
            return jsonify(snapshot)
        try:
            return jsonify(jumbo_lists.live_view(jumbo_lists.fetch_live(list_id)))
        except Exception as exc:  # noqa: BLE001
            return jumbo_error(exc)

    @app.post("/api/jumbo/lists/<list_id>/sync")
    def jumbo_list_sync(list_id: str):
        try:
            return jsonify(jumbo_lists.sync_list(list_id))
        except Exception as exc:  # noqa: BLE001
            return jumbo_error(exc)

    @app.patch("/api/jumbo/lists/<list_id>/items/<int:position>")
    def jumbo_list_item_quantity(list_id: str, position: int):
        body = request.get_json(silent=True) or {}
        try:
            snapshot = jumbo_lists.set_quantity(list_id, position, body.get("quantity"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if not snapshot:
            return jsonify({"error": tr("Regel niet gevonden. Synchroniseer het lijstje eerst.", "Line not found. Sync the list first.")}), 404
        return jsonify(snapshot)

    @app.post("/api/jumbo/lists/<list_id>/reset")
    def jumbo_list_reset(list_id: str):
        snapshot = jumbo_lists.reset_quantities(list_id)
        if not snapshot:
            return jsonify({"error": tr("Dit lijstje is niet gesynchroniseerd.", "This list is not synced.")}), 404
        return jsonify(snapshot)

    @app.post("/api/jumbo/lists/<list_id>/cart")
    def jumbo_list_to_cart(list_id: str):
        """Zet het hele lijstje in het Jumbo-mandje, met de aantallen zoals ze nu staan.

        Regels met een SKU gaan direct; een regel met alleen tekst zoeken we eerst op. Nooit een bestelling.
        """
        try:
            snapshot = jumbo_lists.get_snapshot(list_id) or jumbo_lists.live_view(jumbo_lists.fetch_live(list_id))
            matched: list[dict[str, Any]] = []
            skipped: list[str] = []
            for item in snapshot.get("items") or []:
                if item.get("productId"):
                    matched.append({"productId": item["productId"], "quantity": item["quantity"], "term": item.get("term")})
                    continue
                found = jumbo.search_product(item.get("term") or "")
                if found:
                    matched.append({"productId": found["id"], "quantity": item["quantity"], "term": item.get("term")})
                else:
                    skipped.append(item.get("term") or "")
            if not matched:
                return jsonify({"error": tr("Dit lijstje heeft geen producten.", "This list has no products.")}), 400
            result = jumbo.add_to_cart(matched)
        except Exception as exc:  # noqa: BLE001
            return jumbo_error(exc)
        failed = {entry["productId"] for entry in result["failed"]}
        skipped += [entry["term"] or "" for entry in matched if entry["productId"] in failed]
        done = [entry for entry in matched if entry["productId"] not in failed]
        return jsonify({"added": len(done), "units": sum(entry["quantity"] for entry in done), "name": snapshot.get("name"), "skippedItems": skipped})

    @app.post("/api/jumbo/list")
    def jumbo_list_add():
        """Zet ingrediënten op het Jumbo-lijstje in plaats van in het mandje. Wat er al staat slaan we over."""
        body = request.get_json(silent=True) or {}
        items = body.get("items") or []
        if not items:
            return jsonify({"error": "Geen ingredienten opgegeven"}), 400
        lines = [
            {"term": item.get("title") or item.get("query") or "", "sku": item.get("productId")}
            for item in items
            if isinstance(item, dict)
        ]
        try:
            result = jumbo.add_to_shopping_list(lines)
        except Exception as exc:  # noqa: BLE001
            return jumbo_error(exc)
        return jsonify({"added": len(result["added"]), "skipped": len(result["skipped"]), "skippedItems": result["skipped"]})

    # Bestellingen: lezen uit de lokale kopie, synchroniseren bij Jumbo. De ids zijn tekst (Jumbo-ordernummers).

    @app.get("/api/jumbo/orders")
    def jumbo_orders_list():
        return jsonify(jumbo_orders.list_orders())

    @app.get("/api/jumbo/orders/<order_id>")
    def jumbo_order_detail(order_id: str):
        order = jumbo_orders.get_order(order_id)
        if not order:
            return jsonify({"error": tr("Bestelling niet gevonden.", "Order not found.")}), 404
        return jsonify(order)

    @app.post("/api/jumbo/orders/sync")
    def jumbo_orders_sync():
        try:
            return jsonify(jumbo_orders.sync_orders())
        except Exception as exc:  # noqa: BLE001 - de bewaarde kopie blijft staan
            return jumbo_error(exc)

    @app.get("/api/jumbo/purchases/search")
    def jumbo_purchases_search():
        return jsonify(jumbo_orders.search_purchases(request.args.get("q") or ""))

    # Recepten: zoekresultaten, één recept met ingrediënten en bereiding, en importeren in Mealie.

    @app.get("/api/jumbo/recipes")
    def jumbo_recipe_list():
        # Alleen lezen: zoeken in alle recepten van Jumbo (`offset` voor de volgende pagina).
        try:
            return jsonify({**jumbo_recipes.search(request.args.get("q"), request.args.get("offset")), "pageSize": jumbo_recipes.RECIPE_PAGE})
        except Exception as exc:  # noqa: BLE001
            return jumbo_error(exc)

    @app.get("/api/jumbo/favorite-recipes")
    def jumbo_favorite_recipes():
        # Alleen lezen: je favoriete recepten bij Jumbo, per pagina (`page` begint bij 1).
        try:
            return jsonify({**jumbo_recipes.get_favorites(request.args.get("page")), "pageSize": jumbo_recipes.RECIPE_PAGE})
        except Exception as exc:  # noqa: BLE001
            return jumbo_error(exc)

    @app.get("/api/jumbo/recipes/<recipe_id>")
    def jumbo_recipe_detail(recipe_id: str):
        # Alleen lezen: één recept met ingrediënten en bereiding.
        try:
            return jsonify(jumbo_recipes.get_recipe(recipe_id))
        except Exception as exc:  # noqa: BLE001
            return jumbo_error(exc)

    @app.post("/api/jumbo/recipes/<recipe_id>/import")
    def jumbo_recipe_import(recipe_id: str):
        # Zet het recept in Mealie, als schema.org Recipe met de link naar jumbo.com als bron.
        try:
            mealie.ensure_configured()
        except RuntimeError as exc:  # Mealie is niet gekoppeld: de tekst is van onszelf en zegt wat te doen
            return jsonify({"error": str(exc)}), 400
        try:
            recipe = jumbo_recipes.get_recipe(recipe_id)
            slug = mealie.import_via_jsonld(jumbo_recipes.source_url(recipe), jumbo_recipes.jsonld(recipe))
            return jsonify({"slug": slug, "mealieUrl": mealie.recipe_page_url(slug)})
        except Exception as exc:  # noqa: BLE001
            app.logger.exception("Jumbo recipe import error")
            if isinstance(exc, (ValueError, jumbo.JumboAuthError)):
                return jumbo_error(exc)
            # De melding van Mealie of Jumbo kan het antwoord van de dienst bevatten: alleen in het logboek.
            return jsonify({"error": "Importeren in Mealie mislukt"}), 424

    @app.post("/api/jumbo/explore")
    def jumbo_explore_routes():
        # Alleen de beheerder (auth.ADMIN_PATHS): meldt of Jumbo deze server toelaat en de vorm van de antwoorden.
        return jsonify(jumbo_explore.explore())

    @app.post("/api/ah/auth")
    def ah_auth():
        body = request.get_json(silent=True) or {}
        raw = (body.get("code") or body.get("token") or "").strip()
        if not raw:
            return jsonify({"error": "Geen code of token opgegeven"}), 400

        result = ah.link_account(raw)
        if result.get("connected"):
            return jsonify(result)
        return jsonify({"error": result.get("error") or "Onbekende fout"}), 400

    @app.post("/api/ah/auth/verify")
    def ah_auth_verify():
        body = request.get_json(silent=True) or {}
        token = (body.get("refreshToken") or "").strip()
        if not token:
            return jsonify({"error": "Geen token opgegeven"}), 400

        result = ah.verify_token(token)
        if result.get("ok"):
            if result.get("type") == "refresh":
                ah.save_refresh_token(result.get("refreshToken") or token)
            return jsonify({"connected": True, "type": result.get("type")})
        return jsonify({"error": result["error"]}), 400

    @app.get("/api/ah/auth/start")
    def ah_auth_start():
        return redirect(ah.login_url(), code=302)

    @app.get("/api/ah/auth/callback")
    def ah_auth_callback():
        error = request.args.get("error")
        if error:
            description = request.args.get("error_description") or error
            return redirect(f"/?ah_error={quote_plus(description)}")

        code = request.args.get("code")
        if not code:
            return redirect("/?ah_error=Geen+code+ontvangen")

        callback_url = ah_callback_url()
        try:
            ah.exchange_and_store_oauth_code(code, callback_url)
            return redirect("/?ah_connected=1")
        except Exception as exc:
            return redirect(
                f"/?ah_error={quote_plus(f'Code inwisselen mislukt: {str(exc)[:100]}')}"
            )

    @app.get("/api/ah/auth/proxy/callback")
    def ah_auth_proxy_callback():
        error = request.args.get("error")
        if error:
            description = request.args.get("error_description") or error
            return redirect(f"/?ah_error={quote_plus(description)}")

        code = request.args.get("code")
        if not code:
            return redirect("/?ah_error=Geen+code+ontvangen")

        try:
            ah.exchange_and_store_oauth_code(code)
            return redirect("/?ah_connected=1")
        except Exception as exc:
            return redirect(
                f"/?ah_error={quote_plus(f'Code inwisselen mislukt: {str(exc)[:100]}')}"
            )

    @app.route(
        "/api/ah/auth/proxy/<path:proxy_path>",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    @app.route(
        "/api/ah/auth/proxy",
        defaults={"proxy_path": ""},
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    def ah_auth_proxy(proxy_path: str):
        target_url = f"{ah.AH_LOGIN_BASE}/{proxy_path}".rstrip("/")
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower()
            not in {
                "host",
                "accept-encoding",
                "content-length",
                "content-encoding",
            }
        }
        headers.setdefault("User-Agent", ah.AH_BROWSER_USER_AGENT)
        headers.setdefault("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
        headers.setdefault("Accept-Language", "nl-NL,nl;q=0.9,en;q=0.8")
        proxy_base = ah_proxy_base_url()
        public_origin = public_base_url()
        if "Referer" in headers:
            headers["Referer"] = (
                headers["Referer"]
                .replace(proxy_base, ah.AH_LOGIN_BASE)
                .replace(public_origin, ah.AH_LOGIN_BASE)
            )
        if headers.get("Origin", "").rstrip("/") == public_origin:
            headers["Origin"] = ah.AH_LOGIN_BASE

        try:
            with audit.login_proxy():
                upstream = requests.request(
                    request.method,
                    target_url,
                    params=request.args,
                    data=request.get_data(),
                    headers=headers,
                    allow_redirects=False,
                    timeout=30,
                )
        except requests.RequestException as exc:
            app.logger.exception("AH login proxy error")
            return f"AH login proxy error: {exc}", 502

        content_type = upstream.headers.get("Content-Type", "")
        body = upstream.content
        if any(kind in content_type for kind in ("text/html", "javascript", "json", "text/css")):
            body = ah.rewrite_login_body(body, proxy_base)

        response = Response(body, status=upstream.status_code, content_type=content_type)
        blocked_headers = {
            "content-encoding",
            "content-length",
            "content-security-policy",
            "strict-transport-security",
            "x-frame-options",
            "set-cookie",
            "location",
        }
        for key, value in upstream.headers.items():
            if key.lower() not in blocked_headers:
                response.headers[key] = value

        if location := upstream.headers.get("Location"):
            response.headers["Location"] = ah.rewrite_login_location(
                location,
                proxy_base,
            )

        raw_headers = getattr(upstream.raw, "headers", None)
        cookies = raw_headers.getlist("Set-Cookie") if raw_headers else []
        if not cookies and upstream.headers.get("Set-Cookie"):
            cookies = [upstream.headers["Set-Cookie"]]
        for cookie in cookies:
            response.headers.add("Set-Cookie", ah.sanitize_login_cookie(cookie))

        return response

    return app


app = create_app()
