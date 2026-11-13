"""Wat er in een recept zit en welk soort gerecht het is, voor de filters van Recepten zoeken (4.22.0, vervolg op issue #322).

Eén set filters voor alle bronnen: de menugang (de vaste lijst van offer_courses.py), vegetarisch of veganistisch, vlees, vis
of gevogelte, en de bereidingstijd. Elke bron levert wat hij kan:

- Albert Heijn filtert zelf (de groepen van zijn zoekfunctie, zie `ah_filters`); alleen een tijd boven 15 minuten filteren we zelf.
- Mealie: de tags en categorieën, en wat MealieShopper uit de ingrediënten afleidt (`classify`). Daarvan zet MealieShopper ook
  tags in Mealie (recipe_nutrition.py, `refresh_tags`).
- Jumbo geeft de menugang en de tijd, Picnic losse labels en de tijd, HelloFresh en Marley Spoon een label en de tijd, Crisp
  alleen de tijd. Een bron die een filter niet kent, doet bij dat filter niet mee (`supports`).

Het afleiden uit de ingrediënten is een woordenlijst, geen AI: een regel met vlees, vis of gevogelte maakt het recept niet
vegetarisch, een regel met zuivel, ei of honing niet veganistisch. Een regel die zelf zegt dat hij vegetarisch of plantaardig
is ("vegetarische kipstuckjes", "havermelk"), telt niet mee. Liever een recept te weinig vegetarisch dan een vleesrecept erbij.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from . import offer_courses
from .i18n import tr

COURSES = tuple(course for course in offer_courses.COURSES if course != offer_courses.OTHER)
DIETS = ("vegetarisch", "veganistisch")
PROTEINS = ("vlees", "vis", "gevogelte")
MAX_MINUTES = 24 * 60

# De tags die MealieShopper in Mealie zet; een bestaande tag met dezelfde naam (hoofdletters maken niet uit) wordt hergebruikt.
TAG_NAMES = {"vegetarisch": "Vegetarisch", "veganistisch": "Veganistisch", "vlees": "Vlees", "vis": "Vis", "gevogelte": "Gevogelte"}

# Welke filters een bron kan toepassen. Mealie en AH alles; de rest wat hun zoekresultaten meegeven.
SUPPORT: dict[str, frozenset[str]] = {
    "mealie": frozenset({"course", "diet", "protein", "time"}),
    "ah": frozenset({"course", "diet", "protein", "time"}),
    "jumbo": frozenset({"course", "time"}),
    "picnic": frozenset({"course", "diet", "time"}),
    "crisp": frozenset({"course", "time"}),
    "hellofresh": frozenset({"course", "diet", "time"}),
    "marleyspoon": frozenset({"course", "diet", "time"}),
}

# Picnic, Crisp en de maaltijdboxen geven geen menugang: hun recepten zijn maaltijden, dus hoofdgerecht, tenzij een label
# iets anders zegt.
MEAL_SOURCES = frozenset({"picnic", "crisp", "hellofresh", "marleyspoon"})


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _texts(value: Any) -> list[str]:
    """De teksten in wat een bron meegeeft: een tekst, een lijst, of objecten met een `name` (tags en categorieën van Mealie)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [str(value.get("name") or value.get("text") or "")]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [text for item in value for text in _texts(item)]
    return []


# --- afleiden uit de ingrediënten ------------------------------------------------------------------------------------

# Een regel die zelf plantaardig is: die telt niet als vlees, vis, zuivel of ei.
_PLANT = re.compile(
    r"\bvega|vegetari|\bvegan|plantaardig|vleesvervang|\bveggie|plant[- ]based|meatless|meat[- ]free|\btofu\b|\btempeh\b|\bseitan\b"
    r"|jackfruit|\bquorn\b|\bvaless\b|vivera|garden gourmet|beyond meat|the vegetarian butcher|\bfalafel"
)

# Wat zeker ook veganistisch is. "Vegetarische kipstuckjes" of Quorn kunnen ei of zuivel bevatten: zo'n regel maakt een recept
# vegetarisch, maar niet veganistisch.
_PLANT_VEGAN = re.compile(r"\bvegan|plantaardig|plant[- ]based|\btofu\b|\btempeh\b|\bseitan\b|jackfruit")

