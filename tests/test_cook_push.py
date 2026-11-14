"""De melding van een kooktimer via Web Push (mealieshopper/push.py, issue #311): ook met een vergrendelde telefoon.

De app stuurt haar lopende timers met hun resttijd en het push-abonnement van de browser naar de server; die stuurt bij de
eindtijd een versleutelde melding naar de push-dienst. Hier: wat de server aanneemt (alleen echte push-diensten, geldige
sleutels, geldige timers), de VAPID-sleutel, het versleutelen en ondertekenen (echt ontsleuteld en gecontroleerd met de
bibliotheken), het versturen en opruimen, en dat uitloggen en een verwijderde gebruiker de abonnementen meenemen.
"""

import asyncio
import json
import os
import tempfile
import time
from contextlib import closing
from unittest import mock

import http_ece
import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid02

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from helpers import AppTestCase, db, served  # noqa: E402

from mealieshopper import auth, auth_password, push, users  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

PASSWORD = "koffie-met-melk-42"
ANNA = "een-heel-ander-wachtwoord"
NOW = 1_800_000_000.0
FCM = "https://fcm.googleapis.com/fcm/send/abc123:APA91b-token"


class Browser:
    """Een browser met een push-abonnement: de sleutels die hij bij het abonneren maakt, en ontsleutelen zoals hij doet."""

    def __init__(self, endpoint: str = FCM):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.secret = os.urandom(16)
        self.endpoint = endpoint

    def subscription(self) -> dict:
        public = self.key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        return {
            "endpoint": self.endpoint,
            "expirationTime": None,
            "keys": {"p256dh": auth.b64url_encode(public), "auth": auth.b64url_encode(self.secret)},
        }

    def read(self, body: bytes) -> dict:
        return json.loads(http_ece.decrypt(body, private_key=self.key, auth_secret=self.secret, version="aes128gcm"))


def timer(timer_id: str = "lz9-1", left: float = 600_000, title: str = "Timer klaar: 10 min", body: str = "Pasta · Stap 2") -> dict:
    return {"id": timer_id, "left": left, "title": title, "body": body}


class PushCase(AppTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        auth.forget_runtime_state()
        self.app = create_app()
        self.client = self.client_for(self.app)

    async def put(self, browser: Browser, timers: list, client=None, url: str = "/recipes"):
        return await (client or self.client).put("/api/cook/timers", json={"subscription": browser.subscription(), "timers": timers, "url": url})

    async def subscribe(self, browser: Browser, timers: list, now: float = NOW) -> None:
        """Bewaart een abonnement met timers zoals de route doet, met een vaste klok."""
        await push.save_timers(
            "owner",
            push.parse_subscription(browser.subscription()),
            push.parse_timers(timers, now),
            url="/recipes",
            contact_url="https://shop.example.nl",
        )

    @staticmethod
    def rows(table: str) -> list[dict]:
        with closing(db(push)) as conn:
            return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]


