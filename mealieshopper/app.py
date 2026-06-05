from os import environ
from urllib.parse import quote_plus
import sqlite3

import requests
from flask import Flask, Response, jsonify, redirect, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from . import ah, mealie
from . import auth


def create_app() -> Flask:
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
    auth.register_routes(app)

    def public_base_url() -> str:
        configured = (environ.get("MEALIESHOPPER_PUBLIC_BASE_URL") or "").strip().rstrip("/")
        if configured:
            return configured
        scheme = request.headers.get("X-Forwarded-Proto") or request.scheme or "http"
        host = request.headers.get("X-Forwarded-Host") or request.host
        return f"{scheme}://{host}".rstrip("/")

    def ah_callback_url() -> str:
        return f"{public_base_url()}/api/ah/auth/callback"

    def ah_proxy_base_url() -> str:
        return f"{public_base_url()}/api/ah/auth/proxy"

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
        return render_template("index.html")

    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True})

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
            return jsonify({"error": str(exc) or "Fout bij ophalen weekmenu"}), 502

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
            return jsonify({"error": str(exc) or "Zoeken mislukt"}), 502

    @app.get("/api/ah/auth/status")
    def ah_auth_status():
        try:
            verify = (request.args.get("verify") or "").strip().lower() in {"1", "true", "yes"}
            return jsonify(ah.auth_status(verify=verify))
        except Exception as exc:
            app.logger.exception("AH auth status error")
            return jsonify({"connected": False, "error": str(exc)}), 502

    @app.get("/api/ah/favorite-lists")
    def ah_favorite_lists():
        try:
            return jsonify({"lists": ah.get_favorite_lists()})
        except Exception as exc:
            app.logger.exception("AH favorite lists error")
            return jsonify({"error": str(exc) or "AH lijstjes ophalen mislukt"}), 502

    @app.get("/api/ah/favorite-lists/<list_id>/items")
    def ah_favorite_list_items(list_id: str):
        try:
            return jsonify(ah.get_favorite_list_items(list_id))
        except Exception as exc:
            app.logger.exception("AH favorite list items error")
            return jsonify({"error": str(exc) or "AH lijstinhoud ophalen mislukt"}), 502

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
            return jsonify({"error": str(exc) or "Import mislukt"}), 502

    @app.post("/api/ah/cart")
    def ah_cart():
        body = request.get_json(silent=True) or {}
        items = body.get("items") or []
        if not items:
            return jsonify({"error": "Geen ingredienten opgegeven"}), 400

        matched = []
        diagnostics = []
        for item in items:
            try:
                product = ah.search_product(item.get("query", ""))
                if product:
                    matched.append(
                        {
                            "query": item.get("query", ""),
                            "product": product,
                            "quantity": item.get("quantity", 1),
                        }
                    )
                    diagnostics.append({"query": item.get("query", ""), "status": "gevonden"})
                else:
                    diagnostics.append({"query": item.get("query", ""), "status": "niet gevonden"})
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
                [
                    {"productId": item["product"]["webshopId"], "quantity": item["quantity"]}
                    for item in matched
                ]
            )
        except Exception as exc:
            app.logger.exception("AH cart error")
            return jsonify({"error": str(exc)}), 502

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
                            "title": item["product"]["title"],
                            "price": item["product"]["price"]["now"],
                            "unitSize": item["product"].get("unitSize")
                            or item["product"]["price"].get("unitSize"),
                            "image": (
                                item["product"].get("images") or [{"url": None}]
                            )[0].get("url"),
                        },
                    }
                    for item in matched
                ],
            }
        )

    @app.post("/api/ah/auth")
    def ah_auth():
        body = request.get_json(silent=True) or {}
        code = ah.extract_oauth_code(body.get("code") or "")
        if not code:
            return jsonify({"error": "Geen code opgegeven"}), 400

        try:
            ah.exchange_and_store_oauth_code(code)
            return jsonify({"connected": True})
        except Exception as exc:
            return jsonify({"error": str(exc) or "Onbekende fout"}), 500

    @app.post("/api/ah/auth/verify")
    def ah_auth_verify():
        body = request.get_json(silent=True) or {}
        token = (body.get("refreshToken") or "").strip()
        if not token:
            return jsonify({"error": "Geen token opgegeven"}), 400

        result = ah.verify_token(token)
        if result.get("ok"):
            if result.get("type") == "refresh":
                ah.save_refresh_token(token)
            return jsonify({"connected": True, **result})
        return jsonify({"error": result["error"]}), 400

    @app.get("/api/ah/auth/start")
    def ah_auth_start():
        return redirect(ah.proxied_login_url(ah_proxy_base_url()), code=302)

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