_POULTRY = re.compile(
    r"\bkip|kippen|\bkuiken|\bkalkoen|\beend\b|\beenden|eendenborst|parelhoen|\bpiepkuiken|\bpoulet\b|\bchicken|\bturkey|\bduck\b"
    r"|drumstick|\bkipfilet|\bkipdij|\bkippendij|shoarma ?kip"
)
_MEAT = re.compile(
    r"\bvlees(?!tomaat|tomaten|vervang)|\bgehakt|gehaktbal|\brund|\brunder|\bbiefstuk|\bentrecote|\bossenhaas|\bossenstaart|\bosso buco|\bsukade|\briblap"
    r"|\bstoofvlees|draadjesvlees|\brosbief|\bcarpaccio|\btartaar|filet americain|\bvarken|\bkarbonade|\bspeklap|\bprocureur"
    r"|\bhamlap|\bschouderkarbonade|\bspareribs?|\bspek\b|\bspekjes|\bspekreep|\bontbijtspek|\bbacon|\bpancetta|\bguanciale"
    r"|\blardo|\bham\b|\bhammetje|\bbeenham|\bachterham|\brauwe ham|\bprosciutto|\bserrano|\bjamon|\bparmaham|\bcoppa\b|\bchorizo"
    r"|\bsalami|\bpepperoni|\bworst|worstje|\bbraadworst|\brookworst|\bknakworst|\bmetworst|\bcervelaat|\bsaucijs|\bsalsiccia"
    r"|\bmerguez|\bfrikandel|\bhamburger|\bburgers?\b|\bshoarma|\bgyros|\bkebab|\bdoner|pulled pork|\blam\b|\blams(?!oor)|\bkalf"
    r"|\bkalfs|\bgeit\b|\bgeitenvlees|\bhert\b|\bhertenvlees|\breebout|\bkonijn|\bhaas\b|\blever\b|\bleverworst"
    r"|\bbeef\b|\bpork|\blamb\b|\bveal\b|\bmince|\bsausage|\bsteak|\bmeatball|\bprosciutto|\bbrisket|\bjerky|\bhot ?dog"
)
_FISH = re.compile(
    r"\bvis\b|visfilet|\bzalm|\btonijn|\bkabeljauw|\bkoolvis|\bpangasius|\btilapia|\bmakreel|\bharing|\bsardine|\bsardientje"
    r"|\bforel|\bheek\b|\bschol\b|\btong\b|\bzeebaars|\bdorade|\bzeewolf|\bwijting|\bschelvis|\bpaling|\bbokking|\bstokvis"
    r"|\bgarnaal|\bgarnalen|\bgamba|\bscampi|\bmossel|\binktvis|\bcalamar|\boctopus|\bkreeft|\bkrab\b|\bkrabben|\bsurimi"
    r"|\bsint[- ]jakobsschelp|\boester\b|\boesters\b|\bkokkel|\bvongole|\bfish\b|\bsalmon|\btuna\b|\bcod\b|\bshrimp|\bprawn"
    r"|\bmussel|\bcrab\b|\blobster|\bsquid|\bscallop|\bhaddock|\bmackerel|\btrout|\bsea ?bass"
)
# Niet vegetarisch, maar geen hoofdbestanddeel: een tag Vlees of Vis zou daar vreemd zijn.
_NOT_VEGETARIAN = re.compile(
    r"kippenbouillon|kippen ?fond|runderbouillon|runder ?fond|\bkalfsfond|visbouillon|vis ?fond|\bvissaus|\boestersaus|\bansjovis"
    r"|\bgelatine|\breuzel|\brundvet|\bfish sauce|\boyster sauce|\banchov|chicken stock|beef stock|\bdashi|\bbonito"
)
# Niet veganistisch: zuivel, ei en honing. Plantaardige varianten vallen eruit door `_PLANT` en `_PLANT_DAIRY`.
_ANIMAL = re.compile(
    r"\bmelk|\bkarnemelk|\broom\b|\bslagroom|\bkookroom|\bzure room|creme fraiche|\bcreme\b|\bboter(?!bonen|ham)|\broomboter|\bghee\b|\bkaas|\bkazen"
    r"|\bparmezaan|\bparmigiano|\bpecorino|\bgrana padano|\bmozzarella|\bburrata|\bfeta\b|\bricotta|\bmascarpone|\bbrie\b"
    r"|\bcamembert|\bgeitenkaas|\bhalloumi|\bcheddar|\bgouda|\bemmentaler|\bgruyere|\bcomte\b|\bmanchego|\bgorgonzola|\bstilton"
    r"|\broquefort|\bfontina|\btaleggio|\bpaneer|\bkwark|\byoghurt|\bskyr\b|\bhuttenkase|\bcottage cheese|\bei\b|\beieren|\beidooier"
    r"|\beigeel|\beiwit|\beierdooier|\beiernoedel|\bmayonaise|\bmayo\b|\baioli|\bhoning(?!meloen)|\bmilk\b|\bcream\b|\bbutter\b(?! beans)|\bcheese"
    r"|\bparmesan|\byogh?urt|\beggs?\b|\byolk|\bhoney\b|\bbuttermilk|\bmeringue|\bschuim(pje)?s?\b|\bbechamel"
)
# Plantaardige zuivel en wat alleen zo heet ("pindakaas", "kokosmelk").
_PLANT_DAIRY = re.compile(
    r"\bpindakaas|\bnotenkaas|\bkokosmelk|\bkokosroom|\bkokosyoghurt|\bhavermelk|\bhaverroom|\bsojamelk|\bsojaroom|\bsoja ?yoghurt"
    r"|\bamandelmelk|\brijstmelk|\bnotenmelk|\bcashewmelk|\bpindaboter|\bnotenboter|\bcacaoboter|\bshea|\bkokosboter|\bappelstroop"
    r"|\bcoconut milk|\bcoconut cream|\boat milk|\bsoy milk|\balmond milk|\bpeanut butter|\bnut butter|\bcocoa butter|\bkokos"
    r"|\bbloemkoolroom|\beiwitrijk"
)


