import base64
import hashlib
import json
import os
import struct
import tempfile
import unittest
from contextlib import closing
from datetime import timedelta
from unittest import mock

import cbor2
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.hashes import SHA256

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import auth, auth_password, users  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

PASSWORD = "koffie-met-melk-42"
ANNA = "een-heel-ander-wachtwoord"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class FakeAuthenticator:
    """Een passkey zoals een telefoon die maakt, genoeg voor de server om te controleren."""

    def __init__(self, rp_id="localhost", origin="http://localhost"):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(16)
        self.rp_hash = hashlib.sha256(rp_id.encode()).digest()
        self.origin = origin

    def _client_data(self, kind, challenge):
        return json.dumps({"type": kind, "challenge": challenge, "origin": self.origin}).encode()

    def create(self, challenge):
        numbers = self.key.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")})
        auth_data = self.rp_hash + b"\x45" + struct.pack(">I", 0) + b"\0" * 16 + struct.pack(">H", len(self.credential_id)) + self.credential_id + cose
        return {
            "id": b64(self.credential_id),
            "response": {
                "clientDataJSON": b64(self._client_data("webauthn.create", challenge)),
                "attestationObject": b64(cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})),
            },
        }

    def get(self, challenge):
        client_data = self._client_data("webauthn.get", challenge)
        auth_data = self.rp_hash + b"\x05" + struct.pack(">I", 1)
        signature = self.key.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(SHA256()))
        return {
            "id": b64(self.credential_id),
            "response": {"clientDataJSON": b64(client_data), "authenticatorData": b64(auth_data), "signature": b64(signature)},
        }


class UsersTestCase(unittest.TestCase):
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
        self.post("/api/auth/password", {"username": "admin", "password": PASSWORD}, self.admin)

    def post(self, path, body=None, client=None):
        return (client or self.admin).post(path, json=body or {})

    def invite(self, username="", client=None):
        response = self.post("/api/users/invites", {"username": username}, client)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def join(self, username="anna", password=ANNA):
        """Nodig een gebruiker uit en laat die een wachtwoord kiezen. Geeft de client van die gebruiker."""
        token = self.invite()["token"]
        client = self.app.test_client()
        response = self.post("/api/invites/password", {"token": token, "username": username, "password": password}, client)
        self.assertEqual(response.status_code, 200, response.get_json())
        return client


class MigrationTest(UsersTestCase):
    def test_existing_sessions_of_the_owner_survive_the_upgrade(self):
        with closing(auth.connect()) as conn, conn:
            conn.executescript(
                """
                DROP TABLE users; CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
                INSERT INTO users VALUES ('owner', 'admin', 'x');
                INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES ('SESSION_EPOCH', '3', 'x');
                """
            )
        auth.init_db()
        self.assertEqual(auth.session_epoch("owner"), 3)


