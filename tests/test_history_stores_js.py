"""Execute history store navigation with the real router and tab handlers."""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parents[1]


def functions(filename: str, names: tuple[str, ...]) -> str:
    source: str = (ROOT / 'static' / filename).read_text()
    return '\n'.join(match.group(0) for name in names if (match := re.search(rf'^(?:async )?function {name}\(.*?^}}$', source, re.M | re.S)))


@unittest.skipUnless(shutil.which('node'), 'Node is not installed')
class HistoryStoreNavigationTest(unittest.TestCase):
    def run_script(self, scenario: str) -> dict:
        app: str = (ROOT / 'static/app.js').read_text()
        tab_handlers: str = app[app.index('document.querySelectorAll(".subtab[data-subtab],'):app.index('// Over: versie en commit')]
        router: str = (ROOT / 'static/router.js').read_text().split('// Gewone klik op een navigatielink:')[0]
        prelude: str = r'''
const OFFERS_STORES = ['ah', 'picnic', 'jumbo', 'crisp'];
const OFFER_HISTORY_LIMIT = 30;
const STORES = Object.fromEntries(OFFERS_STORES.map(store => [store, {name: store}]));
const state = {linkedStores: {picnic: true, jumbo: true, crisp: false}, storeStatusKnown: {}, auth: {preferredStore: 'ah'}, weekStartKnown: true};
let offersFetch = null;
const OFFERS_WEEK_START_WAIT = 10;
const offersState = {store: 'ah'};
const offerHistory = {query: '', selected: '', pinned: false, products: [], loadId: 0};
let activeSubtab = 'offers-items';
let activePanel = 'recipes';
const requests = [];
const elements = new Map();
const listeners = new Map();
const localStorage = {getItem: () => '', setItem: () => {}};
const window = {location: new URL('http://localhost/offers/history?store=crisp'), requestAnimationFrame: () => {}};
const history = {pushState: () => {}, replaceState: () => {}};
const escapeHtml = String;
const t = key => key;
const tn = key => key;
const MDI_LOADING = '';
const mdiIcon = () => '';
const offersCache = {};
let offersLoadId = 0;
const offersWeek = () => ({start: '2026-11-02'});
const renderOffersWeek = () => {};
const renderOffers = () => {};
const renderOffersLoading = () => {};
const endOffersLoading = () => {};
const showOfferRecipes = () => {};
const message = () => {};
const renderOfferHistory = () => {};
const offerHistoryKey = product => `${product.store}:${product.productId}`;
const priceImportShowButton = () => {};
const setSubtabMenu = () => {};
function element(selector) {
  if (!elements.has(selector)) elements.set(selector, {value: '', checked: false, hidden: false, innerHTML: '', dataset: {},
    classList: {add: () => {}, remove: () => {}}, addEventListener: () => {}});
  return elements.get(selector);
}
const $ = element;
const panel = {id: 'offers', querySelectorAll: () => [], querySelector: selector => ({classList: {add: () => {activeSubtab = selector.slice(1);}}})};
const tabs = ['offers-items', 'offers-suggestions', 'offers-history'].map(subtab => ({
  dataset: {subtab}, hidden: false, classList: {add: () => {}}, setAttribute(name, value) { this[name] = value; }, closest: selector => selector === '.panel' ? panel : null,
  addEventListener: (event, callback) => listeners.set(subtab, callback),
  click() { listeners.get(subtab)(); if (subtab === 'offers-history') loadOfferHistory(); }
}));
const document = {
  querySelectorAll: selector => selector.startsWith('.subtab[data-subtab]') || selector === '#offers .subtab[data-subtab]' ? tabs : [],
  querySelector: selector => {
    if (selector === '#offers.is-active') return activePanel === 'offers' ? element(selector) : null;
    if (selector === '#offers-history.is-active') return activeSubtab === 'offers-history' ? element(selector) : null;
    if (selector === '#offers-suggestions.is-active') return activeSubtab === 'offers-suggestions' ? element(selector) : null;
    if (selector.includes('.subtab.is-active')) return {dataset: {subtab: activeSubtab}};
    if (selector === '.panel.is-active') return {id: activePanel};
    const match = /\[data-subtab="([^"]+)"\]/.exec(selector);
    return match ? tabs.find(tab => tab.dataset.subtab === match[1]) : null;
  }
};
function showPanel(id) {activePanel = id; if (id === 'offers') showOffers();}
async function jsonFetch(path) {
  requests.push(path);
  if (path.startsWith('/api/prices/history')) {
    const store = new URL(path, 'http://localhost').searchParams.get('store');
    return {products: [{store, productId: 'first'}, {store, productId: 'second'}], total: 2};
  }
  return {};
}
const settle = () => new Promise(resolve => setImmediate(resolve));
'''
        script: str = prelude + functions('app.js', ('routerQuery', 'offersPath', 'weekStartSettled', 'preferredSupermarket', 'preferredSupermarketKnown', 'defaultStoreOf',
                                                     'linkedStoreKeys', 'offersStoreParam'))
        script += functions('offers.js', ('offersHistoryActive', 'offersTabActive', 'offersStores', 'offersFollowPreferred', 'renderOffersStore',
                                           'offersSwitchStore', 'offersRequest', 'offersWeekStartKnown', 'showOffers'))
        script += functions('offers_history.js', ('loadOfferHistory', 'offerHistoryStoreChanged')) + router + tab_handlers
        script += '\n(async () => {\n' + scenario + '\n})().catch(error => {console.error(error); process.exit(1);});'
        result: subprocess.CompletedProcess = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_history_menu_includes_unlinked_stores_and_keeps_history_links(self) -> None:
        result: dict = self.run_script("""
tabs[2].click(); await settle();
console.log(JSON.stringify({stores: offersStores(), menu: $('#offers-store-list').innerHTML}));
""")
        self.assertEqual(result['stores'], ['ah', 'picnic', 'jumbo', 'crisp'])
        self.assertIn('href="/offers/history?store=crisp"', result['menu'])

    def test_direct_unlinked_history_route_loads_crisp_and_keeps_requested_product(self) -> None:
        result: dict = self.run_script("""
state.linkedStores = {};
routerApply('http://localhost/offers/history?store=crisp&q=milk&product=second'); await settle();
console.log(JSON.stringify({store: offersState.store, pending: router.pending, selected: offerHistory.selected,
 path: routerCurrentPath(), requests}));
""")
        self.assertEqual(result['store'], 'crisp')
        self.assertEqual(result['pending'], '')
        self.assertEqual(result['selected'], 'crisp:second')
        self.assertIn('product=second', result['path'])
        self.assertTrue(any('/api/prices/history?store=crisp&' in path for path in result['requests']))
        self.assertFalse(any('/api/offers?store=crisp&' in path for path in result['requests']))

    def test_switching_store_in_history_keeps_unlinked_history_access(self) -> None:
        result: dict = self.run_script("""
tabs[2].click(); await settle(); requests.length = 0;
offersSwitchStore('crisp'); await settle();
console.log(JSON.stringify({store: offersState.store, requests}));
""")
        self.assertEqual(result['store'], 'crisp')
        self.assertTrue(any('/api/prices/history?store=crisp&' in path for path in result['requests']))
        self.assertFalse(any('/api/offers?' in path for path in result['requests']))

    def test_leaving_history_falls_back_before_loading_ordinary_offers(self) -> None:
        result: dict = self.run_script("""
activeSubtab = 'offers-history'; offersState.store = 'crisp';
tabs[0].click(); await settle();
console.log(JSON.stringify({store: offersState.store, stores: offersStores(), menu: $('#offers-store-list').innerHTML, requests}));
""")
        self.assertEqual(result['store'], 'ah')
        self.assertNotIn('crisp', result['stores'])
        self.assertNotIn('data-store="crisp"', result['menu'])
        self.assertTrue(any('/api/offers?store=ah&' in path for path in result['requests']))
        self.assertFalse(any('/api/offers?store=crisp&' in path for path in result['requests']))

    def test_recipe_tab_keeps_linked_store_filter_and_ordinary_fallback(self) -> None:
        result: dict = self.run_script("""
activeSubtab = 'offers-history'; offersState.store = 'crisp';
tabs[1].click(); await settle();
console.log(JSON.stringify({store: offersState.store, stores: offersStores(), requests}));
""")
        self.assertEqual(result['store'], 'ah')
        self.assertNotIn('crisp', result['stores'])
        self.assertFalse(any('/api/offers?store=crisp&' in path for path in result['requests']))

    def test_ordinary_route_still_retries_when_store_credentials_arrive(self) -> None:
        result: dict = self.run_script("""
window.location = new URL('http://localhost/offers?store=crisp');
routerApply(window.location.href); await settle();
const pending = router.pending;
state.linkedStores.crisp = true;
routerRetry(); await settle();
console.log(JSON.stringify({pending, store: offersState.store, remaining: router.pending, requests}));
""")
        self.assertEqual(result['pending'], 'http://localhost/offers?store=crisp')
        self.assertEqual(result['store'], 'crisp')
        self.assertEqual(result['remaining'], '')
        self.assertTrue(any('/api/offers?store=crisp&' in path for path in result['requests']))

    def test_offers_links_from_history_leave_out_an_unlinked_store(self) -> None:
        result: dict = self.run_script("""
tabs[2].click(); await settle();
offersSwitchStore('crisp'); await settle();
routerRefreshLinks();
const crisp = Object.fromEntries(tabs.map(tab => [tab.dataset.subtab, tab.href]));
offersSwitchStore('jumbo'); await settle();
routerRefreshLinks();
const jumbo = Object.fromEntries(tabs.map(tab => [tab.dataset.subtab, tab.href]));
console.log(JSON.stringify({crisp, jumbo}));
""")
        self.assertEqual(result['crisp'], {'offers-items': '/offers', 'offers-suggestions': '/offers/recipes',
                                           'offers-history': '/offers/history?store=crisp'})
        self.assertEqual(result['jumbo'], {'offers-items': '/offers?store=jumbo', 'offers-suggestions': '/offers/recipes?store=jumbo',
                                           'offers-history': '/offers/history?store=jumbo'})

    def test_offers_wait_for_the_week_start_and_ask_for_one_list(self) -> None:
        # Opent de pagina, dan vragen het scherm en het subtab allebei de lijst op, en de eerste dag van de week (uit Mealie) is nog
        # niet binnen. Eerst geen aanvraag, daarna een, voor de goede week.
        result: dict = self.run_script("""
state.weekStartKnown = false; activePanel = 'offers';
showOffers(); showOffers(); await settle();
const waiting = requests.filter(path => path.startsWith('/api/offers?')).length;
weekStartSettled(); showOffers(); await settle();
const loaded = requests.filter(path => path.startsWith('/api/offers?')).length;
console.log(JSON.stringify({waiting, loaded, known: state.weekStartKnown}));
""")
        self.assertEqual(result, {'waiting': 0, 'loaded': 1, 'known': True})

    def test_a_slow_week_start_does_not_hold_the_offers_back(self) -> None:
        result: dict = self.run_script("""
state.weekStartKnown = false; activePanel = 'offers';
showOffers(); await settle();
await new Promise(resolve => setTimeout(resolve, 50)); await settle();
console.log(JSON.stringify({known: state.weekStartKnown, requests: requests.filter(path => path.startsWith('/api/offers?'))}));
""")
        self.assertTrue(result['known'])
        self.assertEqual(len(result['requests']), 1)

    def test_the_product_in_the_address_is_pinned_from_the_first_request(self) -> None:
        # Een link van een aanbieding: /offers/history?q=<naam>&product=<id>. Elke aanvraag noemt het product (pin), ook de eerste
        # die het openen van het subtab doet; een adres zonder product laat het los.
        result: dict = self.run_script("""
routerApply('http://localhost/offers/history?q=milk&product=second'); await settle();
const linked = requests.filter(path => path.startsWith('/api/prices/history'));
requests.length = 0;
routerApply('http://localhost/offers/history?q=milk'); await settle();
console.log(JSON.stringify({linked, plain: requests.filter(path => path.startsWith('/api/prices/history')), pin: offerHistory.pin,
  selected: offerHistory.selected}));
""")
        self.assertTrue(result['linked'])
        self.assertTrue(all('pin=second' in path for path in result['linked']), result['linked'])
        self.assertTrue(result['plain'])
        self.assertFalse(any('pin=' in path for path in result['plain']))
        self.assertEqual(result['pin'], '')