class EndpointTest(PushCase):
    def test_only_the_push_services_of_the_browsers(self):
        allowed = [
            FCM,
            "https://updates.push.services.mozilla.com/wpush/v2/gAAAAA",
            "https://web.push.apple.com/QGuQyavXutnMH5Yw",
            "https://wns2-db5p.notify.windows.com/w/?token=BQYAAAD",
            "https://android.googleapis.com/gcm/send/abc",
            "https://jmt17.google.com/fcm/send/e1Gb_z4HWCw:APA91bF5",  # Chromium zonder de sleutels van Google
            "https://FCM.googleapis.com:443/fcm/send/abc",
        ]
        refused = [
            "http://fcm.googleapis.com/fcm/send/abc",  # geen https
            "https://localhost/push",
            "https://127.0.0.1/fcm/send",
            "https://192.168.1.10/fcm/send",
            "https://example.com/fcm.googleapis.com",
            "https://fcm.googleapis.com.evil.example/x",
            "https://evilfcm.googleapis.com/x",
            "https://www.google.com/x",
            "https://fcm.googleapis.com:8443/fcm/send/abc",
            "https://user:pw@fcm.googleapis.com/fcm/send/abc",
            "https://fcm.googleapis.com\\@evil.example/",
            "https://fcm.googleapis.com/fcm/send/a b",
            "https://fcm.googleapis.com/" + "a" * 1100,
            "ftp://fcm.googleapis.com/x",
            "https://[::1]/x",
            "",
            None,
            42,
            ["https://fcm.googleapis.com/x"],
        ]
        for url in allowed:
            with self.subTest(url=url):
                self.assertTrue(push.push_endpoint(url))
        for url in refused:
            with self.subTest(url=url):
                self.assertFalse(push.push_endpoint(url))

    def test_a_subscription_needs_real_keys(self):
        browser = Browser()
        good = browser.subscription()
        self.assertEqual(push.parse_subscription(good)["endpoint"], FCM)
        point = auth.b64url_decode(good["keys"]["p256dh"])
        broken = {
            "geen object": None,
            "geen sleutels": {"endpoint": FCM},
            "auth te kort": {**good, "keys": {**good["keys"], "auth": auth.b64url_encode(os.urandom(8))}},
            "p256dh te kort": {**good, "keys": {**good["keys"], "p256dh": auth.b64url_encode(point[:33])}},
            "niet op de curve": {**good, "keys": {**good["keys"], "p256dh": auth.b64url_encode(b"\x04" + b"\x01" * 64)}},
            "geen base64url": {**good, "keys": {**good["keys"], "auth": "!!!!" * 6}},
            "een getal": {**good, "keys": {**good["keys"], "auth": 12345}},
            "ander adres": {**good, "endpoint": "https://example.com/push"},
        }
        for name, value in broken.items():
            with self.subTest(name), self.assertRaises(ValueError):
                push.parse_subscription(value)


class TimersTest(PushCase):
    def test_the_end_follows_the_clock_of_the_server(self):
        timers = push.parse_timers([timer("a", 600_000), timer("b", 1500, title="", body=None)], NOW)
        self.assertEqual([item["id"] for item in timers], ["a", "b"])
        self.assertEqual(timers[0]["due"], NOW + 600 + push.GRACE_SECONDS, "de resttijd telt vanaf de klok van de server, plus de marge")
        self.assertEqual((timers[1]["title"], timers[1]["body"]), ("Timer klaar", ""), "zonder titel een eigen tekst")

    def test_odd_timers_are_refused(self):
        cases = {
            "geen lijst": "x",
            "te veel": [timer(f"t{index}") for index in range(push.MAX_TIMERS + 1)],
            "geen id": [{"left": 1000}],
            "raar id": [timer("../etc")],
            "lang id": [timer("a" * 65)],
            "nul": [timer(left=0)],
            "negatief": [timer(left=-5)],
            "langer dan een dag": [timer(left=24 * 3600 * 1000 + 1)],
            "waar": [timer(left=True)],
            "tekst": [timer(left="600000")],
            "geen getal": [timer(left=float("nan"))],
            "geen object": ["lz9-1"],
        }
        for name, value in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                push.parse_timers(value, NOW)
        self.assertEqual(push.parse_timers(None, NOW), [])

    def test_texts_are_short_and_on_one_line(self):
        found = push.parse_timers([timer(title="Timer\nklaar " + "x" * 300, body="Pasta\t·  Stap 2 " + "y" * 400), timer(title="Twee")], NOW)
        self.assertEqual(len(found), 1, "een tweede timer met hetzelfde id vervangt de eerste")
        found = push.parse_timers([timer(title="Timer\nklaar " + "x" * 300, body="Pasta\t·  Stap 2 " + "y" * 400)], NOW)[0]
        self.assertTrue(found["title"].startswith("Timer klaar x"))
        self.assertEqual(len(found["title"]), push.MAX_TITLE_CHARS)
        self.assertTrue(found["body"].startswith("Pasta · Stap 2 y"))
        self.assertEqual(len(found["body"]), push.MAX_BODY_CHARS)

    def test_the_page_to_open_and_the_contact(self):
        for value, expected in {
            "/recipes?week=2026-11-16": "/recipes?week=2026-11-16",
            "//evil.example": "/",
            "https://evil.example": "/",
            "/a\\b": "/",
            "": "/",
            None: "/",
            "/a b": "/",
        }.items():
            self.assertEqual(push.page_url(value), expected, value)
        self.assertEqual(push.contact("https://Shop.Example.nl"), "https://shop.example.nl")
        self.assertEqual(push.contact("https://shop.example.nl:8443"), "https://shop.example.nl:8443")
        self.assertEqual(push.contact("http://192.168.1.5:8000"), push.LOCAL_CONTACT, "zonder https geen adres als contact")
        self.assertEqual(push.contact(""), push.LOCAL_CONTACT)


