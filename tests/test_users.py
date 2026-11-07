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
from helpers import AppTestCase, db, run  # noqa: E402

PASSWORD = "koffie-met-melk-42"
ANNA = "een-heel-ander-wachtwoord"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class FakeAuthenticator:
    """Een passkey zoals een telefoon die maakt, genoeg voor de server om te controleren."""

    def __init__(self, rp_id="localhost", origin="http://localhost"):  # flags: 0x45 = aanwezig + geverifieerd, 0x41 = alleen aanwezig
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(16)
        self.rp_hash = hashlib.sha256(rp_id.encode()).digest()
        self.origin = origin

    def _client_data(self, kind, challenge):
        return json.dumps({"type": kind, "challenge": challenge, "origin": self.origin}).encode()

    def create(self, challenge, flags=0x45):
        numbers = self.key.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")})
        auth_data = self.rp_hash + bytes([flags]) + struct.pack(">I", 0) + b"\0" * 16 + struct.pack(">H", len(self.credential_id)) + self.credential_id + cose
        return {
            "id": b64(self.credential_id),
            "rawId": b64(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64(self._client_data("webauthn.create", challenge)),
                "attestationObject": b64(cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})),
            },
        }

    def get(self, challenge, flags=0x05):
        client_data = self._client_data("webauthn.get", challenge)
        auth_data = self.rp_hash + bytes([flags]) + struct.pack(">I", 1)
        signature = self.key.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(SHA256()))
        return {
            "id": b64(self.credential_id),
            "rawId": b64(self.credential_id),
            "type": "public-key",
            "response": {"clientDataJSON": b64(client_data), "authenticatorData": b64(auth_data), "signature": b64(signature)},
        }


class UsersTestCase(AppTestCase):
    async def asyncSetUp(self):
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
        self.admin = self.client_for(self.app)
        await self.post("/api/auth/password", {"username": "admin", "password": PASSWORD}, self.admin)

    async def post(self, path, body=None, client=None):
        return await (client or self.admin).post(path, json=body or {})

    async def invite(self, username="", client=None):
        response = await self.post("/api/users/invites", {"username": username}, client)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    async def join(self, username="anna", password=ANNA):
        """Nodig een gebruiker uit en laat die een wachtwoord kiezen. Geeft de client van die gebruiker."""
        token = (await self.invite())["token"]
        client = self.client_for(self.app)
        response = await self.post("/api/invites/password", {"token": token, "username": username, "password": password}, client)
        self.assertEqual(response.status_code, 200, response.get_json())
        return client


class MigrationTest(UsersTestCase):
    def test_existing_sessions_of_the_owner_survive_the_upgrade(self):
        with closing(db()) as conn, conn:
            conn.executescript(
                """
                DROP TABLE users; CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
                INSERT INTO users VALUES ('owner', 'admin', 'x');
                INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES ('SESSION_EPOCH', '3', 'x');
                """
            )
        run(auth.init_db())
        self.assertEqual(run(auth.session_epoch("owner")), 3)