class InviteTest(UsersTestCase):
    def test_only_the_administrator_manages_users(self):
        client = self.join()
        for method, path in (("get", "/api/users"), ("post", "/api/users/invites"), ("post", "/api/users/owner/reset"), ("delete", "/api/users/owner")):
            response = getattr(client, method)(path)
            self.assertEqual(response.status_code, 403, path)
        self.assertEqual(self.admin.get("/api/users").status_code, 200)

    def test_invite_creates_an_account_with_its_own_password(self):
        created = self.invite("anna")
        self.assertIn("#invite=" + created["token"], created["url"])
        anonymous = self.app.test_client()
        info = anonymous.get("/api/invites/info", headers={"X-Invite-Token": created["token"]}).get_json()
        self.assertEqual((info["valid"], info["kind"], info["username"], info["invitedBy"]), (True, "new", "anna", "admin"))
        response = self.post("/api/invites/password", {"token": created["token"], "password": ANNA}, anonymous)
        self.assertEqual(response.get_json()["username"], "anna")
        status = anonymous.get("/api/auth/status").get_json()
        self.assertTrue(status["authenticated"])
        self.assertFalse(status["isOwner"])
        self.assertEqual(status["role"], "user")
        self.assertTrue(status["passwordConfigured"])
        # En de uitnodiging is gebruikt.
        self.assertEqual(anonymous.get("/api/invites/info", headers={"X-Invite-Token": created["token"]}).status_code, 404)
        self.assertEqual(self.post("/api/invites/password", {"token": created["token"], "username": "bob", "password": ANNA}, self.app.test_client()).status_code, 404)

    def test_the_token_is_stored_hashed(self):
        token = self.invite()["token"]
        with closing(auth.connect()) as conn:
            rows = conn.execute("SELECT * FROM invites").fetchall()
        self.assertNotIn(token, json.dumps([dict(r) for r in rows]))

    def test_expired_and_unknown_invites_do_not_work(self):
        token = self.invite()["token"]
        with closing(auth.connect()) as conn, conn:
            conn.execute("UPDATE invites SET expires_at = ?", ((auth.utcnow() - timedelta(minutes=1)).isoformat(),))
        client = self.app.test_client()
        self.assertEqual(client.get("/api/invites/info", headers={"X-Invite-Token": token}).status_code, 404)
        self.assertEqual(client.get("/api/invites/info", headers={"X-Invite-Token": "nope"}).status_code, 404)
        self.assertEqual(client.get("/api/invites/info").status_code, 404)
        self.assertEqual(self.post("/api/invites/password", {"token": token, "username": "x1", "password": ANNA}, client).status_code, 404)

    def test_name_and_password_rules_apply(self):
        token = self.invite()["token"]
        client = self.app.test_client()
        self.assertEqual(self.post("/api/invites/password", {"token": token, "username": "Admin", "password": ANNA}, client).status_code, 400)  # naam bezet
        self.assertEqual(self.post("/api/invites/password", {"token": token, "username": "anna", "password": "kort"}, client).status_code, 400)
        self.assertEqual(self.post("/api/invites/password", {"token": token, "username": "", "password": ANNA}, client).status_code, 400)
        self.assertEqual(len(users.list_users()), 1)  # niets half aangemaakt
        self.assertEqual(self.post("/api/invites/password", {"token": token, "username": "anna", "password": ANNA}, client).status_code, 200)

    def test_a_name_in_a_pending_invite_is_reserved(self):
        self.invite("anna")
        self.assertEqual(self.post("/api/users/invites", {"username": "ANNA"}).status_code, 409)

    def test_pending_invites_can_be_revoked(self):
        created = self.invite("anna")
        listed = self.admin.get("/api/users").get_json()
        self.assertEqual([i["id"] for i in listed["invites"]], [created["id"]])
        self.assertEqual(self.admin.delete(f"/api/users/invites/{created['id']}").status_code, 200)
        self.assertEqual(self.app.test_client().get("/api/invites/info", headers={"X-Invite-Token": created["token"]}).status_code, 404)
        self.assertEqual(self.admin.delete(f"/api/users/invites/{created['id']}").status_code, 404)

    def test_an_invite_is_not_a_session(self):
        token = self.invite()["token"]
        client = self.app.test_client()
        self.assertEqual(client.get("/api/users").status_code, 401)
        self.assertEqual(client.get("/api/auth/status").get_json()["authenticated"], False)
        self.assertIsNotNone(token)