class KeyTest(PushCase):
    async def test_one_key_per_installation_stored_encrypted(self):
        first, second = await asyncio.gather(push.public_key(), push.public_key())
        self.assertEqual(first, second, "twee aanvragen tegelijk houden dezelfde sleutel")
        self.assertEqual(await push.public_key(), first)
        raw = auth.b64url_decode(first)
        self.assertEqual((len(raw), raw[0]), (65, 4), "een ongecomprimeerd punt, zoals applicationServerKey het wil")
        with closing(db()) as conn:
            stored = conn.execute("SELECT value FROM app_settings WHERE key = ?", (push.SETTING_VAPID_KEY,)).fetchone()[0]
        self.assertTrue(stored.startswith("gAAAA"), "versleuteld, zoals de andere geheimen")
        self.assertNotIn(first, stored)

    async def test_an_unreadable_key_is_replaced(self):
        with closing(db(push)) as conn, conn:
            conn.execute(
                "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, '')", (push.SETTING_VAPID_KEY, "gAAAAA-van-een-ander-geheim")
            )
        key = await push.public_key()
        self.assertEqual(len(auth.b64url_decode(key)), 65)
        self.assertEqual(await push.public_key(), key)

    async def test_the_route_gives_the_public_key(self):
        response = await self.client.get("/api/push/key")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["publicKey"], await push.public_key())


