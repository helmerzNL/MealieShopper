#!/usr/bin/env python3
"""Exporteer de MealieShopper-huisstijl naar static/brand/ (SVG, PNG en ICO).

    python scripts/build_brand.py [--out MAP]

Vereist Pillow, alleen tijdens het exporteren; de Flask-app heeft geen extra
dependency nodig. De vorm van het M-tasje staat hieronder op een plek: de SVG's
en de PNG's komen uit dezelfde geometrie, zodat ze niet uit elkaar lopen.
Tekst (woordmerk, deelafbeelding) gebruikt Manrope uit static/fonts/.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "static" / "brand"
FONT_PATH = ROOT / "static" / "fonts" / "Manrope-Variable.ttf"

# Ontwerptokens (zie README, sectie Huisstijl)
GREEN = "#176B45"
GREEN_STRONG = "#105235"
MIST = "#EAF5EE"
LEMON = "#F2CF45"
BG = "#F5F7F6"
WHITE = "#FFFFFF"
INK = "#18251E"
MUTED = "#59665E"
BORDER = "#D6DFD9"

SS = 8  # supersampling bij het tekenen van PNG's

# ---------------------------------------------------------------------------
# Geometrie van het beeldmerk (viewBox 0 0 96 96, in het midden uitgelijnd)
# ---------------------------------------------------------------------------
HANDLE_D = "M34 32.5V26.5a14 14 0 0 1 28 0V32.5"
HANDLE_W = 5.5
BODY_D = "M18 32.5H78a6 6 0 0 1 6 6V72.5a14 14 0 0 1-14 14H26a14 14 0 0 1-14-14V38.5a6 6 0 0 1 6-6Z"
RIM_D = "M18 32.5H78a6 6 0 0 1 6 6V56.5H12V38.5a6 6 0 0 1 6-6Z"
M_D = "M30 51V38L48 47.5L66 38V51"
M_W = 5.0
SEPARATOR_D = "M12 56.5H84"
SEPARATOR_W = 2.5

# Het favicon vereenvoudigt de M voor kleine formaten (viewBox 0 0 32 32)
FAV_BG = (0, 0, 32, 32, 7)
FAV_HANDLE_D = "M12.5 12.5V10.5a3.5 3.5 0 0 1 7 0V12.5"
FAV_HANDLE_W = 2.2
FAV_BODY = (6, 12, 26, 27, 3.5)
FAV_M_D = "M10.5 23.5V17L16 21.2L21.5 17V23.5"
FAV_M_W = 2.8


# ---------------------------------------------------------------------------
# Punten voor Pillow (dezelfde vormen als de SVG-paden hierboven)
# ---------------------------------------------------------------------------
def _arc(cx: float, cy: float, r: float, a0: float, a1: float, steps: int = 24):
    return [
        (cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / steps)),
         cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / steps)))
        for i in range(steps + 1)
    ]


def rounded_rect(x0, y0, x1, y1, r_top, r_bottom=None):
    """Omtrek van een rechthoek met afgeronde hoeken (boven/onder apart)."""
    r_bottom = r_top if r_bottom is None else r_bottom
    pts = [(x0 + r_top, y0), (x1 - r_top, y0)]
    if r_top:
        pts += _arc(x1 - r_top, y0 + r_top, r_top, -90, 0)
    pts += [(x1, y0 + r_top), (x1, y1 - r_bottom)]
    if r_bottom:
        pts += _arc(x1 - r_bottom, y1 - r_bottom, r_bottom, 0, 90)
    pts += [(x1 - r_bottom, y1), (x0 + r_bottom, y1)]
    if r_bottom:
        pts += _arc(x0 + r_bottom, y1 - r_bottom, r_bottom, 90, 180)
    pts += [(x0, y1 - r_bottom), (x0, y0 + r_top)]
    if r_top:
        pts += _arc(x0 + r_top, y0 + r_top, r_top, 180, 270)
    return pts


BODY_PTS = rounded_rect(12, 32.5, 84, 86.5, 6, 14)
RIM_PTS = (
    [(18, 32.5), (78, 32.5)] + _arc(78, 38.5, 6, -90, 0)
    + [(84, 38.5), (84, 56.5), (12, 56.5), (12, 38.5)] + _arc(18, 38.5, 6, 180, 270)
)
HANDLE_PTS = [(34, 32.5), (34, 26.5)] + _arc(48, 26.5, 14, 180, 360) + [(62, 26.5), (62, 32.5)]
M_PTS = [(30, 51), (30, 38), (48, 47.5), (66, 38), (66, 51)]
SEPARATOR_PTS = [(12, 56.5), (84, 56.5)]
FAV_HANDLE_PTS = [(12.5, 12.5), (12.5, 10.5)] + _arc(16, 10.5, 3.5, 180, 360) + [(19.5, 10.5), (19.5, 12.5)]
FAV_M_PTS = [(10.5, 23.5), (10.5, 17), (16, 21.2), (21.5, 17), (21.5, 23.5)]


# ---------------------------------------------------------------------------
# Tekenen
# ---------------------------------------------------------------------------
class Canvas:
    """RGBA-doek dat op SS-voud wordt getekend en daarna verkleind."""

    def __init__(self, size: tuple[int, int], background=None):
        self.size = size
        self.img = Image.new("RGBA", (size[0] * SS, size[1] * SS), background or (0, 0, 0, 0))

    def layer(self) -> Image.Image:
        return Image.new("RGBA", self.img.size, (0, 0, 0, 0))

    def finish(self) -> Image.Image:
        return self.img.resize(self.size, Image.LANCZOS)


def _xy(points, ox, oy, k):
    return [(ox + x * k, oy + y * k) for x, y in points]


def stroke(draw: ImageDraw.ImageDraw, points, width_px: float, fill) -> None:
    """Dikke lijn met ronde uiteinden en hoeken: stempel cirkels langs het pad."""
    r = width_px / 2
    step = max(width_px / 6, 0.5)
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        length = math.hypot(x1 - x0, y1 - y0)
        n = max(1, int(length / step))
        for i in range(n + 1):
            t = i / n
            x, y = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
            draw.ellipse((x - r, y - r, x + r, y + r), fill=fill)


def paint_mark(cv: Canvas, ox: float, oy: float, k: float, style: str) -> None:
    """Teken het beeldmerk; (ox, oy) is de linkerbovenhoek, k de schaal per eenheid, in pixels."""
    ox, oy, k = ox * SS, oy * SS, k * SS
    layer = cv.layer()
    d = ImageDraw.Draw(layer)
    handle = _xy(HANDLE_PTS, ox, oy, k)
    body = _xy(BODY_PTS, ox, oy, k)

    if style in ("color", "icon"):
        handle_fill = GREEN if style == "color" else WHITE
        body_fill = GREEN if style == "color" else WHITE
        stroke(d, handle, HANDLE_W * k, handle_fill)
        d.polygon(body, fill=body_fill)
        d.polygon(_xy(RIM_PTS, ox, oy, k), fill=LEMON)
        stroke(d, _xy(M_PTS, ox, oy, k), M_W * k, GREEN)
    else:  # "white" en "mono": een kleur, M en rand als uitsparing
        fill = WHITE if style == "white" else INK
        stroke(d, handle, HANDLE_W * k, fill)
        d.polygon(body, fill=fill)
        cut = Image.new("L", layer.size, 0)
        cd = ImageDraw.Draw(cut)
        stroke(cd, _xy(M_PTS, ox, oy, k), M_W * k, 255)
        stroke(cd, _xy(SEPARATOR_PTS, ox, oy, k), SEPARATOR_W * k, 255)
        layer.paste((0, 0, 0, 0), mask=cut)
    cv.img.alpha_composite(layer)


def paint_favicon(cv: Canvas, size_px: int) -> None:
    k = size_px / 32 * SS
    layer = cv.layer()
    d = ImageDraw.Draw(layer)
    x0, y0, x1, y1, r = FAV_BG
    d.rounded_rectangle((x0 * k, y0 * k, x1 * k, y1 * k), radius=r * k, fill=GREEN)
    stroke(d, _xy(FAV_HANDLE_PTS, 0, 0, k), FAV_HANDLE_W * k, WHITE)
    bx0, by0, bx1, by1, br = FAV_BODY
    d.rounded_rectangle((bx0 * k, by0 * k, bx1 * k, by1 * k), radius=br * k, fill=WHITE)
    stroke(d, _xy(FAV_M_PTS, 0, 0, k), FAV_M_W * k, GREEN)
    cv.img.alpha_composite(layer)


def font(size: int, weight: int = 800) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(FONT_PATH), size)
    f.set_variation_by_axes([weight])
    return f


def render_mark(px: int, style: str) -> Image.Image:
    cv = Canvas((px, px))
    paint_mark(cv, 0, 0, px / 96, style)
    return cv.finish()


def render_icon(px: int, scale: float, rounded: bool = False) -> Image.Image:
    """Appicoon: groene achtergrond met het tasje; `scale` is het aandeel van de breedte."""
    cv = Canvas((px, px))
    layer = cv.layer()
    d = ImageDraw.Draw(layer)
    if rounded:
        d.rounded_rectangle((0, 0, px * SS - 1, px * SS - 1), radius=px * SS * 0.22, fill=GREEN)
    else:
        d.rectangle((0, 0, px * SS, px * SS), fill=GREEN)
    cv.img.alpha_composite(layer)
    k = px / 96 * scale
    off = px * (1 - scale) / 2
    paint_mark(cv, off, off, k, "icon")
    return cv.finish()


def render_favicon(px: int) -> Image.Image:
    cv = Canvas((px, px))
    paint_favicon(cv, px)
    return cv.finish()


# ---------------------------------------------------------------------------
# Woordmerk
# ---------------------------------------------------------------------------
WORDMARK_SIZE = 56
WORDMARK_TX, WORDMARK_TY = 6, 8.25  # beeldmerk: minstens een kwart van de tasbreedte (18) vrij
WORDMARK_TEXT_X = 114
WORDMARK_BASELINE = 77
WORDMARK_HEIGHT = 113


def wordmark_text_width() -> int:
    return math.ceil(font(WORDMARK_SIZE).getlength("MealieShopper"))


def wordmark_width() -> int:
    return WORDMARK_TEXT_X + wordmark_text_width() + 18


def render_wordmark(style: str, scale: float = 1.0, background=None) -> Image.Image:
    """Horizontaal logo als PNG (voor het overzicht); de SVG's zijn de bronbestanden."""
    w, h = wordmark_width(), WORDMARK_HEIGHT
    cv = Canvas((math.ceil(w * scale), math.ceil(h * scale)), background)
    paint_mark(cv, WORDMARK_TX * scale, WORDMARK_TY * scale, scale, style)
    img = cv.finish()
    d = ImageDraw.Draw(img)
    f = font(round(WORDMARK_SIZE * scale))
    x, base = WORDMARK_TEXT_X * scale, WORDMARK_BASELINE * scale
    first_color = {"color": INK, "white": WHITE, "mono": INK}[style]
    second_color = {"color": GREEN, "white": WHITE, "mono": INK}[style]
    d.text((x, base), "Mealie", font=f, fill=first_color, anchor="ls")
    d.text((x + f.getlength("Mealie"), base), "Shopper", font=f, fill=second_color, anchor="ls")
    return img


