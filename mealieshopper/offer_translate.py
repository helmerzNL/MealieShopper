"""Engelse ingrediënten (uit Mealie-imports) naar het Nederlands, zodat ze bij de aanbiedingen te vinden zijn.

De aanbiedingen van AH, Picnic en Jumbo zijn Nederlands, een recept in Mealie is vaak Engels ("2 tbsp mayo", "chicken thighs").
Daarom vertalen we de regel van een ingrediënt vóór het vergelijken, op woorden en uit een vaste lijst; er gaat niets naar een
vertaaldienst. Een Nederlandse regel blijft onaangeroerd: alleen woorden die in de lijst staan worden vervangen.

De supermarkten noemen hetzelfde product niet gelijk (kippendijen, kipdijfilet), dus een woord kan meerdere Nederlandse namen hebben.
`variants` geeft dan meerdere regels terug; wie vergelijkt probeert ze allemaal en houdt de beste. Het eerste is de voorkeur.

Een woord komt alleen in de lijst als het in het Nederlands niet iets anders betekent. "pasta", "ham", "avocado" en "broccoli" zijn in
beide talen gelijk en hoeven er dus niet in.
"""
from __future__ import annotations

import itertools
import re
from typing import Iterable

MAX_VARIANTS = 6  # genoeg voor een paar synoniemen, niet zoveel dat het vergelijken traag wordt
MAX_PHRASE = 4  # het langste frase in de lijst, in woorden