class SendTest(PushCase):
    async def test_the_message_is_encrypted_and_signed(self):
        browser = Browser()
        await self.subscribe(browser, [timer()])
        with served(httpx.Response(201)) as route:
            wait = await push.send_due(NOW + 600 + push.GRACE_SECONDS)
        self.assertEqual(route.call_count, 1)
        sent = route.calls[0].request
        self.assertEqual(str(sent.url), FCM)
        self.assertEqual(sent.headers["TTL"], str(push.TTL_SECONDS))
        self.assertEqual(sent.headers["Content-Encoding"], "aes128gcm")
        self.assertEqual(sent.headers["Urgency"], "high")
        self.assertTrue(sent.headers["User-Agent"].startswith("MealieShopper/"))
        self.assertEqual(
            browser.read(sent.content),
            {"type": "cook-timer", "id": "lz9-1", "title": "Timer klaar: 10 min", "body": "Pasta · Stap 2", "url": "/recipes"},
            "de browser ontsleutelt precies de timer, het recept en de stap",
        )
        authorization = sent.headers["Authorization"]
        self.assertTrue(Vapid02.verify(authorization), "de handtekening klopt met de sleutel in de header")
        self.assertIn(f"k={await push.public_key()}", authorization)
        claims = json.loads(auth.b64url_decode(authorization.split("t=", 1)[1].split(",", 1)[0].split(".")[1]))
        self.assertEqual(claims["aud"], "https://fcm.googleapis.com")
        self.assertEqual(claims["sub"], "https://shop.example.nl")
        self.assertLessEqual(claims["exp"] - time.time(), 24 * 3600)
        self.assertEqual(self.rows("cook_timers"), [], "een verstuurde timer is weg")
        self.assertEqual(len(self.rows("push_subscriptions")), 1, "het abonnement blijft")
        self.assertEqual(wait, push.CHECK_SECONDS)

    async def test_only_when_the_timer_is_due(self):
        await self.subscribe(Browser(), [timer("a", 60_000), timer("b", 600_000)])
        with served(httpx.Response(201)) as route:
            wait = await push.send_due(NOW + 30)
            self.assertEqual(route.call_count, 0)
            self.assertAlmostEqual(wait, 30 + push.GRACE_SECONDS)
            await push.send_due(NOW + 60)
            self.assertEqual(route.call_count, 0, "de marge: draait de app nog, dan meldt ze de timer zelf")
            wait = await push.send_due(NOW + 60 + push.GRACE_SECONDS)
        self.assertEqual(route.call_count, 1)
        self.assertEqual([row["timer_id"] for row in self.rows("cook_timers")], ["b"])
        self.assertEqual(wait, push.CHECK_SECONDS, "hooguit zo lang slapen")

    async def test_a_stopped_or_paused_timer_gets_no_push(self):
        browser = Browser()
        await self.subscribe(browser, [timer("a", 60_000), timer("b", 60_000)])
        await self.subscribe(browser, [timer("b", 60_000)])  # a gestopt
        await self.subscribe(browser, [])  # b gepauzeerd: de app stuurt alleen lopende timers
        with served(httpx.Response(201)) as route:
            await push.send_due(NOW + 3600)
        self.assertEqual(route.call_count, 0)
        self.assertEqual(self.rows("cook_timers"), [])

    async def test_a_subscription_the_service_refuses_is_removed(self):
        for status in (404, 410, 403):
            with self.subTest(status=status):
                await self.subscribe(Browser(), [timer()])
                with served(httpx.Response(status)):
                    await push.send_due(NOW + 700)
                self.assertEqual((self.rows("push_subscriptions"), self.rows("cook_timers")), ([], []))

    async def test_a_passing_failure_is_tried_again_then_given_up(self):
        browser = Browser()
        await self.subscribe(browser, [timer(left=1000)])
        due = NOW + 1 + push.GRACE_SECONDS
        with served(side_effect=[httpx.ConnectError("geen verbinding"), httpx.Response(503), httpx.Response(500)]) as route:
            await push.send_due(due)
            row = self.rows("cook_timers")[0]
            self.assertEqual((row["attempts"], row["due_at"], row["claimed_at"]), (1, due + push.RETRY_SECONDS, None))
            await push.send_due(due + push.RETRY_SECONDS)
            self.assertEqual(self.rows("cook_timers")[0]["due_at"], due + push.RETRY_SECONDS + push.RETRY_SECONDS * 2)
            await push.send_due(due + 60)
        self.assertEqual(route.call_count, push.MAX_ATTEMPTS)
        self.assertEqual(self.rows("cook_timers"), [], "na de laatste poging is de timer weg")
        self.assertEqual(len(self.rows("push_subscriptions")), 1, "het abonnement zelf mankeert niets")

    async def test_a_broken_subscription_is_removed_without_a_request(self):
        await self.subscribe(Browser(), [timer(left=1000)])
        with closing(db(push)) as conn, conn:
            conn.execute("UPDATE push_subscriptions SET p256dh = ?", (auth.b64url_encode(b"\x04" + b"\x01" * 64),))  # geen punt op de curve
        with served(httpx.Response(201)) as route:
            await push.send_due(NOW + 10)
        self.assertEqual(route.call_count, 0)
        self.assertEqual((self.rows("push_subscriptions"), self.rows("cook_timers")), ([], []))

    async def test_an_unexpected_error_does_not_hold_up_the_other_timers(self):
        await self.subscribe(Browser(), [timer("a", left=1000), timer("b", left=2000)])
        outcomes = iter([RuntimeError("onverwacht"), 201])

        async def send(*_args):
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with mock.patch.object(push, "send", send), self.assertLogs("mealieshopper.push", "ERROR"):
            await push.send_due(NOW + 10)
        self.assertEqual(self.rows("cook_timers"), [], "de kapotte timer is weg, de andere is verstuurd")
        self.assertEqual(len(self.rows("push_subscriptions")), 1)

    async def test_a_timer_long_past_gets_no_push(self):
        await self.subscribe(Browser(), [timer(left=1000)])
        with served(httpx.Response(201)) as route:
            await push.send_due(NOW + push.STALE_SECONDS + 60)
        self.assertEqual(route.call_count, 0, "de server stond uit: de app laat de timer zelf afgaan als je haar opent")
        self.assertEqual(self.rows("cook_timers"), [])

    async def test_two_workers_send_once(self):
        await self.subscribe(Browser(), [timer(left=1000)])
        with served(httpx.Response(201)) as route:
            await asyncio.gather(push.send_due(NOW + 10), push.send_due(NOW + 10))
        self.assertEqual(route.call_count, 1)

    async def test_a_claim_of_a_worker_that_stopped_is_taken_over(self):
        await self.subscribe(Browser(), [timer(left=1000)])
        with closing(db(push)) as conn, conn:
            conn.execute("UPDATE cook_timers SET claimed_at = ?", (NOW,))
        with served(httpx.Response(201)) as route:
            await push.send_due(NOW + 10)
            self.assertEqual(route.call_count, 0, "een andere worker is ermee bezig")
            await push.send_due(NOW + push.CLAIM_SECONDS + 10)
        self.assertEqual(route.call_count, 1)

    async def test_the_task_sends_when_the_timer_ends(self):
        browser = Browser()
        with mock.patch.object(push, "GRACE_SECONDS", 0.0), served(httpx.Response(201)) as route:
            await self.client.get("/api/push/key")  # de app draait
            push.start()
            self.addAsyncCleanup(self._stop_task)
            response = await self.put(browser, [timer(left=200)])
            self.assertEqual(response.status_code, 200)
            for _ in range(100):
                if route.call_count:
                    break
                await asyncio.sleep(0.05)
        self.assertEqual(route.call_count, 1, "de taak wordt wakker bij een nieuwe timer en slaapt tot zijn eindtijd")
        self.assertEqual(browser.read(route.calls[0].request.content)["id"], "lz9-1")

    async def _stop_task(self):
        task = push._task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        push._task = push._wake = None