# ---------------------------------------------------------------------------
# SVG-bronnen
# ---------------------------------------------------------------------------
def _svg(view_box: str, body: str, title: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view_box}" role="img">\n'
        f"  <title>{title}</title>\n{body}</svg>\n"
    )


def mark_group(style: str, mask_id: str = "cut") -> str:
    """SVG-groep van het beeldmerk (96 x 96) voor de gegeven stijl."""
    handle = f'<path d="{HANDLE_D}" fill="none" stroke="%s" stroke-width="{HANDLE_W}" stroke-linecap="round" stroke-linejoin="round"/>'
    m_path = f'<path d="{M_D}" fill="none" stroke="%s" stroke-width="{M_W}" stroke-linecap="round" stroke-linejoin="round"/>'
    if style in ("color", "icon"):
        main = GREEN if style == "color" else WHITE
        return (
            "  " + handle % main + "\n"
            f'  <path d="{BODY_D}" fill="{main}"/>\n'
            f'  <path d="{RIM_D}" fill="{LEMON}"/>\n'
            "  " + m_path % GREEN + "\n"
        )
    fill = WHITE if style == "white" else INK
    return (
        "  <defs>\n"
        f'    <mask id="{mask_id}">\n'
        '      <rect width="96" height="96" fill="#fff"/>\n'
        "      " + m_path % "#000" + "\n"
        f'      <path d="{SEPARATOR_D}" stroke="#000" stroke-width="{SEPARATOR_W}"/>\n'
        "    </mask>\n"
        "  </defs>\n"
        "  " + handle % fill + "\n"
        f'  <path d="{BODY_D}" fill="{fill}" mask="url(#{mask_id})"/>\n'
    )


