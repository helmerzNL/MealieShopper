"""De export van je eigen koppelingen als bestand voor een pull request (`shared_links_export`)."""

import os
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest import mock

import helpers
from helpers import AppTestCase, db, run

from mealieshopper import (
    auth,
    auth_password,
    name_keys,
    price_import,
    product_links,
    shared_links,
    shared_links_export,
    tasks,
)
from mealieshopper.app import create_app
from mealieshopper.shared_links import SharedLink

NOW: str = "2026-11-11T01:00:00+00:00"


def product(product_id: str, title: str, unit_size: str = "1 kg") -> dict[str, Any]:
    return {
        "productId": product_id,
        "title": title,
        "unitSize": unit_size,
        "brand": "Merk",
        "image": "https://img.example/x.jpg",
    }


class ExportCase(AppTestCase):
    PASSWORD: str = "koffie-met-melk-42"
    ANNA: str = "een-heel-ander-wachtwoord"

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.shared_dir: Path = Path(tmp.name) / "shared"
        self.shared_dir.mkdir()
        for patch in (
            mock.patch.dict(
                os.environ,
                {
                    "MEALIESHOPPER_DATA_DIR": tmp.name,
                    "PASSKEY_AUTH_ENABLED": "true",
                    "MEALIESHOPPER_AUTH_SECRET": "test-secret",
                },
            ),
            mock.patch.object(auth_password, "SCRYPT_N", 2**4),
            mock.patch.object(shared_links, "DATA_DIR", self.shared_dir),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        auth.forget_runtime_state()
        self.addCleanup(auth.forget_runtime_state)
        run(auth.init_db())
        self.orders: int = 0

    def write_file(self, store: str, links: list[SharedLink]) -> None:
        (self.shared_dir / f"{store}.json").write_text(
            shared_links.dump(links), encoding="utf-8"
        )

    def bought(self, title: str, store: str = "ah") -> None:
        with closing(db(price_import)) as conn, conn:
            self.orders += 1
            conn.execute(
                "INSERT INTO imported_purchases (store, order_ref, position, day, title, quantity, paid, regular, order_total, imported_at) "
                "VALUES (?, ?, 0, '2022-03-14', ?, 1, 2.0, NULL, 10, 'x')",
                (store, f"M{self.orders}", title),
            )

    async def link(
        self,
        name: str,
        product_id: str,
        title: str = "Titel",
        origin: str = "manual",
        store: str = "ah",
        unit: str = "1 kg",
        bought: bool = True,
    ) -> None:
        if bought:
            self.bought(name, store)
        await product_links.save(
            store, name, product(product_id, title, unit), origin=origin
        )

    async def asyncSetUp(self) -> None:
        self.addAsyncCleanup(tasks.cancel_all)
        self.app = create_app()
        self.admin: helpers.AppClient = self.client_for(self.app)
        await self.admin.post(
            "/api/auth/password", json={"username": "admin", "password": self.PASSWORD}
        )


class PendingTest(ExportCase):
    async def test_pending_has_manual_and_confirmed_but_never_exact(self) -> None:
        await self.link("AH Appel", "1", origin="manual")
        await self.link("AH Peer", "2", origin="confirmed")
        await self.link("AH Banaan", "3", origin="exact")
        page = await shared_links_export.pending("ah")
        self.assertEqual(
            [item["name"] for item in page["new"]], ["ah appel", "ah peer"]
        )
        self.assertEqual(page["conflicts"], [])
        self.assertEqual(
            page["new"][0],
            {
                "key": name_keys.key("AH Appel"),
                "name": "ah appel",
                "productId": "1",
                "title": "Titel",
                "unitSize": "1 kg",
            },
        )
        self.assertIn(
            "/blob/beta/mealieshopper/shared_link_data/ah.json", page["fileUrl"]
        )

    async def test_pending_skips_article_links_and_names_that_are_not_from_an_imported_order(
        self,
    ) -> None:
        await product_links.save(
            "ah", "AH Kaas", product("1", "Kaas"), article="123456"
        )
        await self.link("AH Zonder bestelling", "2", bought=False)
        await self.link("AH Andere winkel", "3", store="jumbo", bought=False)
        self.bought("AH Andere winkel", "jumbo")
        await self.link("AH Wel", "4")
        page = await shared_links_export.pending("ah")
        self.assertEqual([item["name"] for item in page["new"]], ["ah wel"])

    async def test_pending_skips_what_the_file_already_has(self) -> None:
        self.write_file("ah", [SharedLink("ah appel", "1", "Appel", "1 kg")])
        await self.link("AH Appel", "1")
        page = await shared_links_export.pending("ah")
        self.assertEqual((page["new"], page["conflicts"]), ([], []))

    async def test_other_product_for_a_shared_name_is_a_conflict_not_new(self) -> None:
        self.write_file("ah", [SharedLink("ah appel", "1", "Oude appel", "1 kg")])
        await self.link("AH Appel", "9", title="Nieuwe appel")
        page = await shared_links_export.pending("ah")
        self.assertEqual(page["new"], [])
        self.assertEqual(len(page["conflicts"]), 1)
        conflict = page["conflicts"][0]
        self.assertEqual(
            (
                conflict["productId"],
                conflict["sharedProductId"],
                conflict["sharedTitle"],
            ),
            ("9", "1", "Oude appel"),
        )

    async def test_two_own_names_with_the_same_key_and_different_products_are_both_left_out_and_reported(
        self,
    ) -> None:
        await self.link("AH Les pain triomphe heel online", "1")
        await self.link("AH Les pains triomphe heel", "2")
        await self.link("AH Gewoon", "3")
        page = await shared_links_export.pending("ah")
        self.assertEqual([item["name"] for item in page["new"]], ["ah gewoon"])
        self.assertEqual(len(page["ambiguous"]), 1)
        ambiguous = page["ambiguous"][0]
        self.assertEqual(ambiguous["key"], name_keys.key("AH Les pain triomphe heel"))
        self.assertEqual(ambiguous["productIds"], ["1", "2"])
        self.assertEqual(len(ambiguous["names"]), 2)
        text = await shared_links_export.merged(
            "ah", [ambiguous["key"], name_keys.key("AH Gewoon")]
        )
        self.assertEqual(
            [link.name for link in shared_links.parse(text)], ["ah gewoon"]
        )

    async def test_two_names_with_the_same_key_and_the_same_product_give_one_item(
        self,
    ) -> None:
        await self.link("AH Les pain triomphe heel online", "1")
        await self.link("AH Les pains triomphe heel", "1")
        page = await shared_links_export.pending("ah")
        self.assertEqual(len(page["new"]), 1)
        self.assertEqual(page["ambiguous"], [])


class MergedTest(ExportCase):
    async def test_merged_passes_the_file_test_and_is_sorted(self) -> None:
        self.write_file("ah", [SharedLink("ah zout", "7", "Zout", "1 kg")])
        await self.link("AH Appel", "1")
        await self.link("AH Peer", "2")
        page = await shared_links_export.pending("ah")
        text = await shared_links_export.merged(
            "ah", [item["key"] for item in page["new"]]
        )
        self.assertEqual(shared_links.problems(shared_links.parse(text)), [])
        self.assertEqual(shared_links.dump(shared_links.parse(text)), text)
        self.assertEqual(
            [link.name for link in shared_links.parse(text)],
            ["ah appel", "ah peer", "ah zout"],
        )

    async def test_merged_only_has_the_four_fields(self) -> None:
        await self.link("AH Appel", "1", title="Appel")
        page = await shared_links_export.pending("ah")
        text = await shared_links_export.merged("ah", [page["new"][0]["key"]])
        self.assertEqual(
            text.splitlines()[1],
            '  {"name": "ah appel", "productId": "1", "title": "Appel", "unitSize": "1 kg"}',
        )
        for forbidden in (
            "price",
            "date",
            "brand",
            "image",
            "Merk",
            "img.example",
            "origin",
            "createdAt",
        ):
            self.assertNotIn(forbidden, text)

    async def test_merged_takes_only_the_checked_keys_and_a_checked_conflict_replaces_the_line(
        self,
    ) -> None:
        self.write_file(
            "ah",
            [
                SharedLink("ah appel", "1", "Oude appel", "1 kg"),
                SharedLink("ah zout", "7", "Zout", "1 kg"),
            ],
        )
        await self.link("AH Appel", "9", title="Nieuwe appel")
        await self.link("AH Peer", "2")
        await self.link("AH Kers", "3")
        page = await shared_links_export.pending("ah")
        keys = {item["name"]: item["key"] for item in page["new"] + page["conflicts"]}
        unchecked = await shared_links_export.merged("ah", [keys["ah peer"]])
        self.assertEqual(
            {link.name: link.product_id for link in shared_links.parse(unchecked)},
            {"ah appel": "1", "ah peer": "2", "ah zout": "7"},
        )
        replaced = await shared_links_export.merged(
            "ah", [keys["ah appel"], "~onbekend"]
        )
        self.assertEqual(
            {link.name: link.product_id for link in shared_links.parse(replaced)},
            {"ah appel": "9", "ah zout": "7"},
        )
        none = await shared_links_export.merged("ah", [])
        self.assertEqual(
            none,
            shared_links.dump(
                shared_links.parse(
                    (self.shared_dir / "ah.json").read_text(encoding="utf-8")
                )
            ),
        )

    async def test_merged_refuses_a_file_that_would_have_problems(self) -> None:
        await self.link("AH Appel", "1")
        key = (await shared_links_export.pending("ah"))["new"][0]["key"]
        with (
            mock.patch.object(shared_links, "problems", return_value=["kapot"]),
            self.assertRaisesRegex(ValueError, "kapot"),
        ):
            await shared_links_export.merged("ah", [key])

    async def test_a_broken_file_is_an_error_and_never_an_export_without_the_rest(
        self,
    ) -> None:
        (self.shared_dir / "ah.json").write_text("geen json", encoding="utf-8")
        await self.link("AH Appel", "1")
        with self.assertRaises(ValueError):
            await shared_links_export.pending("ah")


class RoutesTest(ExportCase):
    async def join(self) -> helpers.AppClient:
        invite = await self.admin.post("/api/users/invites", json={"username": "anna"})
        client: helpers.AppClient = self.client_for(self.app)
        await client.post(
            "/api/invites/password",
            json={"token": invite.get_json()["token"], "password": self.ANNA},
        )
        return client

    def test_only_the_exact_export_path_is_admin_only(self) -> None:
        self.assertTrue(auth.admin_only("/api/shared-links/export"))
        self.assertFalse(auth.admin_only("/api/shared-links/reject"))
        self.assertFalse(auth.admin_only("/api/shared-links/restore"))

    async def test_export_routes_are_admin_only(self) -> None:
        anna, anonymous = await self.join(), self.client_for(self.app)
        for client, expected in ((anna, 403), (anonymous, 401)):
            self.assertEqual(
                (await client.get("/api/shared-links/export?store=ah")).status_code,
                expected,
            )
            self.assertEqual(
                (
                    await client.post(
                        "/api/shared-links/export", json={"store": "ah", "keys": []}
                    )
                ).status_code,
                expected,
            )
        reject = await anna.post(
            "/api/shared-links/reject",
            json={"store": "ah", "name": "AH Appel", "productId": "1"},
        )
        self.assertEqual(reject.status_code, 200)

    async def test_routes_validate_the_input(self) -> None:
        for query in ("", "?store=nope", "?store=aldi"):
            response = await self.admin.get(f"/api/shared-links/export{query}")
            self.assertEqual(response.status_code, 400, query)
            self.assertTrue(response.get_json()["error"])
        for body in (
            {},
            {"store": "nope", "keys": []},
            {"store": "aldi", "keys": []},
            {"store": "ah"},
            {"store": "ah", "keys": "x"},
            {"store": "ah", "keys": ["a", 1]},
            {"store": "ah", "keys": ["a"] * 5001},
        ):
            response = await self.admin.post("/api/shared-links/export", json=body)
            self.assertEqual(response.status_code, 400, str(body)[:60])
            self.assertTrue(response.get_json()["error"])

    async def test_post_gives_the_file_as_a_download_and_ignores_keys_that_are_not_pending(
        self,
    ) -> None:
        await self.link("AH Appel", "1", title="Appel")
        page = (await self.admin.get("/api/shared-links/export?store=ah")).get_json()
        self.assertEqual(len(page["new"]), 1)
        response = await self.admin.post(
            "/api/shared-links/export",
            json={"store": "ah", "keys": [page["new"][0]["key"], "~niet-klaar"]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["Content-Type"].split(";")[0], "application/json"
        )
        self.assertEqual(
            response.headers["Content-Disposition"], 'attachment; filename="ah.json"'
        )
        self.assertEqual(
            [link.name for link in shared_links.parse(response.text)], ["ah appel"]
        )
