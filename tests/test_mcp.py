import base64
import hashlib
import json
import os
import re
import secrets
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from datetime import timedelta
from unittest import mock
from urllib.parse import parse_qs, urlsplit

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import ah, ah_lists, ah_recipes, auth, auth_password, mcp, mealie, oauth, picnic, picnic_recipes  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

PASSWORD = "koffie-met-melk-42"
ANNA = "een-heel-ander-wachtwoord"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"
BASE = "http://localhost"
MCP_URL = BASE + "/mcp"


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class McpTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(
            os.environ,
            {"PASSKEY_AUTH_ENABLED": "true", "MEALIESHOPPER_DATA_DIR": tmp.name, "MEALIESHOPPER_AUTH_SECRET": "test-secret"},
        )
        env.start()
        self.addCleanup(env.stop)
        cost = mock.patch.object(auth_password, "SCRYPT_N", 2**4)
        cost.start()
        self.addCleanup(cost.stop)
        self.app = create_app()
        self.admin = self.app.test_client()
        self.admin.post("/api/auth/password", json={"username": "admin", "password": PASSWORD})

    def join(self, name="anna", password=ANNA):
        token = self.admin.post("/api/users/invites", json={"username": name}).get_json()["token"]
        client = self.app.test_client()
        client.post("/api/invites/password", json={"token": token, "password": password})
        return client

    def register(self, uris=(REDIRECT,), name="Claude"):
        response = self.app.test_client().post("/oauth/register", json={"client_name": name, "redirect_uris": list(uris)})
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["client_id"]

    def authorize(self, user, client_id, challenge, **extra):
        params = {"response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT, "code_challenge": challenge,
                  "code_challenge_method": "S256", "state": "xyz", "resource": MCP_URL, **extra}
        page = user.get("/oauth/authorize", query_string=params)
        self.assertEqual(page.status_code, 200, page.get_data(as_text=True)[:300])
        signed = re.search(r'name="request" value="([^"]+)"', page.get_data(as_text=True)).group(1)
        return signed

    def grant(self, user=None, client_id=None):
        """Doorloop de hele OAuth-stroom en geef (tokens, client_id) terug."""
        user = user or self.admin
        client_id = client_id or self.register()
        verifier, challenge = pkce()
        signed = self.authorize(user, client_id, challenge)
        done = user.post("/oauth/authorize", data={"request": signed, "decision": "allow"})
        self.assertEqual(done.status_code, 302)
        query = parse_qs(urlsplit(done.headers["Location"]).query)
        self.assertEqual(query["state"], ["xyz"])
        token = self.app.test_client().post(
            "/oauth/token",
            data={"grant_type": "authorization_code", "code": query["code"][0], "client_id": client_id, "redirect_uri": REDIRECT, "code_verifier": verifier},
        )
        self.assertEqual(token.status_code, 200, token.get_json())
        return token.get_json(), client_id

    def rpc(self, token, method, params=None, request_id=1, **kwargs):
        body = {"jsonrpc": "2.0", "method": method, **({"id": request_id} if request_id is not None else {}), **({"params": params} if params is not None else {})}
        return self.app.test_client().post("/mcp", json=body, headers={"Authorization": f"Bearer {token}"}, **kwargs)

    def call(self, token, name, arguments=None):
        reply = self.rpc(token, "tools/call", {"name": name, "arguments": arguments or {}}).get_json()
        return reply["result"]


class AvailabilityTest(McpTestCase):
    def test_off_without_login(self):
        with mock.patch.dict(os.environ, {"PASSKEY_AUTH_ENABLED": "false"}):
            self.assertEqual(mcp.enabled(), (False, "no_login"))
            self.assertEqual(self.app.test_client().post("/mcp", json={}).status_code, 404)
            self.assertEqual(self.app.test_client().get("/.well-known/oauth-authorization-server").status_code, 404)
            self.assertEqual(self.app.test_client().post("/oauth/register", json={"redirect_uris": [REDIRECT]}).status_code, 404)

    def test_can_be_switched_off(self):
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_MCP": "false"}):
            self.assertEqual(mcp.enabled(), (False, "disabled"))
            self.assertEqual(self.app.test_client().post("/mcp", json={}).status_code, 404)
            status = self.admin.get("/api/mcp/status").get_json()
            self.assertEqual((status["enabled"], status["reason"]), (False, "disabled"))

    def test_on_with_login(self):
        self.assertEqual(mcp.enabled(), (True, ""))


class MetadataTest(McpTestCase):
    def test_protected_resource_and_authorization_server(self):
        client = self.app.test_client()
        resource = client.get("/.well-known/oauth-protected-resource").get_json()
        self.assertEqual((resource["resource"], resource["authorization_servers"]), (MCP_URL, [BASE]))
        self.assertEqual(client.get("/.well-known/oauth-protected-resource/mcp").get_json(), resource)
        meta = client.get("/.well-known/oauth-authorization-server").get_json()
        self.assertEqual(meta["issuer"], BASE)
        self.assertEqual(meta["code_challenge_methods_supported"], ["S256"])
        self.assertEqual(meta["token_endpoint_auth_methods_supported"], ["none"])
        for key in ("authorization_endpoint", "token_endpoint", "registration_endpoint", "revocation_endpoint"):
            self.assertTrue(meta[key].startswith(BASE + "/oauth/"), key)

    def test_public_base_url_is_used_behind_a_proxy(self):
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_PUBLIC_BASE_URL": "https://shop.example.com"}):
            meta = self.app.test_client().get("/.well-known/oauth-authorization-server").get_json()
            self.assertEqual(meta["issuer"], "https://shop.example.com")
            self.assertEqual(self.app.test_client().get("/.well-known/oauth-protected-resource").get_json()["resource"], "https://shop.example.com/mcp")

    def test_cors_for_token_and_metadata(self):
        client = self.app.test_client()
        response = client.options("/oauth/token")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
        self.assertIn("Authorization", response.headers["Access-Control-Allow-Headers"])
        self.assertEqual(client.get("/.well-known/oauth-authorization-server").headers["Access-Control-Allow-Origin"], "*")
        self.assertNotIn("Access-Control-Allow-Origin", client.get("/api/about").headers)