class PermissionsTest(UsersTestCase):
    def test_a_user_cannot_link_mealie_or_ah_but_can_see_the_status(self):
        client = self.join()
        for method, path in (
            ("post", "/api/mealie/auth"), ("post", "/api/mealie/auth/logout"), ("post", "/api/ah/auth"),
            ("post", "/api/ah/auth/verify"), ("get", "/api/ah/auth/start"), ("get", "/api/ah/auth/callback"),
            ("get", "/api/ah/auth/proxy"), ("post", "/api/ah/auth/proxy/anything"),
        ):
            response = getattr(client, method)(path)
            self.assertEqual(response.status_code, 403, path)
            self.assertIn("beheerder", response.get_json()["error"], path)
        self.assertEqual(client.get("/api/mealie/status").status_code, 200)
        self.assertNotEqual(client.get("/api/ah/auth/status").status_code, 403)

    def test_the_administrator_still_can(self):
        self.assertNotEqual(self.admin.post("/api/mealie/auth", json={}).status_code, 403)
        self.assertNotEqual(self.admin.post("/api/ah/auth", json={}).status_code, 403)

    def test_basic_functions_stay_open_for_users(self):
        client = self.join()
        self.assertEqual(client.get("/api/ah/lists").status_code, 200)
        self.assertEqual(client.get("/api/about").status_code, 200)
        self.assertEqual(self.app.test_client().get("/api/ah/lists").status_code, 401)

    def test_without_login_everyone_is_the_administrator(self):
        with mock.patch.dict(os.environ, {"PASSKEY_AUTH_ENABLED": "false"}), self.app.test_request_context():
            self.assertTrue(auth.is_owner())
        with self.app.test_request_context():
            self.assertTrue(auth.is_owner() is False)  # niet ingelogd terwijl inloggen aanstaat

    def test_which_paths_are_for_the_administrator(self):
        self.assertFalse(auth.admin_only("/api/ah/auth/status"))
        self.assertTrue(auth.admin_only("/api/users"))
        self.assertTrue(auth.admin_only("/api/users/invites"))
        self.assertTrue(auth.admin_only("/api/mealie/auth/logout"))
        self.assertFalse(auth.admin_only("/api/mealie/status"))
        self.assertFalse(auth.admin_only("/api/mealplan"))
        self.assertFalse(auth.admin_only("/api/users_other"))


class LoginTest(UsersTestCase):
    def test_each_user_logs_in_with_their_own_password(self):
        self.join("anna", ANNA)
        for name, password, ok in (("anna", ANNA, True), ("ANNA", ANNA, True), ("anna", PASSWORD, False), ("admin", ANNA, False), ("admin", PASSWORD, True)):
            response = self.post("/api/auth/password/login", {"username": name, "password": password}, self.app.test_client())
            self.assertEqual(response.status_code, 200 if ok else 401, (name, password))

    def test_mfa_is_per_user(self):
        anna = self.join("anna", ANNA)
        started = self.post("/api/auth/mfa/start", client=anna).get_json()
        secret = started["secret"].replace(" ", "")
        self.assertEqual(started["account"], "anna")
        confirmed = self.post("/api/auth/mfa/confirm", {"code": auth_password.totp_code(secret)}, anna)
        self.assertEqual(confirmed.status_code, 200)
        self.assertTrue(self.app.test_client().get("/api/auth/status").status_code == 200)
        # Anna heeft nu MFA, de beheerder niet.
        login = self.post("/api/auth/password/login", {"username": "anna", "password": ANNA}, self.app.test_client()).get_json()
        self.assertTrue(login["mfaRequired"])
        no_mfa = self.post("/api/auth/password/login", {"username": "admin", "password": PASSWORD}, self.app.test_client()).get_json()
        self.assertNotIn("mfaRequired", no_mfa)
        self.assertFalse(self.admin.get("/api/auth/status").get_json()["mfaEnabled"])
        self.assertTrue(anna.get("/api/auth/status").get_json()["mfaEnabled"])
        # De code van Anna werkt met haar eigen kaartje.
        client = self.app.test_client()
        ticket = self.post("/api/auth/password/login", {"username": "anna", "password": ANNA}, client).get_json()["ticket"]
        ok = self.post("/api/auth/password/mfa", {"ticket": ticket, "code": auth_password.totp_code(secret, at=__import__("time").time() + 30)}, client)
        self.assertEqual(ok.status_code, 200, ok.get_json())
        self.assertEqual(ok.get_json()["username"], "anna")

    def test_changing_a_password_only_logs_that_user_out(self):
        anna = self.join("anna", ANNA)
        anna_phone = self.app.test_client()
        self.post("/api/auth/password/login", {"username": "anna", "password": ANNA}, anna_phone)
        changed = self.post("/api/auth/password", {"password": "nieuw-wachtwoord-123", "currentPassword": ANNA}, anna)
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(anna.get("/api/auth/status").get_json()["username"], "anna")
        self.assertFalse(anna_phone.get("/api/auth/status").get_json()["authenticated"])  # ander apparaat van Anna eruit
        self.assertTrue(self.admin.get("/api/auth/status").get_json()["authenticated"])  # de beheerder niet

    def test_a_user_cannot_rename_themselves_or_take_a_name(self):
        anna = self.join("anna", ANNA)
        renamed = self.post("/api/auth/password", {"username": "hacker", "password": "nieuw-wachtwoord-123", "currentPassword": ANNA}, anna)
        self.assertEqual(renamed.get_json()["username"], "anna")
        taken = self.post("/api/auth/password", {"username": "anna", "password": "nieuw-wachtwoord-123", "currentPassword": PASSWORD}, self.admin)
        self.assertEqual(taken.status_code, 400)  # de beheerder kan niet 'anna' worden

    def test_users_only_see_their_own_passkeys(self):
        anna = self.join("anna", ANNA)
        phone = FakeAuthenticator()
        options = self.post("/api/auth/register/options", {"credentialName": "Telefoon"}, anna).get_json()
        self.assertEqual(options["userId"], anna.get("/api/auth/status").get_json()["userId"])
        self.post("/api/auth/register/verify", {"userId": options["userId"], "credential": phone.create(options["options"]["challenge"])}, anna)
        self.assertEqual(len(anna.get("/api/auth/credentials").get_json()["credentials"]), 1)
        self.assertEqual(self.admin.get("/api/auth/credentials").get_json()["credentials"], [])
        deleted = self.admin.delete("/api/auth/credentials/" + b64(phone.credential_id))
        self.assertEqual(deleted.status_code, 404)  # niet van de beheerder
        self.assertEqual(len(anna.get("/api/auth/credentials").get_json()["credentials"]), 1)