class InviteTest(UsersTestCase):
    async def test_only_the_administrator_manages_users(self):
        client = await self.join()
        for method, path in (("get", "/api/users"), ("post", "/api/users/invites"), ("post", "/api/users/owner/reset"), ("delete", "/api/users/owner")):
            response = await getattr(client, method)(path)
            self.assertEqual(response.status_code, 403, path)
        self.assertEqual((await self.admin.get("/api/users")).status_code, 200)

    async def test_invite_creates_an_account_with_its_own_password(self):
        created = await self.invite("anna")
        self.assertIn("#invite=" + created["token"], created["url"])
        anonymous = self.client_for(self.app)
        info = (await anonymous.get("/api/invites/info", headers={"X-Invite-Token": created["token"]})).get_json()
        self.assertEqual((info["valid"], info["kind"], info["username"], info["invitedBy"]), (True, "new", "anna", "admin"))
        response = await self.post("/api/invites/password", {"token": created["token"], "password": ANNA}, anonymous)
        self.assertEqual(response.get_json()["username"], "anna")
        status = (await anonymous.get("/api/auth/status")).get_json()
        self.assertTrue(status["authenticated"])
        self.assertFalse(status["isOwner"])
        self.assertEqual(status["role"], "user")
        self.assertTrue(status["passwordConfigured"])
        # En de uitnodiging is gebruikt.
        self.assertEqual((await anonymous.get("/api/invites/info", headers={"X-Invite-Token": created["token"]})).status_code, 404)
        self.assertEqual((await self.post("/api/invites/password", {"token": created["token"], "username": "bob", "password": ANNA}, self.client_for(self.app))).status_code, 404)

    async def test_the_token_is_stored_hashed(self):
        token = (await self.invite())["token"]
        with closing(db()) as conn:
            rows = conn.execute("SELECT * FROM invites").fetchall()
        self.assertNotIn(token, json.dumps([dict(r) for r in rows]))

    async def test_expired_and_unknown_invites_do_not_work(self):
        token = (await self.invite())["token"]
        with closing(db()) as conn, conn:
            conn.execute("UPDATE invites SET expires_at = ?", ((auth.utcnow() - timedelta(minutes=1)).isoformat(),))
        client = self.client_for(self.app)
        self.assertEqual((await client.get("/api/invites/info", headers={"X-Invite-Token": token})).status_code, 404)
        self.assertEqual((await client.get("/api/invites/info", headers={"X-Invite-Token": "nope"})).status_code, 404)
        self.assertEqual((await client.get("/api/invites/info")).status_code, 404)
        self.assertEqual((await self.post("/api/invites/password", {"token": token, "username": "x1", "password": ANNA}, client)).status_code, 404)

    async def test_name_and_password_rules_apply(self):
        token = (await self.invite())["token"]
        client = self.client_for(self.app)
        self.assertEqual((await self.post("/api/invites/password", {"token": token, "username": "Admin", "password": ANNA}, client)).status_code, 400)  # naam bezet
        self.assertEqual((await self.post("/api/invites/password", {"token": token, "username": "anna", "password": "kort"}, client)).status_code, 400)
        self.assertEqual((await self.post("/api/invites/password", {"token": token, "username": "", "password": ANNA}, client)).status_code, 400)
        self.assertEqual(len(await users.list_users()), 1)  # niets half aangemaakt
        self.assertEqual((await self.post("/api/invites/password", {"token": token, "username": "anna", "password": ANNA}, client)).status_code, 200)

    async def test_a_name_in_a_pending_invite_is_reserved(self):
        await self.invite("anna")
        self.assertEqual((await self.post("/api/users/invites", {"username": "ANNA"})).status_code, 409)

    async def test_pending_invites_can_be_revoked(self):
        created = await self.invite("anna")
        listed = (await self.admin.get("/api/users")).get_json()
        self.assertEqual([i["id"] for i in listed["invites"]], [created["id"]])
        self.assertEqual((await self.admin.delete(f"/api/users/invites/{created['id']}")).status_code, 200)
        self.assertEqual((await self.client_for(self.app).get("/api/invites/info", headers={"X-Invite-Token": created["token"]})).status_code, 404)
        self.assertEqual((await self.admin.delete(f"/api/users/invites/{created['id']}")).status_code, 404)

    async def test_an_invite_is_not_a_session(self):
        token = (await self.invite())["token"]
        client = self.client_for(self.app)
        self.assertEqual((await client.get("/api/users")).status_code, 401)
        self.assertEqual((await client.get("/api/auth/status")).get_json()["authenticated"], False)
        self.assertIsNotNone(token)