# Engels (kleine letters, enkelvoud) -> Nederlands. Een tuple geeft meerdere namen, de voorkeur eerst.
LEXICON: dict[str, str | tuple[str, ...]] = {
    # groente
    "tomato": "tomaat", "cherry tomato": ("cherrytomaat", "tomaat"), "sun dried tomato": "zongedroogde tomaat",
    "potato": "aardappel", "sweet potato": "zoete aardappel", "onion": "ui", "red onion": "rode ui",
    "spring onion": ("bosui", "lente-ui"), "green onion": ("bosui", "lente-ui"), "scallion": ("bosui", "lente-ui"),
    "shallot": "sjalot", "garlic": "knoflook", "carrot": ("wortel", "peen"), "cucumber": "komkommer",
    "bell pepper": "paprika", "sweet pepper": "paprika", "capsicum": "paprika", "red pepper": "paprika",
    "chili": ("rode peper", "chilipeper"), "chilli": ("rode peper", "chilipeper"), "chili pepper": ("rode peper", "chilipeper"),
    "chilli pepper": ("rode peper", "chilipeper"), "jalapeno": "jalapeno",
    "lettuce": "sla", "iceberg lettuce": "ijsbergsla", "spinach": "spinazie", "cauliflower": "bloemkool", "cabbage": "kool",
    "red cabbage": "rode kool", "white cabbage": "witte kool", "kale": "boerenkool", "zucchini": "courgette",
    "eggplant": "aubergine", "mushroom": "champignon", "leek": "prei", "celery": "selderij", "bean sprout": "taugé",
    "pea": "erwten", "green bean": "sperziebonen", "corn": "mais", "sweetcorn": "mais", "pumpkin": "pompoen",
    "beetroot": "bieten", "radish": "radijs", "asparagus": "asperges", "brussels sprout": "spruitjes", "fennel": "venkel",
    # fruit
    "lemon": "citroen", "lime": ("limoen", "lime"), "orange": "sinaasappel", "apple": "appel", "pear": "peer", "banana": "banaan",
    "grape": "druiven", "strawberry": "aardbei", "blueberry": "bosbes", "raspberry": "framboos", "pineapple": "ananas",
    "melon": "meloen", "watermelon": "watermeloen", "peach": "perzik", "cherry": "kers", "plum": "pruim", "coconut": "kokos",
    # kruiden
    "parsley": "peterselie", "basil": "basilicum", "cilantro": "koriander", "coriander": "koriander", "dill": "dille",
    "mint": "munt", "thyme": "tijm", "rosemary": "rozemarijn", "chive": "bieslook", "sage": "salie", "ginger": "gember",
    "bay leaf": "laurier", "herb": "kruiden", "spice": "specerijen",
    # vlees
    "chicken": "kip", "chicken breast": ("kipfilet", "kippenborst"), "chicken thigh": ("kippendijen", "kipdijfilet", "kippendij"),
    "chicken drumstick": ("kippenpoten", "kipdrumsticks"), "chicken fillet": "kipfilet", "chicken wing": "kippenvleugels",
    "beef": "rundvlees", "ground beef": ("rundergehakt", "gehakt"), "minced beef": ("rundergehakt", "gehakt"),
    "minced meat": "gehakt", "mince": "gehakt", "ground meat": "gehakt", "ground pork": ("varkensgehakt", "gehakt"),
    "pork": "varkensvlees", "pork chop": "varkenskotelet", "pork tenderloin": "varkenshaas", "bacon": ("spek", "bacon"),
    "sausage": "worst", "smoked sausage": "rookworst", "lamb": "lamsvlees", "turkey": "kalkoen", "steak": "biefstuk",
    "meatball": "gehaktballen",
    # vis
    "salmon": "zalm", "smoked salmon": "gerookte zalm", "tuna": "tonijn", "shrimp": "garnalen", "prawn": "garnalen",
    "cod": "kabeljauw", "fish": "vis", "mussel": "mosselen", "fish fillet": "visfilet",
    # zuivel en eieren
    "milk": "melk", "cream": ("room", "slagroom"), "heavy cream": "slagroom", "whipping cream": "slagroom", "double cream": "slagroom",
    "sour cream": "zure room", "butter": "boter", "cheese": "kaas", "cream cheese": "roomkaas", "old cheese": "oude kaas",
    "mature cheese": "oude kaas", "old mature cheese": "oude kaas", "grated cheese": "geraspte kaas", "parmesan": "parmezaanse kaas",
    "goat cheese": "geitenkaas", "yoghurt": "yoghurt", "yogurt": "yoghurt", "egg": ("ei", "eieren"), "egg yolk": "eigeel",
    "egg white": "eiwit", "creme fraiche": "creme fraiche",
    # granen, pasta en brood
    "rice": "rijst", "noodle": "noedels", "bread": "brood", "bread roll": "broodje", "roll": "broodje", "bun": "broodje",
    "burger bun": "hamburgerbroodje", "flour": "bloem", "oat": "havermout", "cornstarch": "maizena", "corn starch": "maizena",
    "cornflour": "maizena", "breadcrumb": "paneermeel", "pita": "pitabroodje", "baguette": "stokbrood",
    # voorraad en sauzen
    "oil": "olie", "olive oil": "olijfolie", "vegetable oil": "zonnebloemolie", "vinegar": "azijn", "sugar": "suiker",
    "brown sugar": "bruine suiker", "salt": "zout", "black pepper": "peper", "honey": "honing", "soy sauce": "sojasaus",
    "fish sauce": "vissaus", "mayo": "mayonaise", "mayonnaise": "mayonaise", "mustard": "mosterd", "curry sauce": "currysaus",
    "tomato paste": "tomatenpuree", "tomato puree": "tomatenpuree", "tomato sauce": "tomatensaus", "coconut milk": "kokosmelk",
    "peanut butter": "pindakaas", "stock": "bouillon", "broth": "bouillon", "chicken stock": "kippenbouillon",
    "vegetable stock": "groentebouillon", "bean": "bonen", "kidney bean": "kidneybonen", "chickpea": "kikkererwten",
    "lentil": "linzen", "sauce": "saus", "soup": "soep", "dressing": "dressing", "baking powder": "bakpoeder",
    # noten, zaden en snoep
    "nut": "noten", "peanut": "pinda's", "almond": "amandelen", "walnut": "walnoten", "cashew": "cashewnoten",
    "sesame seed": "sesamzaad", "raisin": "rozijnen", "olive": "olijven", "chocolate": "chocolade",
    # drank, bouillonblokjes en sauzen met meer woorden
    "wine": "wijn", "beer": "bier", "stock cube": "bouillonblokje", "bouillon cube": "bouillonblokje", "bread crumb": "paneermeel",
    "chilli sauce": "chilisaus", "chili sauce": "chilisaus", "sweet chilli sauce": ("sweet chilli saus", "chilisaus"),
    "sweet chili sauce": ("sweet chilli saus", "chilisaus"), "parmesan cheese": "parmezaanse kaas",
}

