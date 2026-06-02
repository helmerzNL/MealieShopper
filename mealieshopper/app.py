from urllib.parse import quote_plus

from flask import Flask, jsonify, redirect, render_template, request

from . import ah, mealie
from . import auth


def create_app() -> Flask:
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    auth.init_db()
    auth.register_routes(app)

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
        code = (body.get("code") or "").strip()
        if not code:
            return jsonify({"error": "Geen code opgegeven"}), 400

        try:
            return jsonify({"refreshToken": ah.exchange_oauth_code(code)["refreshToken"]})
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
            return jsonify(result)
        return jsonify({"error": result["error"]}), 400

    @app.get("/api/ah/auth/start")
    def ah_auth_start():
        callback_url = request.url_root.rstrip("/") + "/api/ah/auth/callback"
        params = (
            f"client_id=appie&redirect_uri={quote_plus(callback_url)}&response_type=code"
        )
        return redirect(f"https://login.ah.nl/secure/oauth/authorize?{params}", code=302)

    @app.get("/api/ah/auth/callback")
    def ah_auth_callback():
        error = request.args.get("error")
        if error:
            description = request.args.get("error_description") or error
            return redirect(f"/?ah_error={quote_plus(description)}")

        code = request.args.get("code")
        if not code:
            return redirect("/?ah_error=Geen+code+ontvangen")

        callback_url = request.url_root.rstrip("/") + "/api/ah/auth/callback"
        try:
            tokens = ah.exchange_oauth_code(code, callback_url)
            return redirect(f"/?ah_refresh={quote_plus(tokens['refreshToken'])}")
        except Exception as exc:
            return redirect(
                f"/?ah_error={quote_plus(f'Code inwisselen mislukt: {str(exc)[:100]}')}"
            )

    return app


app = create_app()