def svg_mark(style: str) -> str:
    titles = {"color": "MealieShopper beeldmerk", "white": "MealieShopper beeldmerk, wit", "mono": "MealieShopper beeldmerk, eenkleurig"}
    return _svg("0 0 96 96", mark_group(style), titles[style])


def svg_icon() -> str:
    body = (
        f'  <rect width="96" height="96" rx="21" fill="{GREEN}"/>\n'
        '  <g transform="translate(48 48) scale(0.66) translate(-48 -48)">\n'
        + mark_group("icon").replace("\n  ", "\n    ").replace("  ", "    ", 1)
        + "  </g>\n"
    )
    return _svg("0 0 96 96", body, "MealieShopper appicoon")


def svg_favicon() -> str:
    x0, y0, x1, y1, r = FAV_BG
    bx0, by0, bx1, by1, br = FAV_BODY
    body = (
        f'  <rect width="32" height="32" rx="{r}" fill="{GREEN}"/>\n'
        f'  <path d="{FAV_HANDLE_D}" fill="none" stroke="{WHITE}" stroke-width="{FAV_HANDLE_W}" stroke-linecap="round" stroke-linejoin="round"/>\n'
        f'  <rect x="{bx0}" y="{by0}" width="{bx1 - bx0}" height="{by1 - by0}" rx="{br}" fill="{WHITE}"/>\n'
        f'  <path d="{FAV_M_D}" fill="none" stroke="{GREEN}" stroke-width="{FAV_M_W}" stroke-linecap="round" stroke-linejoin="round"/>\n'
    )
    return _svg("0 0 32 32", body, "MealieShopper favicon")