# Aanvulling: minder gangbare groente, vlees, zuivel en bakproducten, en producten met meer woorden.
LEXICON.update({
    # vlees en vis
    "duck": "eend", "duck breast": "eendenborst", "turkey breast": "kalkoenfilet", "ground turkey": ("kalkoengehakt", "gehakt"),
    "pork belly": "buikspek", "lamb chop": "lamskoteletten", "anchovy": ("ansjovis", "ansjovisfilets"), "crab": "krab",
    "squid": "inktvis", "crab meat": "krab", "meat": "vlees", "mackerel": "makreel", "white fish": "witvis", "salmon fillet": "zalmfilet", "cod fillet": "kabeljauwfilet",
    # groente en fruit
    "arugula": "rucola", "rocket": "rucola", "watercress": "waterkers", "butternut squash": ("butternut", "pompoen"), "turnip": "knolraap",
    "parsnip": "pastinaak", "celeriac": "knolselderij", "artichoke": "artisjok", "caper": "kappertjes", "pickle": "augurken",
    "gherkin": "augurken", "sauerkraut": "zuurkool", "sweet corn": "mais", "green pea": "erwten", "snow pea": "peultjes",
    "sugar snap pea": "sugarsnaps", "canned tomato": ("tomatenblokjes", "gepelde tomaten"), "chopped tomato": ("tomatenblokjes", "gepelde tomaten"),
    "cranberry": "cranberry's", "blackberry": "bramen", "pomegranate": "granaatappel", "fig": "vijgen", "date": "dadels",
    "baby spinach": "spinazie", "broccoli floret": "broccoliroosjes", "new potato": "krieltjes", "mashed potato": "aardappelpuree",
    "french fry": "frites", "lemon juice": "citroensap", "lime juice": "limoensap", "orange juice": "sinaasappelsap",
    "apple juice": "appelsap", "tomato juice": "tomatensap",
    # zuivel
    "buttermilk": "karnemelk", "whole milk": "volle melk", "skimmed milk": "magere melk", "greek yogurt": "griekse yoghurt",
    "greek yoghurt": "griekse yoghurt", "almond milk": "amandelmelk", "oat milk": "havermelk", "soy milk": "sojamelk",
    "coconut cream": "kokosroom", "feta cheese": "feta",
    # granen, pasta en brood
    "rice noodle": "rijstnoedels", "egg noodle": "eiernoedels", "brown rice": "zilvervliesrijst", "basmati rice": "basmatirijst",
    "lasagne sheet": "lasagnebladen", "plain flour": "bloem", "self raising flour": "zelfrijzend bakmeel", "wholemeal bread": "volkorenbrood",
    "sourdough bread": "zuurdesembrood", "pita bread": "pitabroodjes", "tortilla wrap": "wraps",
    # voorraad, bakken en sauzen
    "extra virgin olive oil": "olijfolie", "sunflower oil": "zonnebloemolie", "coconut oil": "kokosolie", "sesame oil": "sesamolie",
    "peanut oil": "arachideolie", "white wine vinegar": "witte wijnazijn", "balsamic vinegar": "balsamicoazijn", "rice vinegar": "rijstazijn",
    "apple cider vinegar": "appelciderazijn", "caster sugar": "suiker", "icing sugar": "poedersuiker", "maple syrup": "ahornsiroop",
    "golden syrup": "stroop", "vanilla extract": "vanille-extract", "vanilla": "vanille", "baking soda": "zuiveringszout", "yeast": "gist",
    "cocoa powder": "cacaopoeder", "cocoa": "cacao", "dijon mustard": "dijonmosterd", "worcestershire sauce": "worcestersaus",
    "hot sauce": "hete saus", "teriyaki sauce": "teriyakisaus", "oyster sauce": "oestersaus", "hoisin sauce": "hoisinsaus",
    "black bean": "zwarte bonen", "white bean": "witte bonen", "cannellini bean": "witte bonen", "red lentil": "rode linzen",
    "green lentil": "groene linzen", "tomato ketchup": "tomatenketchup",
    # noten en zaden
    "hazelnut": "hazelnoten", "pecan": "pecannoten", "pistachio": "pistachenoten", "pine nut": "pijnboompitten",
    "sunflower seed": "zonnebloempitten",
    # kruiden en specerijen (in een theelepel tellen ze niet mee, in een eetlepel ook niet: zie offers.DRIED_SPICES)
    "cumin": "komijn", "cinnamon": "kaneel", "nutmeg": "nootmuskaat", "turmeric": "kurkuma", "cardamom": "kardemom",
    "saffron": "saffraan", "curry powder": "kerriepoeder", "paprika powder": "paprikapoeder", "cayenne": "cayennepeper",
})

