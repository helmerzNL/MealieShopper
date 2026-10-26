import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

os.environ.setdefault("MEALIESHOPPER_DATA_DIR", tempfile.mkdtemp())

from mealieshopper import auth, jumbo, offers, offers_schedule, picnic  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402


class TempDataDirTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name, "PASSKEY_AUTH_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        auth.init_db()
        jumbo._search_variant = 0
        jumbo._promo_dates = True


def jumbo_product(sku, title, price=299, promo=None, tags=(), **extra):
    return {"sku": sku, "title": title, "link": f"/producten/{sku}", "subtitle": "500 g",
            "price": {"price": price, "promoPrice": promo}, "availability": {"isAvailable": True},
            "promotions": [{"tags": [{"text": text} for text in tags]}] if tags else [], **extra}


class JumboOffersTest(TempDataDirTest):
    def test_offer_carries_the_period_of_its_promotion(self):
        raw = jumbo_product("9", "Kaas", tags=("2 voor 3,99",))
        raw["promotions"][0].update(start={"iso": "2026-10-21T00:01:00+02:00"}, end={"iso": "2026-10-27T23:59:59+01:00"})
        offer = jumbo.offers_in_search({"searchProducts": {"products": [raw]}})[0]
        self.assertEqual((offer["validFrom"], offer["validUntil"]), ("2026-10-21", "2026-10-27"))

    def test_only_products_with_an_action_are_offers(self):
        data = {"searchProducts": {"products": [
            jumbo_product("1", "Kipfilet", 499, 349, ["2e halve prijs"]),
            jumbo_product("2", "Melk", 129),
            jumbo_product("3", "Kaas", 599, tags=["Nu 10% korting"]),
            jumbo_product("4", "Brood", 199, 99, availability={"isAvailable": False}),
        ]}}
        found = {o["id"]: o for o in jumbo.offers_in_search(data)}
        self.assertEqual(sorted(found), ["1", "3"])
        chicken = found["1"]
        self.assertEqual((chicken["price"], chicken["priceBefore"], chicken["promoLabel"]), (3.49, 4.99, "2e halve prijs"))
        self.assertEqual((chicken["unitSize"], chicken["url"]), ("500 g", "https://www.jumbo.com/producten/1"))
        self.assertEqual((found["3"]["price"], found["3"]["priceBefore"], found["3"]["promoLabel"]), (5.99, None, "Nu 10% korting"))

    def test_odd_answers_do_not_crash(self):
        for odd in (None, {}, {"searchProducts": None}, {"searchProducts": {"products": [None, "x", {}]}}):
            self.assertEqual(jumbo.offers_in_search(odd), [])

    def test_offers_are_collected_over_the_terms_without_duplicates(self):
        def search(term):
            return {"searchProducts": {"products": [jumbo_product("1", "Kipfilet", 499, 349), jumbo_product(f"x-{term}", term, 200, 100)]}}

        with mock.patch.object(jumbo, "_search_data", side_effect=search):
            found = jumbo.get_offers()
        self.assertEqual(len(found), len(jumbo.OFFER_TERMS) + 1)
        self.assertEqual([o["id"] for o in found].count("1"), 1)

    def test_a_term_that_fails_does_not_block_the_rest_but_all_failing_is_an_error(self):
        def search(term):
            if term == "kip":
                raise RuntimeError("stuk")
            return {"searchProducts": {"products": [jumbo_product(term, term, 200, 100)]}}

        with mock.patch.object(jumbo, "_search_data", side_effect=search):
            self.assertEqual(len(jumbo.get_offers()), len(jumbo.OFFER_TERMS) - 1)
        with mock.patch.object(jumbo, "_search_data", side_effect=RuntimeError("alles stuk")):
            with self.assertRaisesRegex(RuntimeError, "alles stuk"):
                jumbo.get_offers()

    def test_a_refused_link_is_reported_as_such(self):
        with mock.patch.object(jumbo, "_search_data", side_effect=jumbo.JumboAuthError("verlopen")):
            with self.assertRaises(jumbo.JumboAuthError):
                jumbo.get_offers()

    def test_search_still_filters_unavailable_products(self):
        data = {"searchProducts": {"products": [jumbo_product("1", "A", 100), jumbo_product("2", "B", 100, availability={"isAvailable": False})]}}
        with mock.patch.object(jumbo, "_search_data", return_value=data):
            self.assertEqual([p["id"] for p in jumbo.search_products("a")], ["1"])