class PermissionsTest(UsersTestCase):
    async def test_a_user_cannot_link_mealie_or_ah_but_can_see_the_status(self):
        client = await self.join()
        for method, path in (
            ("post", "/api/mealie/auth"), ("post", "/api/mealie/auth/logout"), ("post", "/api/ah/auth"),
            ("post", "/api/ah/auth/verify"), ("get", "/api/ah/auth/start"), ("get", "/api/ah/auth/callback"),
            ("get", "/api/ah/auth/proxy"), ("post", "/api/ah/auth/proxy/anything"),
            ("post", "/api/picnic/auth"), ("post", "/api/picnic/auth/2fa/request"),
            ("post", "/api/picnic/auth/2fa/verify"), ("post", "/api/picnic/auth/logout"),
            ("post", "/api/picnic/explore"),
            ("post", "/api/jumbo/auth"), ("post", "/api/jumbo/auth/logout"), ("get", "/api/jumbo/auth/start"),
            ("post", "/api/jumbo/explore"),
        ):
            response = await getattr(client, method)(path)
            self.assertEqual(response.status_code, 403, path)
            self.assertIn("beheerder", response.get_json()["error"], path)
        self.assertEqual((await client.get("/api/mealie/status")).status_code, 200)
        self.assertNotEqual((await client.get("/api/ah/auth/status")).status_code, 403)
        self.assertNotEqual((await client.get("/api/picnic/auth/status")).status_code, 403)
        self.assertNotEqual((await client.get("/api/jumbo/auth/status")).status_code, 403)

    async def test_the_administrator_still_can(self):
        self.assertNotEqual((await self.admin.post("/api/mealie/auth", json={})).status_code, 403)
        self.assertNotEqual((await self.admin.post("/api/ah/auth", json={})).status_code, 403)
        self.assertNotEqual((await self.admin.post("/api/picnic/auth", json={})).status_code, 403)
        self.assertNotEqual((await self.admin.post("/api/jumbo/auth", json={})).status_code, 403)

    async def test_basic_functions_stay_open_for_users(self):
        client = await self.join()
        self.assertEqual((await client.get("/api/ah/lists")).status_code, 200)
        self.assertEqual((await client.get("/api/about")).status_code, 200)
        self.assertEqual((await self.client_for(self.app).get("/api/ah/lists")).status_code, 401)

    def test_without_login_everyone_is_the_administrator(self):
        async def is_owner():
            async with self.app.test_request_context("/"):
                return await auth.is_owner()

        with mock.patch.dict(os.environ, {"PASSKEY_AUTH_ENABLED": "false"}):
            self.assertTrue(run(is_owner()))
        self.assertTrue(run(is_owner()) is False)  # niet ingelogd terwijl inloggen aanstaat

    def test_which_paths_are_for_the_administrator(self):
        self.assertFalse(auth.admin_only("/api/ah/auth/status"))
        self.assertFalse(auth.admin_only("/api/picnic/auth/status"))
        self.assertTrue(auth.admin_only("/api/picnic/auth"))
        self.assertTrue(auth.admin_only("/api/picnic/auth/2fa/verify"))
        self.assertFalse(auth.admin_only("/api/picnic/search"))
        self.assertFalse(auth.admin_only("/api/jumbo/auth/status"))
        self.assertTrue(auth.admin_only("/api/jumbo/auth"))
        self.assertTrue(auth.admin_only("/api/jumbo/auth/start"))
        self.assertTrue(auth.admin_only("/api/jumbo/explore"))
        self.assertFalse(auth.admin_only("/api/jumbo/search"))
        self.assertTrue(auth.admin_only("/api/users"))
        self.assertTrue(auth.admin_only("/api/users/invites"))
        self.assertTrue(auth.admin_only("/api/mealie/auth/logout"))
        self.assertFalse(auth.admin_only("/api/mealie/status"))
        self.assertFalse(auth.admin_only("/api/mealplan"))
        self.assertFalse(auth.admin_only("/api/users_other"))


