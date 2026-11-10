"""De nachtelijke synchronisatie van bestellingen en kassabonnen (purchase_sync.py)."""
from __future__ import annotations

import logging
import unittest
from unittest import mock

import helpers  # noqa: F401 - zet het pad naar de app

from mealieshopper import ah, ah_orders, crisp, crisp_orders, jumbo, jumbo_orders, picnic, picnic_orders, purchase_sync


class NightlyPurchaseSyncTest(unittest.IsolatedAsyncioTestCase):
    def linked(self, **stores: str) -> None:
        """Welke winkels gekoppeld zijn: de naam van de winkel met het token, de rest heeft er geen."""
        checks = {"ah": (ah, "configured_refresh_token"), "jumbo": (jumbo, "configured_refresh_token"),
                  "picnic": (picnic, "configured_auth_key"), "crisp": (crisp, "configured_client_id")}
        for store, (module, name) in checks.items():
            patcher = mock.patch.object(module, name, new_callable=mock.AsyncMock, return_value=stores.get(store, ""))
            patcher.start()
            self.addCleanup(patcher.stop)

    def syncs(self) -> dict[str, mock.AsyncMock]:
        targets = {"ah.orders": (ah_orders, "sync_orders"), "ah.receipts": (ah_orders, "sync_receipts"),
                   "jumbo.orders": (jumbo_orders, "sync_orders"), "picnic.orders": (picnic_orders, "sync_orders"),
                   "crisp.orders": (crisp_orders, "sync_orders")}
        found: dict[str, mock.AsyncMock] = {}
        for key, (module, name) in targets.items():
            patcher = mock.patch.object(module, name, new_callable=mock.AsyncMock, return_value={"done": key})
            found[key] = patcher.start()
            self.addCleanup(patcher.stop)
        return found

    async def test_without_a_linked_store_nothing_happens(self) -> None:
        self.linked()
        syncs = self.syncs()
        self.assertIsNone(await purchase_sync.sync_nightly())
        self.assertEqual([key for key, sync in syncs.items() if sync.called], [])

    async def test_only_linked_stores_are_synced(self) -> None:
        self.linked(ah="token", picnic="key")
        syncs = self.syncs()
        self.assertEqual(
            await purchase_sync.sync_nightly(),
            {"ah": {"orders": {"done": "ah.orders"}, "receipts": {"done": "ah.receipts"}}, "picnic": {"orders": {"done": "picnic.orders"}}},
        )
        self.assertEqual(sorted(key for key, sync in syncs.items() if sync.called), ["ah.orders", "ah.receipts", "picnic.orders"])

    async def test_a_failing_part_is_reported_and_the_rest_goes_on(self) -> None:
        self.linked(ah="token", jumbo="token")
        syncs = self.syncs()
        syncs["ah.orders"].side_effect = RuntimeError("AH zegt nee")
        with self.assertLogs("mealieshopper.purchase_sync", level=logging.WARNING) as logs:
            result = await purchase_sync.sync_nightly()
        self.assertEqual(result, {"ah": {"orders": {"error": "AH zegt nee"}, "receipts": {"done": "ah.receipts"}},
                                  "jumbo": {"orders": {"done": "jumbo.orders"}}})
        self.assertIn("ah", logs.output[0])

    async def test_a_failing_link_check_skips_that_store(self) -> None:
        self.linked(jumbo="token")
        self.syncs()
        with mock.patch.object(ah, "configured_refresh_token", new_callable=mock.AsyncMock, side_effect=RuntimeError("stuk")), \
             self.assertLogs("mealieshopper.purchase_sync", level=logging.WARNING):
            self.assertEqual(await purchase_sync.sync_nightly(), {"jumbo": {"orders": {"done": "jumbo.orders"}}})


if __name__ == "__main__":
    unittest.main()