class RegistrationTest(McpTestCase):
    def test_redirect_uri_rules(self):
        good = ["https://claude.ai/api/mcp/auth_callback", "http://localhost:33418/callback", "http://127.0.0.1/cb", "http://[::1]:8000/cb", "vscode://ms-vscode.cursor/auth", "cursor://anysphere.cursor-mcp/oauth/callback"]
        bad = ["http://evil.example.com/cb", "javascript:alert(1)", "data:text/html,x", "file:///etc/passwd", "https://claude.ai/cb#frag", "", "/relative", "https://", "http://localhost.evil.com/cb", "ftp://x.example.com/"]
        for uri in good:
            self.assertTrue(oauth.valid_redirect_uri(uri), uri)
        for uri in bad:
            self.assertFalse(oauth.valid_redirect_uri(uri), uri)
        self.assertFalse(oauth.valid_redirect_uri(None))
        self.assertFalse(oauth.valid_redirect_uri("https://x.example/" + "a" * 2100))

    def test_registration_validates_the_request(self):
        client = self.app.test_client()
        for body in ({}, {"redirect_uris": []}, {"redirect_uris": ["http://evil.example.com/"]}, {"redirect_uris": [REDIRECT], "token_endpoint_auth_method": "client_secret_basic"},
                     {"redirect_uris": [REDIRECT], "grant_types": ["password"]}, {"redirect_uris": ["https://a.example/%d" % i for i in range(11)]}):
            response = client.post("/oauth/register", json=body)
            self.assertEqual(response.status_code, 400, body)
            self.assertIn("error", response.get_json())
        self.assertEqual(client.post("/oauth/register", data="no json").status_code, 400)

    def test_registration_gives_a_public_client(self):
        data = self.app.test_client().post("/oauth/register", json={"client_name": "  Claude\n  Desktop ", "redirect_uris": [REDIRECT]}).get_json()
        self.assertTrue(data["client_id"].startswith("msc_"))
        self.assertEqual(data["client_name"], "Claude Desktop")
        self.assertNotIn("client_secret", data)

    def test_the_number_of_clients_is_limited(self):
        with mock.patch.object(oauth, "MAX_CLIENTS", 2):
            self.register(), self.register()
            response = self.app.test_client().post("/oauth/register", json={"redirect_uris": [REDIRECT]})
            self.assertEqual(response.status_code, 503)

    def test_a_full_registry_makes_room_by_dropping_unused_clients_but_never_used_ones(self):
        with mock.patch.object(oauth, "MAX_CLIENTS", 2):
            _, used = self.grant()
            unused = self.register()
            with closing(oauth._connect()) as conn, conn:
                conn.execute("UPDATE oauth_clients SET created_at = ? WHERE client_id = ?", ((oauth._now() - timedelta(minutes=20)).isoformat(), unused))
            self.register()  # er is plek gemaakt
            self.assertIsNone(oauth.get_client(unused))
            self.assertIsNotNone(oauth.get_client(used))
            self.assertEqual(self.app.test_client().post("/oauth/register", json={"redirect_uris": [REDIRECT]}).status_code, 503)

    def test_stale_unused_clients_are_cleaned_up(self):
        client_id = self.register()
        with closing(oauth._connect()) as conn, conn:
            conn.execute("UPDATE oauth_clients SET created_at = ?", ((oauth._now() - timedelta(days=2)).isoformat(),))
        self.register()
        self.assertIsNone(oauth.get_client(client_id))