class LoginTest(UsersTestCase):
    async def test_each_user_logs_in_with_their_own_password(self):
        await self.join("anna", ANNA)
        for name, password, ok in (("anna", ANNA, True), ("ANNA", ANNA, True), ("anna", PASSWORD, False), ("admin", ANNA, False), ("admin", PASSWORD, True)):
            response = await self.post("/api/auth/password/login", {"username": name, "password": password}, self.client_for(self.app))
            self.assertEqual(response.status_code, 200 if ok else 401, (name, password))

    async def test_mfa_is_per_user(self):
        anna = await self.join("anna", ANNA)
        started = (await self.post("/api/auth/mfa/start", client=anna)).get_json()
        secret = started["secret"].replace(" ", "")
        self.assertEqual(started["account"], "anna")
        confirmed = await self.post("/api/auth/mfa/confirm", {"code": auth_password.totp_code(secret)}, anna)
        self.assertEqual(confirmed.status_code, 200)
        self.assertTrue((await self.client_for(self.app).get("/api/auth/status")).status_code == 200)
        # Anna heeft nu MFA, de beheerder niet.
        login = (await self.post("/api/auth/password/login", {"username": "anna", "password": ANNA}, self.client_for(self.app))).get_json()
        self.assertTrue(login["mfaRequired"])
        no_mfa = (await self.post("/api/auth/password/login", {"username": "admin", "password": PASSWORD}, self.client_for(self.app))).get_json()
        self.assertNotIn("mfaRequired", no_mfa)
        self.assertFalse((await self.admin.get("/api/auth/status")).get_json()["mfaEnabled"])
        self.assertTrue((await anna.get("/api/auth/status")).get_json()["mfaEnabled"])
        # De code van Anna werkt met haar eigen kaartje.
        client = self.client_for(self.app)
        ticket = (await self.post("/api/auth/password/login", {"username": "anna", "password": ANNA}, client)).get_json()["ticket"]
        ok = await self.post("/api/auth/password/mfa", {"ticket": ticket, "code": auth_password.totp_code(secret, at=__import__("time").time() + 30)}, client)
        self.assertEqual(ok.status_code, 200, ok.get_json())
        self.assertEqual(ok.get_json()["username"], "anna")

    async def test_changing_a_password_only_logs_that_user_out(self):
        anna = await self.join("anna", ANNA)
        anna_phone = self.client_for(self.app)
        await self.post("/api/auth/password/login", {"username": "anna", "password": ANNA}, anna_phone)
        changed = await self.post("/api/auth/password", {"password": "nieuw-wachtwoord-123", "currentPassword": ANNA}, anna)
        self.assertEqual(changed.status_code, 200)
        self.assertEqual((await anna.get("/api/auth/status")).get_json()["username"], "anna")
        self.assertFalse((await anna_phone.get("/api/auth/status")).get_json()["authenticated"])  # ander apparaat van Anna eruit
        self.assertTrue((await self.admin.get("/api/auth/status")).get_json()["authenticated"])  # de beheerder niet

    async def test_a_user_cannot_rename_themselves_or_take_a_name(self):
        anna = await self.join("anna", ANNA)
        renamed = await self.post("/api/auth/password", {"username": "hacker", "password": "nieuw-wachtwoord-123", "currentPassword": ANNA}, anna)
        self.assertEqual(renamed.get_json()["username"], "anna")
        taken = await self.post("/api/auth/password", {"username": "anna", "password": "nieuw-wachtwoord-123", "currentPassword": PASSWORD}, self.admin)
        self.assertEqual(taken.status_code, 400)  # de beheerder kan niet 'anna' worden

    async def test_users_only_see_their_own_passkeys(self):
        anna = await self.join("anna", ANNA)
        phone = FakeAuthenticator()
        options = (await self.post("/api/auth/register/options", {"credentialName": "Telefoon"}, anna)).get_json()
        self.assertEqual(options["userId"], (await anna.get("/api/auth/status")).get_json()["userId"])
        await self.post("/api/auth/register/verify", {"userId": options["userId"], "credential": phone.create(options["options"]["challenge"])}, anna)
        self.assertEqual(len((await anna.get("/api/auth/credentials")).get_json()["credentials"]), 1)
        self.assertEqual((await self.admin.get("/api/auth/credentials")).get_json()["credentials"], [])
        deleted = await self.admin.delete("/api/auth/credentials/" + b64(phone.credential_id))
        self.assertEqual(deleted.status_code, 404)  # niet van de beheerder
        self.assertEqual(len((await anna.get("/api/auth/credentials")).get_json()["credentials"]), 1)


