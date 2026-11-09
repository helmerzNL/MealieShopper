import hashlib
import inspect
import mimetypes
from collections.abc import Awaitable, Callable, Iterable
from datetime import date, timedelta
from os import environ
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
import sqlite3

import httpx
from quart import Quart, Response, jsonify, redirect, render_template, request, send_file, send_from_directory

from . import about, ah, ah_explore, ah_lists, ah_orders, ah_planner, ah_recipes, ai_match, ai_review, audit, auth_password, backup, crisp, crisp_orders, factory_reset, hellofresh, image_cache, jumbo, jumbo_explore, jumbo_lists, jumbo_orders, jumbo_recipes, marleyspoon, mcp_http, mealie, mealie_images, offers, offers_schedule, onboarding, picnic, picnic_explore, picnic_orders, picnic_recipes, price_archive, price_import, prices, product_images, product_links, profile, recipe_bulk, recipe_nutrition, request_body, server_settings, updates, users
from . import auth, deals, http_client, tasks, urls
from . import assets
from .body_limits import DEFAULT_MAX_BODY, SizedRequest
from .forwarded import ForwardedHeaders
from .keep_running import FinishAfterDisconnect
from .response_cache import ResponseCache
from .i18n import tr


# Python kent .webmanifest niet overal; browsers verwachten application/manifest+json.
mimetypes.add_type("application/manifest+json", ".webmanifest")

CART_PREVIEW_WORKERS: int = 6  # zoveel regels van een controlelijst zoeken we tegelijk bij de winkel

AH_PROXY_DROPPED_HEADERS: frozenset[str] = frozenset(
    {
        "host",
        "accept-encoding",
        "content-length",
        "content-encoding",
        "remote-addr",  # zet Quart er zelf bij, de browser niet: zonder deze gaan dezelfde headers mee als in 3.13.1
        # Van de reverse proxy: het IP van de bezoeker en de interne opbouw horen niet bij AH.
        "x-forwarded-for",
        "x-forwarded-proto",
        "x-forwarded-host",
        "x-forwarded-port",
        "x-forwarded-prefix",
        "forwarded",
        "x-real-ip",
        "authorization",  # bedoeld voor MealieShopper zelf
        "cookie",  # apart, zonder de eigen cookies: zie _ah_proxy_cookie
    }
)

# Headers van AH die niet naar de browser gaan. De lengte en codering kloppen niet meer na het herschrijven, de
# beveiligingsheaders van AH horen niet bij MealieShopper, cookies en Location gaan apart. De hop-by-hop-headers
# (RFC 9110, 7.6.1) gelden alleen voor de verbinding met AH, en Date en Server zet hypercorn zelf: zo liet Gunicorn ze
# in 3.x ook weg (`gunicorn.util.hop_headers`), anders staan ze er twee keer.
AH_PROXY_BLOCKED_RESPONSE_HEADERS: frozenset[str] = frozenset(
    {
        "content-encoding",
        "content-length",
        "content-security-policy",
        "strict-transport-security",
        "x-frame-options",
        "set-cookie",
        "location",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "date",
        "server",
    }
)


def _ah_proxy_cookie(headers: Iterable[str]) -> str | None:
    """De Cookie-header voor AH uit alle Cookie-headers van de browser (over HTTP/2 elke cookie apart): zonder de eigen
    cookies van MealieShopper (`ms_`), of None als er niets overblijft."""
    kept: list[str] = []
    for header in headers:
        for pair in header.split(";"):
            pair = pair.strip()
            if not pair:
                continue
            if pair.partition("=")[0].strip().startswith("ms_"):
                continue
            kept.append(pair)
    return "; ".join(kept) or None