class RouteTest(PushCase):
    async def test_the_timers_of_a_browser_replace_what_the_server_had(self):
        browser = Browser()
        response = await self.put(browser, [timer("a"), timer("b")])
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json(), {"timers": 2, "publicKey": await push.public_key()})
        self.assertEqual((await self.put(browser, [timer("b", 1000)], url="//evil.example")).get_json()["timers"], 1)
        rows = self.rows("cook_timers")
        self.assertEqual([(row["timer_id"], row["url"]) for row in rows], [("b", "/")])
        self.assertAlmostEqual(rows[0]["due_at"], time.time() + 1 + push.GRACE_SECONDS, delta=5)
        subscription = self.rows("push_subscriptions")
        self.assertEqual([(row["user_id"], row["endpoint"], row["session_id"]) for row in subscription], [("owner", FCM, "")])

    async def test_bad_input_is_refused_without_saving(self):
        browser = Browser()
        cases = {
            "intern adres": {"subscription": Browser("http://192.168.1.1:8123/api/").subscription(), "timers": [timer()]},
            "geen abonnement": {"timers": [timer()]},
            "rare timer": {"subscription": browser.subscription(), "timers": [timer(left=-1)]},
            "geen lijst": {"subscription": browser.subscription(), "timers": {"a": 1}},
        }
        for name, body in cases.items():
            with self.subTest(name):
                response = await self.client.put("/api/cook/timers", json=body)
                self.assertEqual(response.status_code, 400)
                self.assertTrue(response.get_json()["error"])
        self.assertEqual((await self.client.put("/api/cook/timers", json=["x"])).status_code, 400)
        self.assertEqual(self.rows("push_subscriptions"), [])

    async def test_a_browser_can_take_its_subscription_away(self):
        browser = Browser()
        await self.put(browser, [timer()])
        response = await self.client.delete("/api/push/subscription", json={"endpoint": FCM})
        self.assertEqual(response.get_json(), {"removed": 1})
        self.assertEqual((self.rows("push_subscriptions"), self.rows("cook_timers")), ([], []))
        self.assertEqual((await self.client.delete("/api/push/subscription", json={})).get_json(), {"removed": 0})