def ingredient_lines(recipe: dict[str, Any]) -> list[str]:
    """De regels van de ingrediënten van een Mealie-recept als tekst: de weergave, de oorspronkelijke tekst, het ingrediënt en de notitie."""
    lines: list[str] = []
    for item in recipe.get("recipeIngredient") or [] if isinstance(recipe, dict) else []:
        if isinstance(item, str):
            parts: list[Any] = [item]
        elif isinstance(item, dict):
            food = item.get("food") if isinstance(item.get("food"), dict) else {}
            parts = [item.get("display"), item.get("originalText"), food.get("name"), item.get("note")]
        else:
            continue
        text = " ".join(str(part) for part in parts if part)
        if text.strip():
            lines.append(text)
    return lines


def classify(lines: Iterable[str]) -> dict[str, Any] | None:
    """Uit de regels van de ingrediënten: `{"diet": "veganistisch" | "vegetarisch" | "", "proteins": [...]}`, of None zonder regels.

    `proteins` zijn de hoofdbestanddelen (vlees, vis, gevogelte); een bouillon, vissaus of gelatine maakt een recept alleen niet
    vegetarisch."""
    folded = [_fold(line) for line in lines if str(line or "").strip()]
    if not folded:
        return None
    proteins: set[str] = set()
    vegetarian = True
    vegan = True
    for line in folded:
        plant = bool(_PLANT.search(line))
        if not plant:
            if _NOT_VEGETARIAN.search(line):  # eerst: een kippenbouillon maakt een recept geen gevogelterecept
                vegetarian = False
            elif _POULTRY.search(line):
                proteins.add("gevogelte")
            elif _FISH.search(line):
                proteins.add("vis")
            elif _MEAT.search(line):
                proteins.add("vlees")
        if plant and not _PLANT_VEGAN.search(line):
            vegan = False
        elif not plant and _ANIMAL.search(_PLANT_DAIRY.sub(" ", line)):
            vegan = False
    if proteins:
        vegetarian = False
    diet = "veganistisch" if vegetarian and vegan else "vegetarisch" if vegetarian else ""
    return {"diet": diet, "proteins": sorted(proteins)}


# --- uit labels: tags, categorieën en de labels van de winkels --------------------------------------------------------