# Woorden die in een Engelse regel een kleur of eigenschap geven en in het Nederlands een eigen vorm hebben.
ADJECTIVES: dict[str, str] = {
    "red": "rode", "green": "groene", "yellow": "gele", "white": "witte", "black": "zwarte", "brown": "bruine",
    "fresh": "verse", "dried": "gedroogde", "smoked": "gerookte", "old": "oude", "young": "jonge", "sweet": "zoete",
}

_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


def _lookup(words: list[str]) -> str | tuple[str, ...] | None:
    """De vertaling van een rij woorden (kleine letters), ook als het laatste woord een meervoud is."""
    phrase = " ".join(words)
    if phrase in LEXICON:
        return LEXICON[phrase]
    last = words[-1]
    candidates: list[str] = []
    if last.endswith("ies") and len(last) > 4:
        candidates.append(last[:-3] + "y")
    if last.endswith("es") and len(last) > 3:
        candidates.append(last[:-2])
    if last.endswith("s") and len(last) > 2:
        candidates.append(last[:-1])
    for singular in candidates:
        key = " ".join(words[:-1] + [singular])
        if key in LEXICON:
            return LEXICON[key]
    return None


def _as_tuple(value: str | tuple[str, ...]) -> tuple[str, ...]:
    return (value,) if isinstance(value, str) else value


def variants(text: str) -> list[str]:
    """De ingrediëntregel in het Nederlands, met een variant per verschillende naam. Zonder Engelse woorden: alleen de regel zelf.

    "800 gr chicken thighs" wordt ["800 gr kippendijen", "800 gr kipdijfilet", "800 gr kippendij"]. Aantallen, eenheden en
    wat tussen haakjes staat blijven staan; die worden bij het vergelijken al apart behandeld.
    """
    text = str(text or "")
    # Wat tussen haakjes staat ("(or pork/bacon)") wordt bij het vergelijken genegeerd, dus ook niet vertaald.
    quoted = [(match.start(), match.end()) for match in re.finditer(r"\([^)]*\)", text)]
    spans = [
        (match.start(), match.end(), match.group(0).lower())
        for match in _WORD.finditer(text)
        if not any(start <= match.start() < end for start, end in quoted)
    ]
    segments: list[tuple[int, int, tuple[str, ...]]] = []
    index = 0
    while index < len(spans):
        hit = None
        for length in range(min(MAX_PHRASE, len(spans) - index), 0, -1):
            chunk = spans[index:index + length]
            # Een frase bestaat uit woorden die alleen door spaties gescheiden zijn ("bell pepper", niet "bell, pepper").
            if any(text[chunk[i][1]:chunk[i + 1][0]].strip(" -") for i in range(length - 1)):
                continue
            found = _lookup([word for _, _, word in chunk])
            if found is not None:
                hit = (chunk[0][0], chunk[-1][1], _as_tuple(found), length)
                break
        if hit:
            segments.append(hit[:3])
            index += hit[3]
            continue
        word = spans[index][2]
        if word in ADJECTIVES:
            segments.append((spans[index][0], spans[index][1], (ADJECTIVES[word],)))
        index += 1
    if not segments:
        return [text]

    options: Iterable[tuple[str, ...]] = itertools.islice(itertools.product(*(names for _, _, names in segments)), MAX_VARIANTS)
    results: list[str] = []
    for choice in options:
        out, cursor = [], 0
        for (start, end, _), name in zip(segments, choice):
            out.append(text[cursor:start])
            out.append(name)
            cursor = end
        out.append(text[cursor:])
        line = "".join(out)
        if line not in results:
            results.append(line)
    return results