class SessionTest(AppTestCase):
    """Met inloggen: een abonnement hoort bij de sessie waarmee het binnenkwam."""

    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(
            os.environ, {"PASSKEY_AUTH_ENABLED": "true", "MEALIESHOPPER_DATA_DIR": tmp.name, "MEALIESHOPPER_AUTH_SECRET": "test-secret"}
        )
        env.start()
        self.addCleanup(env.stop)
        cost = mock.patch.object(auth_password, "SCRYPT_N", 2**4)
        cost.start()
        self.addCleanup(cost.stop)
        auth.forget_runtime_state()
        self.app = create_app()
        self.admin = self.client_for(self.app)
        await self.admin.post("/api/auth/password", json={"username": "admin", "password": PASSWORD})

    async def put(self, client, browser: Browser):
        return await client.put("/api/cook/timers", json={"subscription": browser.subscription(), "timers": [timer(left=1000)]})

    async def join(self, username="anna", password=ANNA):
        token = (await self.admin.post("/api/users/invites", json={"username": username})).get_json()["token"]
        client = self.client_for(self.app)
        response = await client.post("/api/invites/password", json={"token": token, "username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.get_json())
        return client

    async def test_without_a_session_nothing_is_saved(self):
        stranger = self.client_for(self.app)
        self.assertEqual((await self.put(stranger, Browser())).status_code, 401)
        self.assertEqual((await stranger.get("/api/push/key")).status_code, 401)

    async def test_logging_out_takes_the_subscription_of_that_session_away(self):
        phone, laptop = Browser(), Browser("https://updates.push.services.mozilla.com/wpush/v2/laptop")
        self.assertEqual((await self.put(self.admin, phone)).status_code, 200)
        other = self.client_for(self.app)
        await other.post("/api/auth/password/login", json={"username": "admin", "password": PASSWORD})
        self.assertEqual((await self.put(other, laptop)).status_code, 200)
        with closing(db()) as conn:
            sessions = {row[0]: row[1] for row in conn.execute("SELECT endpoint, session_id FROM push_subscriptions")}
        self.assertTrue(all(sessions.values()), "elk abonnement weet bij welke sessie het hoort")
        await self.admin.post("/api/auth/logout", json={})
        with closing(db()) as conn:
            left = [row[0] for row in conn.execute("SELECT endpoint FROM push_subscriptions")]
            timers = conn.execute("SELECT COUNT(*) FROM cook_timers").fetchone()[0]
        self.assertEqual(left, [laptop.endpoint], "alleen het toestel dat uitlogde")
        self.assertEqual(timers, 1)

    async def test_a_session_that_ended_gets_no_push(self):
        await self.put(self.admin, Browser())
        with closing(db()) as conn, conn:
            conn.execute("UPDATE users SET session_epoch = session_epoch + 1")  # overal uitgelogd, of een nieuw wachtwoord
        with served(httpx.Response(201)) as route:
            await push.send_due(time.time() + 60)
        self.assertEqual(route.call_count, 0)
        with closing(db()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0], 0)

    async def test_a_live_session_gets_its_push(self):
        browser = Browser()
        await self.put(self.admin, browser)
        with served(httpx.Response(201)) as route:
            await push.send_due(time.time() + 60)
        self.assertEqual(route.call_count, 1)
        self.assertEqual(browser.read(route.calls[0].request.content)["title"], "Timer klaar: 10 min")

    async def test_a_deleted_user_takes_the_subscriptions_along(self):
        anna = await self.join()
        self.assertEqual((await self.put(anna, Browser())).status_code, 200)
        anna_id = (await anna.get("/api/auth/status")).get_json()["userId"]
        self.assertEqual((await self.admin.delete(f"/api/users/{anna_id}")).status_code, 200)
        with closing(db()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM cook_timers").fetchone()[0], 0)
        self.assertEqual(await users.get_user(anna_id), None)