def tile(unit_id, name, cents=199, regular=None, promo_text=None, **extra):
    unit = {"id": unit_id, "name": name, "display_price": cents, "image_id": f"img{unit_id}", "unit_quantity": "1 st", "decorators": [], **extra}
    if regular:
        unit["price"] = regular
    if promo_text:
        unit["decorators"].append({"type": "PROMO", "text": promo_text})
    return {"type": "SELLING_UNIT_TILE", "sellingUnit": unit}


def page(*tiles, links=()):
    return {"body": {"children": [{"content": list(tiles)}, {"links": [f"picnic://store/page;id={link}" for link in links]}]}}


class PicnicOffersTest(TempDataDirTest):
    def setUp(self):
        super().setUp()
        picnic.save_auth_key("key")
        patcher = mock.patch.object(picnic, "OFFER_PAGE_DELAY", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def serve(self, pages, tabs=()):
        def authed(method, path, **kwargs):
            if path == "/bootstrap":
                return {"tabs": list(tabs)}
            reference = path.split("/pages/", 1)[1]
            if reference not in pages:
                raise RuntimeError("404")
            return pages[reference]

        return mock.patch.object(picnic, "_authed", side_effect=authed)

    def test_a_tile_with_a_discount_becomes_an_offer(self):
        offers_on_page = picnic.offers_from_page(page(tile("s1", "Kipfilet", 349, 499, "2e halve prijs"), tile("s2", "Melk", 129)))
        first, second = offers_on_page
        self.assertEqual((first["price"], first["priceBefore"], first["promoLabel"], first["promo"]), (3.49, 4.99, "2e halve prijs", True))
        self.assertEqual((second["price"], second["priceBefore"], second["promo"]), (1.29, None, False))

    def test_the_page_of_the_offers_tab_is_used_and_only_promotions_are_kept(self):
        tabs = [{"id": "deals", "title": "Aanbiedingen", "tab_type": "PAGE", "target": {"type": "PICNIC_PAGE_REFERENCE", "reference": "deals-root"}}]
        pages = {"deals-root": page(tile("s1", "Kipfilet", 349, 499), tile("s2", "Melk", 129))}
        with self.serve(pages, tabs):
            found = picnic.get_offers()
        self.assertEqual([(o["id"], o["price"], o["priceBefore"]) for o in found], [("s1", 3.49, 4.99)])
        self.assertNotIn("promo", found[0])

    def test_linked_pages_are_followed_and_fallback_names_are_tried(self):
        pages = {"deals-page-root": page(links=["week-deals"]), "week-deals": page(tile("s1", "Kaas", 299, 399))}
        with self.serve(pages):
            found = picnic.get_offers()
        self.assertEqual([o["id"] for o in found], ["s1"])

    def test_a_page_without_promotion_markers_keeps_all_its_products(self):
        pages = {"deals-page-root": page(tile("s1", "Kaas", 299), tile("s2", "Melk", 129))}
        with self.serve(pages):
            self.assertEqual([o["id"] for o in picnic.get_offers()], ["s1", "s2"])

    HOME_TABS = [
        {"id": "home-tab", "title": "Ontdek", "label": "Ontdek", "tab_type": "PAGE", "target": {"type": "PICNIC_PAGE_REFERENCE", "reference": "home_page_root"}},
        {"id": "meals-page", "title": "Koken", "tab_type": "PAGE", "target": {"type": "PICNIC_PAGE_REFERENCE", "reference": "meals-page-root"}},
    ]

    def test_without_an_offers_tab_the_discover_page_is_used_and_only_discounts_count(self):
        pages = {"home_page_root": page(tile("s1", "Kipfilet", 349, 499, "2e halve prijs"), tile("s2", "Melk", 129))}
        with self.serve(pages, self.HOME_TABS):
            found = picnic.get_offers()
        self.assertEqual([o["id"] for o in found], ["s1"], "de gewone producten van de startpagina zijn geen aanbiedingen")

    def test_references_put_offer_pages_first_and_mark_the_home_page_strict(self):
        with self.serve({}, self.HOME_TABS):
            references = picnic.offer_references()
        self.assertEqual(references[-1], ("home_page_root", True))
        self.assertEqual([ref for ref, strict in references if strict], ["home_page_root"])
        self.assertEqual([ref for ref, _ in references[:3]], list(picnic.OFFER_FALLBACKS))
        with self.serve({}, []):
            self.assertEqual(picnic.offer_references()[-1], (picnic.HOME_FALLBACK, True))

    def test_the_strict_page_only_follows_links_about_offers_including_lazy_pages(self):
        home = page(tile("s1", "Melk", 129), links=["aanbiedingen-page", "fruit-page"])
        home["body"]["lazy"] = {"pageConfig": {"id": "week-deals-content"}}
        pages = {
            "home_page_root": home,
            "aanbiedingen-page": page(tile("s2", "Kaas", 299, 399)),
            "week-deals-content": page(tile("s3", "Brood", 99, 149, "1+1 gratis")),
            "fruit-page": page(tile("s4", "Appel", 99, 199)),
        }
        with self.serve(pages, self.HOME_TABS):
            found = picnic.get_offers()
        self.assertEqual(sorted(o["id"] for o in found), ["s2", "s3"], "fruit-page gaat niet over aanbiedingen")

    def test_a_home_page_without_any_discount_is_an_error_not_a_list_of_products(self):
        pages = {"home_page_root": page(tile("s1", "Melk", 129), tile("s2", "Kaas", 299))}
        with self.serve(pages, self.HOME_TABS):
            with self.assertRaisesRegex(RuntimeError, "verkenner|explorer"):
                picnic.get_offers()

    def test_discount_markers_cover_ranges_and_other_promo_decorators(self):
        ranged = picnic.offers_from_page(page(tile("s1", "Koffie", 599, price_ranges=[{"x": 1}])))
        self.assertTrue(ranged[0]["promo"])
        other = picnic.offers_from_page(page({"type": "SELLING_UNIT_TILE", "sellingUnit": {"id": "s2", "name": "Thee", "display_price": 199, "decorators": [{"type": "DISCOUNT_LABEL", "text": "25% korting"}]}}))
        self.assertEqual((other[0]["promo"], other[0]["promoLabel"]), (True, "25% korting"))
        plain = picnic.offers_from_page(page({"type": "SELLING_UNIT_TILE", "sellingUnit": {"id": "s3", "name": "Water", "display_price": 59, "decorators": [{"type": "QUANTITY", "quantity": 1}]}}))
        self.assertFalse(plain[0]["promo"])

    def test_no_page_at_all_is_a_clear_error_and_an_unlinked_account_too(self):
        with self.serve({}):
            with self.assertRaisesRegex(RuntimeError, "geen aanbiedingen|no offers"):
                picnic.get_offers()
        auth.set_secret(picnic.AUTH_KEY_SECRET, "")
        with self.assertRaises(RuntimeError):
            picnic.get_offers()


def text_tile(unit_id, name, cents, *texts):
    """Een tegel zoals de startpagina hem toont: de tegel zelf met de teksten ernaast in hetzelfde onderdeel."""
    children = [{"type": "SELLING_UNIT_TILE", "sellingUnit": {"id": unit_id, "name": name, "display_price": cents, "image_id": "i", "unit_quantity": "1 st", "decorators": []}}]
    children += [{"type": "RICH_TEXT", "markdown": text} for text in texts]
    return {"type": "PML", "id": f"tile-{unit_id}", "pml": {"component": {"children": children}}}


def promo_card(title, reference):
    return {"type": "PML", "id": "core-horizontal-card-item_0__3", "pml": {"component": {"children": [
        {"type": "RICH_TEXT", "markdown": f"#(#123456){title}#(#123456)"},
        {"type": "TOUCHABLE", "onPress": {"actionType": "OPEN", "target": f"nl.picnic-supermarkt://store/page;id={reference}"}}]}}}


class PicnicHomePageOffersTest(TempDataDirTest):
    HOME_TABS = [{"id": "home-tab", "title": "Ontdek", "tab_type": "PAGE", "target": {"type": "PICNIC_PAGE_REFERENCE", "reference": "home_page_root"}}]

    def setUp(self):
        super().setUp()
        picnic.save_auth_key("key")
        patcher = mock.patch.object(picnic, "OFFER_PAGE_DELAY", 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.asked = []

    def serve(self, pages):
        def authed(method, path, **kwargs):
            self.asked.append(path)
            if path == "/bootstrap":
                return {"tabs": self.HOME_TABS}
            reference = path.split("/pages/", 1)[1]
            if reference not in pages:
                raise RuntimeError("404")
            return pages[reference]

        return mock.patch.object(picnic, "_authed", side_effect=authed)

    def test_the_discount_text_next_to_a_tile_makes_it_an_offer_with_that_label(self):
        page = {"body": {"children": [
            text_tile("s1", "Pasta", 129, "Pasta 500 g", "1+1 gratis"),
            text_tile("s2", "Kaas", 299, "Kaas 400 g", "25% korting"),
            text_tile("s3", "Melk", 129, "Melk 1 l"),
            text_tile("s4", "Cola", 199, "Cola", "2 voor €5"),
            text_tile("s5", "Brood", 189, "2e = 50% korting"),
            text_tile("s6", "Thee", 249, "nu €1.99"),
        ]}}
        offers_on_page = {o["id"]: o for o in picnic.offers_from_page(page)}
        self.assertEqual({k: (v["promo"], v["promoLabel"]) for k, v in offers_on_page.items()}, {
            "s1": (True, "1+1 gratis"), "s2": (True, "25% korting"), "s3": (False, ""), "s4": (True, "2 voor €5"),
            "s5": (True, "2e = 50% korting"), "s6": (True, "nu €1.99")})
        self.assertEqual(offers_on_page["s1"]["price"], 1.29)

    def test_color_markup_is_removed_from_texts(self):
        self.assertEqual(picnic._clean_text("#(#1A2B3C)Appels en peren#(#1A2B3C)"), "Appels en peren")
        self.assertEqual(picnic._clean_text(None), "")

    def test_references_keep_their_parameters_and_both_app_schemes_are_read(self):
        page = {"a": "nl.picnic-supermarkt://store/page;id=promo-group-deep-dive,promo_group_id=abc-4",
                "b": "picnic://store/page;id=product-details-page-root,id=s1&show_category_action=true",
                "c": "picnic://store/page;id=loyalty-page-root-1-09", "d": "picnic://store/page"}
        self.assertEqual(picnic.page_references(page), [
            "promo-group-deep-dive?promo_group_id=abc-4", "product-details-page-root?id=s1&show_category_action=true", "loyalty-page-root-1-09"])

    def test_cards_under_acties_are_followed_and_every_product_there_counts(self):
        home = {"body": {"children": [
            text_tile("s1", "Pasta", 129, "1+1 gratis"), text_tile("s2", "Melk", 129, "Melk"),
            promo_card("Appels en peren", "promo-group-deep-dive,promo_group_id=g1-4"),
            promo_card("Pringles", "promo-group-deep-dive,promo_group_id=g2-7"),
            {"type": "PML", "id": "recipe", "pml": {"component": {"children": [{"type": "TOUCHABLE", "onPress": {"target": "picnic://store/page;id=selling-group-details-page,selling_group_id=x"}}]}}},
        ]}}
        pages = {
            "home_page_root": home,
            "promo-group-deep-dive?promo_group_id=g1-4": {"body": {"children": [text_tile("s3", "Elstar", 199, "Elstar", "20% korting"), text_tile("s4", "Conference", 249, "Conference")]}},
            "promo-group-deep-dive?promo_group_id=g2-7": {"body": {"children": [text_tile("s5", "Pringles", 199, "Pringles")]}},
        }
        with self.serve(pages):
            found = {o["id"]: o for o in picnic.get_offers()}
        self.assertEqual(sorted(found), ["s1", "s3", "s4", "s5"], "s2 is een gewoon product op de startpagina")
        self.assertEqual(found["s1"]["promoLabel"], "1+1 gratis")
        self.assertEqual(found["s3"]["promoLabel"], "20% korting", "de tekst van de tegel gaat voor")
        self.assertEqual(found["s4"]["promoLabel"], "Appels en peren", "zonder tekst de titel van de kaart")
        self.assertEqual(found["s5"]["promoLabel"], "Pringles")
        self.assertNotIn("promo", found["s1"])
        self.assertIn("/pages/promo-group-deep-dive?promo_group_id=g1-4", self.asked)
        self.assertFalse(any("selling-group-details-page" in path for path in self.asked), "recepten volgen we niet")

    def test_lazy_pages_of_an_action_page_are_read_and_a_failing_card_does_not_stop_the_rest(self):
        group = {"body": {"children": [text_tile("s3", "Elstar", 199, "Elstar")]}, "lazy": {"pageConfig": {"id": "promo-group-content"}}}
        home = {"body": {"children": [promo_card("Een", "promo-group-deep-dive,promo_group_id=g1-4"), promo_card("Twee", "promo-group-deep-dive,promo_group_id=g2-7")]}}
        pages = {"home_page_root": home, "promo-group-deep-dive?promo_group_id=g2-7": group, "promo-group-content": {"body": {"children": [text_tile("s9", "Peer", 99, "Peer")]}}}
        with self.serve(pages):
            found = picnic.get_offers()
        self.assertEqual(sorted(o["id"] for o in found), ["s3", "s9"])

    def test_the_number_of_pages_is_capped(self):
        cards = [promo_card(f"Groep {i}", f"promo-group-deep-dive,promo_group_id=g{i}-1") for i in range(80)]
        pages = {"home_page_root": {"body": {"children": cards}}}
        pages.update({f"promo-group-deep-dive?promo_group_id=g{i}-1": {"body": {"children": [text_tile(f"s{i}", "x", 100, "x")]}} for i in range(80)})
        with self.serve(pages):
            found = picnic.get_offers()
        self.assertLessEqual(len([p for p in self.asked if "/pages/" in p and "home_page_root" in p or "promo-group" in p]), picnic.MAX_OFFER_PAGES)
        self.assertGreater(len(found), 10)


class PicnicExplorerTest(TempDataDirTest):
    def test_the_report_describes_the_offer_pages_without_content(self):
        from mealieshopper import picnic_explore

        home = page(tile("s1", "Kipfilet", 349, 499, "2e halve prijs"), tile("s2", "Melk", 129), links=["aanbiedingen-page"])
        home["body"]["lazy"] = {"pageConfig": {"id": "week-deals-content"}}
        pages = {"home_page_root": home, "aanbiedingen-page": page(tile("s3", "Kaas", 299, 399)), "week-deals-content": page(tile("s4", "Brood", 99, 149))}
        tabs = [{"id": "home-tab", "title": "Ontdek", "tab_type": "PAGE", "target": {"type": "PICNIC_PAGE_REFERENCE", "reference": "home_page_root"}}]

        def authed(method, path, **kwargs):
            if path == "/bootstrap":
                return {"tabs": tabs}
            reference = path.split("/pages/", 1)[1]
            if reference not in pages:
                raise RuntimeError('404: {"error": {"code": "NOT_FOUND"}}')
            return pages[reference]

        picnic.save_auth_key("key")
        with mock.patch.object(picnic, "_authed", side_effect=authed):
            probes = picnic_explore.offer_probes()
        names = [p["name"] for p in probes]
        self.assertEqual(names[0], "aanbiedingen: welke pagina's zoeken we (pagina, streng)")
        home_probe = next(p for p in probes if p["name"] == "aanbiedingen: GET pages/home_page_root")
        census = home_probe["info"]["telling van de tegels"]
        self.assertEqual((census["tegels"], census["als aanbieding herkend"], census["`price` anders dan `display_price`"]), (2, 1, 1))
        self.assertIn("PROMO: text", census["decorators"])
        self.assertEqual(home_probe["info"]["pagina's die later laden (pageConfig)"], ["week-deals-content"])
        self.assertIn("aanbiedingen: GET pages/aanbiedingen-page", names)
        self.assertIn("aanbiedingen: GET pages/week-deals-content", names)
        report = repr(probes)
        for secret in ("Kipfilet", "Melk", "Kaas"):
            self.assertNotIn(secret, report)


class PicnicExplorerDetailTest(TempDataDirTest):
    PAGE = {"body": {"children": [
        {"type": "PML", "id": "h", "pml": {"component": {"type": "RICH_TEXT", "markdown": "#(X)Acties#(X) van de week"}}},
        {"type": "PML", "id": "card-1", "pml": {"component": {"children": [
            {"type": "RICH_TEXT", "markdown": "Geheime merknaam"},
            {"type": "TOUCHABLE", "onPress": {"actionType": "OPEN", "target": "picnic://store/page;id=promo-appels-123"}}]}},
         "analytics": {"contexts": [{"data": {"campaign_id": "abc", "type": "promo"}, "schema": "iglu:x/promo"}]}},
        {"type": "PML", "id": "tile", "pml": {"children": [
            {"type": "SELLING_UNIT_TILE", "sellingUnit": {"id": "s1", "name": "Geheime melk", "display_price": 129}},
            {"type": "RICH_TEXT", "markdown": "Geheime melk 1 l"}, {"type": "RICH_TEXT", "markdown": "-25%"}, {"type": "RICH_TEXT", "markdown": "€ 1,29"}]}},
    ]}}

    def test_text_patterns_show_discount_like_texts_without_product_names(self):
        from mealieshopper import picnic_explore

        report = picnic_explore.tile_text_patterns(self.PAGE)
        self.assertEqual(report["tegels met precies een tegel in het onderdeel"], 1)
        self.assertEqual(report["aantal teksten per tegel"], {3: 1})
        self.assertEqual(set(report["kortingachtige teksten (cijfers als #)"]), {"-##%", "€ #,##"})
        self.assertNotIn("Geheime", repr(report))

    def test_promo_cards_show_where_a_card_leads_without_its_content(self):
        from mealieshopper import picnic_explore

        report = picnic_explore.promo_cards(self.PAGE)
        card = report["kaart 1"]
        self.assertEqual(report["kaarten gevonden (eerste 2)"], 1)
        self.assertIn("picnic://store/page;id=promo-appels-<id>", card["teksten die naar een pagina of campagne wijzen"])
        self.assertIn({"actionType": "str: OPEN", "target": "str"}, card["acties en verwijzingen (vorm)"])
        self.assertNotIn("Geheime", repr(report))
        self.assertEqual(picnic_explore.promo_cards({"body": {}}), "geen kop met Acties")

    def test_the_default_report_skips_the_favorites_exploration(self):
        from mealieshopper import picnic_explore

        asked = []

        def authed(method, path, **kwargs):
            asked.append(path)
            return {"tabs": []} if path == "/bootstrap" else {}

        picnic.save_auth_key("key")
        with mock.patch.object(picnic, "_authed", side_effect=authed):
            picnic_explore.explore()
            short = len(asked)
            asked.clear()
            picnic_explore.explore(extended=True)
        self.assertLess(short, 40, "zonder extended geen ruim honderd verzoeken")
        self.assertGreater(len(asked), short)
        self.assertFalse(any("liked_sellables" in path for path in asked[:short]))

    def test_the_route_passes_extended_on(self):
        from mealieshopper import picnic_explore

        client = create_app().test_client()
        with mock.patch.object(picnic_explore, "explore", return_value={"probes": [], "ok": 0, "linked": True}) as explore:
            client.post("/api/picnic/explore")
            client.post("/api/picnic/explore?extended=1")
        self.assertEqual([call.kwargs for call in explore.call_args_list], [{"extended": False}, {"extended": True}])


class SyncAllTest(TempDataDirTest):
    def fetchers(self, **overrides):
        base = {
            "ah": lambda: [{"id": "1", "title": "Kip", "price": 3.0, "priceBefore": 4.0}],
            "picnic": lambda: [{"id": "p1", "title": "Melk"}],
            "jumbo": lambda: [{"id": "j1", "title": "Kaas"}, {"id": "j2", "title": "Brood"}],
        }
        base.update(overrides)
        return mock.patch.dict(offers.FETCHERS, base)

    def test_unlinked_stores_are_skipped_and_linked_ones_synced(self):
        with self.fetchers(), mock.patch.object(offers, "linked", side_effect=lambda store: store != "picnic"):
            result = offers.sync_all()
        self.assertEqual(result["stores"]["ah"]["count"], 1)
        self.assertEqual(result["stores"]["jumbo"]["count"], 2)
        self.assertIn("skipped", result["stores"]["picnic"])
        self.assertEqual(offers.load("picnic")["total"], 0)

    def test_one_failing_store_does_not_block_the_others_and_keeps_its_copy(self):
        with self.fetchers(), mock.patch.object(offers, "linked", return_value=True):
            offers.sync_all()

        def boom():
            raise RuntimeError("Jumbo geweigerd")

        with self.fetchers(jumbo=boom), mock.patch.object(offers, "linked", return_value=True):
            result = offers.sync_all()
        self.assertEqual(result["stores"]["jumbo"], {"error": "Jumbo geweigerd"})
        self.assertEqual(result["stores"]["picnic"]["count"], 1)
        self.assertEqual(offers.load("jumbo")["total"], 2, "de vorige kopie blijft staan")

    def test_linked_follows_the_store_connections(self):
        self.assertTrue(offers.linked("ah"))
        self.assertFalse(offers.linked("picnic"))
        self.assertFalse(offers.linked("jumbo"))
        picnic.save_auth_key("key")
        auth.set_secret(jumbo.REFRESH_SECRET, "ref")
        self.assertTrue(offers.linked("picnic") and offers.linked("jumbo"))

    def test_overview_lists_every_store(self):
        with self.fetchers(), mock.patch.object(offers, "linked", return_value=True):
            offers.sync_all()
        overview = offers.overview()
        self.assertEqual(sorted(overview), ["ah", "jumbo", "picnic"])
        self.assertEqual((overview["jumbo"]["total"], bool(overview["jumbo"]["syncedAt"])), (2, True))


class ScheduleTest(TempDataDirTest):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"MEALIESHOPPER_OFFERS_SYNC_HOUR": "5", "MEALIESHOPPER_OFFERS_SCHEDULE": "true"})
        patcher.start()
        self.addCleanup(patcher.stop)
        fake = mock.patch.object(offers, "sync_all", return_value={"stores": {"ah": {"count": 1}}, "finishedAt": "x"})
        self.sync_all = fake.start()
        self.addCleanup(fake.stop)

    @staticmethod
    def at(day, hour):
        return datetime(2026, 10, day, hour, 0, tzinfo=timezone.utc).astimezone()

    def test_the_slot_is_the_configured_hour_of_today_or_yesterday(self):
        now = datetime(2026, 10, 22, 14, 30).astimezone()
        self.assertEqual(offers_schedule.latest_slot(now), now.replace(hour=5, minute=0, second=0, microsecond=0))
        early = datetime(2026, 10, 22, 3, 0).astimezone()
        self.assertEqual(offers_schedule.latest_slot(early), datetime(2026, 10, 21, 5, 0).astimezone())
        self.assertEqual(offers_schedule.next_slot(now), datetime(2026, 10, 23, 5, 0).astimezone())

    def test_it_runs_once_per_day(self):
        morning = datetime(2026, 10, 22, 6, 0).astimezone()
        self.assertTrue(offers_schedule.run_if_due(morning))
        self.assertFalse(offers_schedule.run_if_due(morning + timedelta(hours=3)))
        self.assertTrue(offers_schedule.run_if_due(morning + timedelta(days=1)))
        self.assertEqual(self.sync_all.call_count, 2)

    def test_it_catches_up_after_the_app_was_off_but_not_before_the_hour_of_the_first_day(self):
        self.assertTrue(offers_schedule.run_if_due(datetime(2026, 10, 22, 3, 0).astimezone()), "de run van gisteren is nooit gedaan")
        self.assertFalse(offers_schedule.run_if_due(datetime(2026, 10, 22, 4, 0).astimezone()))
        self.assertTrue(offers_schedule.run_if_due(datetime(2026, 10, 22, 5, 1).astimezone()))

    def test_only_the_first_claim_wins(self):
        now = datetime(2026, 10, 22, 6, 0).astimezone()
        self.assertTrue(offers_schedule._claim(now))
        self.assertFalse(offers_schedule._claim(now))

    def test_it_can_be_switched_off(self):
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_OFFERS_SCHEDULE": "false"}):
            self.assertFalse(offers_schedule.run_if_due(datetime(2026, 10, 22, 6, 0).astimezone()))
            self.assertFalse(offers_schedule.status()["enabled"])
            self.assertIsNone(offers_schedule.status()["nextRun"])
        self.sync_all.assert_not_called()

    def test_a_bad_hour_falls_back_to_the_default(self):
        for value in ("25", "-1", "abc", ""):
            with mock.patch.dict(os.environ, {"MEALIESHOPPER_OFFERS_SYNC_HOUR": value}):
                self.assertEqual(offers_schedule.sync_hour(), 5)

    def test_status_remembers_the_last_run(self):
        self.assertIsNone(offers_schedule.status()["lastRun"])
        with mock.patch.object(offers_schedule, "_local_now", return_value=datetime(2026, 10, 24, 12, 0).astimezone()):
            offers_schedule.sync_now()
        status = offers_schedule.status()
        self.assertEqual((status["lastTrigger"], status["lastResult"]["stores"]["ah"]["count"], status["hour"]), ("manual", 1, 5))
        self.assertTrue(status["enabled"] and status["nextRun"])
        offers_schedule.run_if_due(datetime(2026, 10, 25, 6, 0).astimezone())
        self.assertEqual(offers_schedule.status()["lastTrigger"], "scheduled")

    def test_a_failing_run_does_not_stop_the_loop_condition(self):
        self.sync_all.side_effect = RuntimeError("stuk")
        with self.assertRaises(RuntimeError):
            offers_schedule.run_if_due(datetime(2026, 10, 22, 6, 0).astimezone())