def svg_wordmark(style: str) -> str:
    first = {"color": INK, "white": WHITE, "mono": INK}[style]
    second = {"color": GREEN, "white": WHITE, "mono": INK}[style]
    group = mark_group(style, mask_id=f"cut-{style}").replace("\n  ", "\n    ").replace("  ", "    ", 1)
    body = (
        f'  <g transform="translate({WORDMARK_TX} {WORDMARK_TY})">\n{group}  </g>\n'
        f'  <text x="{WORDMARK_TEXT_X}" y="{WORDMARK_BASELINE}" font-family="Manrope, Inter, system-ui, sans-serif" '
        f'font-size="{WORDMARK_SIZE}" font-weight="800">'
        f'<tspan fill="{first}">Mealie</tspan><tspan fill="{second}">Shopper</tspan></text>\n'
    )
    titles = {"color": "MealieShopper", "white": "MealieShopper, wit", "mono": "MealieShopper, eenkleurig"}
    return _svg(f"0 0 {wordmark_width()} {WORDMARK_HEIGHT}", body, titles[style])


# ---------------------------------------------------------------------------
# Deelafbeelding en overzicht
# ---------------------------------------------------------------------------
def render_social_card() -> Image.Image:
    w, h = 1200, 630
    cv = Canvas((w, h), GREEN)
    layer = cv.layer()
    d = ImageDraw.Draw(layer)
    cx, cy, r = 955 * SS, 315 * SS, 245 * SS
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=GREEN_STRONG)
    # decoratief citroenaccent, op het supersample-doek getekend voor gladde uiteinden
    d.rounded_rectangle((80 * SS, 392 * SS, 176 * SS, 404 * SS), radius=6 * SS, fill=LEMON)
    cv.img.alpha_composite(layer)
    size = 330
    paint_mark(cv, 955 - size / 2, 315 - size / 2, size / 96, "icon")
    img = cv.finish()
    d = ImageDraw.Draw(img)
    d.text((80, 270), "MealieShopper", font=font(78), fill=WHITE, anchor="ls")
    d.text((80, 345), "Van recept naar geregeld.", font=font(40, 600), fill=MIST, anchor="ls")
    return img.convert("RGB")


