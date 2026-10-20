"""HTTP-kant van de MCP-server: /mcp, de OAuth-endpoints en het beheer ervan in de app."""
from __future__ import annotations

import json
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import Flask, Response, jsonify, make_response, redirect, render_template, request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from . import auth, mcp, oauth
from .i18n import current_lang, tr

CORS_PATHS = ("/mcp", "/oauth/register", "/oauth/token", "/oauth/revoke", "/.well-known/")
MAX_BODY = 1_000_000


def _json(payload: Any, status: int = 200, **headers: str) -> Response:
    response = jsonify(payload)
    response.status_code = status
    for key, value in headers.items():
        response.headers[key.replace("_", "-")] = value
    return response


def register_routes(app: Flask, base_url: Callable[[], str]) -> None:
    app.jinja_env.globals["tr"] = tr
    app.jinja_env.globals["lang"] = current_lang

    def mcp_url() -> str:
        return f"{base_url()}/mcp"

    def serializer() -> URLSafeTimedSerializer:
        return URLSafeTimedSerializer(auth.signing_secret(), salt="mealieshopper-oauth-authorize")

    def unavailable() -> Response:
        _, reason = mcp.enabled()
        text = (
            tr("De MCP-server staat uit.", "The MCP server is turned off.")
            if reason == "disabled"
            else tr(
                "De MCP-server werkt alleen als inloggen aanstaat. Stel eerst een passkey of wachtwoord in.",
                "The MCP server only works when login is turned on. Set up a passkey or password first.",
            )
        )
        return _json({"error": text}, 404)

    @app.after_request
    def cors(response: Response) -> Response:
        # Token, registratie en MCP draaien zonder cookies: een webpagina (zoals de MCP Inspector) mag ze lezen.
        if request.path.startswith(CORS_PATHS):
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, Mcp-Protocol-Version, Mcp-Session-Id"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
            response.headers["Access-Control-Expose-Headers"] = "WWW-Authenticate"
        return response

    # --- metadata (RFC 9728 en RFC 8414) ---------------------------------------------------------

    @app.route("/.well-known/oauth-protected-resource", methods=["GET", "OPTIONS"])
    @app.route("/.well-known/oauth-protected-resource/mcp", methods=["GET", "OPTIONS"])
    def protected_resource():
        if request.method == "OPTIONS":
            return "", 204
        if not mcp.enabled()[0]:
            return unavailable()
        return _json(
            {
                "resource": mcp_url(),
                "authorization_servers": [base_url()],
                "scopes_supported": [oauth.SCOPE],
                "bearer_methods_supported": ["header"],
                "resource_name": "MealieShopper",
            }
        )

    @app.route("/.well-known/oauth-authorization-server", methods=["GET", "OPTIONS"])
    def authorization_server():
        if request.method == "OPTIONS":
            return "", 204
        if not mcp.enabled()[0]:
            return unavailable()
        base = base_url()
        return _json(
            {
                "issuer": base,
                "authorization_endpoint": f"{base}/oauth/authorize",
                "token_endpoint": f"{base}/oauth/token",
                "registration_endpoint": f"{base}/oauth/register",
                "revocation_endpoint": f"{base}/oauth/revoke",
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"],
                "scopes_supported": [oauth.SCOPE],
            }
        )

    # --- OAuth ------------------------------------------------------------------------------------

    def oauth_error(exc: oauth.OAuthError) -> Response:
        return _json(
            {"error": exc.code, "error_description": exc.description},
            exc.status,
            Cache_Control="no-store",
            Pragma="no-cache",
        )

    def form_or_json() -> dict[str, str]:
        if request.is_json:
            data = request.get_json(silent=True)
            return {str(k): v for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}
        return request.form.to_dict()

    @app.route("/oauth/register", methods=["POST", "OPTIONS"])
    def oauth_register():
        if request.method == "OPTIONS":
            return "", 204
        if not mcp.enabled()[0]:
            return unavailable()
        metadata = request.get_json(silent=True)
        if not isinstance(metadata, dict):
            return oauth_error(oauth.OAuthError("invalid_client_metadata", "Stuur een JSON-object"))
        try:
            return _json(oauth.register_client(metadata), 201, Cache_Control="no-store")
        except oauth.OAuthError as exc:
            return oauth_error(exc)

    def redirect_with(uri: str, **params: str) -> Response:
        parts = urlsplit(uri)
        query = parse_qsl(parts.query, keep_blank_values=True) + [(k, v) for k, v in params.items() if v]
        return redirect(urlunsplit(parts._replace(query=urlencode(query))), 302)

    def error_page(message: str, status: int = 400) -> Response:
        response = make_response(render_template("oauth.html", error=message, base=base_url()), status)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/oauth/authorize")
    def oauth_authorize():
        if not mcp.enabled()[0]:
            return error_page(unavailable().get_json()["error"], 404)
        params = request.args.to_dict()
        try:
            info = oauth.check_authorize_request(params, mcp_url())
        except oauth.OAuthError as exc:
            return error_page(exc.description)
        if info["error"]:
            return redirect_with(info["redirect_uri"], error=info["error"].code, error_description=info["error"].description, state=info["state"], iss=base_url())
        if not auth.current_user():
            return redirect("/?" + urlencode({"next": request.full_path.rstrip("?")}), 302)
        user = auth.current_user()
        signed = serializer().dumps(
            {k: info[k] for k in ("redirect_uri", "state", "code_challenge", "resource")}
            | {"client_id": info["client"]["client_id"], "user_id": user["id"]}
        )
        response = make_response(
            render_template("oauth.html", error="", client=info["client"]["name"], signed=signed, base=base_url(), username=user["username"])
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        return response

    @app.post("/oauth/authorize")
    def oauth_authorize_decision():
        if not mcp.enabled()[0]:
            return error_page(unavailable().get_json()["error"], 404)
        origin = request.headers.get("Origin")
        if origin and urlsplit(origin).netloc != request.host:
            return error_page(tr("Dit verzoek komt niet van deze pagina.", "This request did not come from this page."), 403)
        user = auth.current_user()
        if not user:
            return error_page(tr("Je bent niet ingelogd.", "You are not logged in."), 401)
        try:
            data = serializer().loads(request.form.get("request", ""), max_age=600)
        except BadSignature:
            return error_page(tr("Dit verzoek is verlopen. Begin opnieuw vanuit de app.", "This request has expired. Start again from the app."))
        if data.get("user_id") != user["id"]:  # tussendoor als iemand anders ingelogd
            return error_page(tr("Je bent ondertussen als iemand anders ingelogd. Begin opnieuw vanuit de app.", "You are now logged in as someone else. Start again from the app."), 403)
        client = oauth.get_client(data["client_id"])
        if not client or data["redirect_uri"] not in client["redirect_uris"]:
            return error_page(tr("Onbekende app.", "Unknown app."))
        if request.form.get("decision") != "allow":
            return redirect_with(data["redirect_uri"], error="access_denied", state=data["state"], iss=base_url())
        code = oauth.issue_code(user["id"], data["client_id"], data["redirect_uri"], data["code_challenge"], data["resource"])
        return redirect_with(data["redirect_uri"], code=code, state=data["state"], iss=base_url())

    @app.route("/oauth/token", methods=["POST", "OPTIONS"])
    def oauth_token():
        if request.method == "OPTIONS":
            return "", 204
        if not mcp.enabled()[0]:
            return unavailable()
        try:
            return _json(oauth.token_request(form_or_json(), mcp_url()), Cache_Control="no-store", Pragma="no-cache")
        except oauth.OAuthError as exc:
            return oauth_error(exc)

    @app.route("/oauth/revoke", methods=["POST", "OPTIONS"])
    def oauth_revoke():
        if request.method == "OPTIONS":
            return "", 204
        oauth.revoke_token(form_or_json().get("token", ""))
        return "", 200

    # --- MCP ---------------------------------------------------------------------------------------

    def unauthorized(invalid: bool) -> Response:
        challenge = f'Bearer resource_metadata="{base_url()}/.well-known/oauth-protected-resource"'
        if invalid:
            challenge += ', error="invalid_token"'
        return _json({"error": "unauthorized"}, 401, WWW_Authenticate=challenge)

    @app.route("/mcp", methods=["POST", "GET", "DELETE", "OPTIONS"])
    def mcp_endpoint():
        if request.method == "OPTIONS":
            return "", 204
        if not mcp.enabled()[0]:
            return unavailable()
        if request.method != "POST":
            return _json({"error": "Method not allowed"}, 405, Allow="POST")  # geen streams en geen sessies
        header = request.headers.get("Authorization", "")
        token = header[7:].strip() if header[:7].lower() == "bearer " else ""
        if not token or not oauth.verify_access_token(token, mcp_url()):
            return unauthorized(bool(token))
        version = request.headers.get("Mcp-Protocol-Version")
        if version and version not in mcp.SUPPORTED_PROTOCOLS:
            return _json({"error": f"Unsupported MCP protocol version: {version}"}, 400)
        if (request.content_length or 0) > MAX_BODY:
            return _json({"error": "Request too large"}, 413)
        try:
            message = json.loads(request.get_data(as_text=True))
        except ValueError:
            return _json({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, 400)
        if isinstance(message, list):
            replies = [reply for reply in map(mcp.handle_message, message) if reply is not None]
            return _json(replies) if replies else Response(status=202)
        reply = mcp.handle_message(message)
        return _json(reply) if reply is not None else Response(status=202)

    # --- beheer in de app (achter de gewone inlog) ---------------------------------------------------

    @app.get("/api/mcp/status")
    def mcp_status():
        on, reason = mcp.enabled()
        base = base_url()
        host = urlsplit(base).hostname or ""
        return jsonify(
            {
                "enabled": on,
                "reason": reason,
                "url": f"{base}/mcp",
                "secure": base.startswith("https://") or host in oauth.LOOPBACK_HOSTS,
                "apps": oauth.connected_apps(auth.acting_user_id()) if on else [],
                "tools": [{"name": tool["name"], "title": tool["annotations"]["title"], "readOnly": tool["annotations"]["readOnlyHint"]} for tool in mcp.tool_descriptors()],
            }
        )

    @app.delete("/api/mcp/apps/<client_id>")
    def mcp_revoke_app(client_id: str):
        if not oauth.revoke_app(auth.acting_user_id(), client_id):
            return jsonify({"error": tr("App niet gevonden.", "App not found.")}), 404
        return jsonify({"status": "ok"})