class RoutesTest(TempDataDirTest):
    def setUp(self):
        super().setUp()
        self.client = create_app().test_client()

    def test_status_and_sync_all(self):
        status = self.client.get("/api/offers/status").get_json()
        self.assertEqual(sorted(status["stores"]), ["ah", "jumbo", "picnic"])
        with mock.patch.object(offers, "sync_all", return_value={"stores": {"ah": {"count": 2}}, "finishedAt": "x"}):
            response = self.client.post("/api/offers/sync-all")
        self.assertEqual((response.status_code, response.get_json()["stores"]["ah"]["count"]), (200, 2))
        self.assertEqual(self.client.get("/api/offers/status").get_json()["lastTrigger"], "manual")

    def test_sync_all_is_for_the_administrator_only(self):
        from mealieshopper import auth as auth_module

        self.assertTrue(auth_module.admin_only("/api/offers/sync-all"))
        self.assertFalse(auth_module.admin_only("/api/offers/status"))
        self.assertFalse(auth_module.admin_only("/api/offers/sync"))

    def test_the_connect_page_has_the_sync_button(self):
        html = self.client.get("/").get_data(as_text=True)
        link = html[html.index('id="settings-link"'):html.index('id="settings-users"')]
        self.assertIn('id="offers-sync-all"', link)
        self.assertIn('id="offers-sync-stores"', link)


if __name__ == "__main__":
    unittest.main()