_LABEL_VEGAN = re.compile(r"\bvegan|veganistisch|plantaardig|plant[- ]based")
_LABEL_VEGETARIAN = re.compile(r"vegetari|\bveggie|\bvega\b|zonder[- ]vlees|meat[- ]?free|meatless")
_LABEL_PROTEINS = (
    ("gevogelte", re.compile(r"gevogelte|\bkip\b|kippen|\bkalkoen|\beend\b|\bpoultry|\bchicken|\bturkey")),
    ("vis", re.compile(r"\bvis\b|\bfish|seafood|zeevruchten|schelpdier|\bzalm|\btonijn")),
    ("vlees", re.compile(r"\bvlees\b|\bmeat\b|\brund|\bvarken|\bbeef\b|\bpork\b|\blam\b|\blamb\b")),
)


def from_labels(*labels: Any) -> dict[str, Any]:
    """Wat tags, categorieën of labels zeggen: `{"diet": ..., "proteins": [...]}`. "Zonder vlees" telt als vegetarisch, niet als vlees."""
    texts = [_fold(text) for label in labels for text in _texts(label) if text]
    diet = ""
    if any(_LABEL_VEGAN.search(text) for text in texts):
        diet = "veganistisch"
    elif any(_LABEL_VEGETARIAN.search(text) for text in texts):
        diet = "vegetarisch"
    proteins = sorted({name for name, pattern in _LABEL_PROTEINS for text in texts if pattern.search(text) and not _LABEL_VEGETARIAN.search(text)})
    return {"diet": diet, "proteins": [] if diet else proteins}


def combined(labels: dict[str, Any], derived: dict[str, Any] | None) -> dict[str, Any]:
    """Wat er in het recept zit: een vegetarische of veganistische tag gaat voor (je kent je recept), anders de ingrediënten;
    vlees, vis en gevogelte uit allebei."""
    derived = derived or {"diet": "", "proteins": []}
    diet = labels["diet"] or derived["diet"]
    proteins = [] if diet else sorted(set(labels["proteins"]) | set(derived["proteins"]))
    return {"diet": diet, "proteins": proteins}


def wanted_tags(derived: dict[str, Any] | None, labels: dict[str, Any]) -> list[str]:
    """De tags die MealieShopper bij een recept zet: wat de ingrediënten zeggen en de tags nog niet zeggen. Een veganistisch
    recept krijgt ook Vegetarisch. Een recept met een vegetarische tag krijgt geen Vlees, Vis of Gevogelte."""
    if not derived:
        return []
    names: list[str] = []
    if derived["diet"] and not labels["diet"]:
        names += [TAG_NAMES["veganistisch"], TAG_NAMES["vegetarisch"]] if derived["diet"] == "veganistisch" else [TAG_NAMES["vegetarisch"]]
    if not labels["diet"]:
        names += [TAG_NAMES[name] for name in derived["proteins"] if name not in labels["proteins"]]
    return names


def matches(facts: dict[str, Any], diet: str, protein: str) -> bool:
    """Of een recept (`combined`) bij de keuze past: vegetarisch laat ook veganistische recepten door."""
    if diet == "veganistisch" and facts["diet"] != "veganistisch":
        return False
    if diet == "vegetarisch" and facts["diet"] not in DIETS:
        return False
    return not protein or protein in facts["proteins"]


# --- de filters van een zoekopdracht -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Wanted:
    """De filters voor alle bronnen: menugang, dieet, vlees/vis/gevogelte en de hoogste bereidingstijd (in minuten)."""

    course: str = ""
    diet: str = ""
    protein: str = ""
    max_minutes: int | None = None

    def kinds(self) -> set[str]:
        return {
            kind for kind, value in (("course", self.course), ("diet", self.diet), ("protein", self.protein), ("time", self.max_minutes)) if value
        }

    def active(self) -> bool:
        return bool(self.kinds())