class PasskeyInviteTest(UsersTestCase):
    async def test_an_invited_user_can_choose_a_passkey_and_log_in_with_it(self):
        token = (await self.invite("bob"))["token"]
        anonymous = self.client_for(self.app)
        phone = FakeAuthenticator()
        options = await self.post("/api/auth/register/options", {"invite": token, "credentialName": "Bobs telefoon"}, anonymous)
        self.assertEqual(options.status_code, 200, options.get_json())
        data = options.get_json()
        self.assertEqual(data["username"], "bob")
        verified = await self.post("/api/auth/register/verify", {"invite": token, "userId": data["userId"], "credential": phone.create(data["options"]["challenge"])}, anonymous)
        self.assertEqual(verified.status_code, 200, verified.get_json())
        status = (await anonymous.get("/api/auth/status")).get_json()
        self.assertEqual((status["username"], status["isOwner"], status["credentialCount"]), ("bob", False, 1))
        self.assertEqual((await self.client_for(self.app).get("/api/invites/info", headers={"X-Invite-Token": token})).status_code, 404)
        self.assertEqual(await auth.get_setting("AUTH_MODE"), "password")  # raakt de keuze van de beheerder niet

        # Inloggen met de passkey, op een ander apparaat.
        other = self.client_for(self.app)
        login = (await self.post("/api/auth/login/options", client=other)).get_json()["options"]
        self.assertIn(b64(phone.credential_id), [c["id"] for c in login["allowCredentials"]])
        done = await self.post("/api/auth/login/verify", {"credential": phone.get(login["challenge"])}, other)
        self.assertEqual(done.status_code, 200, done.get_json())
        self.assertEqual(done.get_json()["username"], "bob")
        self.assertFalse((await other.get("/api/auth/status")).get_json()["isOwner"])

    async def register_with(self, phone, flags=0x45, challenge=None, name="carol"):
        token = (await self.invite(name))["token"]
        anonymous = self.client_for(self.app)
        data = (await self.post("/api/auth/register/options", {"invite": token}, anonymous)).get_json()
        credential = phone.create(challenge or data["options"]["challenge"], flags=flags)
        return anonymous, await self.post("/api/auth/register/verify", {"invite": token, "userId": data["userId"], "credential": credential}, anonymous)

    async def test_a_password_manager_passkey_without_user_verification_is_accepted(self):
        # Enpass, Bitwarden and friends report user presence only (flags 0x41 / 0x01).
        phone = FakeAuthenticator()
        _, verified = await self.register_with(phone, flags=0x41)
        self.assertEqual(verified.status_code, 200, verified.get_json())
        other = self.client_for(self.app)
        login = (await self.post("/api/auth/login/options", client=other)).get_json()["options"]
        done = await self.post("/api/auth/login/verify", {"credential": phone.get(login["challenge"], flags=0x01)}, other)
        self.assertEqual(done.status_code, 200, done.get_json())

    async def test_a_passkey_without_user_presence_is_refused(self):
        _, verified = await self.register_with(FakeAuthenticator(), flags=0x44)
        self.assertEqual(verified.status_code, 400)
        phone = FakeAuthenticator()
        await self.register_with(phone, name="dave")
        other = self.client_for(self.app)
        login = (await self.post("/api/auth/login/options", client=other)).get_json()["options"]
        self.assertEqual((await self.post("/api/auth/login/verify", {"credential": phone.get(login["challenge"], flags=0x04)}, other)).status_code, 400)

    async def test_a_passkey_from_another_origin_or_for_another_challenge_is_refused(self):
        self.assertEqual((await self.register_with(FakeAuthenticator(origin="https://evil.example")))[1].status_code, 400)
        self.assertEqual((await self.register_with(FakeAuthenticator(), challenge=b64(os.urandom(32)), name="erin"))[1].status_code, 400)

    async def test_a_bad_or_used_invite_gets_no_passkey_options(self):
        self.assertEqual((await self.post("/api/auth/register/options", {"invite": "nope"}, self.client_for(self.app))).status_code, 404)
        self.assertEqual((await self.post("/api/auth/register/options", {}, self.client_for(self.app))).status_code, 401)

    async def test_a_taken_name_is_refused_before_a_passkey_is_made(self):
        token = (await self.invite())["token"]
        response = await self.post("/api/auth/register/options", {"invite": token, "username": "admin"}, self.client_for(self.app))
        self.assertEqual(response.status_code, 400)