class PasskeyInviteTest(UsersTestCase):
    def test_an_invited_user_can_choose_a_passkey_and_log_in_with_it(self):
        token = self.invite("bob")["token"]
        anonymous = self.app.test_client()
        phone = FakeAuthenticator()
        options = self.post("/api/auth/register/options", {"invite": token, "credentialName": "Bobs telefoon"}, anonymous)
        self.assertEqual(options.status_code, 200, options.get_json())
        data = options.get_json()
        self.assertEqual(data["username"], "bob")
        verified = self.post("/api/auth/register/verify", {"invite": token, "userId": data["userId"], "credential": phone.create(data["options"]["challenge"])}, anonymous)
        self.assertEqual(verified.status_code, 200, verified.get_json())
        status = anonymous.get("/api/auth/status").get_json()
        self.assertEqual((status["username"], status["isOwner"], status["credentialCount"]), ("bob", False, 1))
        self.assertEqual(self.app.test_client().get("/api/invites/info", headers={"X-Invite-Token": token}).status_code, 404)
        self.assertEqual(auth.get_setting("AUTH_MODE"), "password")  # raakt de keuze van de beheerder niet

        # Inloggen met de passkey, op een ander apparaat.
        other = self.app.test_client()
        login = self.post("/api/auth/login/options", client=other).get_json()["options"]
        self.assertIn(b64(phone.credential_id), [c["id"] for c in login["allowCredentials"]])
        done = self.post("/api/auth/login/verify", {"credential": phone.get(login["challenge"])}, other)
        self.assertEqual(done.status_code, 200, done.get_json())
        self.assertEqual(done.get_json()["username"], "bob")
        self.assertFalse(other.get("/api/auth/status").get_json()["isOwner"])

    def test_a_bad_or_used_invite_gets_no_passkey_options(self):
        self.assertEqual(self.post("/api/auth/register/options", {"invite": "nope"}, self.app.test_client()).status_code, 404)
        self.assertEqual(self.post("/api/auth/register/options", {}, self.app.test_client()).status_code, 401)

    def test_a_taken_name_is_refused_before_a_passkey_is_made(self):
        token = self.invite()["token"]
        response = self.post("/api/auth/register/options", {"invite": token, "username": "admin"}, self.app.test_client())
        self.assertEqual(response.status_code, 400)