class AuthorizeTest(McpTestCase):
    def test_not_logged_in_goes_to_the_login_and_back(self):
        client_id = self.register()
        _, challenge = pkce()
        response = self.app.test_client().get("/oauth/authorize", query_string={"response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT, "code_challenge": challenge, "code_challenge_method": "S256"})
        self.assertEqual(response.status_code, 302)
        location = urlsplit(response.headers["Location"])
        self.assertEqual(location.path, "/")
        back = parse_qs(location.query)["next"][0]
        self.assertTrue(back.startswith("/oauth/authorize?"))
        self.assertIn(client_id, back)

    def test_consent_page_names_the_app_and_the_user(self):
        client_id = self.register(name="Mijn <b>AI</b>")
        _, challenge = pkce()
        page = self.admin.get("/oauth/authorize", query_string={"response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT, "code_challenge": challenge, "code_challenge_method": "S256"})
        html = page.get_data(as_text=True)
        self.assertIn("Mijn &lt;b&gt;AI&lt;/b&gt;", html)
        self.assertNotIn("<b>AI</b>", html)
        self.assertIn("admin", html)
        self.assertIn("kan niets verwijderen", html)
        self.assertEqual(page.headers["Cache-Control"], "no-store")
        self.assertEqual(page.headers["X-Frame-Options"], "DENY")

    def test_bad_client_or_redirect_never_redirects(self):
        client_id = self.register()
        _, challenge = pkce()
        base = {"response_type": "code", "code_challenge": challenge, "code_challenge_method": "S256"}
        for params in ({**base, "client_id": "unknown", "redirect_uri": REDIRECT}, {**base, "client_id": client_id, "redirect_uri": "https://evil.example.com/cb"}):
            response = self.admin.get("/oauth/authorize", query_string=params)
            self.assertEqual(response.status_code, 400)
            self.assertNotIn("Location", response.headers)

    def test_protocol_errors_go_back_to_the_app(self):
        client_id = self.register()
        _, challenge = pkce()
        good = {"response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT, "code_challenge": challenge, "code_challenge_method": "S256", "state": "s1"}
        cases = [
            ({"response_type": "token"}, "unsupported_response_type"),
            ({"code_challenge_method": "plain"}, "invalid_request"),
            ({"code_challenge": ""}, "invalid_request"),
            ({"resource": "https://other.example.com/mcp"}, "invalid_target"),
            ({"scope": "admin"}, "invalid_scope"),
        ]
        for change, error in cases:
            response = self.admin.get("/oauth/authorize", query_string={**good, **change})
            self.assertEqual(response.status_code, 302, change)
            query = parse_qs(urlsplit(response.headers["Location"]).query)
            self.assertEqual((query["error"], query["state"]), ([error], ["s1"]), change)

    def test_deny_and_allow(self):
        client_id = self.register()
        _, challenge = pkce()
        signed = self.authorize(self.admin, client_id, challenge)
        denied = self.admin.post("/oauth/authorize", data={"request": signed, "decision": "deny"})
        self.assertEqual(parse_qs(urlsplit(denied.headers["Location"]).query)["error"], ["access_denied"])
        allowed = self.admin.post("/oauth/authorize", data={"request": signed, "decision": "allow"})
        self.assertIn("code", parse_qs(urlsplit(allowed.headers["Location"]).query))

    def test_the_decision_is_protected_against_forgery(self):
        client_id = self.register()
        _, challenge = pkce()
        signed = self.authorize(self.admin, client_id, challenge)
        post = lambda client, **kw: client.post("/oauth/authorize", data={"request": kw.get("request", signed), "decision": "allow"}, headers=kw.get("headers", {}))
        self.assertEqual(post(self.admin, request=signed + "x").status_code, 400)  # aangepaste ondertekening
        self.assertEqual(post(self.admin, headers={"Origin": "https://evil.example.com"}).status_code, 403)
        self.assertEqual(post(self.app.test_client()).status_code, 401)  # niet ingelogd
        self.assertEqual(post(self.admin, headers={"Origin": BASE}).status_code, 302)

    def test_the_consent_belongs_to_the_user_who_saw_it(self):
        anna = self.join()
        client_id = self.register()
        _, challenge = pkce()
        signed = self.authorize(anna, client_id, challenge)
        stolen = self.admin.post("/oauth/authorize", data={"request": signed, "decision": "allow"})
        self.assertEqual(stolen.status_code, 403)

    def test_loopback_redirect_may_pick_its_own_port(self):
        client_id = self.register(("http://localhost:33418/callback",))
        _, challenge = pkce()
        page = self.admin.get("/oauth/authorize", query_string={"response_type": "code", "client_id": client_id, "redirect_uri": "http://localhost:51234/callback", "code_challenge": challenge, "code_challenge_method": "S256"})
        self.assertEqual(page.status_code, 200)
        other_path = self.admin.get("/oauth/authorize", query_string={"response_type": "code", "client_id": client_id, "redirect_uri": "http://localhost:51234/other", "code_challenge": challenge, "code_challenge_method": "S256"})
        self.assertEqual(other_path.status_code, 400)


class TokenTest(McpTestCase):
    def exchange(self, client_id, code, verifier, **extra):
        form = {"grant_type": "authorization_code", "code": code, "client_id": client_id, "redirect_uri": REDIRECT, "code_verifier": verifier, **extra}
        return self.app.test_client().post("/oauth/token", data=form)

    def code(self, user=None, client_id=None):
        client_id = client_id or self.register()
        verifier, challenge = pkce()
        signed = self.authorize(user or self.admin, client_id, challenge)
        done = (user or self.admin).post("/oauth/authorize", data={"request": signed, "decision": "allow"})
        return client_id, verifier, parse_qs(urlsplit(done.headers["Location"]).query)["code"][0]

    def test_full_flow_returns_bearer_tokens(self):
        tokens, _ = self.grant()
        self.assertEqual((tokens["token_type"], tokens["expires_in"], tokens["scope"]), ("Bearer", 3600, "mealieshopper"))
        self.assertTrue(tokens["access_token"].startswith("msa_") and tokens["refresh_token"].startswith("msr_"))
        with closing(oauth._connect()) as conn:
            stored = json.dumps([dict(r) for r in conn.execute("SELECT * FROM oauth_tokens")] + [dict(r) for r in conn.execute("SELECT * FROM oauth_codes")])
        self.assertNotIn(tokens["access_token"], stored)
        self.assertNotIn(tokens["refresh_token"], stored)

    def test_token_endpoint_accepts_json_too(self):
        client_id, verifier, code = self.code()
        response = self.app.test_client().post("/oauth/token", json={"grant_type": "authorization_code", "code": code, "client_id": client_id, "redirect_uri": REDIRECT, "code_verifier": verifier})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_wrong_pkce_verifier_fails(self):
        client_id, _, code = self.code()
        response = self.exchange(client_id, code, pkce()[0])
        self.assertEqual((response.status_code, response.get_json()["error"]), (400, "invalid_grant"))

    def test_code_checks(self):
        client_id, verifier, code = self.code()
        other = self.register(name="Andere")
        self.assertEqual(self.exchange(other, code, verifier).status_code, 400)  # andere app
        self.assertEqual(self.exchange(client_id, code, verifier, redirect_uri="https://claude.ai/other").status_code, 400)
        self.assertEqual(self.exchange(client_id, code, verifier, resource="https://other.example.com/mcp").status_code, 400)
        self.assertEqual(self.exchange(client_id, "nonsense", verifier).status_code, 400)
        self.assertEqual(self.exchange(client_id, code, "kort").status_code, 400)
        self.assertEqual(self.exchange(client_id, code, verifier).status_code, 200)

    def test_a_code_works_once_and_reuse_revokes_the_tokens(self):
        client_id, verifier, code = self.code()
        first = self.exchange(client_id, code, verifier).get_json()
        self.assertEqual(self.rpc(first["access_token"], "ping").status_code, 200)
        self.assertEqual(self.exchange(client_id, code, verifier).status_code, 400)
        self.assertEqual(self.rpc(first["access_token"], "ping").status_code, 401)

    def test_expired_code(self):
        client_id, verifier, code = self.code()
        with closing(oauth._connect()) as conn, conn:
            conn.execute("UPDATE oauth_codes SET expires_at = ?", ((oauth._now() - timedelta(seconds=1)).isoformat(),))
        self.assertEqual(self.exchange(client_id, code, verifier).status_code, 400)

    def test_unknown_grant_type(self):
        response = self.app.test_client().post("/oauth/token", data={"grant_type": "password"})
        self.assertEqual(response.get_json()["error"], "unsupported_grant_type")

    def test_refresh_rotates_and_a_replay_revokes_everything(self):
        tokens, client_id = self.grant()
        refresh = lambda token, cid=client_id: self.app.test_client().post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": token, "client_id": cid})
        second = refresh(tokens["refresh_token"])
        self.assertEqual(second.status_code, 200)
        new = second.get_json()
        self.assertNotEqual(new["access_token"], tokens["access_token"])
        self.assertEqual(self.rpc(new["access_token"], "ping").status_code, 200)
        replay = refresh(tokens["refresh_token"])  # het oude token is al gebruikt
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(self.rpc(new["access_token"], "ping").status_code, 401)  # de hele reeks is ingetrokken
        self.assertEqual(refresh(new["refresh_token"]).status_code, 400)
        self.assertEqual(refresh("msr_nope").status_code, 400)

    def test_refresh_needs_the_same_client(self):
        tokens, _ = self.grant()
        response = self.app.test_client().post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": self.register(name="x")})
        self.assertEqual(response.status_code, 400)

    def test_revoke(self):
        tokens, _ = self.grant()
        client = self.app.test_client()
        self.assertEqual(client.post("/oauth/revoke", data={"token": "unknown"}).status_code, 200)
        self.assertEqual(client.post("/oauth/revoke", data={"token": tokens["refresh_token"]}).status_code, 200)
        self.assertEqual(self.rpc(tokens["access_token"], "ping").status_code, 401)

    def test_expired_access_token(self):
        tokens, _ = self.grant()
        with closing(oauth._connect()) as conn, conn:
            conn.execute("UPDATE oauth_tokens SET access_expires = ?", ((oauth._now() - timedelta(seconds=1)).isoformat(),))
        self.assertEqual(self.rpc(tokens["access_token"], "ping").status_code, 401)


class UserBindingTest(McpTestCase):
    def test_a_token_acts_for_the_user_who_gave_consent(self):
        anna = self.join()
        tokens, _ = self.grant(anna)
        self.assertEqual(oauth.verify_access_token(tokens["access_token"], MCP_URL)["username"], "anna")
        mine = anna.get("/api/mcp/status").get_json()["apps"]
        self.assertEqual([a["name"] for a in mine], ["Claude"])
        self.assertEqual(self.admin.get("/api/mcp/status").get_json()["apps"], [])  # de beheerder ziet de apps van Anna niet

    def test_users_cannot_revoke_each_others_apps(self):
        anna = self.join()
        _, client_id = self.grant(anna)
        self.assertEqual(self.admin.delete(f"/api/mcp/apps/{client_id}").status_code, 404)
        self.assertEqual(anna.delete(f"/api/mcp/apps/{client_id}").status_code, 200)
        self.assertEqual(anna.get("/api/mcp/status").get_json()["apps"], [])

    def test_revoking_cuts_off_the_token(self):
        tokens, client_id = self.grant()
        self.assertEqual(self.rpc(tokens["access_token"], "ping").status_code, 200)
        self.assertEqual(self.admin.delete(f"/api/mcp/apps/{client_id}").status_code, 200)
        self.assertEqual(self.rpc(tokens["access_token"], "ping").status_code, 401)

    def test_a_new_password_cuts_off_that_users_apps_only(self):
        anna = self.join()
        anna_tokens, _ = self.grant(anna)
        admin_tokens, _ = self.grant(self.admin)
        anna.post("/api/auth/password", json={"password": "nieuw-wachtwoord-123", "currentPassword": ANNA})
        self.assertEqual(self.rpc(anna_tokens["access_token"], "ping").status_code, 401)
        self.assertEqual(self.rpc(admin_tokens["access_token"], "ping").status_code, 200)
        refresh = self.app.test_client().post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": anna_tokens["refresh_token"], "client_id": self.register(name="x")})
        self.assertEqual(refresh.status_code, 400)

    def test_deleting_the_user_removes_their_apps(self):
        anna = self.join()
        tokens, _ = self.grant(anna)
        anna_id = anna.get("/api/auth/status").get_json()["userId"]
        self.admin.delete(f"/api/users/{anna_id}")
        self.assertEqual(self.rpc(tokens["access_token"], "ping").status_code, 401)
        with closing(oauth._connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM oauth_tokens").fetchone()[0], 0)

    def test_status_requires_a_session(self):
        self.assertEqual(self.app.test_client().get("/api/mcp/status").status_code, 401)
        self.assertEqual(self.app.test_client().delete("/api/mcp/apps/x").status_code, 401)
        status = self.admin.get("/api/mcp/status").get_json()
        self.assertEqual((status["enabled"], status["url"]), (True, MCP_URL))
        self.assertIn("import_recipe_to_mealie", [t["name"] for t in status["tools"]])

    def test_a_session_cookie_is_not_a_token_and_a_token_is_not_a_session(self):
        tokens, _ = self.grant()
        client = self.app.test_client()
        self.assertEqual(client.get("/api/ah/lists", headers={"Authorization": f"Bearer {tokens['access_token']}"}).status_code, 401)
        cookie = self.admin.get_cookie(auth.SESSION_COOKIE_NAME).value
        self.assertEqual(self.app.test_client().post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers={"Authorization": f"Bearer {cookie}"}).status_code, 401)


class LoginReturnTest(McpTestCase):
    def test_the_app_sends_you_back_to_the_consent_page_after_logging_in(self):
        # De server stuurt naar /?next=...; het script van de pagina moet na het inloggen terugkeren,
        # maar alleen naar de toestemmingspagina (geen open redirect).
        source = (Path(__file__).resolve().parent.parent / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function continueAfterLogin", source)
        self.assertIn(r"/^\/oauth\/authorize\?/.test(next)", source)
        self.assertIn("window.location.replace(next)", source)
        self.assertLess(source.index("if (continueAfterLogin())"), source.index("await loadCredentials();"))


class SettingsEffectTest(McpTestCase):
    def test_changing_the_public_address_makes_apps_ask_permission_again(self):
        tokens, _ = self.grant()
        self.assertEqual(self.rpc(tokens["access_token"], "ping").status_code, 200)
        self.assertEqual(self.admin.put("/api/server-settings", json={"publicBaseUrl": "https://shop.example.com"}).status_code, 200)
        # Het token is uitgegeven voor het oude adres van de MCP-server.
        response = self.app.test_client().post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, base_url="https://shop.example.com",
                                               headers={"Authorization": f"Bearer {tokens['access_token']}"})
        self.assertEqual(response.status_code, 401)

    def test_a_token_stops_answering_while_the_server_is_switched_off_and_works_again_after(self):
        tokens, _ = self.grant()
        self.admin.put("/api/server-settings", json={"mcpEnabled": False})
        self.assertEqual(self.rpc(tokens["access_token"], "ping").status_code, 404)
        self.admin.put("/api/server-settings", json={"mcpEnabled": True})
        self.assertEqual(self.rpc(tokens["access_token"], "ping").status_code, 200)


class ProtocolTest(McpTestCase):
    def setUp(self):
        super().setUp()
        self.token = self.grant()[0]["access_token"]

    def test_unauthorized_points_to_the_metadata(self):
        for headers in ({}, {"Authorization": "Bearer msa_wrong"}, {"Authorization": "Basic abc"}):
            response = self.app.test_client().post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=headers)
            self.assertEqual(response.status_code, 401)
            challenge = response.headers["WWW-Authenticate"]
            self.assertIn(f'resource_metadata="{BASE}/.well-known/oauth-protected-resource"', challenge)
        self.assertIn("invalid_token", self.app.test_client().post("/mcp", json={}, headers={"Authorization": "Bearer msa_wrong"}).headers["WWW-Authenticate"])

    def test_initialize_negotiates_the_version(self):
        for asked, expected in (("2025-03-26", "2025-03-26"), ("2025-06-18", "2025-06-18"), ("1999-01-01", mcp.LATEST_PROTOCOL), (None, mcp.LATEST_PROTOCOL)):
            result = self.rpc(self.token, "initialize", {"protocolVersion": asked, "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}).get_json()["result"]
            self.assertEqual(result["protocolVersion"], expected)
            self.assertEqual(result["serverInfo"]["name"], "mealieshopper")
            self.assertIn("tools", result["capabilities"])
            self.assertIn("AH-mandje vullen kan niet", result["instructions"])

    def test_notifications_get_no_body(self):
        response = self.rpc(self.token, "notifications/initialized", request_id=None)
        self.assertEqual((response.status_code, response.get_data()), (202, b""))

    def test_batches(self):
        batch = [{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "method": "notifications/initialized"}, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
        replies = self.app.test_client().post("/mcp", json=batch, headers={"Authorization": f"Bearer {self.token}"}).get_json()
        self.assertEqual([r["id"] for r in replies], [1, 2])

    def test_errors(self):
        auth_header = {"Authorization": f"Bearer {self.token}"}
        client = self.app.test_client()
        self.assertEqual(client.post("/mcp", data="{kapot", headers=auth_header).get_json()["error"]["code"], -32700)
        self.assertEqual(client.post("/mcp", json={"jsonrpc": "1.0", "id": 1, "method": "ping"}, headers=auth_header).get_json()["error"]["code"], -32600)
        self.assertEqual(self.rpc(self.token, "bogus/method").get_json()["error"]["code"], -32601)
        self.assertEqual(self.rpc(self.token, "tools/call", {"name": "nope"}).get_json()["error"]["code"], -32602)
        self.assertEqual(self.rpc(self.token, "tools/list", [1]).get_json()["error"]["code"], -32602)
        self.assertEqual(client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers={**auth_header, "Mcp-Protocol-Version": "1999-01-01"}).status_code, 400)
        self.assertEqual(client.post("/mcp", data="x" * 2_000_000, headers=auth_header).status_code, 413)

    def test_no_streams_and_no_sessions(self):
        for method in ("get", "delete"):
            response = getattr(self.app.test_client(), method)("/mcp", headers={"Authorization": f"Bearer {self.token}"})
            self.assertEqual(response.status_code, 405)
            self.assertEqual(response.headers["Allow"], "POST")

    def test_tools_list_has_schemas_and_honest_annotations(self):
        tools = {t["name"]: t for t in self.rpc(self.token, "tools/list").get_json()["result"]["tools"]}
        self.assertEqual(set(tools), {"get_week_menu", "search_allerhande_recipes", "list_saved_ah_recipes", "list_ah_lists", "get_ah_list", "search_ah_list_items", "search_ah_products", "import_recipe_to_mealie",
                             "get_ah_basket", "list_ah_orders", "get_ah_order", "list_ah_receipts", "get_ah_receipt", "search_ah_purchases",
                             "search_picnic_products", "get_picnic_basket", "list_picnic_deliveries",
                             "list_picnic_delivery_slots", "list_picnic_recipes", "list_picnic_favorites", "search_jumbo_products", "get_jumbo_basket", "get_jumbo_list", "list_jumbo_lists",
                         "list_jumbo_orders", "get_jumbo_order", "search_jumbo_recipes", "list_jumbo_favorite_recipes", "get_jumbo_recipe"})
        for name, tool in tools.items():
            self.assertEqual(tool["inputSchema"]["type"], "object", name)
            self.assertFalse(tool["inputSchema"]["additionalProperties"], name)
            self.assertTrue(tool["description"], name)
            self.assertIs(tool["annotations"]["destructiveHint"], False, name)
        self.assertEqual([n for n, t in tools.items() if not t["annotations"]["readOnlyHint"]], ["import_recipe_to_mealie"])
        # Geen enkele tool vult het mandje of verwijdert iets.
        # De tools die over het mandje of bestellingen gaan, lezen alleen; er is geen tool die iets toevoegt, wijzigt of verwijdert.
        for name, tool in tools.items():
            if any(word in name for word in ("cart", "basket", "order", "receipt", "delete", "remove", "clear")):
                self.assertTrue(name.startswith(("get_", "list_", "search_")), name)
                self.assertTrue(tool["annotations"]["readOnlyHint"], name)
        self.assertFalse(any(name.startswith(("add_", "fill_", "place_", "delete_", "remove_", "clear_", "update_")) for name in tools))


class PurchaseToolsTest(McpTestCase):
    def setUp(self):
        super().setUp()
        self.token = self.grant()[0]["access_token"]

    def data(self, name, args=None):
        result = self.call(self.token, name, args)
        self.assertFalse(result["isError"], result["content"][0]["text"])
        return json.loads(result["content"][0]["text"])

    def seed(self):
        from mealieshopper import ah_orders

        ah_orders.save_basket({"id": 9, "state": "OPEN", "total": 4.5, "items": [{"productId": 1, "title": "Koffiebonen", "quantity": 2, "amount": 4.5}]})
        with mock.patch.object(ah, "get_open_fulfillments", return_value=[{"id": 5, "state": "SCHEDULED", "total": 20.0, "deliveryDate": "2026-10-20"}]), mock.patch.object(ah, "get_closed_fulfillments", return_value=[]), mock.patch.object(
            ah, "get_order_details", return_value={"id": 5, "state": "DELIVERED", "deliveryDate": "2026-10-20", "items": [{"productId": 1, "title": "Koffiebonen", "brand": "AH", "quantity": 1, "amount": 5.0, "taxonomy": "Koffie"}]}
        ):
            ah_orders.sync_orders()
        with mock.patch.object(ah, "get_receipts", return_value=[{"id": "R1", "dateTime": "2026-10-01T10:00:00", "total": 5.0}]), mock.patch.object(
            ah, "get_receipt", return_value={"id": "R1", "items": [{"productId": 1, "name": "Koffiebonen", "quantity": 1, "price": 5.0, "amount": 5.0}], "discounts": [], "payments": [{"method": "PIN", "amount": 5.0}]}
        ):
            ah_orders.sync_receipts()

    def test_empty_copy_says_so(self):
        basket = self.data("get_ah_basket")
        self.assertEqual((basket["itemCount"], basket["syncedAt"]), (0, None))
        self.assertIn("Nog niet gesynchroniseerd", basket["note"])
        self.assertIn("Nog niet gesynchroniseerd", self.data("list_ah_orders")["note"])

    def test_tools_read_the_local_copy_and_never_ask_ah(self):
        self.seed()
        with mock.patch.object(ah, "_get_json", side_effect=AssertionError("AH")), mock.patch.object(ah, "_graphql", side_effect=AssertionError("AH")):
            basket = self.data("get_ah_basket")
            orders = self.data("list_ah_orders")
            order = self.data("get_ah_order", {"order_id": 5})
            receipts = self.data("list_ah_receipts")
            receipt = self.data("get_ah_receipt", {"receipt_id": "R1"})
            found = self.data("search_ah_purchases", {"query": "koffie"})
        self.assertIn("niet de boodschappenlijst", basket["note"])
        self.assertEqual((basket["units"], basket["total"]), (2, 4.5))
        self.assertEqual((orders["total"], orders["orders"][0]["id"], orders["orders"][0]["itemCount"]), (1, 5, 1))
        self.assertEqual((order["items"][0]["title"], order["items"][0]["taxonomy"]), ("Koffiebonen", "Koffie"))
        self.assertEqual((receipts["receipts"][0]["id"], receipt["payments"][0]["method"]), ("R1", "PIN"))
        self.assertEqual((found["total"], found["products"][0]["product"]), (2, "Koffiebonen"))

    def test_unknown_ids_and_bad_input_are_tool_errors(self):
        self.seed()
        for name, args in (("get_ah_order", {"order_id": 999}), ("get_ah_order", {"order_id": "x"}), ("get_ah_order", {}), ("get_ah_receipt", {"receipt_id": "nope"}), ("search_ah_purchases", {"query": " "})):
            self.assertTrue(self.call(self.token, name, args)["isError"], (name, args))

    def test_limits(self):
        self.seed()
        self.assertEqual(len(self.data("list_ah_orders", {"limit": 1})["orders"]), 1)
        self.assertEqual(len(self.data("search_ah_purchases", {"query": "koffie", "limit": 1})["occurrences"]), 1)


class ToolsTest(McpTestCase):
    def setUp(self):
        super().setUp()
        self.token = self.grant()[0]["access_token"]

    def text(self, result):
        return result["content"][0]["text"]

    def data(self, name, args=None):
        result = self.call(self.token, name, args)
        self.assertFalse(result["isError"], self.text(result))
        return json.loads(self.text(result))

    def test_picnic_recipes_tool(self):
        recipes = [{"id": f"r{n}", "name": f"Recept {n}", "image": "https://x/y.png", "minutes": 20, "tags": [], "segment": None} for n in range(5)]
        with mock.patch.object(picnic, "configured_auth_key", return_value="key"), \
                mock.patch.object(picnic_recipes, "get_recipes", return_value=recipes) as get:
            default = self.data("list_picnic_recipes")
            cookbook = self.data("list_picnic_recipes", {"source": "cookbook", "limit": 2})
        self.assertEqual((default["source"], default["total"], len(default["recipes"])), ("saved", 5, 5))
        self.assertEqual((cookbook["source"], cookbook["total"], len(cookbook["recipes"])), ("cookbook", 5, 2))
        self.assertEqual([c.args for c in get.call_args_list], [("saved",), ("cookbook",)])
        self.assertEqual(set(default["recipes"][0]), {"id", "name", "minutes", "tags"})  # geen afbeeldingsadressen

    def test_picnic_favorites_tool(self):
        products = [{"id": f"s{n}", "title": f"Product {n}", "unitSize": "1 l", "price": 1.5, "isBonus": n == 0,
                     "image": "https://x/y.png", "maxCount": 5} for n in range(4)]
        with mock.patch.object(picnic, "configured_auth_key", return_value="key"), \
                mock.patch.object(picnic, "get_favorites", return_value={"products": products, "total": 40, "filtered": True}):
            data = self.data("list_picnic_favorites", {"limit": 2})
            self.assertEqual((data["total"], len(data["products"]), data["onlyFavorites"]), (4, 2, True))
            self.assertEqual(set(data["products"][0]), {"title", "size", "price", "promotion"})
        with mock.patch.object(picnic, "configured_auth_key", return_value=""):
            self.assertTrue(self.call(self.token, "list_picnic_favorites")["isError"])

    def test_picnic_recipes_tool_refuses_unknown_sources_and_an_unlinked_account(self):
        with mock.patch.object(picnic, "configured_auth_key", return_value="key"), \
                mock.patch.object(picnic_recipes, "get_recipes", side_effect=AssertionError("niet ophalen")):
            self.assertTrue(self.call(self.token, "list_picnic_recipes", {"source": "boodschappen"})["isError"])
        with mock.patch.object(picnic, "configured_auth_key", return_value=""):
            self.assertTrue(self.call(self.token, "list_picnic_recipes")["isError"])

    def test_week_menu(self):
        entries = [
            {"date": "2026-10-20", "entryType": "dinner", "recipe": {"name": "Pasta", "slug": "pasta"}, "recipeDetail": {"recipeIngredient": [{"display": "400 g  spaghetti"}, {"note": "peper"}, {}]}},
            {"date": "2026-10-19", "entryType": "dinner", "title": "Restjes", "recipe": None, "recipeDetail": None},
        ]
        with mock.patch.object(mealie, "get_meal_plan_with_recipes", return_value=entries) as plan, mock.patch.object(mealie, "mealie_url", return_value="https://mealie.example"):
            data = self.data("get_week_menu", {"start_date": "2026-10-19", "days": 3})
        plan.assert_called_once_with("2026-10-19", "2026-10-21")
        self.assertEqual([m["date"] for m in data["meals"]], ["2026-10-19", "2026-10-20"])
        self.assertEqual(data["meals"][0]["recipe"], "Restjes")
        self.assertEqual(data["meals"][1]["url"], "https://mealie.example/recipe/pasta")
        self.assertEqual(data["meals"][1]["ingredients"], ["400 g spaghetti", "peper"])

    def test_week_menu_defaults_to_this_week_and_validates(self):
        with mock.patch.object(mealie, "get_meal_plan_with_recipes", return_value=[]) as plan:
            self.data("get_week_menu")
        start, end = plan.call_args.args
        self.assertEqual((__import__("datetime").date.fromisoformat(end) - __import__("datetime").date.fromisoformat(start)).days, 6)
        self.assertEqual(__import__("datetime").date.fromisoformat(start).weekday(), 0)
        bad = self.call(self.token, "get_week_menu", {"start_date": "morgen"})
        self.assertTrue(bad["isError"])
        self.assertIn("JJJJ-MM-DD", self.text(bad))

    def test_search_recipes(self):
        found = {"total": 5, "hasNextPage": True, "recipes": [{"title": "Pesto", "webPath": "/allerhande/recept/R-R1/pesto", "totalTime": 20, "servings": 4}]}
        with mock.patch.object(ah, "search_recipes", return_value=found) as search:
            data = self.data("search_allerhande_recipes", {"query": " pasta  pesto ", "page": 1})
        search.assert_called_once_with("pasta pesto", 1)
        self.assertEqual(data["recipes"][0]["url"], "https://www.ah.nl/allerhande/recept/R-R1/pesto")
        self.assertTrue(self.call(self.token, "search_allerhande_recipes", {"query": "  "})["isError"])

    def test_saved_recipes_filter_on_all_words(self):
        saved = {"recipes": [{"title": "Pasta pesto", "url": "u1"}, {"title": "Pasta carbonara", "url": "u2"}], "total": 2, "syncedAt": "x"}
        with mock.patch.object(ah_recipes, "get", return_value=saved):
            self.assertEqual(self.data("list_saved_ah_recipes", {"query": "pasta PESTO"})["total"], 1)
            self.assertEqual(self.data("list_saved_ah_recipes")["total"], 2)

    def test_lists(self):
        ah_lists.save_snapshot({"id": "abc", "name": "Weekboodschappen", "items": [
            {"id": "1", "productId": 10, "quantity": 2, "url": "", "product": {"title": "Melk", "brand": "AH", "unitSize": "1 l", "price": 1.25, "isBonus": True}}]})
        ah_lists.set_quantity("abc", 0, 5)
        with mock.patch.object(ah, "get_favorite_lists", return_value=[{"id": "ABC", "name": "Weekboodschappen", "itemCount": 1}]):
            lists = self.data("list_ah_lists")
        self.assertEqual((lists["lists"][0]["id"], lists["lists"][0]["synced"]), ("ABC", True))
        with mock.patch.object(ah, "get_favorite_list_items", side_effect=AssertionError("lokaal")):
            detail = self.data("get_ah_list", {"list_id": "abc"})
        item = detail["items"][0]
        self.assertEqual((item["product"], item["quantity"], item["ahQuantity"], item["bonus"]), ("Melk", 5, 2, True))
        self.assertEqual(detail["source"], "lokale kopie")
        self.assertEqual(self.data("search_ah_list_items", {"query": "melk"})["matches"][0]["list"], "Weekboodschappen")
        self.assertTrue(self.call(self.token, "get_ah_list", {})["isError"])

    def test_unsynced_list_is_read_live(self):
        live = {"id": "ZZZ", "name": "Live", "items": [{"productId": 1, "quantity": 1, "url": "", "product": {"title": "Brood"}}]}
        with mock.patch.object(ah, "get_favorite_list_items", return_value=live):
            self.assertEqual(self.data("get_ah_list", {"list_id": "zzz"})["source"], "live van AH")

    def test_long_lists_are_cut_off(self):
        items = [{"productId": i, "quantity": 1, "url": "", "product": {"title": f"P{i}"}} for i in range(1, 260)]
        with mock.patch.object(ah, "get_favorite_list_items", return_value={"id": "BIG", "name": "Groot", "items": items}):
            data = self.data("get_ah_list", {"list_id": "big"})
        self.assertEqual((len(data["items"]), data["total"]), (200, 259))

    def test_products(self):
        found = [{"webshopId": "42", "title": "Melk", "brand": "AH", "unitSize": "1 l", "price": {"now": 1.25}, "isBonus": False}]
        with mock.patch.object(ah, "search_products", return_value=found) as search:
            data = self.data("search_ah_products", {"query": "melk", "limit": 50})
        search.assert_called_once_with("melk", 10)  # het maximum
        self.assertEqual((data["products"][0]["price"], data["products"][0]["url"]), (1.25, "https://www.ah.nl/producten/product/wi42"))

    def test_import_recipe(self):
        with mock.patch.object(mealie, "import_from_url", return_value="pasta-pesto") as imp, mock.patch.object(mealie, "mealie_url", return_value="https://mealie.example"):
            data = self.data("import_recipe_to_mealie", {"url": "https://www.ah.nl/allerhande/recept/R-R1/pesto"})
        imp.assert_called_once()
        self.assertEqual(data, {"slug": "pasta-pesto", "mealieUrl": "https://mealie.example/recipe/pasta-pesto"})

    def test_import_refuses_addresses_on_your_own_network(self):
        with mock.patch.object(mealie, "import_from_url") as imp:
            for url in ("http://localhost:9000/x", "http://127.0.0.1/x", "http://192.168.1.10/recipe", "http://10.0.0.5/", "http://169.254.169.254/latest/meta-data",
                        "http://[::1]/x", "http://nas/recept", "http://mealie.local/x", "http://box.lan/x", "http://service.internal/x", "https://LOCALHOST./x"):
                result = self.call(self.token, "import_recipe_to_mealie", {"url": url})
                self.assertTrue(result["isError"], url)
                self.assertIn("eigen netwerk", self.text(result), url)
        imp.assert_not_called()
        with mock.patch.object(mealie, "import_from_url", return_value="x"), mock.patch.object(mealie, "mealie_url", return_value="https://m.example"):
            for url in ("https://www.ah.nl/allerhande/recept/R-R1/x", "http://93.184.216.34/recipe", "https://recepten.example.org/pasta"):
                self.assertFalse(self.call(self.token, "import_recipe_to_mealie", {"url": url})["isError"], url)

    def test_import_refuses_other_schemes(self):
        with mock.patch.object(mealie, "import_from_url") as imp:
            for url in ("file:///etc/passwd", "javascript:alert(1)", "ftp://x.example/recipe", "gewoon tekst", "http://"):
                self.assertTrue(self.call(self.token, "import_recipe_to_mealie", {"url": url})["isError"], url)
        imp.assert_not_called()

    def test_service_errors_come_back_as_tool_errors(self):
        with mock.patch.object(mealie, "get_meal_plan_with_recipes", side_effect=RuntimeError("Mealie onbereikbaar")):
            result = self.call(self.token, "get_week_menu")
        self.assertEqual((result["isError"], self.text(result)), (True, "Mealie onbereikbaar"))

    def test_bad_arguments(self):
        self.assertTrue(self.call(self.token, "search_ah_products", {"query": "melk", "limit": "veel"})["isError"])
        reply = self.rpc(self.token, "tools/call", {"name": "list_ah_lists", "arguments": "nee"}).get_json()
        self.assertTrue(reply["result"]["isError"])


if __name__ == "__main__":
    unittest.main()


class ToolSwitchTest(McpTestCase):
    def setUp(self):
        super().setUp()
        self.token = self.grant()[0]["access_token"]

    def switch(self, name, enabled):
        from mealieshopper import server_settings

        server_settings.set_tools_enabled({name: enabled})

    def listed(self):
        return {tool["name"] for tool in self.rpc(self.token, "tools/list").get_json()["result"]["tools"]}

    def test_a_switched_off_tool_is_hidden_and_refused(self):
        self.assertIn("get_week_menu", self.listed())
        self.switch("get_week_menu", False)
        self.assertNotIn("get_week_menu", self.listed())
        self.assertIn("get_ah_list", self.listed())
        result = self.call(self.token, "get_week_menu")
        self.assertTrue(result["isError"])
        self.assertIn("uitgezet", result["content"][0]["text"])

    def test_switching_it_back_on_restores_it(self):
        self.switch("get_week_menu", False)
        self.switch("get_week_menu", True)
        self.assertIn("get_week_menu", self.listed())

    def test_the_status_marks_disabled_tools(self):
        user = self.join()
        self.switch("search_ah_products", False)
        tools = {tool["name"]: tool["enabled"] for tool in user.get("/api/mcp/status").get_json()["tools"]}
        self.assertFalse(tools["search_ah_products"])
        self.assertTrue(tools["get_week_menu"])