class ResetAndDeleteTest(UsersTestCase):
    async def test_reset_link_replaces_the_old_login_of_a_user(self):
        anna = await self.join("anna", ANNA)
        anna_id = (await anna.get("/api/auth/status")).get_json()["userId"]
        reset = (await self.post(f"/api/users/{anna_id}/reset")).get_json()
        self.assertEqual(reset["kind"], "reset")
        # Zolang de link niet gebruikt is, werkt het oude wachtwoord nog.
        self.assertEqual((await self.post("/api/auth/password/login", {"username": "anna", "password": ANNA}, self.client_for(self.app))).status_code, 200)
        client = self.client_for(self.app)
        info = (await client.get("/api/invites/info", headers={"X-Invite-Token": reset["token"]})).get_json()
        self.assertEqual((info["kind"], info["username"]), ("reset", "anna"))
        # De naam uit het verzoek telt niet bij een reset.
        done = await self.post("/api/invites/password", {"token": reset["token"], "username": "someoneelse", "password": "nieuw-wachtwoord-123"}, client)
        self.assertEqual(done.get_json()["username"], "anna")
        self.assertEqual((await self.post("/api/auth/password/login", {"username": "anna", "password": ANNA}, self.client_for(self.app))).status_code, 401)
        self.assertEqual((await self.post("/api/auth/password/login", {"username": "anna", "password": "nieuw-wachtwoord-123"}, self.client_for(self.app))).status_code, 200)
        self.assertFalse((await anna.get("/api/auth/status")).get_json()["authenticated"])  # oude sessie is weg
        self.assertEqual(len(await users.list_users()), 2)

    async def test_reset_also_drops_mfa(self):
        anna = await self.join("anna", ANNA)
        secret = (await self.post("/api/auth/mfa/start", client=anna)).get_json()["secret"].replace(" ", "")
        await self.post("/api/auth/mfa/confirm", {"code": auth_password.totp_code(secret)}, anna)
        anna_id = (await anna.get("/api/auth/status")).get_json()["userId"]
        token = (await self.post(f"/api/users/{anna_id}/reset")).get_json()["token"]
        await self.post("/api/invites/password", {"token": token, "password": "nieuw-wachtwoord-123"}, self.client_for(self.app))
        self.assertFalse(await auth.mfa_enabled(anna_id))
        self.assertEqual(await auth.recovery_codes_left(anna_id), 0)

    async def test_the_owner_cannot_be_reset_or_deleted(self):
        self.assertEqual((await self.post("/api/users/owner/reset")).status_code, 404)
        self.assertEqual((await self.admin.delete("/api/users/owner")).status_code, 400)
        self.assertEqual((await self.post("/api/users/nobody/reset")).status_code, 404)
        self.assertEqual((await self.admin.delete("/api/users/nobody")).status_code, 404)

    async def test_deleting_a_user_removes_the_account_and_the_session(self):
        anna = await self.join("anna", ANNA)
        anna_id = (await anna.get("/api/auth/status")).get_json()["userId"]
        self.assertEqual((await self.admin.delete(f"/api/users/{anna_id}")).status_code, 200)
        self.assertFalse((await anna.get("/api/auth/status")).get_json()["authenticated"])
        self.assertEqual((await self.post("/api/auth/password/login", {"username": "anna", "password": ANNA}, self.client_for(self.app))).status_code, 401)
        self.assertEqual([u["username"] for u in await users.list_users()], ["admin"])
        with closing(db()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM password_credentials WHERE user_id = ?", (anna_id,)).fetchone()[0], 0)

    async def test_the_user_list_shows_how_each_user_logs_in(self):
        await self.join("anna", ANNA)
        listed = {u["username"]: u for u in (await self.admin.get("/api/users")).get_json()["users"]}
        self.assertEqual(listed["admin"]["role"], "owner")
        self.assertEqual((listed["anna"]["role"], listed["anna"]["password"], listed["anna"]["passkeys"], listed["anna"]["mfa"]), ("user", True, 0, False))
        self.assertEqual(list(listed)[0], "admin")


if __name__ == "__main__":
    unittest.main()