class ResetAndDeleteTest(UsersTestCase):
    def test_reset_link_replaces_the_old_login_of_a_user(self):
        anna = self.join("anna", ANNA)
        anna_id = anna.get("/api/auth/status").get_json()["userId"]
        reset = self.post(f"/api/users/{anna_id}/reset").get_json()
        self.assertEqual(reset["kind"], "reset")
        # Zolang de link niet gebruikt is, werkt het oude wachtwoord nog.
        self.assertEqual(self.post("/api/auth/password/login", {"username": "anna", "password": ANNA}, self.app.test_client()).status_code, 200)
        client = self.app.test_client()
        info = client.get("/api/invites/info", headers={"X-Invite-Token": reset["token"]}).get_json()
        self.assertEqual((info["kind"], info["username"]), ("reset", "anna"))
        # De naam uit het verzoek telt niet bij een reset.
        done = self.post("/api/invites/password", {"token": reset["token"], "username": "someoneelse", "password": "nieuw-wachtwoord-123"}, client)
        self.assertEqual(done.get_json()["username"], "anna")
        self.assertEqual(self.post("/api/auth/password/login", {"username": "anna", "password": ANNA}, self.app.test_client()).status_code, 401)
        self.assertEqual(self.post("/api/auth/password/login", {"username": "anna", "password": "nieuw-wachtwoord-123"}, self.app.test_client()).status_code, 200)
        self.assertFalse(anna.get("/api/auth/status").get_json()["authenticated"])  # oude sessie is weg
        self.assertEqual(len(users.list_users()), 2)

    def test_reset_also_drops_mfa(self):
        anna = self.join("anna", ANNA)
        secret = self.post("/api/auth/mfa/start", client=anna).get_json()["secret"].replace(" ", "")
        self.post("/api/auth/mfa/confirm", {"code": auth_password.totp_code(secret)}, anna)
        anna_id = anna.get("/api/auth/status").get_json()["userId"]
        token = self.post(f"/api/users/{anna_id}/reset").get_json()["token"]
        self.post("/api/invites/password", {"token": token, "password": "nieuw-wachtwoord-123"}, self.app.test_client())
        self.assertFalse(auth.mfa_enabled(anna_id))
        self.assertEqual(auth.recovery_codes_left(anna_id), 0)

    def test_the_owner_cannot_be_reset_or_deleted(self):
        self.assertEqual(self.post("/api/users/owner/reset").status_code, 404)
        self.assertEqual(self.admin.delete("/api/users/owner").status_code, 400)
        self.assertEqual(self.post("/api/users/nobody/reset").status_code, 404)
        self.assertEqual(self.admin.delete("/api/users/nobody").status_code, 404)

    def test_deleting_a_user_removes_the_account_and_the_session(self):
        anna = self.join("anna", ANNA)
        anna_id = anna.get("/api/auth/status").get_json()["userId"]
        self.assertEqual(self.admin.delete(f"/api/users/{anna_id}").status_code, 200)
        self.assertFalse(anna.get("/api/auth/status").get_json()["authenticated"])
        self.assertEqual(self.post("/api/auth/password/login", {"username": "anna", "password": ANNA}, self.app.test_client()).status_code, 401)
        self.assertEqual([u["username"] for u in users.list_users()], ["admin"])
        with closing(auth.connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM password_credentials WHERE user_id = ?", (anna_id,)).fetchone()[0], 0)

    def test_the_user_list_shows_how_each_user_logs_in(self):
        self.join("anna", ANNA)
        listed = {u["username"]: u for u in self.admin.get("/api/users").get_json()["users"]}
        self.assertEqual(listed["admin"]["role"], "owner")
        self.assertEqual((listed["anna"]["role"], listed["anna"]["password"], listed["anna"]["passkeys"], listed["anna"]["mfa"]), ("user", True, 0, False))
        self.assertEqual(list(listed)[0], "admin")


if __name__ == "__main__":
    unittest.main()
