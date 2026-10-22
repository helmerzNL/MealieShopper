"""De categorieën waarin de aanbiedingen worden getoond (Groente & Fruit, Vlees, Frisdrank ...).

Geen enkele winkel geeft bij een aanbieding een categorie die voor alle drie dezelfde is, dus we delen zelf in op
de producttitel (en, als de winkel er een geeft, een categorie-hint). De regels staan op volgorde: de eerste die past
wint. Specifiekere eerst, zodat kippensoep bij Soepen valt en niet bij Vlees, en appelsap bij Frisdrank en niet bij Fruit.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

# De volgorde van deze lijst is de volgorde waarin categorieën met hetzelfde aantal aanbiedingen worden getoond.
CATEGORIES = (
    "groente_fruit", "vlees", "vis", "kaas_vleeswaren", "zuivel_eieren", "brood_bakkerij", "maaltijden",
    "sauzen_conserven", "ontbijt_beleg", "snacks_snoep", "frisdrank", "koffie_thee", "bier_wijn", "diepvries",
    "huishouden", "drogisterie", "baby", "huisdier", "overig",
)
OTHER = "overig"

# (categorie, patroon op de titel zonder accenten en in kleine letters). \b = woordgrens.
_RULES: tuple[tuple[str, str], ...] = (
    ("diepvries", r"diepvries|\bijs\b|ijsjes|roomijs|ijsklontjes|bevroren|magnum|ben (?:&|en) jerry"),
    ("baby", r"luier|baby|pampers|billendoekjes|flesvoeding|opvolgmelk"),
    ("huisdier", r"kattenvoer|hondenvoer|kattenbrokken|kattenbak|kattengrit|huisdier|whiskas|pedigree|vogelvoer|konijnenvoer|hondensnack|kattensnack"),
    ("drogisterie", r"shampoo|tandpasta|tandenborstel|douche|deodorant|handzeep|\bzeep\b|bodylotion|haarverf|scheer|maandverband|tampon|wattenschijf|zonnebrand|vitamine|paracetamol|gezichtscr|dagcr|nivea|conditioner|mondwater|haarlak|wegwerpscheer"),
    ("huishouden", r"wasmiddel|afwasmiddel|schoonmaak|toiletpapier|keukenpapier|keukenrol|vuilniszak|allesreiniger|wasverzachter|vaatwas|spons|aluminiumfolie|bakpapier|batterij|batterijen|gloeilamp|\bwc\b|ontkalker|luchtverfrisser|vershoudfolie|zakdoekjes|servetten|kaarsen"),
    ("sauzen_conserven", r"soep|bouillon|tomatenpuree|passata|ketchup|mayonaise|mosterd|saus\b|sauzen|pesto|sambal|ketjap|dressing|\bolie\b|olijfolie|azijn|kruiden|specerij|\bzout\b|\bsuiker\b|\bbloem\b|bakmix|conserven|blik\b|\bpeper\b|kerrie|bonen in|kikkererwten|linzen|\bmais\b|jus\b|bouillonblokjes|marinade|currypasta|kookroom"),
    ("frisdrank", r"cola\b|fanta|sprite|7up|pepsi|frisdrank|limonade|siroop|\bsap\b|\w+sap\b|energy|red bull|mineraalwater|bruisend|\bwater\b|ice ?tea|ijsthee|fuze|tonic|bitter lemon|ranja|cassis|smoothie|\bdrink\b|isostar|aquarius|dubbeldrank|lipton"),
    ("koffie_thee", r"koffie|espresso|cappuccino|nespresso|senseo|douwe egberts|\bthee\b|\btea\b|koffiecups|koffiepads|cacao"),
    ("bier_wijn", r"\bbier\b|pils\b|heineken|grolsch|amstel|bavaria|wijn|prosecco|champagne|\bcava\b|whisky|wodka|\bgin\b|\brum\b|likeur|jenever|cider|radler|sherry|\bport\b|rosé|rose wijn|bacardi"),
    ("ontbijt_beleg", r"pindakaas|hagelslag|\bjam\b|honing|chocopasta|nutella|vlokken|muesli|cornflakes|havermout|granola|cruesli|ontbijtgranen|ontbijtkoek|appelstroop|speculoospasta|smeerseltjes|vruchtenhagel|\bstroop\b|knäckebröd|knackebrod"),
    ("zuivel_eieren", r"chocolademelk|melk\b(?!chocolade)|yoghurt|yogurt|kwark|\bvla\b|\bboter\b|roomboter|margarine|halvarine|bakken en braden|slagroom|\broom\b|creme fraiche|\bei\b|eieren|eitjes|skyr|pudding|dessert|zuivel|karnemelk|fristi|optimel|campina|danone|activia|plantaardig"),
    ("snacks_snoep", r"chips|\blay'?s\b|doritos|pringles|cheetos|\bbifi\b|bugles|tostitos|noten|pinda|popcorn|koek|biscuit|chocolade|\breep\b|snoep|\bdrop\b|kauwgom|winegum|haribo|snickers|twix|bounty|kitkat|tony|m&m|candy|wafel|stroopwafel|speculaas|borrel|bitterbal|zoutjes|toefje|bonbon|mars\b|lolly|marshmallow|paprikachips|nacho|crackers|rijstwafel|mintjes|pepermunt"),
    ("brood_bakkerij", r"brood|boterham|wrap|tortilla|croissant|broodje|bagel|pistolet|baguette|ciabatta|cake\b|gebak|taart|muffin|donut|krentenbol|beschuit|toast|focaccia|bakkerij|afbak|pannenkoek|poffertjes|ontbijtkoek"),
    # Gevulde pasta (tortelloni ricotta spinazie) is een maaltijd en geen kaas; de regel moet vóór kaas staan, anders wint "ricotta".
    ("maaltijden", r"tortelloni|tortellini|ravioli|cannelloni|agnolotti|gnocchi|tagliatelle|fusilli|farfalle|rigatoni|linguine|fettuccine"),
    ("kaas_vleeswaren", r"\bkaas\b|kaas\b|ricotta|mascarpone|burrata|pecorino|gorgonzola|camembert|\bham\b|achterham|salami|filet americain|leverworst|\bpate\b|gouda|edammer|\bbrie\b|mozzarella|\bfeta\b|cheddar|parmezaan|roomkaas|smeerkaas|vleeswaren|rosbief|cervelaat|chorizo|rookvlees|gerookte kip|boerenkaas|emmentaler|geitenkaas|hummus|smeerworst|plakjes|blokjes kaas"),
    ("vis", r"\bvis\b|zalm|tonijn|garnaal|garnalen|kabeljauw|pangasius|haring|makreel|mossel|scampi|forel|sushi|gamba|visstick|ansjovis|kibbeling|tilapia|schol\b|\btong\b|zeevruchten|surimi|vissticks|kreeft|krab\b"),
    ("vlees", r"\bkip|gehakt|rund|varken|worst|spek|bacon|biefstuk|steak|schnitzel|burger|hamburger|ribs|\blam\b|lams|kalkoen|fricandeau|kipfilet|drumstick|saté|sate\b|shoarma|gyros|\bvlees|tartaar|\bfilet\b|karbonade|speklap|braadstuk|stoofvlees|haas\b|kalf|eend\b|gevogelte|spareribs|slavink|kogelbiefstuk|gebraden|braad"),
    ("maaltijden", r"pasta|spaghetti|macaroni|penne|lasagne|noodles|\bmie\b|rijst|couscous|quinoa|bulgur|nasi|bami|taco|curry|maaltijd|pizza|quiche|loempia|wereldkeuken|salade\b|sandwich|tosti|noedels|gnocchi|ravioli|tortellini|risotto|stamppot|snelkookrijst|hoofdgerecht"),
    ("groente_fruit", r"groente|fruit|appel|peer\b|peren|banaan|bananen|sinaasappel|mandarijn|citroen|limoen|druiven|aardbei|bosbes|framboos|frambozen|kiwi|mango|ananas|avocado|meloen|tomaat|tomaten|komkommer|paprika|\bui\b|\buien\b|knoflook|aardappel|wortel|\bsla\b|ijsbergsla|spinazie|broccoli|bloemkool|courgette|aubergine|champignon|\bprei\b|kool\b|boerenkool|spruit|\bbiet\b|bieten|pompoen|sperziebonen|doperwten|rucola|asperge|radijs|gember|peterselie|basilicum|koriander|bosui|snoeptomaat|nectarine|perzik|pruim|kers|kersen|framboos|watermeloen|zoete aardappel|witlof|andijvie|venkel|selderij|\bpeen\b|pastinaak|rettich|lente-ui|krieltjes|salade"),
)
_COMPILED = tuple((category, re.compile(pattern)) for category, pattern in _RULES)


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def categorize(title: Any, hint: Any = "") -> str:
    """De categorie van een aanbieding: eerst op de titel, daarna op de hint van de winkel, anders `overig`."""
    for text in (_fold(title), _fold(hint)):
        if not text.strip():
            continue
        for category, pattern in _COMPILED:
            if pattern.search(text):
                return category
    return OTHER


def summarize(categories: list[str]) -> list[dict[str, Any]]:
    """Hoeveel aanbiedingen per categorie, de grootste eerst en `overig` achteraan."""
    counts: dict[str, int] = {}
    for category in categories:
        counts[category] = counts.get(category, 0) + 1
    order = {category: index for index, category in enumerate(CATEGORIES)}
    ranked = sorted(counts.items(), key=lambda item: (item[0] == OTHER, -item[1], order.get(item[0], len(order))))
    return [{"id": category, "count": count} for category, count in ranked]