def create_app() -> Quart:
    app = Quart(__name__, template_folder="../templates", static_folder="../static")
    # De versie van de statische bestanden: uit hun inhoud (assets.py), tenzij MEALIESHOPPER_ASSET_VERSION hem vastzet.
    app.config["ASSET_VERSION"] = environ.get("MEALIESHOPPER_ASSET_VERSION") or assets.asset_version(Path(app.static_folder or "static"))
    # Quart bewaart statische bestanden standaard 12 uur in de browser (Flask niet). Met 0 vraagt de browser elke keer na of het
    # bestand veranderd is (ETag, 304), zoals voorheen. None helpt niet: dan valt de browser terug op zijn eigen schatting.
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    # Grenzen op een aanvraag: 16 MiB voor het lichaam, ruimer alleen voor de routes in body_limits.LARGE_BODIES (een back-up
    # klaarzetten, de prijsimport). 120 s om het lichaam te lezen en het antwoord te versturen (Quart staat standaard 60 s
    # toe). Het uploaden en downloaden van een back-up heeft geen tijdslimiet (backup.py), een groot bestand over een trage
    # verbinding mag niet halverwege stoppen.
    app.request_class = SizedRequest
    app.config["MAX_CONTENT_LENGTH"] = DEFAULT_MAX_BODY
    app.config["BODY_TIMEOUT"] = 120
    app.config["RESPONSE_TIMEOUT"] = 120
    # Achter de reverse proxy: schema, host en client-adres uit X-Forwarded-* (een hop), zoals ProxyFix voorheen. Daarbinnen,
    # direct om Quart: een handler maakt zijn werk af als de client weggaat (keep_running.py), zoals een thread onder 3.x.
    app.asgi_app = ForwardedHeaders(FinishAfterDisconnect(app.asgi_app))  # type: ignore[method-assign]
    # De receptvoorstellen bij Aanbiedingen staan standaard verborgen zolang de koppelingen niet goed genoeg zijn.
    app.config["OFFERS_RECIPES"] = environ.get("MEALIESHOPPER_OFFERS_RECIPES", "false").strip().lower() in {"1", "true", "yes", "on"}

    @app.context_processor
    async def offers_recipes_flag():
        # Het tabblad Recepten bij Aanbiedingen: aan met de omgevingsvariabele, of zodra er een AI-sleutel is ingesteld (zie ai_review.py).
        return {"offers_recipes_visible": app.config["OFFERS_RECIPES"] or await ai_review.configured()}
    audit.register_routes(app)
    auth.register_routes(app)
    auth_password.register_routes(app)
    onboarding.register_routes(app)
    factory_reset.register_routes(app)
    backup.register_routes(app)
    price_archive.register_routes(app)
    profile.register_routes(app)

    tasks.register(app)

    @app.before_serving
    async def _startup() -> None:
        """Opstartwerk van de app: database, opruimen en de dagelijkse aanbiedingen-run."""
        try:
            await auth.init_db()  # idempotent: schema en migratie een keer bij het starten, `ensure_db` vangt een latere eerste aanroep op
        except (sqlite3.Error, OSError):
            app.logger.warning("Kon de database niet aanmaken", exc_info=True)
        try:
            removed = await auth.purge_obsolete_secrets()
            if removed:
                app.logger.info("Opgeslagen gegevens van verwijderde koppelingen gewist (%d)", removed)
        except (sqlite3.Error, OSError):
            # Een onbereikbare data-map meldt zich bij het eerste gebruik; opstarten blijft mogelijk.
            app.logger.warning("Kon oude koppelingsgegevens niet opruimen", exc_info=True)
        try:
            for host in await image_cache.lift_stale_pauses():  # eenmalig: de pauze die een versie van voor 3.7.1 Picnic gaf (#236)
                app.logger.info("Pauze van de afbeeldingencache opgeheven voor %s", host)
        except (sqlite3.Error, OSError):
            app.logger.warning("Kon de pauze van de afbeeldingencache niet opheffen", exc_info=True)
        # De dagelijkse aanbiedingen-run hoort niet in de tests: die maken de app vaak aan.
        if not environ.get("MEALIESHOPPER_TESTING"):
            offers_schedule.start()
            tasks.spawn("warm-offers", warm_at_start())  # de eerste /api/offers na een start wacht niet op de catalogus en het oordeel

    # Fouten van externe diensten (AH, Mealie) geven we terug als 424 in
    # plaats van 502: reverse proxies (Cloudflare, SWAG, NPM) vervangen een 502
    # door hun eigen HTML-pagina, waardoor de echte foutmelding verdwijnt.

    public_base_url = server_settings.public_base_url  # in de app in te stellen, zie server_settings.py

    async def ah_callback_url() -> str:
        return f"{await public_base_url()}/api/ah/auth/callback"

    async def ah_proxy_base_url() -> str:
        return f"{await public_base_url()}/api/ah/auth/proxy"

    server_settings.register_routes(app)
    users.register_routes(app, public_base_url)
    mcp_http.register_routes(app, public_base_url)

    request_body.register(app)  # een JSON-body die geen object is: 400 in plaats van 500

    @app.errorhandler(sqlite3.Error)
    @app.errorhandler(OSError)
    async def database_error(exc):
        app.logger.exception("Storage error")
        message = tr(
            "Auth database is niet bereikbaar. Controleer MEALIESHOPPER_DATA_DIR en de /data volume mount/rechten.",
            "The auth database is not reachable. Check MEALIESHOPPER_DATA_DIR and the /data volume mount and permissions.",
        )
        if request.path.startswith("/api/"):
            return jsonify({"error": message, "detail": str(exc)}), 503
        return message, 503

    @app.before_request
    async def require_passkey_auth():
        blocked = await auth.require_auth_for_request()
        if blocked:
            payload, status = blocked
            return jsonify(payload), status

    @app.get("/")
    async def index(**_route):
        return await render_template("index.html", public_base_url=await public_base_url(), build_channel=about.build_channel())

    # Elke pagina heeft een eigen adres (/recipes, /cart, /stores/ah/orders, ...): de server geeft dezelfde pagina terug en
    # static/router.js toont het juiste scherm. Zo werken herladen, een bladwijzer en Ctrl+klik naar een nieuw tabblad.
    for root in auth.PAGE_ROOTS:
        app.add_url_rule(f"/{root}", f"page_{root}", index)
        app.add_url_rule(f"/{root}/<path:rest>", f"page_{root}_rest", index)

    @app.get("/favicon.ico")
    async def favicon():
        # Browsers vragen /favicon.ico op zonder <link>; dit blijft zonder inloggen bereikbaar.
        return await send_from_directory(
            Path(app.static_folder) / "brand", "favicon.ico", mimetype="image/x-icon"
        )

    @app.get("/healthz")
    async def healthz():
        # assetVersion: een tabblad dat al open stond, vergelijkt hem met zijn eigen versie (router.js, routerStale).
        return jsonify({"ok": True, "assetVersion": app.config["ASSET_VERSION"]})

    @app.get("/api/about")
    async def about_info():
        return jsonify(await about.about())

    # Alleen de beheerder: /api/update staat in auth.ADMIN_PREFIXES.
    @app.get("/api/update")
    async def update_status():
        return jsonify(await updates.status())

    @app.post("/api/update/check")
    async def update_check():
        return jsonify(await updates.status(force=True))

    @app.get("/api/mealplan")
    async def mealplan():
        start = request.args.get("start")
        end = request.args.get("end")
        if not start or not end:
            return jsonify({"error": tr("start en end parameters zijn verplicht", "The start and end parameters are required")}), 400

        try:
            return jsonify(await mealie.get_meal_plan_with_recipes(start, end))
        except Exception as exc:
            app.logger.exception("Mealplan error")
            return jsonify({"error": str(exc) or tr("Fout bij ophalen weekmenu", "Could not load the week menu")}), 424

    # Het weekmenu plannen: elke wijziging gaat meteen naar Mealie (zie mealie.py). Iedereen die is ingelogd mag dit, net als
    # recepten importeren; daarom staan deze paden niet in auth.ADMIN_PREFIXES.
    async def mealie_action(action: Callable[[], Awaitable[Any]], error_text: str, status: int = 200) -> tuple[Response, int]:
        try:
            return jsonify(await action()), status
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001 - Mealie is een externe dienst; toon zijn fout
            app.logger.exception("Mealie error")
            return jsonify({"error": str(exc) or error_text}), 424

    async def reused_recipe(source_url: str) -> Any:
        """Bij `?reuse=1` (Inplannen): staat het recept al in Mealie (zelfde bron), dan dat recept in plaats van een nieuwe import."""
        if (request.args.get("reuse") or "").strip().lower() not in {"1", "true", "yes"}:
            return None
        slug = await mealie.find_recipe_by_source(source_url)
        return jsonify({"slug": slug, "mealieUrl": await mealie.recipe_page_url(slug), "reused": True}) if slug else None

    @app.get("/api/mealplan/options")
    async def mealplan_options():
        async def options() -> dict[str, Any]:
            # `recipeScale`: of het venster het aantal porties mag laten kiezen (Mealie 3.29 of nieuwer).
            return {"entryTypes": await mealie.entry_types(), "recipeScale": await mealie.supports_recipe_scale()}

        return await mealie_action(options, tr("Mealie niet bereikbaar", "Mealie is not reachable"))

    @app.post("/api/mealplan")
    async def mealplan_create():
        body = await request_body.json_object()
        return await mealie_action(lambda: mealie.create_meal_plan_entry(body), tr("Plannen mislukt", "Planning failed"), 201)

    @app.post("/api/mealplan/random")
    async def mealplan_random():
        body = await request_body.json_object()
        return await mealie_action(lambda: mealie.random_meal_plan_entry(body), tr("Verrassen mislukt", "Surprise failed"), 201)

    @app.patch("/api/mealplan/<int:entry_id>")
    async def mealplan_update(entry_id: int):
        body = await request_body.json_object()
        return await mealie_action(lambda: mealie.update_meal_plan_entry(entry_id, body), tr("Wijzigen mislukt", "Changing failed"))

    @app.delete("/api/mealplan/<int:entry_id>")
    async def mealplan_delete(entry_id: int):
        return await mealie_action(lambda: mealie.delete_meal_plan_entry(entry_id), tr("Verwijderen mislukt", "Removing failed"))

    @app.get("/api/mealie/recipes")
    async def mealie_recipes_search():
        async def found() -> dict[str, Any]:
            # Met een grens op de calorieën (issue #283) zoekt de cache van recipe_nutrition.py, anders Mealie zelf.
            kcal_min, kcal_max = recipe_nutrition.kcal_range(request.args.get("kcalMin"), request.args.get("kcalMax"))
            if kcal_min is None and kcal_max is None:
                return await mealie.search_recipes(request.args.get("q"), request.args.get("page") or 1)
            return await recipe_nutrition.search(request.args.get("q"), kcal_min, kcal_max, request.args.get("page") or 1)

        return await mealie_action(found, tr("Zoeken in Mealie mislukt", "Searching Mealie failed"))

    # Voedingswaarden van oude AH-recepten in Mealie aanvullen (alleen de beheerder: het pad staat in auth.ADMIN_PATHS).
    @app.get("/api/mealie/nutrition/backfill")
    async def mealie_nutrition_backfill_status():
        return jsonify(recipe_nutrition.backfill_progress())

    @app.post("/api/mealie/nutrition/backfill")
    async def mealie_nutrition_backfill_start():
        async def start() -> dict[str, Any]:
            await mealie.ensure_configured()
            return recipe_nutrition.start_backfill()

        if recipe_nutrition.backfill_progress().get("state") == "running":
            return jsonify({"error": tr("Het aanvullen loopt al.", "Filling in is already running.")}), 409
        return await mealie_action(start, tr("Aanvullen starten mislukt", "Could not start filling in"), 202)

    @app.delete("/api/mealie/nutrition/backfill")
    async def mealie_nutrition_backfill_stop():
        return jsonify(recipe_nutrition.stop_backfill())

    @app.get("/api/mealie/recipes/<slug>")
    async def mealie_recipe_detail(slug: str):
        return await mealie_action(lambda: mealie.recipe_detail(slug), tr("Recept ophalen mislukt", "Could not fetch the recipe"))

    # Recepten van AH en Jumbo in één keer in Mealie zetten (zie recipe_bulk.py). Een selectie mag iedereen die ook één
    # recept mag importeren; een hele groep of lijst alleen de beheerder (dat pad staat in auth.ADMIN_PATHS).
    async def bulk_action(action: Callable[[], Awaitable[Any]], status: int = 200) -> tuple[Response, int]:
        try:
            return jsonify(await action()), status
        except recipe_bulk.Busy as exc:
            return jsonify({"error": str(exc), "job": recipe_bulk.progress()}), 409
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except ValueError as exc:  # onze eigen tekst: wat er mis is met de keuze, of wat er nog gekoppeld moet worden
            return jsonify({"error": str(exc)}), 400
        except Exception:  # noqa: BLE001 - de tekst kan het antwoord van een dienst bevatten: alleen in het logboek
            app.logger.exception("Bulk import error")
            return jsonify({"error": tr("De import starten is mislukt", "Starting the import failed")}), 424

    async def bulk_user() -> dict[str, Any] | None:
        return await auth.current_user() if await auth.auth_required() else None

    async def bulk_start(whole: bool) -> tuple[Response, int]:
        body = await request_body.json_object()

        async def start() -> Any:
            # Binnen bulk_action: een fout bij het opzoeken van de gebruiker geeft dezelfde 424 als een fout bij het starten.
            return await recipe_bulk.start(
                body.get("store"), body.get("ids"), body.get("group"), whole=whole, titles=body.get("titles"), user=await bulk_user()
            )

        return await bulk_action(start, 202)

    @app.get("/api/mealie/bulk-import")
    async def bulk_import_status():
        return jsonify(recipe_bulk.progress())

    @app.post("/api/mealie/bulk-import")
    async def bulk_import_selection():
        return await bulk_start(False)

    @app.post("/api/mealie/bulk-import/group")
    async def bulk_import_group():
        return await bulk_start(True)

    @app.post("/api/mealie/bulk-import/cancel")
    async def bulk_import_cancel():
        async def cancel() -> Any:
            return recipe_bulk.cancel(await bulk_user(), await auth.is_owner())

        return await bulk_action(cancel)

    @app.get("/api/ah/search")
    async def ah_search():
        query = (request.args.get("q") or "").strip()
        page = int(request.args.get("page") or 0)
        if not query:
            return jsonify({"error": tr("Zoekterm ontbreekt", "Search term is missing")}), 400

        try:
            return jsonify(await ah.search_recipes(query, page))
        except Exception as exc:
            app.logger.exception("AH search error")
            return jsonify({"error": str(exc) or tr("Zoeken mislukt", "Search failed")}), 424

    @app.get("/api/ah/recipes/saved")
    async def ah_saved_recipes():
        # Uit de lokale kopie; ?refresh=1 haalt ze opnieuw bij AH op, ?cached=1 vraagt nooit aan AH.
        def flag(name: str) -> bool:
            return (request.args.get(name) or "").strip().lower() in {"1", "true", "yes"}

        try:
            return jsonify(await ah_recipes.get(force=flag("refresh"), fetch_if_missing=not flag("cached")))
        except Exception as exc:
            app.logger.exception("AH saved recipes error")
            return jsonify({"error": str(exc) or tr("Bewaarde recepten ophalen mislukt", "Could not fetch saved recipes")}), 424

    # De weekplanner van de AH-app (zie ah_planner.py): alleen lezen bij AH. *Naar weekmenu* zet de Allerhande-recepten en
    # de losse producten (als bestaand recept, eigen recept of menu) in het weekmenu van Mealie. Iedereen die is ingelogd mag
    # dat, net als plannen en importeren; daarom staan deze paden niet in auth.ADMIN_PREFIXES. Het scherm stuurt een dag per keer.
    @app.get("/api/ah/planner")
    async def ah_week_planner():
        try:
            return jsonify(await ah_planner.get())
        except LookupError as exc:  # AH is niet gekoppeld: de tekst is van onszelf en zegt wat te doen
            return jsonify({"error": str(exc)}), 424
        except Exception:  # noqa: BLE001 - de fout kan het antwoord van AH bevatten: alleen in het logboek
            app.logger.exception("AH week planner error")
            return jsonify({"error": tr("De weekplanner ophalen bij AH is mislukt", "Could not fetch the week planner from AH")}), 424

    @app.post("/api/ah/planner/to-meal-plan")
    async def ah_week_planner_to_meal_plan():
        try:
            await mealie.ensure_configured()
        except RuntimeError as exc:  # Mealie is niet gekoppeld: de tekst is van onszelf en zegt wat te doen
            return jsonify({"error": str(exc)}), 400
        try:
            day = ah_planner.clean_day(await request.get_json(silent=True))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        try:
            return jsonify(await ah_planner.to_meal_plan(day))
        except Exception:  # noqa: BLE001 - het weekmenu van Mealie niet te lezen: de melding van Mealie alleen in het logboek
            app.logger.exception("AH week planner to meal plan error")
            return jsonify({"error": tr("Het weekmenu van Mealie lezen is mislukt", "Could not read the Mealie week menu")}), 424

    # Een Allerhande-recept bekijken en in Mealie zetten, met dezelfde routes als bij Jumbo, Picnic en de maaltijdboxen.
    @app.get("/api/ah/recipes/<int:recipe_id>")
    async def ah_recipe_detail(recipe_id: int):
        try:
            return jsonify(await ah.recipe_detail(recipe_id))
        except Exception:  # noqa: BLE001 - de fout kan het antwoord van AH bevatten: alleen in het logboek
            app.logger.exception("AH recipe error")
            return jsonify({"error": tr("Het recept ophalen bij AH is mislukt", "Could not fetch the recipe from AH")}), 424

    @app.post("/api/ah/recipes/<int:recipe_id>/import")
    async def ah_recipe_import(recipe_id: int):
        try:
            await mealie.ensure_configured()
        except RuntimeError as exc:  # Mealie is niet gekoppeld: de tekst is van onszelf en zegt wat te doen
            return jsonify({"error": str(exc)}), 400
        url = ah.recipe_url(recipe_id)
        found = await reused_recipe(url)
        if found:
            return found
        try:
            slug = await mealie.import_from_url(url)
            return jsonify({"slug": slug, "mealieUrl": await mealie.recipe_page_url(slug)})
        except Exception:  # noqa: BLE001 - de melding van Mealie of AH kan hun antwoord bevatten: alleen in het logboek
            app.logger.exception("AH recipe import error")
            return jsonify({"error": tr("Importeren in Mealie mislukt", "Importing into Mealie failed")}), 424

    @app.get("/api/mealie/recipes/<recipe_id>/image")
    async def mealie_recipe_image(recipe_id: str):
        if not mealie_images.valid_recipe_id(recipe_id):
            return jsonify({"error": tr("Ongeldig recept", "Invalid recipe")}), 400
        version = (request.args.get("v") or "")[:64]
        try:
            content_type, data = await mealie_images.get_image(
                recipe_id, request.args.get("size") or mealie_images.DEFAULT_SIZE, version
            )
        except mealie_images.ImageNotFound:
            return jsonify({"error": tr("Dit recept heeft geen afbeelding", "This recipe has no image")}), 404
        except Exception as exc:
            app.logger.exception("Mealie image error")
            return jsonify({"error": str(exc) or tr("Afbeelding ophalen mislukt", "Could not fetch the image")}), 424
        response = Response(data, mimetype=content_type)
        # Met ?v= (de afbeeldingsversie van Mealie) verandert het adres als het plaatje verandert.
        response.headers["Cache-Control"] = (
            "private, max-age=31536000, immutable" if version else "private, max-age=3600"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/api/ah/auth/status")
    async def ah_auth_status():
        try:
            verify = (request.args.get("verify") or "").strip().lower() in {"1", "true", "yes"}
            return jsonify(await ah.auth_status(verify=verify))
        except Exception as exc:
            app.logger.exception("AH auth status error")
            return jsonify({"connected": False, "error": str(exc)}), 424

    @app.get("/api/ah/favorite-lists")
    async def ah_favorite_lists():
        try:
            return jsonify({"lists": await ah.get_favorite_lists()})
        except Exception as exc:
            app.logger.exception("AH favorite lists error")
            return jsonify({"error": str(exc) or tr("AH lijstjes ophalen mislukt", "Could not fetch AH lists")}), 424

    @app.get("/api/ah/favorite-lists/<list_id>/items")
    async def ah_favorite_list_items(list_id: str):
        try:
            return jsonify(await ah.get_favorite_list_items(list_id))
        except Exception as exc:
            app.logger.exception("AH favorite list items error")
            return jsonify({"error": str(exc) or tr("AH lijstinhoud ophalen mislukt", "Could not fetch the contents of the AH list")}), 424

    @app.get("/api/ah/lists")
    async def ah_lists_overview():
        # Altijd 200: lokale kopieen blijven zichtbaar ook als AH niet bereikbaar is.
        return jsonify(await ah_lists.merged_lists())

    @app.get("/api/ah/lists/search")
    async def ah_lists_search():
        # Alleen de lokale kopieen: snel genoeg om bij elke toetsaanslag te vragen.
        query = (request.args.get("q") or "").strip()
        return jsonify({"query": query, "items": await ah_lists.search_items(query)})

    @app.get("/api/ah/lists/<list_id>")
    async def ah_list_detail(list_id: str):
        snapshot = await ah_lists.get_snapshot(list_id)
        if snapshot:
            return jsonify(snapshot)
        try:
            return jsonify({**await ah.get_favorite_list_items(list_id), "source": "ah"})
        except Exception as exc:
            app.logger.exception("AH list detail error")
            return jsonify({"error": str(exc) or tr("AH lijstinhoud ophalen mislukt", "Could not fetch the contents of the AH list")}), 424

    @app.post("/api/ah/lists/<list_id>/sync")
    async def ah_list_sync(list_id: str):
        try:
            return jsonify(await ah_lists.sync_list(list_id))
        except Exception as exc:
            app.logger.exception("AH list sync error")
            return jsonify({"error": str(exc) or tr("Synchroniseren mislukt", "Sync failed")}), 424

    @app.patch("/api/ah/lists/<list_id>/items/<int:position>")
    async def ah_list_item_quantity(list_id: str, position: int):
        body = await request_body.json_object()
        try:
            snapshot = await ah_lists.set_quantity(list_id, position, body.get("quantity"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if not snapshot:
            return jsonify({"error": tr("Regel niet gevonden. Synchroniseer het lijstje eerst.", "Line not found. Sync the list first.")}), 404
        return jsonify(snapshot)

    @app.post("/api/ah/lists/<list_id>/reset")
    async def ah_list_reset(list_id: str):
        snapshot = await ah_lists.reset_quantities(list_id)
        if not snapshot:
            return jsonify({"error": tr("Dit lijstje is niet gesynchroniseerd.", "This list is not synced.")}), 404
        return jsonify(snapshot)

    @app.post("/api/ah/lists/<list_id>/cart")
    async def ah_list_to_cart(list_id: str):
        """Zet het hele lijstje met een druk op de knop in het AH-mandje, met de aantallen zoals ze nu staan."""
        snapshot = await ah_lists.get_snapshot(list_id)
        try:
            if not snapshot:
                snapshot = await ah.get_favorite_list_items(list_id)
            items = ah_lists.cart_items_from(snapshot)
            if not items:
                return jsonify({"error": tr("Dit lijstje heeft geen producten.", "This list has no products.")}), 400
            await ah.add_to_shopping_list(items)
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

    async def ah_sync(action: Callable[[], Awaitable[Any]], error_text: str) -> Response | tuple[Response, int]:
        try:
            return jsonify(await action())
        except Exception as exc:  # noqa: BLE001 - AH is onofficieel; toon de fout, de bewaarde kopie blijft staan
            app.logger.exception("AH order sync error")
            return jsonify({"error": str(exc) or error_text}), 424

    @app.get("/api/ah/basket")
    async def ah_basket():
        return jsonify(await ah_orders.get_basket())

    @app.post("/api/ah/basket/sync")
    async def ah_basket_sync():
        return await ah_sync(ah_orders.sync_basket, tr("Mandje ophalen mislukt", "Fetching the basket failed"))

    @app.get("/api/ah/orders")
    async def ah_orders_list():
        return jsonify(await ah_orders.list_orders(summary=True))

    @app.get("/api/ah/orders/<int:order_id>")
    async def ah_order_detail(order_id: int):
        order = await ah_orders.get_order(order_id)
        if not order:
            return jsonify({"error": tr("Bestelling niet gevonden.", "Order not found.")}), 404
        return jsonify(order)

    @app.post("/api/ah/orders/sync")
    async def ah_orders_sync():
        return await ah_sync(ah_orders.sync_orders, tr("Bestellingen ophalen mislukt", "Fetching the orders failed"))

    @app.get("/api/ah/receipts")
    async def ah_receipts_list():
        await ah_orders.backfill_receipt_images_async()  # ontbreken er nog foto's bij gekoppelde bonregels, dan komen er nu wat bij
        return jsonify(await ah_orders.list_receipts(summary=True))

    @app.get("/api/ah/receipts/<receipt_id>")
    async def ah_receipt_detail(receipt_id: str):
        receipt = await ah_orders.get_receipt(receipt_id)
        if not receipt:
            return jsonify({"error": tr("Kassabon niet gevonden", "Receipt not found")}), 404
        return jsonify(receipt)

    @app.post("/api/ah/receipts/<receipt_id>/products")
    async def ah_receipt_products(receipt_id: str):
        # Bij het openen van een bon: de regels die nog bij geen webwinkelproduct horen opzoeken bij AH (zuinig, zie
        # ah_orders.resolve_receipt_names), de foto's van de gekoppelde regels die er nog geen hebben erbij halen, en de bon met
        # de koppelingen teruggeven. Mislukt het opzoeken, dan komt de bon toch.
        receipt = await ah_orders.get_receipt(receipt_id, lookup=lambda names: ah_orders.resolve_receipt_names(names, searches=ah_orders.RECEIPT_PRODUCTS_OPEN_SEARCHES, trigger="open"), images=True)
        if not receipt:
            return jsonify({"error": tr("Kassabon niet gevonden.", "Receipt not found.")}), 404
        return jsonify(receipt)

    @app.get("/api/ah/linking")
    async def ah_linking_status():
        # De toestand van het koppelen van bonregels en foto's: de pauze na een 403 of 429 van AH, het uurbudget, wat er nog open
        # staat en de volgende ronde (ah_orders.linking_status). Voor iedereen: de melding op de bestelpagina leest het.
        return jsonify(await ah_orders.linking_status())

    @app.post("/api/ah/linking/run")
    async def ah_linking_run():
        # Een ronde op afroep (de knop "Nu ophalen"), als achtergrondtask binnen het tempo van AH: voor een bon (receiptId) of voor alle
        # bonnen. Met images alleen de foto's van alle bonnen, zonder zoekopdrachten (de knop "Foto's aanvullen"). Samen met een
        # receiptId weigeren we dat. Met force heft de beheerder eerst de pauze na een 403 of 429 op. Iedereen mag een ronde starten.
        # Een lege aanvraag is een ronde voor alle bonnen. Een body die er wel is maar geen JSON-object is, weigeren we: anders start
        # een verminkte aanvraag de grote ronde.
        body = await request.get_json(silent=True) if await request.get_data(cache=True) else {}
        receipt_id = body.get("receiptId") if isinstance(body, dict) else None
        force = body.get("force", False) if isinstance(body, dict) else False
        images = body.get("images", False) if isinstance(body, dict) else False
        if not isinstance(body, dict) or not isinstance(receipt_id, (str, type(None))) or not isinstance(force, bool) or not isinstance(images, bool):
            return jsonify({"error": tr("Ongeldige aanvraag: receiptId is tekst, force en images zijn waar of onwaar.", "Invalid request: receiptId is text, force and images are true or false.")}), 400
        receipt_id = (receipt_id or "").strip() or None
        if images and receipt_id:
            return jsonify({"error": tr("Ongeldige aanvraag: foto's aanvullen gaat over alle bonnen, niet over één bon.", "Invalid request: filling in photos covers all receipts, not one receipt.")}), 400
        if force and not await auth.is_owner():
            return jsonify({"error": tr("Alleen de beheerder kan de pauze van AH opheffen.", "Only the administrator can lift the AH pause.")}), 403
        if receipt_id and not await ah_orders.receipt_exists(receipt_id):
            return jsonify({"error": tr("Kassabon niet gevonden.", "Receipt not found.")}), 404
        try:
            return jsonify(await ah_orders.run_linking(receipt_id, force=force, images=images)), 202
        except ah_orders.LinkingBusy:
            return jsonify(await ah_orders.linking_status()), 409

    @app.post("/api/ah/receipts/sync")
    async def ah_receipts_sync():
        return await ah_sync(ah_orders.sync_receipts, tr("Kassabonnen ophalen mislukt", "Fetching the receipts failed"))

    def search_limit(default: int) -> int:
        # De aankopentabel vraagt alles (limit=all) en filtert zelf; zonder limit blijft het de korte lijst.
        value = (request.args.get("limit") or "").strip().lower()
        if value == "all":
            return 100000
        return max(1, min(int(value), 100000)) if value.isdigit() else default

    @app.get("/api/ah/receipts/search")
    async def ah_receipts_search():
        return jsonify(await ah_orders.search_receipts(request.args.get("q") or "", limit=search_limit(200)))

    @app.get("/api/ah/purchases/search")
    async def ah_purchases_search():
        return jsonify(await ah_orders.search_purchases(request.args.get("q") or "", limit=search_limit(50)))

    @app.post("/api/ah/explore")
    async def ah_explore_routes():
        # Alleen de beheerder (auth.ADMIN_PATHS): roept leesroutes aan en meldt de vorm van de antwoorden.
        return jsonify(await ah_explore.explore())

    # --- aanbiedingen en receptvoorstellen: lezen uit de lokale kopie, vernieuwen op verzoek ----------------

    offers_cache = ResponseCache()

    async def offers_action(action: Callable[[], Any], error_text: str) -> Response | tuple[Response, int]:
        try:
            result: Any = action()
            if inspect.isawaitable(result):
                result = await result
            return jsonify(result)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001 - winkels zijn onofficieel; toon de fout, de bewaarde kopie blijft staan
            app.logger.exception("Offers error")
            return jsonify({"error": str(exc) or error_text}), 424

    def offers_week() -> str:
        """De week van de aanbiedingen: `?week=` (een datum in de week die het weekmenu toont), anders de week van vandaag."""
        return request.args.get("week") or date.today().isoformat()

    async def offers_etag(store: str, week: str) -> str:
        """De ETag van de lijst: de synchronisatie, de dag, de week, de build en de catalogus van de prijsgeschiedenis."""
        return hashlib.sha1(f"{await offers.signature(store, week)}|{await prices.signature(store)}".encode("utf-8")).hexdigest()

    async def offers_with_verdicts(store: str, week: str) -> dict[str, Any]:
        data = await offers.load(store, week)
        await prices.annotate(data["store"], data["offers"])  # het oordeel bij elke aanbieding (#178), een fout laat de lijst staan
        data["packMargin"] = prices.PACK_MARGIN  # de client vergelijkt verpakkingen per eenheid met dezelfde marge
        return data

    async def warm_offers() -> None:
        """Rekent de lijst met oordeel van elke winkel alvast uit voor deze week en de volgende, in de cache van /api/offers. Na een
        synchronisatie (offers_schedule) en bij de start, zodat de eerste bezoeker er niet op wacht. Een fout bij een winkel laat de
        andere met rust."""
        today = date.today()
        for store in offers.STORES:
            for week in (today.isoformat(), (today + timedelta(days=7)).isoformat()):
                try:
                    etag = await offers_etag(store, week)
                    await offers_cache.get((store, week), etag, lambda: offers_with_verdicts(store, week))
                except Exception:  # noqa: BLE001 - opwarmen is een extraatje
                    app.logger.warning("Aanbiedingen van %s opwarmen mislukt", store, exc_info=True)

    app.extensions["mealieshopper_warm_offers"] = warm_offers
    offers_schedule.set_warmer(warm_offers)

    async def warm_at_start() -> None:
        await prices.warm_catalogues()
        await warm_offers()

    @app.get("/api/offers")
    async def offers_list():
        # Cache: de browser bewaart de lijst en vraagt bij elke keer alleen of er iets veranderd is (ETag). Is de lijst niet veranderd
        # (geen nieuwe synchronisatie, nog dezelfde dag, dezelfde week), dan volgt een 304 zonder de lijst te laden of te versturen.
        store = request.args.get("store") or "ah"
        week = offers_week()
        try:
            etag = await offers_etag(store, week)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        headers = {"ETag": f'"{etag}"', "Cache-Control": "private, no-cache"}
        if request.if_none_match.contains(etag):
            return Response(status=304, headers=headers)

        # Dezelfde ETag is dezelfde lijst: een tweede browser of apparaat, en aanvragen die tegelijk binnenkomen, krijgen de lijst die
        # er al ligt of op komst is, in plaats van het prijsoordeel opnieuw uit te rekenen (response_cache.py).
        response = await offers_action(
            lambda: offers_cache.get((store, week), etag, lambda: offers_with_verdicts(store, week)), tr("Aanbiedingen laden mislukt", "Loading the offers failed")
        )
        if isinstance(response, Response) and response.status_code == 200:
            response.headers.update(headers)
        return response

    @app.get("/api/offers/history")
    async def offers_history():
        return await offers_action(
            lambda: offers.history(request.args.get("store") or None, request.args.get("q") or "", request.args.get("limit") or 50),
            tr("Geschiedenis van de aanbiedingen laden mislukt", "Loading the offers history failed"),
        )

    @app.get("/api/prices/history")
    async def prices_history():
        # ?bought=1: alleen de producten die je minstens één keer kocht. ?pin=<id>: dat product staat er altijd in (een link van een
        # aanbieding of bon naar de prijsgeschiedenis), ?product=<id>: alleen dat product (de hover bij een aanbieding).
        bought = (request.args.get("bought") or "").strip().lower() in {"1", "true", "yes"}
        return await offers_action(
            # Namen op de bon die bij de zoekterm passen en nog bij geen product horen, zoekt dit meteen op bij AH (zuinig: zie
            # ah_orders.resolve_receipt_names).
            lambda: prices.history(request.args.get("store") or None, request.args.get("q") or "", request.args.get("limit") or 50, bought=bought,
                                   lookup=ah_orders.resolve_receipt_names, product=request.args.get("product") or None,
                                   pin=request.args.get("pin") or None),
            tr("Prijsgeschiedenis laden mislukt", "Loading the price history failed"),
        )

    @app.get("/api/prices/images/<int:image_id>")
    async def prices_image(image_id: int):
        # Een bewaarde afbeelding van een aanbieding die je eerder kocht (zie product_images.py); uit de database, nooit van de winkel.
        found = await product_images.get(image_id)
        if not found:
            return jsonify({"error": tr("Afbeelding niet gevonden", "Image not found")}), 404
        content_type, data = found
        response = Response(data, mimetype=content_type)
        # Met ?v= (een stuk van de SHA-256 van de afbeelding) verandert het adres als de inhoud verandert.
        response.headers["Cache-Control"] = "private, max-age=31536000, immutable" if request.args.get("v") else "private, max-age=3600"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/api/images/<token>")
    async def cached_image(token: str):
        # Een productfoto van een winkel uit de eigen cache (image_cache.py): het token is het winkeladres in base64. Staat de foto
        # er nog niet, dan haalt de server hem nu op (zoals een browser dat zou doen) en bewaart hem voor altijd. Een onbekende
        # host, een host in pauze na een 403 of een mislukking geven 404; de <img> verbergt zich dan (data-hide-on-error).
        url = image_cache.url_for(token)
        found = await image_cache.fetch(url) if url else None
        if not found:
            return jsonify({"error": tr("Afbeelding niet gevonden", "Image not found")}), 404
        # Quart kent geen etag= bij send_file: de ETag is de digest, en de 304 bij If-None-Match volgt daarna uit make_conditional.
        response = await send_file(image_cache.images_dir() / found["path"], mimetype=found["content_type"], add_etags=False, cache_timeout=31536000)
        response.set_etag(found["digest"])
        await response.make_conditional(request, accept_ranges=True, complete_length=response.content_length)
        response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/api/system/images")
    async def cached_images_status():
        # Voor de regel onder Instellingen > Systeem: hoeveel winkelfoto's er in de cache staan (image_cache.py).
        return jsonify(await image_cache.stats())

    # Oude aankopen inlezen uit bevestigingsmails (zie price_import.py). Alleen de beheerder: /api/prices/import staat in
    # auth.ADMIN_PREFIXES, want het schrijft (en wist) gegevens van het hele huishouden.
    async def import_action(action: Callable[[], Awaitable[Any]]) -> Any:
        try:
            return jsonify(await action())
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/prices/import")
    async def prices_import_summary():
        return await import_action(lambda: price_import.summary(request.args.get("store") or "ah"))

    @app.post("/api/prices/import")
    async def prices_import_run():
        body = await request_body.json_object()
        return await import_action(lambda: price_import.run(body.get("data"), body.get("store") or "ah", dry_run=bool(body.get("dryRun"))))

    @app.delete("/api/prices/import")
    async def prices_import_remove():
        # Met ?ref= alleen die ene ingelezen bestelling (de prullenbak bij Bestellingen), zonder alle van de winkel.
        return await import_action(lambda: price_import.remove(request.args.get("store") or "ah", request.args.get("ref")))

    # De ingelezen bestellingen bij Bestellingen, naast die uit je account. Lezen mag iedereen, net als de bestellingen zelf;
    # importeren en verwijderen gaan via /api/prices/import en zijn van de beheerder.
    @app.get("/api/imported-orders/<store>")
    async def imported_orders_list(store: str):
        return await import_action(lambda: price_import.list_orders(store, summary=True))

    @app.get("/api/imported-orders/<store>/<path:ref>")
    async def imported_order_detail(store: str, ref: str):
        try:
            order = await price_import.get_order(store, ref)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if not order:
            return jsonify({"error": tr("Bestelling niet gevonden", "Order not found")}), 404
        return jsonify(order)

    # Je eigen koppelingen (#238, zie product_links.py): een regel op een kassabon of in een ingelezen bestelling die de app niet
    # (of verkeerd) herkende, koppel je zelf aan een product van de winkel. Iedereen die de bon of bestelling ziet, mag koppelen en
    # ontkoppelen: het is een keuze die je weer terugdraait, net als een ander product in de controlelijst.
    @app.get("/api/product-links")
    async def product_links_list():
        return await import_action(lambda: product_links.list_links(request.args.get("store") or None))

    @app.get("/api/product-links/search")
    async def product_links_search():
        # Doet één zoekopdracht bij de winkel, in dezelfde vorm voor elke winkel (prices.link_candidates).
        try:
            return jsonify(await prices.link_candidates(request.args.get("store"), request.args.get("q") or ""))
        except ValueError as exc:  # een onbekende winkel, of een winkel met alleen aanbiedingen (Lidl, Aldi): de tekst is van onszelf
            return jsonify({"error": str(exc)}), 400
        except Exception:  # noqa: BLE001 - de winkel gaf een fout: niet gekoppeld, of niet bereikbaar
            app.logger.exception("Zoeken om te koppelen mislukt")
            return jsonify({"error": tr("Zoeken mislukt", "Search failed")}), 424

    @app.put("/api/product-links")
    async def product_links_save():
        body = await request_body.json_object()

        async def save() -> dict[str, Any]:
            return {"link": await product_links.save(body.get("store"), body.get("name"), body.get("product"), body.get("article"))}

        return await import_action(save)

    @app.delete("/api/product-links/<store>/<kind>/<path:key>")
    async def product_links_remove(store: str, kind: str, key: str):
        async def remove() -> dict[str, Any]:
            return {"removed": await product_links.remove(store, kind, key)}

        return await import_action(remove)

    @app.get("/api/prices/compare")
    async def prices_compare():
        # Doet één zoekopdracht bij de winkel: andere verpakkingen van hetzelfde product, per kilo, liter of stuk naast elkaar.
        return await offers_action(
            lambda: prices.compare(request.args.get("store"), request.args.get("id")),
            tr("Verpakkingen vergelijken mislukt", "Comparing pack sizes failed"),
        )

    @app.post("/api/offers/sync")
    async def offers_sync():
        body = await request_body.json_object()
        return await offers_action(lambda: offers.sync(body.get("store") or "ah"), tr("Aanbiedingen ophalen mislukt", "Fetching the offers failed"))

    @app.get("/api/offers/status")
    async def offers_status():
        return await offers_action(offers_schedule.status, tr("Status van de aanbiedingen laden mislukt", "Loading the offers status failed"))

    @app.post("/api/offers/sync-all")
    async def offers_sync_all():
        # Alleen de beheerder (auth.ADMIN_PATHS): de knop onder Instellingen > Koppelen.
        return await offers_action(offers_schedule.sync_now, tr("Aanbiedingen ophalen mislukt", "Fetching the offers failed"))

    @app.get("/api/offers/recipes")
    async def offers_recipes():
        return await offers_action(
            lambda: offers.suggest(request.args.get("store") or "ah", course=request.args.get("course"), week=offers_week()),
            tr("Receptvoorstellen laden mislukt", "Loading the recipe suggestions failed"),
        )

    @app.route("/api/offers/rejections", methods=["POST", "DELETE"])
    async def offers_rejections():
        # Een koppeling als onjuist melden (POST) of de melding weer intrekken (DELETE).
        body = await request_body.json_object()
        action = offers.report_mismatch if request.method == "POST" else offers.undo_mismatch
        return await offers_action(
            lambda: action(body.get("store") or "ah", body.get("ingredient"), body.get("offerId")),
            tr("Koppeling melden mislukt", "Reporting the match failed"),
        )

    # --- AI-beoordeling van de koppelingen (optioneel, alleen de beheerder: auth.ADMIN_PATHS) -------------

    async def ai_status() -> dict[str, Any]:
        data: dict[str, Any] = await ai_review.status()
        # Het tabblad Recepten bij Aanbiedingen is zichtbaar met de omgevingsvariabele of zodra er een AI-sleutel is.
        data["recipesTab"] = bool(app.config["OFFERS_RECIPES"] or data["active"])
        data["search"] = ai_match.progress()  # de stand van een lopende ronde "Recepten zoeken met AI", ook na een herlading van de pagina
        return data

    @app.get("/api/offers/ai")
    async def offers_ai_status():
        return await offers_action(ai_status, tr("AI-instellingen laden mislukt", "Loading the AI settings failed"))

    @app.put("/api/offers/ai")
    async def offers_ai_save():
        body = await request_body.json_object()
        async def save_and_report() -> dict[str, Any]:
            await ai_review.save(body)
            return await ai_status()

        return await offers_action(save_and_report, tr("AI-instellingen opslaan mislukt", "Saving the AI settings failed"))

    @app.post("/api/offers/ai/test")
    async def offers_ai_test():
        body = await request_body.json_object()
        return await offers_action(lambda: ai_review.test(body.get("provider")), tr("De AI testen mislukt", "Testing the AI failed"))

    @app.post("/api/offers/ai/review")
    async def offers_ai_review():
        body = await request_body.json_object()
        return await offers_action(
            lambda: ai_review.review(body.get("store") or "ah", body.get("max") or ai_review.DEFAULT_MAX_PAIRS),
            tr("Beoordelen met AI mislukt", "Reviewing with AI failed"),
        )

    @app.get("/api/offers/ai/search/plan")
    async def offers_ai_search_plan():
        return await offers_action(
            lambda: ai_match.plan(request.args.get("store") or "ah", request.args.get("max") or ai_match.DEFAULT_MAX_RECIPES),
            tr("De omvang van het zoeken bepalen mislukt", "Determining the size of the search failed"),
        )

    @app.post("/api/offers/ai/search")
    async def offers_ai_search():
        body = await request_body.json_object()
        return await offers_action(
            lambda: ai_match.start(body.get("store") or "ah", body.get("max") or ai_match.DEFAULT_MAX_RECIPES),
            tr("Recepten zoeken met AI mislukt", "Searching recipes with AI failed"),
        )

    @app.get("/api/offers/ai/search/status")
    async def offers_ai_search_status():
        return await offers_action(ai_match.progress, tr("De stand van het zoeken laden mislukt", "Loading the state of the search failed"))

    @app.post("/api/offers/recipes/sync")
    async def offers_recipes_sync():
        return await offers_action(offers.sync_recipes, tr("Recepten indexeren mislukt", "Indexing the recipes failed"))

    @app.get("/api/mealie/status")
    async def mealie_status():
        verify = (request.args.get("verify") or "").strip().lower() in {"1", "true", "yes"}
        return jsonify(await mealie.status(verify=verify))

    @app.post("/api/mealie/auth")
    async def mealie_auth():
        body = await request_body.json_object()
        try:
            return jsonify(await mealie.link(body.get("url"), body.get("token")))
        except (ValueError, RuntimeError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/mealie/auth/logout")
    async def mealie_auth_logout():
        return jsonify(await mealie.clear())

    @app.post("/api/mealie/import")
    async def import_recipe():
        body = await request_body.json_object()
        url = body.get("url")
        if not (isinstance(url, str) and url.strip()):
            return jsonify({"error": tr("URL ontbreekt", "URL is missing")}), 400
        # Alleen een openbaar adres: geen Mealie, NAS of router op het eigen netwerk (urls.py, dezelfde regel als de MCP-tool).
        try:
            url = urls.public_http_url(url)
        except urls.UnsafeUrl:
            return jsonify({"error": tr("Alleen een openbaar http(s)-adres kan worden geimporteerd.", "Only a public http(s) address can be imported.")}), 400

        try:
            slug = await mealie.import_from_url(url)
            return jsonify({"slug": slug, "mealieUrl": await mealie.recipe_page_url(slug)})
        except Exception as exc:
            app.logger.exception("Mealie import error")
            return jsonify({"error": str(exc) or tr("Import mislukt", "Import failed")}), 424

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
            "imageLarge": ah.product_photo(images),
            "isBonus": bool(product.get("isBonus")),
            "deal": deals.candidate_deal("ah", product),
            "url": ah.product_web_url(product["webshopId"]),
            "packs": packs,
            "doubt": doubt,
        }

    async def cart_preview(search: Any, make_candidate: Any) -> Any:
        """Zoek per ingredient de beste producten en reken het aantal verpakkingen uit.

        Een regel met `doubt` is er niet zeker van; de gebruiker kiest dan zelf. Gedeeld door
        alle supermarkten: `search(query, limit)` en `make_candidate(product, quantity, unit)`
        zijn de enige winkelspecifieke delen.
        """
        body = await request_body.json_object()
        items = body.get("items") or []
        if not items:
            return jsonify({"error": tr("Geen ingredienten opgegeven", "No ingredients given")}), 400
        # Het weekmenu in het mandje (`compare`): na het voorgestelde product alleen hetzelfde product in een andere maat of wat per
        # eenheid goedkoper is (#178). Zelf zoeken met "Zoek een ander product" laat de eerste vijf zoekresultaten zien, zoals voorheen.
        compare = bool(body.get("compare"))
        # *Liever biologisch* (#218): bij het weekmenu eerst een biologisch product van dezelfde soort, als de winkel het heeft.
        organic = compare and body.get("organic") is True

        async def resolve(item: dict[str, Any]) -> dict[str, Any]:
            query = str(item.get("query") or "")
            result = {
                "query": query,
                "display": item.get("display") or query,
                "quantity": item.get("quantity"),
                "unit": item.get("unit"),
                "candidates": [],
            }

            def candidates_of(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
                return [
                    {
                        **make_candidate(product, item.get("quantity"), item.get("unit")),
                        "organic": prices.is_organic(product),
                        "category": product.get("category"),  # alleen AH geeft een categorie (#223)
                    }
                    for product in products
                ]

            async def search_organic() -> list[dict[str, Any]]:
                try:
                    return candidates_of(await search(f"{prices.ORGANIC_QUERY} {query}", prices.COMPARE_LIMIT))
                except Exception:  # noqa: BLE001 - lukt de extra zoekopdracht niet, dan blijft het gewone product staan
                    return []

            try:
                products = await search(query, prices.COMPARE_LIMIT if compare else prices.CHECK_LIST_SIZE)
            except Exception as exc:  # noqa: BLE001 - een mislukte zoekopdracht geldt alleen voor deze regel
                result["error"] = str(exc)
                return result
            candidates = candidates_of(products)
            regular = candidates[0] if candidates else None  # het gewone voorstel bepaalt de soort, ook met Liever biologisch (#223)
            if organic and candidates:
                candidates, found = await prices.organic_first(candidates, search_organic, query)
                result["organic"] = True
                result["organicMissing"] = not found
            result["candidates"] = prices.check_list_candidates(candidates, compare, query, regular)
            return result

        # Hooguit zes regels tegelijk, zoals de zes threads van voorheen, in de volgorde van `items` en elk met een kopie van
        # de context (verzoek, taal, auditlog). Een fout buiten de `try` van `resolve` annuleert de andere regels: het
        # antwoord is dan toch een 500, dan hoeven hun zoekopdrachten niet meer door te lopen.
        resolved: list[dict[str, Any]] = await tasks.map_bounded(CART_PREVIEW_WORKERS, resolve, items)
        return jsonify({"items": resolved})

    @app.post("/api/ah/cart/preview")
    async def ah_cart_preview():
        return await cart_preview(prices.recorded("ah", ah.search_products), cart_candidate)

    @app.post("/api/ah/cart")
    async def ah_cart():
        body = await request_body.json_object()
        items = body.get("items") or []
        if not items:
            return jsonify({"error": tr("Geen ingredienten opgegeven", "No ingredients given")}), 400

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
                product = await ah.search_product(query)
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
                        "error": tr("Geen producten gevonden voor de opgegeven ingredienten", "No products found for the given ingredients"),
                        "diagnostics": diagnostics,
                    }
                ),
                404,
            )

        try:
            await ah.add_to_shopping_list(
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

    async def store_cart(store: Any, error: Any, auth_error: type) -> Any:
        """Zoek (indien nodig) de producten en zet ze in het mandje van de winkel; nooit een bestelling."""
        body = await request_body.json_object()
        items = body.get("items") or []
        if not items:
            return jsonify({"error": tr("Geen ingredienten opgegeven", "No ingredients given")}), 400

        matched: list[dict[str, Any]] = []
        skipped: list[str] = []
        for item in items:
            query = item.get("query", "")
            try:
                if item.get("productId"):
                    # Door de gebruiker bevestigd (of aangepast) in de controlelijst.
                    matched.append({"productId": item["productId"], "quantity": item.get("quantity"), "query": query})
                    continue
                product = await store.search_product(query)
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
            return jsonify({"error": tr("Geen producten gevonden voor de opgegeven ingredienten", "No products found for the given ingredients"), "skippedItems": skipped}), 404
        try:
            result = await store.add_to_cart(matched)
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
        return store_error("Picnic", exc, picnic.PicnicAuthError, picnic.Picnic2FAError, picnic.PicnicBlockedError)

    @app.get("/api/picnic/auth/status")
    async def picnic_auth_status():
        verify = (request.args.get("verify") or "").strip().lower() in {"1", "true", "yes"}
        return jsonify(await picnic.status(verify=verify))

    @app.post("/api/picnic/auth")
    async def picnic_auth():
        body = await request_body.json_object()
        try:
            return jsonify(await picnic.login(body.get("email"), body.get("password") or ""))
        except Exception as exc:  # noqa: BLE001
            return picnic_error(exc)

    @app.post("/api/picnic/auth/2fa/request")
    async def picnic_auth_2fa_request():
        body = await request_body.json_object()
        try:
            await picnic.request_2fa_code(body.get("channel") or "SMS")
            return jsonify({"requested": True})
        except Exception as exc:  # noqa: BLE001
            return picnic_error(exc)

    @app.post("/api/picnic/auth/2fa/verify")
    async def picnic_auth_2fa_verify():
        body = await request_body.json_object()
        try:
            return jsonify(await picnic.verify_2fa(body.get("code")))
        except Exception as exc:  # noqa: BLE001
            return picnic_error(exc)

    @app.post("/api/picnic/auth/logout")
    async def picnic_auth_logout():
        return jsonify(await picnic.unlink())

    @app.get("/api/picnic/search")
    async def picnic_search():
        query = (request.args.get("q") or "").strip()
        if not query:
            return jsonify({"error": tr("Zoekterm ontbreekt", "Search term is missing")}), 400
        try:
            return jsonify({"products": await prices.recorded("picnic", picnic.search_products)(query, 10)})
        except Exception as exc:  # noqa: BLE001
            return picnic_error(exc)

    @app.post("/api/picnic/cart/preview")
    async def picnic_cart_preview():
        return await cart_preview(prices.recorded("picnic", picnic.search_products), picnic.candidate)

    @app.get("/api/picnic/cart")
    async def picnic_cart_get():
        try:
            return jsonify(await picnic.get_cart())
        except Exception as exc:  # noqa: BLE001
            return picnic_error(exc)

    @app.post("/api/picnic/cart")
    async def picnic_cart():
        return await store_cart(picnic, picnic_error, picnic.PicnicAuthError)

    @app.get("/api/picnic/deliveries")
    async def picnic_deliveries():
        current = (request.args.get("current") or "").strip().lower() in {"1", "true", "yes"}
        try:
            return jsonify({"deliveries": await picnic.get_deliveries(current_only=current)})
        except Exception as exc:  # noqa: BLE001
            return picnic_error(exc)

    # Afgeleverde bestellingen: lezen uit de lokale kopie, synchroniseren bij Picnic. De ids zijn tekst (leveringsnummers).

    @app.get("/api/picnic/orders")
    async def picnic_orders_list():
        return jsonify(await picnic_orders.list_orders())

    @app.get("/api/picnic/orders/<order_id>")
    async def picnic_order_detail(order_id: str):
        order = await picnic_orders.get_order(order_id)
        if not order:
            return jsonify({"error": tr("Bestelling niet gevonden.", "Order not found.")}), 404
        return jsonify(order)

    @app.post("/api/picnic/orders/sync")
    async def picnic_orders_sync():
        try:
            return jsonify(await picnic_orders.sync_orders())
        except Exception as exc:  # noqa: BLE001 - de bewaarde kopie blijft staan
            return picnic_error(exc)

    @app.get("/api/picnic/deliveries/<delivery_id>")
    async def picnic_delivery(delivery_id: str):
        # Alleen lezen: één levering met haar producten.
        try:
            return jsonify(await picnic.get_delivery(delivery_id))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001
            return picnic_error(exc)

    @app.put("/api/picnic/cart/items/<product_id>")
    async def picnic_cart_set_quantity(product_id: str):
        # Een exact aantal; 0 haalt het product uit het mandje. Plaatst nooit een bestelling.
        body = await request_body.json_object()
        try:
            return jsonify(await picnic.set_quantity(product_id, body.get("quantity")))
        except Exception as exc:  # noqa: BLE001
            return picnic_error(exc)

    @app.post("/api/picnic/cart/clear")
    async def picnic_cart_clear():
        # Het hele mandje leegmaken vraagt om een expliciete bevestiging, ook van andere aanroepers.
        body = await request_body.json_object()
        if body.get("confirm") is not True:
            return jsonify({"error": tr("Bevestiging ontbreekt", "Confirmation is missing")}), 400
        try:
            return jsonify(await picnic.clear_cart())
        except Exception as exc:  # noqa: BLE001
            return picnic_error(exc)

    @app.get("/api/picnic/favorites")
    async def picnic_favorites():
        # Alleen lezen: de favoriete producten uit de Favorieten-tab van de Picnic-app. De producten worden lui
        # gecontroleerd: met `budget` hooguit zoveel nieuwe per aanroep (`pending` zegt hoeveel er nog komen),
        # met `refresh=1` vergeet de server eerst wat hij wist.
        budget = request.args.get("budget", type=int)
        try:
            return jsonify(await picnic.get_favorites(
                budget=max(1, min(budget, 30)) if budget else None,
                refresh=request.args.get("refresh") == "1",
            ))
        except Exception as exc:  # noqa: BLE001
            return picnic_error(exc)

    @app.get("/api/picnic/recipes")
    async def picnic_recipe_list():
        # Alleen lezen: de recepten van Picnic (opgeslagen of het kookboek), met id en naam.
        source = (request.args.get("source") or "saved").strip().lower()
        query = (request.args.get("q") or "").strip()
        try:
            if query:  # zoeken in je bewaarde recepten en het kookboek, voor Recepten zoeken
                return jsonify({"source": "search", **await picnic_recipes.search(query)})
            return jsonify({"source": source, "recipes": await picnic_recipes.get_recipes(source)})
        except Exception as exc:  # noqa: BLE001
            return picnic_error(exc)

    @app.get("/api/picnic/recipes/<recipe_id>")
    async def picnic_recipe_detail(recipe_id: str):
        # Alleen lezen: één recept met ingrediënten en bereiding.
        try:
            return jsonify(await picnic_recipes.get_recipe(recipe_id))
        except Exception as exc:  # noqa: BLE001
            return picnic_error(exc)

    @app.post("/api/picnic/recipes/<recipe_id>/import")
    async def picnic_recipe_import(recipe_id: str):
        # Zet het recept in Mealie, als schema.org Recipe (Picnic-recepten hebben geen webpagina).
        try:
            await mealie.ensure_configured()
        except RuntimeError as exc:  # Mealie is niet gekoppeld: de tekst is van onszelf en zegt wat te doen
            return jsonify({"error": str(exc)}), 400
        found = await reused_recipe(picnic_recipes.source_url(recipe_id))
        if found:
            return found
        try:
            recipe = await picnic_recipes.get_recipe(recipe_id)
            slug = await mealie.import_via_jsonld(picnic_recipes.source_url(recipe_id), picnic_recipes.jsonld(recipe))
            return jsonify({"slug": slug, "mealieUrl": await mealie.recipe_page_url(slug)})
        except Exception as exc:  # noqa: BLE001
            app.logger.exception("Picnic recipe import error")
            if isinstance(exc, (ValueError, picnic.PicnicAuthError)):
                return picnic_error(exc)
            # De melding van Mealie of Picnic kan het antwoord van de dienst bevatten: alleen in het logboek.
            return jsonify({"error": tr("Importeren in Mealie mislukt", "Importing into Mealie failed")}), 424

    @app.get("/api/picnic/slots")
    async def picnic_slots():
        # Alleen lezen: een bezorgmoment kiezen doe je in de Picnic-app.
        try:
            limit = max(1, min(30, int(request.args.get("limit") or 8)))
            return jsonify({"slots": await picnic.get_delivery_slots(limit)})
        except Exception as exc:  # noqa: BLE001
            return picnic_error(exc)

    @app.post("/api/picnic/explore")
    async def picnic_explore_routes():
        # Alleen de beheerder (auth.ADMIN_PATHS): roept leesroutes aan en meldt de vorm van de antwoorden.
        return jsonify(await picnic_explore.explore(extended=(request.args.get("extended") or "") == "1"))

    # --- Crisp --------------------------------------------------------------------------
    # Koppelen met een code per mail (zoals de Crisp-app); de producten gaan, net als bij Picnic, direct in het echte
    # mandje. Recepten van Crisp zijn te zoeken, te bekijken en in Mealie te zetten.

    def crisp_error(exc: Exception) -> Any:
        return store_error("Crisp", exc, crisp.CrispAuthError, crisp.CrispLoginError, crisp.CrispBasketError)

    @app.get("/api/crisp/auth/status")
    async def crisp_auth_status():
        verify = (request.args.get("verify") or "").strip().lower() in {"1", "true", "yes"}
        return jsonify(await crisp.status(verify=verify))

    @app.post("/api/crisp/auth")
    async def crisp_auth():
        body = await request_body.json_object()
        try:
            return jsonify(await crisp.start_login(body.get("email"), body.get("country") or "nl"))
        except Exception as exc:  # noqa: BLE001
            return crisp_error(exc)

    @app.post("/api/crisp/auth/verify")
    async def crisp_auth_verify():
        body = await request_body.json_object()
        try:
            return jsonify(await crisp.verify_login(body.get("code")))
        except Exception as exc:  # noqa: BLE001
            return crisp_error(exc)

    @app.post("/api/crisp/auth/logout")
    async def crisp_auth_logout():
        return jsonify(await crisp.unlink())

    @app.get("/api/crisp/search")
    async def crisp_search():
        query = (request.args.get("q") or "").strip()
        if not query:
            return jsonify({"error": tr("Zoekterm ontbreekt", "Search term missing")}), 400
        try:
            return jsonify({"products": await prices.recorded("crisp", crisp.search_products)(query, 10)})
        except Exception as exc:  # noqa: BLE001
            return crisp_error(exc)

    @app.post("/api/crisp/cart/preview")
    async def crisp_cart_preview():
        return await cart_preview(prices.recorded("crisp", crisp.search_products), crisp.candidate)

    @app.get("/api/crisp/cart")
    async def crisp_cart_get():
        try:
            return jsonify(await crisp.get_cart())
        except Exception as exc:  # noqa: BLE001
            return crisp_error(exc)

    @app.post("/api/crisp/cart")
    async def crisp_cart():
        return await store_cart(crisp, crisp_error, crisp.CrispAuthError)

    @app.put("/api/crisp/cart/items/<product_id>")
    async def crisp_cart_set_quantity(product_id: str):
        # Het aantal van één product in het echte Crisp-mandje; 0 haalt het weg. Nooit een bestelling.
        body = await request_body.json_object()
        try:
            return jsonify(await crisp.set_quantity(product_id, body.get("quantity")))
        except Exception as exc:  # noqa: BLE001
            return crisp_error(exc)

    @app.post("/api/crisp/cart/clear")
    async def crisp_cart_clear():
        # Het mandje leegmaken vraagt om een expliciete bevestiging, zoals bij Picnic.
        body = await request_body.json_object()
        if body.get("confirm") is not True:
            return jsonify({"error": tr("Bevestiging ontbreekt", "Confirmation is missing")}), 400
        try:
            return jsonify(await crisp.clear_cart())
        except Exception as exc:  # noqa: BLE001
            return crisp_error(exc)

    # Bestellingen: de bezorgde uit de lokale kopie (crisp_orders), de aankomende live.
    @app.get("/api/crisp/orders")
    async def crisp_orders_list():
        return jsonify(await crisp_orders.list_orders())

    @app.get("/api/crisp/orders/open")
    async def crisp_orders_open():
        try:
            return jsonify({"orders": await crisp.get_open_orders()})
        except Exception as exc:  # noqa: BLE001
            return crisp_error(exc)

    @app.post("/api/crisp/orders/sync")
    async def crisp_orders_sync():
        try:
            return jsonify(await crisp_orders.sync_orders())
        except Exception as exc:  # noqa: BLE001
            return crisp_error(exc)

    @app.get("/api/crisp/orders/<order_id>")
    async def crisp_order_detail(order_id: str):
        # Uit de lokale kopie als de producten er al in staan; anders (een aankomende bestelling, of nog niet
        # gesynchroniseerd) live bij Crisp. Lukt dat niet, dan wat we hebben.
        local = await crisp_orders.get_order(order_id)
        if local and local["itemsSynced"]:
            return jsonify(local)
        try:
            return jsonify(await crisp.get_order(order_id))
        except Exception as exc:  # noqa: BLE001
            if local:
                return jsonify(local)
            return crisp_error(exc)

    @app.get("/api/crisp/favorites")
    async def crisp_favorites():
        try:
            return jsonify(await crisp.get_favorites())
        except Exception as exc:  # noqa: BLE001
            return crisp_error(exc)

    @app.get("/api/crisp/recipes")
    async def crisp_recipe_list():
        # Met `q` zoeken in alle Crisp-recepten (Recepten zoeken); anders een lijst, zoals bij Picnic. Crisp heeft één
        # lijst: je bewaarde recepten (Winkels > Crisp > Recepten).
        query = request.args.get("q")
        source = (request.args.get("source") or "saved").strip().lower()
        try:
            if query is not None:
                return jsonify(await crisp.search_recipes(query))
            if source != "saved":
                raise ValueError(tr("Onbekende bron voor recepten (kies saved)", "Unknown recipe source (choose saved)"))
            return jsonify({"source": "saved", **await crisp.get_saved_recipes()})
        except Exception as exc:  # noqa: BLE001
            return crisp_error(exc)

    @app.get("/api/crisp/recipes/<recipe_id>")
    async def crisp_recipe_detail(recipe_id: str):
        try:
            return jsonify(await crisp.get_recipe(recipe_id))
        except Exception as exc:  # noqa: BLE001
            return crisp_error(exc)

    @app.post("/api/crisp/recipes/<recipe_id>/import")
    async def crisp_recipe_import(recipe_id: str):
        # Zet het recept in Mealie, als schema.org Recipe (Crisp-recepten hebben geen webpagina).
        try:
            await mealie.ensure_configured()
            source = crisp.source_url(recipe_id)
        except (RuntimeError, ValueError) as exc:  # Mealie niet gekoppeld of een ongeldig id: de tekst is van onszelf
            return jsonify({"error": str(exc)}), 400
        found = await reused_recipe(source)
        if found:
            return found
        try:
            recipe = await crisp.get_recipe(recipe_id)
            slug = await mealie.import_via_jsonld(source, crisp.jsonld(recipe))
            return jsonify({"slug": slug, "mealieUrl": await mealie.recipe_page_url(slug)})
        except Exception as exc:  # noqa: BLE001
            app.logger.exception("Crisp recipe import error")
            if isinstance(exc, (ValueError, crisp.CrispAuthError)):
                return crisp_error(exc)
            # De melding van Mealie of Crisp kan het antwoord van de dienst bevatten: alleen in het logboek.
            return jsonify({"error": tr("Importeren in Mealie mislukt", "Importing into Mealie failed")}), 424

    @app.post("/api/crisp/explore")
    async def crisp_explore_routes():
        # Alleen de beheerder (auth.ADMIN_PATHS): roept leesroutes aan en meldt de vorm van de antwoorden.
        return jsonify(await crisp.explore())

    # --- Jumbo --------------------------------------------------------------------------
    # Koppelen werkt zoals bij AH (de beheerder plakt een regel uit de browserconsole); de producten
    # gaan, net als bij Picnic, direct in het echte mandje.

    def jumbo_error(exc: Exception) -> Any:
        return store_error("Jumbo", exc, jumbo.JumboAuthError)

    def load_weeks() -> int:
        """`?load=N` bij het zoeken: eerst de komende N weken ophalen (hooguit `hellofresh.MAX_LOAD_WEEKS`); anders alleen de bewaarde menu's."""
        try:
            return max(0, min(int(request.args.get("load", "0")), hellofresh.MAX_LOAD_WEEKS))
        except ValueError:
            return 0

    # --- Maaltijdboxen: de menu's en recepten van HelloFresh, zonder account (alleen gelezen uit openbare pagina's)
    def hellofresh_error(exc: Exception) -> Any:
        # HelloFreshError draagt een tekst van onszelf (nooit het antwoord van HelloFresh): die tonen we, met 424 omdat
        # de fout bij HelloFresh ligt en niet bij de gebruiker.
        if isinstance(exc, hellofresh.HelloFreshError):
            app.logger.warning("HelloFresh: %s", exc, exc_info=exc.__cause__ is not None)
            return jsonify({"error": str(exc)}), 424
        return store_error("HelloFresh", exc)

    @app.get("/api/hellofresh/menu")
    async def hellofresh_menu():
        refresh = (request.args.get("refresh") or "").strip().lower() in {"1", "true", "yes"}
        try:
            return jsonify(await hellofresh.get_menu(request.args.get("week"), refresh=refresh))
        except Exception as exc:  # noqa: BLE001
            return hellofresh_error(exc)

    @app.get("/api/hellofresh/search")
    async def hellofresh_search():
        try:
            return jsonify(await hellofresh.search(request.args.get("q"), load=load_weeks()))
        except Exception as exc:  # noqa: BLE001
            return hellofresh_error(exc)

    @app.get("/api/hellofresh/recipes/<recipe_key>")
    async def hellofresh_recipe_detail(recipe_key: str):
        try:
            return jsonify(await hellofresh.detail(recipe_key))
        except Exception as exc:  # noqa: BLE001
            return hellofresh_error(exc)

    @app.post("/api/hellofresh/recipes/<recipe_key>/import")
    async def hellofresh_recipe_import(recipe_key: str):
        try:
            await mealie.ensure_configured()
        except RuntimeError as exc:  # Mealie is niet gekoppeld: de tekst is van onszelf en zegt wat te doen
            return jsonify({"error": str(exc)}), 400
        if hellofresh.recipe_key(hellofresh.source_url(recipe_key)):
            found = await reused_recipe(hellofresh.source_url(recipe_key))
            if found:
                return found
        try:
            return jsonify(await hellofresh.run_import(recipe_key))
        except Exception as exc:  # noqa: BLE001
            # run_import zet een tekst van onszelf in de fout (nooit het antwoord van Mealie of HelloFresh) en legt de
            # poging vast in het auditlog; het volledige antwoord staat alleen in het serverlogboek.
            return hellofresh_error(exc)

    # Marley Spoon: het menu, en het recept in Mealie zetten (het detail vraagt het bezoekerstoken van de receptpagina)
    def marleyspoon_error(exc: Exception) -> Any:
        if isinstance(exc, marleyspoon.MarleySpoonError):
            app.logger.warning("Marley Spoon: %s", exc, exc_info=exc.__cause__ is not None)
            return jsonify({"error": str(exc)}), 424
        return store_error("Marley Spoon", exc)

    @app.get("/api/marleyspoon/menu")
    async def marleyspoon_menu():
        refresh = (request.args.get("refresh") or "").strip().lower() in {"1", "true", "yes"}
        try:
            return jsonify(await marleyspoon.get_menu(request.args.get("week"), refresh=refresh))
        except Exception as exc:  # noqa: BLE001
            return marleyspoon_error(exc)

    @app.get("/api/marleyspoon/search")
    async def marleyspoon_search():
        try:
            return jsonify(await marleyspoon.search(request.args.get("q"), load=load_weeks()))
        except Exception as exc:  # noqa: BLE001
            return marleyspoon_error(exc)

    @app.get("/api/marleyspoon/recipes/<recipe_key>")
    async def marleyspoon_recipe_detail(recipe_key: str):
        try:
            return jsonify(await marleyspoon.detail(recipe_key))
        except Exception as exc:  # noqa: BLE001
            return marleyspoon_error(exc)

    @app.post("/api/marleyspoon/recipes/<recipe_key>/import")
    async def marleyspoon_recipe_import(recipe_key: str):
        try:
            await mealie.ensure_configured()
        except RuntimeError as exc:  # Mealie is niet gekoppeld: de tekst is van onszelf en zegt wat te doen
            return jsonify({"error": str(exc)}), 400
        if marleyspoon.recipe_key(recipe_key):
            found = await reused_recipe(marleyspoon.source_url(recipe_key))
            if found:
                return found
        try:
            return jsonify(await marleyspoon.run_import(recipe_key))
        except Exception as exc:  # noqa: BLE001
            # run_import zet een tekst van onszelf in de fout en legt de poging vast in het auditlog.
            return marleyspoon_error(exc)

    @app.get("/api/jumbo/auth/status")
    async def jumbo_auth_status():
        verify = (request.args.get("verify") or "").strip().lower() in {"1", "true", "yes"}
        return jsonify(await jumbo.status(verify=verify))

    @app.get("/api/jumbo/auth/start")
    async def jumbo_auth_start():
        return redirect(await jumbo.start_login(), code=302)

    @app.post("/api/jumbo/auth")
    async def jumbo_auth():
        body = await request_body.json_object()
        try:
            return jsonify(await jumbo.link_account(body.get("code") or body.get("token") or ""))
        except Exception as exc:  # noqa: BLE001
            return jumbo_error(exc)

    @app.post("/api/jumbo/auth/logout")
    async def jumbo_auth_logout():
        return jsonify(await jumbo.unlink())

    @app.get("/api/jumbo/search")
    async def jumbo_search():
        query = (request.args.get("q") or "").strip()
        if not query:
            return jsonify({"error": tr("Zoekterm ontbreekt", "Search term is missing")}), 400
        try:
            return jsonify({"products": await prices.recorded("jumbo", jumbo.search_products)(query, 10)})
        except Exception as exc:  # noqa: BLE001
            return jumbo_error(exc)

    @app.post("/api/jumbo/cart/preview")
    async def jumbo_cart_preview():
        return await cart_preview(prices.recorded("jumbo", jumbo.search_products), jumbo.candidate)

    @app.get("/api/jumbo/cart")
    async def jumbo_cart_get():
        try:
            return jsonify(await jumbo.get_cart())
        except Exception as exc:  # noqa: BLE001
            return jumbo_error(exc)

    @app.post("/api/jumbo/cart")
    async def jumbo_cart():
        return await store_cart(jumbo, jumbo_error, jumbo.JumboAuthError)

    # Het Jumbo-lijstje: één lijstje, bewaard als lokale kopie en in dezelfde vorm als de AH-lijstjes.

    @app.get("/api/jumbo/lists")
    async def jumbo_lists_overview():
        # Altijd 200: de lokale kopie blijft zichtbaar ook als Jumbo niet bereikbaar is.
        return jsonify(await jumbo_lists.merged_lists())

    @app.get("/api/jumbo/lists/search")
    async def jumbo_lists_search():
        query = (request.args.get("q") or "").strip()
        return jsonify({"query": query, "items": await jumbo_lists.search_items(query)})

    @app.get("/api/jumbo/lists/<list_id>")
    async def jumbo_list_detail(list_id: str):
        snapshot = await jumbo_lists.get_snapshot(list_id)
        if snapshot:
            return jsonify(snapshot)
        try:
            return jsonify(jumbo_lists.live_view(await jumbo_lists.fetch_live(list_id)))
        except Exception as exc:  # noqa: BLE001
            return jumbo_error(exc)

    @app.post("/api/jumbo/lists/<list_id>/sync")
    async def jumbo_list_sync(list_id: str):
        try:
            return jsonify(await jumbo_lists.sync_list(list_id))
        except Exception as exc:  # noqa: BLE001
            return jumbo_error(exc)

    @app.patch("/api/jumbo/lists/<list_id>/items/<int:position>")
    async def jumbo_list_item_quantity(list_id: str, position: int):
        body = await request_body.json_object()
        try:
            snapshot = await jumbo_lists.set_quantity(list_id, position, body.get("quantity"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if not snapshot:
            return jsonify({"error": tr("Regel niet gevonden. Synchroniseer het lijstje eerst.", "Line not found. Sync the list first.")}), 404
        return jsonify(snapshot)

    @app.post("/api/jumbo/lists/<list_id>/reset")
    async def jumbo_list_reset(list_id: str):
        snapshot = await jumbo_lists.reset_quantities(list_id)
        if not snapshot:
            return jsonify({"error": tr("Dit lijstje is niet gesynchroniseerd.", "This list is not synced.")}), 404
        return jsonify(snapshot)

    @app.post("/api/jumbo/lists/<list_id>/cart")
    async def jumbo_list_to_cart(list_id: str):
        """Zet het hele lijstje in het Jumbo-mandje, met de aantallen zoals ze nu staan.

        Regels met een SKU gaan direct; een regel met alleen tekst zoeken we eerst op. Nooit een bestelling.
        """
        try:
            snapshot = await jumbo_lists.get_snapshot(list_id) or jumbo_lists.live_view(await jumbo_lists.fetch_live(list_id))
            matched: list[dict[str, Any]] = []
            skipped: list[str] = []
            for item in snapshot.get("items") or []:
                if item.get("productId"):
                    matched.append({"productId": item["productId"], "quantity": item["quantity"], "term": item.get("term")})
                    continue
                found = await jumbo.search_product(item.get("term") or "")
                if found:
                    matched.append({"productId": found["id"], "quantity": item["quantity"], "term": item.get("term")})
                else:
                    skipped.append(item.get("term") or "")
            if not matched:
                return jsonify({"error": tr("Dit lijstje heeft geen producten.", "This list has no products.")}), 400
            result = await jumbo.add_to_cart(matched)
        except Exception as exc:  # noqa: BLE001
            return jumbo_error(exc)
        failed = {entry["productId"] for entry in result["failed"]}
        skipped += [entry["term"] or "" for entry in matched if entry["productId"] in failed]
        done = [entry for entry in matched if entry["productId"] not in failed]
        return jsonify({"added": len(done), "units": sum(entry["quantity"] for entry in done), "name": snapshot.get("name"), "skippedItems": skipped})

    @app.post("/api/jumbo/list")
    async def jumbo_list_add():
        """Zet ingrediënten op het Jumbo-lijstje in plaats van in het mandje. Wat er al staat slaan we over."""
        body = await request_body.json_object()
        items = body.get("items") or []
        if not items:
            return jsonify({"error": tr("Geen ingredienten opgegeven", "No ingredients given")}), 400
        lines = [
            {"term": item.get("title") or item.get("query") or "", "sku": item.get("productId")}
            for item in items
            if isinstance(item, dict)
        ]
        try:
            result = await jumbo.add_to_shopping_list(lines)
        except Exception as exc:  # noqa: BLE001
            return jumbo_error(exc)
        return jsonify({"added": len(result["added"]), "skipped": len(result["skipped"]), "skippedItems": result["skipped"]})

    # Bestellingen: lezen uit de lokale kopie, synchroniseren bij Jumbo. De ids zijn tekst (Jumbo-ordernummers).

    @app.get("/api/jumbo/orders")
    async def jumbo_orders_list():
        return jsonify(await jumbo_orders.list_orders(summary=True))

    @app.get("/api/jumbo/orders/<order_id>")
    async def jumbo_order_detail(order_id: str):
        order = await jumbo_orders.get_order(order_id)
        if not order:
            return jsonify({"error": tr("Bestelling niet gevonden.", "Order not found.")}), 404
        return jsonify(order)

    @app.post("/api/jumbo/orders/sync")
    async def jumbo_orders_sync():
        try:
            return jsonify(await jumbo_orders.sync_orders())
        except Exception as exc:  # noqa: BLE001 - de bewaarde kopie blijft staan
            return jumbo_error(exc)

    @app.get("/api/jumbo/purchases/search")
    async def jumbo_purchases_search():
        return jsonify(await jumbo_orders.search_purchases(request.args.get("q") or "", limit=search_limit(50)))

    # Recepten: zoekresultaten, één recept met ingrediënten en bereiding, en importeren in Mealie.

    @app.get("/api/jumbo/recipes")
    async def jumbo_recipe_list():
        # Alleen lezen: zoeken in alle recepten van Jumbo (`offset` voor de volgende pagina).
        try:
            return jsonify({**await jumbo_recipes.search(request.args.get("q"), request.args.get("offset")), "pageSize": jumbo_recipes.RECIPE_PAGE})
        except Exception as exc:  # noqa: BLE001
            return jumbo_error(exc)

    @app.get("/api/jumbo/favorite-recipes")
    async def jumbo_favorite_recipes():
        # Alleen lezen: je favoriete recepten bij Jumbo, per pagina (`page` begint bij 1).
        try:
            return jsonify({**await jumbo_recipes.get_favorites(request.args.get("page")), "pageSize": jumbo_recipes.RECIPE_PAGE})
        except Exception as exc:  # noqa: BLE001
            return jumbo_error(exc)

    @app.get("/api/jumbo/recipe-lists")
    async def jumbo_recipe_lists():
        # Alleen lezen: je receptenlijsten bij Jumbo (Favorieten eerst, dan je eigen lijstjes) met aantallen.
        try:
            return jsonify({"lists": await jumbo_recipes.get_lists()})
        except Exception as exc:  # noqa: BLE001
            return jumbo_error(exc)

    @app.get("/api/jumbo/recipe-lists/<list_id>")
    async def jumbo_recipe_list_recipes(list_id: str):
        # Alleen lezen: de recepten van een eigen lijstje, per pagina (`page` begint bij 1).
        try:
            return jsonify({**await jumbo_recipes.get_list_recipes(list_id, request.args.get("page")), "pageSize": jumbo_recipes.RECIPE_PAGE})
        except Exception as exc:  # noqa: BLE001
            return jumbo_error(exc)

    @app.get("/api/jumbo/recipes/<recipe_id>")
    async def jumbo_recipe_detail(recipe_id: str):
        # Alleen lezen: één recept met ingrediënten en bereiding.
        try:
            return jsonify(await jumbo_recipes.get_recipe(recipe_id))
        except Exception as exc:  # noqa: BLE001
            return jumbo_error(exc)

    @app.post("/api/jumbo/recipes/<recipe_id>/import")
    async def jumbo_recipe_import(recipe_id: str):
        # Zet het recept in Mealie, als schema.org Recipe met de link naar jumbo.com als bron.
        try:
            await mealie.ensure_configured()
        except RuntimeError as exc:  # Mealie is niet gekoppeld: de tekst is van onszelf en zegt wat te doen
            return jsonify({"error": str(exc)}), 400
        try:
            recipe = await jumbo_recipes.get_recipe(recipe_id)
            found = await reused_recipe(jumbo_recipes.source_url(recipe))
            if found:
                return found
            slug = await mealie.import_via_jsonld(jumbo_recipes.source_url(recipe), jumbo_recipes.jsonld(recipe))
            return jsonify({"slug": slug, "mealieUrl": await mealie.recipe_page_url(slug)})
        except Exception as exc:  # noqa: BLE001
            app.logger.exception("Jumbo recipe import error")
            if isinstance(exc, (ValueError, jumbo.JumboAuthError)):
                return jumbo_error(exc)
            # De melding van Mealie of Jumbo kan het antwoord van de dienst bevatten: alleen in het logboek.
            return jsonify({"error": tr("Importeren in Mealie mislukt", "Importing into Mealie failed")}), 424

    @app.post("/api/jumbo/explore")
    async def jumbo_explore_routes():
        # Alleen de beheerder (auth.ADMIN_PATHS): meldt of Jumbo deze server toelaat en de vorm van de antwoorden.
        return jsonify(await jumbo_explore.explore())

    @app.post("/api/ah/auth")
    async def ah_auth():
        body = await request_body.json_object()
        raw = (body.get("code") or body.get("token") or "").strip()
        if not raw:
            return jsonify({"error": tr("Geen code of token opgegeven", "No code or token given")}), 400

        result = await ah.link_account(raw)
        if result.get("connected"):
            return jsonify(result)
        return jsonify({"error": result.get("error") or tr("Onbekende fout", "Unknown error")}), 400

    @app.post("/api/ah/auth/verify")
    async def ah_auth_verify():
        body = await request_body.json_object()
        token = (body.get("refreshToken") or "").strip()
        if not token:
            return jsonify({"error": tr("Geen token opgegeven", "No token given")}), 400

        result = await ah.adopt_refresh_token(token)  # bewaart een vervangen refresh-token, ook als de browser weggaat
        if result.get("ok"):
            return jsonify({"connected": True, "type": result.get("type")})
        return jsonify({"error": result["error"]}), 400

    @app.get("/api/ah/auth/start")
    async def ah_auth_start():
        return redirect(ah.login_url(), code=302)

    @app.get("/api/ah/auth/callback")
    async def ah_auth_callback():
        error = request.args.get("error")
        if error:
            description = request.args.get("error_description") or error
            return redirect(f"/?ah_error={quote_plus(description)}")

        code = request.args.get("code")
        if not code:
            return redirect("/?ah_error=Geen+code+ontvangen")

        callback_url = await ah_callback_url()
        try:
            await ah.exchange_and_store_oauth_code(code, callback_url)
            return redirect("/?ah_connected=1")
        except Exception as exc:
            return redirect(
                f"/?ah_error={quote_plus(f'Code inwisselen mislukt: {str(exc)[:100]}')}"
            )

    @app.get("/api/ah/auth/proxy/callback")
    async def ah_auth_proxy_callback():
        error = request.args.get("error")
        if error:
            description = request.args.get("error_description") or error
            return redirect(f"/?ah_error={quote_plus(description)}")

        code = request.args.get("code")
        if not code:
            return redirect("/?ah_error=Geen+code+ontvangen")

        try:
            await ah.exchange_and_store_oauth_code(code)
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
    async def ah_auth_proxy(proxy_path: str) -> Response | tuple[str, int]:
        target_url = f"{ah.AH_LOGIN_BASE}/{proxy_path}".rstrip("/")
        headers: dict[str, str] = {
            key: value for key, value in request.headers.items() if key.lower() not in AH_PROXY_DROPPED_HEADERS
        }
        cookie: str | None = _ah_proxy_cookie(request.headers.getlist("Cookie"))
        if cookie is not None:
            headers["Cookie"] = cookie
        headers.setdefault("User-Agent", ah.AH_BROWSER_USER_AGENT)
        headers.setdefault("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
        headers.setdefault("Accept-Language", "nl-NL,nl;q=0.9,en;q=0.8")
        proxy_base = await ah_proxy_base_url()
        public_origin = await public_base_url()
        if "Referer" in headers:
            headers["Referer"] = (
                headers["Referer"]
                .replace(proxy_base, ah.AH_LOGIN_BASE)
                .replace(public_origin, ah.AH_LOGIN_BASE)
            )
        if headers.get("Origin", "").rstrip("/") == public_origin:
            headers["Origin"] = ah.AH_LOGIN_BASE

        data: bytes = await request.get_data()
        if not data and request.method not in {"GET", "HEAD"}:
            headers["Content-Length"] = "0"  # zoals requests: httpx zet hem bij een leeg lichaam alleen voor POST, PUT en PATCH
        try:
            with audit.login_proxy():
                # Geen doorverwijzingen volgen: de browser volgt ze zelf via de herschreven Location. Daardoor leunt deze
                # stroom ook niet op een cookie binnen een gevolgde keten (http_client wist de cookies na elk antwoord).
                upstream: httpx.Response = await http_client.client("ah-login-proxy", follow_redirects=False).request(
                    request.method,
                    target_url,
                    params=request.args,
                    content=data,
                    # latin-1 zoals Quart ze las en requests ze schreef: een UTF-8-byte van de browser gaat ongewijzigd door.
                    # Een gewone str codeert httpx als ASCII, en dan geeft zo'n byte een UnicodeEncodeError (500).
                    headers=httpx.Headers(headers, encoding="latin-1"),
                    timeout=30,
                )
        except (httpx.HTTPError, httpx.InvalidURL) as exc:  # InvalidURL (een stuurteken in het pad) valt niet onder HTTPError
            app.logger.exception("AH login proxy error")
            return f"AH login proxy error: {exc}", 502

        content_type = upstream.headers.get("Content-Type", "")
        body = upstream.content
        if any(kind in content_type for kind in ("text/html", "javascript", "json", "text/css")):
            body = ah.rewrite_login_body(body, proxy_base)

        response = Response(body, status=upstream.status_code, content_type=content_type)
        for key, value in upstream.headers.items():
            if key.lower() not in AH_PROXY_BLOCKED_RESPONSE_HEADERS:
                response.headers[key] = value

        if location := upstream.headers.get("Location"):
            response.headers["Location"] = ah.rewrite_login_location(
                location,
                proxy_base,
            )

        # Elke Set-Cookie apart: `items()` hierboven voegt dubbele headers samen, en cookies kun je niet samenvoegen.
        # http_client leegt alleen de cookiejar van de client, de headers van het antwoord blijven heel.
        for cookie in upstream.headers.get_list("Set-Cookie"):
            response.headers.add("Set-Cookie", ah.sanitize_login_cookie(cookie))

        return response

    return app


app = create_app()