def render_preview() -> Image.Image:
    w, h = 1600, 1180
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)

    def card(box, fill=WHITE):
        d.rounded_rectangle(box, radius=12, fill=fill, outline=BORDER)

    def label(xy, text, size=16, weight=600, fill=MUTED):
        d.text(xy, text, font=font(size, weight), fill=fill)

    d.text((60, 52), "MealieShopper huisstijl", font=font(40), fill=INK)
    label((60, 108), "Van recept naar geregeld.", 22, 500)

    # Appiconen en favicons
    label((60, 170), "App-icoon en favicon", 18, 700, INK)
    card((60, 205, 1540, 465))
    x = 100
    for px, name in ((192, "icon.svg 192"), (96, "96"), (48, "48")):
        icon = render_icon(px, 0.66, rounded=True)
        img.paste(icon, (x, 240 + (192 - px) // 2), icon)
        label((x, 440), name, 14)
        x += px + 50
    x += 20
    for px in (48, 32, 16):
        img.paste(render_favicon(px), (x, 300 + (48 - px)), render_favicon(px))
        label((x, 440), f"favicon {px}", 14)
        x += 90
    big = render_favicon(16).resize((128, 128), Image.NEAREST)
    img.paste(big, (x + 20, 300), big)
    label((x + 20, 440), "16 px, 8x vergroot", 14)
    mask = render_icon(160, 0.62)
    img.paste(mask, (1230, 255))
    d.ellipse((1230 + 16, 255 + 16, 1230 + 144, 255 + 144), outline=LEMON, width=3)
    label((1230, 440), "maskable, veilige cirkel", 14)

    # Beeldmerk en woordmerk
    label((60, 505), "Beeldmerk en woordmerk", 18, 700, INK)
    card((60, 540, 520, 800))
    card((540, 540, 1000, 800), GREEN)
    card((1020, 540, 1540, 800))
    for box, style in (((60, 540, 520, 800), "color"), ((540, 540, 1000, 800), "white"), ((1020, 540, 1540, 800), "mono")):
        m = render_mark(180, style)
        mx = box[0] + (box[2] - box[0] - 180) // 2
        img.paste(m, (mx, box[1] + 30), m)
    label((90, 760), "kleur", 14)
    label((570, 760), "wit op groen", 14, 600, MIST)
    label((1050, 760), "eenkleurig", 14)
    wm = render_wordmark("color", 0.9, WHITE)
    card((60, 830, 760, 960))
    img.paste(wm, (60 + (700 - wm.width) // 2, 830 + (130 - wm.height) // 2), wm)
    wm = render_wordmark("white", 0.9, GREEN)
    card((780, 830, 1540, 960), GREEN)
    img.paste(wm, (780 + (760 - wm.width) // 2, 830 + (130 - wm.height) // 2), wm)

    # Kleuren
    label((60, 995), "Kleuren", 18, 700, INK)
    swatches = [
        ("Merk / primair", GREEN), ("Hover", GREEN_STRONG), ("Selectie", MIST), ("Citroen (decoratief)", LEMON),
        ("Achtergrond", BG), ("Oppervlak", WHITE), ("Tekst", INK), ("Ondersteunend", MUTED), ("Rand", BORDER),
    ]
    sw = 160
    for i, (name, color) in enumerate(swatches):
        x0 = 60 + i * (sw + 5)
        d.rounded_rectangle((x0, 1030, x0 + sw - 10, 1090), radius=8, fill=color, outline=BORDER)
        label((x0, 1098), name, 13, 600, INK)
        label((x0, 1118), color, 13, 500)
    return img


# ---------------------------------------------------------------------------
def build(out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def text(name: str, content: str) -> None:
        path = out / name
        path.write_text(content, encoding="utf-8")
        written.append(path)

    def image(name: str, img: Image.Image, **kwargs) -> None:
        path = out / name
        img.save(path, **kwargs)
        written.append(path)

    text("icon.svg", svg_icon())
    text("mark.svg", svg_mark("color"))
    text("mark-white.svg", svg_mark("white"))
    text("mark-mono.svg", svg_mark("mono"))
    text("wordmark.svg", svg_wordmark("color"))
    text("wordmark-white.svg", svg_wordmark("white"))
    text("wordmark-mono.svg", svg_wordmark("mono"))
    text("favicon.svg", svg_favicon())

    sizes = [16, 32, 48]
    favicons = {px: render_favicon(px) for px in sizes}
    for px in sizes:
        image(f"favicon-{px}.png", favicons[px])
    image(
        "favicon.ico", favicons[48], format="ICO", sizes=[(px, px) for px in sizes],
        append_images=[favicons[16], favicons[32]],
    )

    image("icon-192.png", render_icon(192, 0.70).convert("RGB"))
    image("icon-512.png", render_icon(512, 0.70).convert("RGB"))
    image("icon-maskable-512.png", render_icon(512, 0.62).convert("RGB"))  # tasje binnen de veilige cirkel (80%)
    image("apple-touch-icon.png", render_icon(180, 0.70).convert("RGB"))
    image("social-card.png", render_social_card())
    image("brand-preview.png", render_preview())
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="doelmap (standaard static/brand)")
    args = parser.parse_args()
    for path in build(args.out):
        print(path.relative_to(args.out.parent) if args.out.parent in path.parents else path)


if __name__ == "__main__":
    main()