def wanted_from(source: Any) -> Wanted:
    """De filters uit een verzoek: `course`, `diet`, `protein` en `maxMinutes`. Een onbekende waarde telt als niet gekozen."""
    raw_minutes = str(source.get("maxMinutes") or "").strip()
    try:
        minutes = int(float(raw_minutes)) if raw_minutes else None
    except ValueError:
        raise ValueError(tr("Geef de bereidingstijd op als een aantal minuten.", "Give the cooking time as a number of minutes.")) from None
    course, diet, protein = (str(source.get(key) or "").strip() for key in ("course", "diet", "protein"))
    return Wanted(
        course=course if course in COURSES else "",
        diet=diet if diet in DIETS else "",
        protein=protein if protein in PROTEINS else "",
        max_minutes=max(1, min(MAX_MINUTES, minutes)) if minutes is not None and minutes > 0 else None,
    )


def supports(source: str, wanted: Wanted) -> bool:
    """Of een bron alle gekozen filters kan toepassen; zo niet, dan doet hij niet mee."""
    return wanted.kinds() <= SUPPORT.get(source, frozenset())


def card_course(source: str, card: dict[str, Any]) -> str:
    """De menugang van een kaart van een winkel of maaltijdbox (offer_courses.COURSES)."""
    course = offer_courses.course_of(card.get("course"), card.get("courses"), card.get("tags"), card.get("label"), card.get("segment"))
    if course == offer_courses.OTHER and source in MEAL_SOURCES:
        return "hoofdgerecht"
    return course


def card_passes(source: str, card: dict[str, Any], wanted: Wanted) -> bool:
    """Of een kaart van een winkel of maaltijdbox door de filters komt. Een kaart zonder tijd valt weg bij een grens op de tijd."""
    if wanted.course and card_course(source, card) != wanted.course:
        return False
    if wanted.diet or wanted.protein:
        facts = from_labels(card.get("tags"), card.get("label"), card.get("segment"), card.get("courses"))
        if not matches(facts, wanted.diet, wanted.protein):
            return False
    if wanted.max_minutes:
        minutes = card.get("minutes") if card.get("minutes") is not None else card.get("totalTime")
        if not isinstance(minutes, (int, float)) or isinstance(minutes, bool) or minutes <= 0 or minutes > wanted.max_minutes:
            return False
    return True


def filter_cards(source: str, cards: list[dict[str, Any]], wanted: Wanted) -> list[dict[str, Any]]:
    return [card for card in cards if card_passes(source, card, wanted)] if wanted.active() else cards


# --- Albert Heijn ----------------------------------------------------------------------------------------------------

# Per menugang de groep en waarde van de zoekfunctie van AH. Waarden binnen een groep tellen bij AH als "en", dus één waarde.
_AH_COURSES = {
    "ontbijt": ("menugang", "ontbijt"),
    "lunch": ("menugang", "lunch"),
    "voorgerecht": ("menugang", "voorgerecht"),
    "soep": ("soort-gerecht", "soep"),
    "hoofdgerecht": ("menugang", "hoofdgerecht"),
    "bijgerecht": ("menugang", "bijgerecht"),
    "dessert": ("menugang", "nagerecht"),
    "snack": ("menugang", "tussendoortje"),
}
AH_QUICK_MINUTES = 15  # "snel" bij AH: een kooktijd van hooguit 15 minuten


def ah_filters(wanted: Wanted) -> list[dict[str, Any]]:
    """De filters voor de zoekfunctie van AH (`RecipeSearchQueryFilter`: een groep met waarden). Een veganistisch recept is bij AH
    ook vegetarisch. Een grens op de tijd van 15 minuten of minder is "snel"; een ruimere grens filtert `ah_scan_needed`."""
    found: list[dict[str, Any]] = []
    if wanted.course:
        group, value = _AH_COURSES[wanted.course]
        found.append({"group": group, "values": [value]})
    if wanted.diet:
        found.append({"group": "speciale-wensen", "values": [wanted.diet]})
    if wanted.protein:
        found.append({"group": "recepten-met", "values": [wanted.protein]})
    if wanted.max_minutes and wanted.max_minutes <= AH_QUICK_MINUTES:
        found.append({"group": "veel-gebruikt", "values": ["snel"]})
    return found


def ah_scan_needed(wanted: Wanted) -> bool:
    """Of MealieShopper de recepten van AH zelf op tijd moet filteren: bij een grens boven de 15 minuten van "snel"."""
    return bool(wanted.max_minutes and wanted.max_minutes > AH_QUICK_MINUTES)
