#!/usr/bin/env python3
"""Vidéo motion design « AUTOPIEC x Groupama Antilles-Guyane » (1920x1080, 30 i/s).

Les images sont dessinées avec Pillow (supersampling x2 pour l'anticrénelage),
la musique est synthétisée avec numpy, puis ffmpeg assemble le tout en MP4.
Tous les textes et couleurs se règlent dans config.json.

Usage :
    python3 render.py                      # -> output/autopiec-groupama.mp4
    python3 render.py --preview            # quelques images clés dans output/apercu/
    python3 render.py --config autre.json --out output/autre.mp4
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
import wave
from functools import lru_cache
from multiprocessing import Pool

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.abspath(__file__))
W, H = 1920, 1080
S = 2              # facteur de supersampling
FPS = 30
BEAT = 0.5         # 120 BPM : chaque changement de scène tombe sur un temps

CFG = {}
C = {}


# --------------------------------------------------------------------------- #
# Courbes d'animation
# --------------------------------------------------------------------------- #
def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def prog(t, start, dur):
    return clamp((t - start) / dur)


def lerp(a, b, x):
    return a + (b - a) * x


def ease_out_cubic(x):
    return 1 - (1 - x) ** 3


def ease_in_out_cubic(x):
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def ease_out_expo(x):
    return 1.0 if x >= 1 else 1 - 2 ** (-10 * x)


def ease_out_back(x, s=1.70158):
    x -= 1
    return 1 + (s + 1) * x ** 3 + s * x ** 2


# --------------------------------------------------------------------------- #
# Couleurs, polices, calques
# --------------------------------------------------------------------------- #
def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (255,)


def mix(c1, c2, k):
    return tuple(int(lerp(a, b, k)) for a, b in zip(c1[:3], c2[:3])) + (255,)


FONT_DIRS = [os.path.join(ROOT, "fonts"), "/usr/share/fonts/opentype/inter",
             "/usr/share/fonts/truetype/dejavu"]
FONT_FILES = {
    "black": ["Inter-Black.otf", "Inter-ExtraBold.otf", "DejaVuSans-Bold.ttf"],
    "bold": ["Inter-Bold.otf", "DejaVuSans-Bold.ttf"],
    "semi": ["Inter-SemiBold.otf", "Inter-Bold.otf", "DejaVuSans-Bold.ttf"],
    "medium": ["Inter-Medium.otf", "Inter-Regular.otf", "DejaVuSans.ttf"],
}


@lru_cache(maxsize=None)
def font(weight, size):
    for name in FONT_FILES[weight]:
        for d in FONT_DIRS:
            path = os.path.join(d, name)
            if os.path.exists(path):
                return ImageFont.truetype(path, int(size * S))
    return ImageFont.load_default(int(size * S))


def text_w(text, weight, size):
    return font(weight, size).getlength(text) / S


def fit_size(text, weight, size, max_w):
    while size > 10 and text_w(text, weight, size) > max_w:
        size -= 1
    return size


def wrap(text, weight, size, max_w):
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if cur and text_w(trial, weight, size) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def segments(line, accent):
    """« la recherche {de pièces} » -> [(texte, couleur par défaut), (texte, accent)]."""
    out = []
    for part in re.split(r"(\{[^}]*\})", line):
        if part.startswith("{"):
            out.append((part[1:-1], accent))
        elif part:
            out.append((part, None))
    return out


PAD = 10 * S


@lru_cache(maxsize=512)
def text_layer(text, weight, size, color, tracking=0.0):
    """Calque d'une ligne de texte ; hauteur = ascendante + descendante (alignement stable)."""
    f = font(weight, size)
    asc, desc = f.getmetrics()
    tr = tracking * size * S
    width = f.getlength(text) + tr * max(0, len(text) - 1)
    im = Image.new("RGBA", (int(width) + 2 * PAD, asc + desc + 2 * PAD), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if tracking:
        x = PAD
        for ch in text:
            d.text((x, PAD), ch, font=f, fill=color)
            x += f.getlength(ch) + tr
    else:
        d.text((PAD, PAD), text, font=f, fill=color)
    return im


def composite(img, layer, x, y):
    x, y = int(round(x)), int(round(y))
    l, t = max(0, -x), max(0, -y)
    r, b = min(layer.width, img.width - x), min(layer.height, img.height - y)
    if r <= l or b <= t:
        return
    if (l, t, r, b) != (0, 0, layer.width, layer.height):
        layer = layer.crop((l, t, r, b))
    img.alpha_composite(layer, (x + l, y + t))


def fade(layer, opacity):
    if opacity >= 0.999:
        return layer
    layer = layer.copy()
    layer.putalpha(layer.getchannel("A").point(lambda v: int(v * opacity)))
    return layer


def paste(img, layer, cx, cy, scale=1.0, opacity=1.0, angle=0.0):
    """Colle un calque centré en (cx, cy), en unités 1920x1080."""
    if opacity <= 0.004 or scale <= 0.01:
        return
    if abs(scale - 1) > 1e-3:
        layer = layer.resize((max(1, int(layer.width * scale)),
                              max(1, int(layer.height * scale))), Image.BICUBIC)
    if angle:
        layer = layer.rotate(angle, resample=Image.BICUBIC, expand=True)
    composite(img, fade(layer, opacity), cx * S - layer.width / 2, cy * S - layer.height / 2)


def put_text(img, text, weight, size, color, x, y, align="l", opacity=1.0, tracking=0.0):
    """Écrit une ligne ; (x, y) = bord gauche (ou centre si align='c') et milieu vertical."""
    if opacity <= 0.004:
        return
    layer = text_layer(text, weight, size, color, tracking)
    lx = x * S - PAD if align == "l" else x * S - layer.width / 2
    composite(img, fade(layer, opacity), lx, y * S - layer.height / 2)


def reveal(img, line, weight, size, x, y, t, color, accent=None, align="l",
           stagger=0.028, dur=0.6, tracking=0.0):
    """Lettres qui montent une à une derrière un masque ; accepte le balisage {accent}."""
    if t <= 0:
        return
    segs = segments(line, accent or color)
    full = "".join(s for s, _ in segs)
    f = font(weight, size)
    asc, desc = f.getmetrics()
    lh = asc + desc
    tr = tracking * size * S
    width = f.getlength(full) + tr * max(0, len(full) - 1)
    layer = Image.new("RGBA", (int(width) + 2 * PAD, lh + 2 * PAD), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    i = 0
    for text, col in segs:
        for ch in text:
            p = ease_out_expo(prog(t, i * stagger, dur))
            if p > 0 and ch != " ":
                cx = PAD + f.getlength(full[:i]) + tr * i
                d.text((cx, PAD + (1 - p) * (lh + PAD)), ch, font=f, fill=col or color)
            i += 1
    lx = x * S - PAD if align == "l" else x * S - layer.width / 2
    composite(img, layer, lx, y * S - layer.height / 2)


def P(*pts):
    return [(x * S, y * S) for x, y in pts]


def ring(d, cx, cy, r, width, color):
    if r > 0 and width > 0:
        d.ellipse(P((cx - r, cy - r), (cx + r, cy + r)), outline=color,
                  width=max(1, int(width * S)))


def disc(d, cx, cy, r, color):
    if r > 0:
        d.ellipse(P((cx - r, cy - r), (cx + r, cy + r)), fill=color)


def rrect(d, x0, y0, x1, y1, r, fill=None, outline=None, width=1):
    if x1 - x0 < 1 or y1 - y0 < 1:
        return
    r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
    d.rounded_rectangle(P((x0, y0), (x1, y1)), radius=int(r * S), fill=fill,
                        outline=outline, width=int(width * S))


def card(d, x0, y0, x1, y1, r=26, fill=None, border=None, shadow=True):
    if shadow:
        rrect(d, x0 + 4, y0 + 12, x1 + 4, y1 + 14, r, fill=mix(C["fond_clair"], C["marine"], 0.12))
    rrect(d, x0, y0, x1, y1, r, fill=fill or C["blanc"], outline=border, width=2 if border else 1)


# --------------------------------------------------------------------------- #
# Pictogrammes vectoriels (repère -1..1, taille = demi-côté en px)
# --------------------------------------------------------------------------- #
def icon(d, name, cx, cy, s, color, hole=None):
    lw = max(1, int(s * 0.13 * S))

    def p(x, y):
        return (cx + x * s) * S, (cy + y * s) * S

    def line(*pts, closed=False):
        pts = [p(*q) for q in pts]
        if closed:
            pts.append(pts[0])
        d.line(pts, fill=color, width=lw, joint="curve")

    def circ(x, y, r, fill=False):
        box = [p(x - r, y - r), p(x + r, y + r)]
        if fill:
            d.ellipse(box, fill=color)
        else:
            d.ellipse(box, outline=color, width=lw)

    def poly(*pts):
        d.polygon([p(*q) for q in pts], fill=color)

    def arc(x, y, r, a0, a1):
        d.arc([p(x - r, y - r), p(x + r, y + r)], a0, a1, fill=color, width=lw)

    if name == "loupe":
        circ(-0.15, -0.15, 0.5)
        line((0.22, 0.22), (0.68, 0.68))
    elif name == "graphique":
        for x, h in ((-0.6, 0.55), (-0.1, 0.95), (0.4, 1.35)):
            d.rectangle([p(x, 0.68 - h), p(x + 0.3, 0.68)], fill=color)
    elif name == "camion":
        line((-0.8, -0.45), (0.2, -0.45), (0.2, 0.35), (-0.8, 0.35), closed=True)
        line((0.2, -0.2), (0.55, -0.2), (0.8, 0.08), (0.8, 0.35), (0.2, 0.35))
        circ(-0.45, 0.5, 0.15, fill=True)
        circ(0.5, 0.5, 0.15, fill=True)
    elif name == "ecran":
        line((-0.78, -0.58), (0.78, -0.58), (0.78, 0.32), (-0.78, 0.32), closed=True)
        line((0, 0.34), (0, 0.6))
        line((-0.38, 0.66), (0.38, 0.66))
    elif name == "reseau":
        line((0, -0.5), (-0.55, 0.42))
        line((0, -0.5), (0.55, 0.42))
        line((-0.55, 0.42), (0.55, 0.42))
        for x, y in ((0, -0.5), (-0.55, 0.42), (0.55, 0.42)):
            circ(x, y, 0.22, fill=True)
    elif name == "document":
        line((-0.52, -0.72), (0.22, -0.72), (0.55, -0.4), (0.55, 0.72), (-0.52, 0.72), closed=True)
        for y in (-0.15, 0.12, 0.39):
            line((-0.28, y), (0.3, y))
    elif name == "envoi":
        line((0, 0.25), (0, -0.62))
        line((-0.32, -0.32), (0, -0.64), (0.32, -0.32))
        line((-0.62, 0.1), (-0.62, 0.62), (0.62, 0.62), (0.62, 0.1))
    elif name == "bulle":
        d.rounded_rectangle([p(-0.75, -0.6), p(0.75, 0.32)], radius=int(0.25 * s * S),
                            outline=color, width=lw)
        poly((-0.38, 0.28), (-0.5, 0.72), (0.02, 0.28))
        for x in (-0.35, 0, 0.35):
            circ(x, -0.14, 0.09, fill=True)
    elif name == "panier":
        line((-0.85, -0.58), (-0.58, -0.58), (-0.32, 0.28), (0.55, 0.28), (0.78, -0.32), (-0.48, -0.32))
        circ(-0.22, 0.56, 0.13, fill=True)
        circ(0.45, 0.56, 0.13, fill=True)
    elif name == "personnes":
        circ(0.22, -0.38, 0.27)
        arc(0.22, 0.55, 0.55, 200, 340)
        circ(-0.4, -0.22, 0.2)
        arc(-0.4, 0.55, 0.42, 205, 300)
    elif name == "diplome":
        poly((-0.85, -0.2), (0, -0.58), (0.85, -0.2), (0, 0.18))
        line((-0.48, 0.0), (-0.48, 0.42), (0, 0.6), (0.48, 0.42), (0.48, 0.0))
        line((0.85, -0.2), (0.85, 0.4))
    elif name == "cible":
        circ(0, 0, 0.7)
        circ(0, 0, 0.4)
        circ(0, 0, 0.13, fill=True)
        line((0.05, -0.05), (0.72, -0.72))
    elif name == "horloge":
        circ(0, 0, 0.7)
        line((0, -0.42), (0, 0), (0.3, 0.18))
    elif name == "recyclage":
        arc(0, 0, 0.62, 200, 335)
        arc(0, 0, 0.62, 20, 155)
        poly((0.62, -0.42), (0.75, -0.02), (0.32, -0.12))
        poly((-0.62, 0.42), (-0.75, 0.02), (-0.32, 0.12))
    elif name == "check":
        line((-0.5, 0.02), (-0.14, 0.38), (0.52, -0.34))
    elif name == "repere":
        circ(0, -0.22, 0.48, fill=True)
        poly((-0.41, 0.02), (0.41, 0.02), (0, 0.8))
        if hole:
            box = [p(-0.18, -0.4), p(0.18, -0.04)]
            d.ellipse(box, fill=hole)
    elif name == "calendrier":
        line((-0.7, -0.5), (0.7, -0.5), (0.7, 0.7), (-0.7, 0.7), closed=True)
        line((-0.7, -0.15), (0.7, -0.15))
        line((-0.35, -0.72), (-0.35, -0.35))
        line((0.35, -0.72), (0.35, -0.35))
    elif name == "voiture":
        poly((-0.95, 0.25), (-0.9, -0.05), (-0.5, -0.12), (-0.25, -0.45), (0.35, -0.45),
             (0.62, -0.1), (0.95, 0.0), (0.95, 0.25))
        circ(-0.5, 0.3, 0.2, fill=True)
        circ(0.55, 0.3, 0.2, fill=True)
        if hole:
            for x in (-0.5, 0.55):
                d.ellipse([p(x - 0.09, 0.21), p(x + 0.09, 0.39)], fill=hole)
    else:
        circ(0, 0, 0.5, fill=True)


# --------------------------------------------------------------------------- #
# Visuels issus de la présentation
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=None)
def asset(name, scale=1.0):
    im = Image.open(os.path.join(ROOT, "assets", name)).convert("RGBA")
    if name == "tableau_de_bord.png":
        # On ne garde que l'écran (sans le bord du portable ni le décor)
        im = im.crop((30, 44, 622, 436))
        mask = Image.new("L", im.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, im.width - 1, im.height - 1), radius=14, fill=255)
        im.putalpha(mask)
    w, h = int(im.width * scale * S), int(im.height * scale * S)
    return im.resize((w, h), Image.LANCZOS)


def logos_lockup(img, cx, cy, scale, t, cross_color=None):
    """AUTOPIEC  ×  Groupama, chaque élément entrant à son tour."""
    a = asset("logo_autopiec.png", scale)
    g = asset("logo_groupama.png", scale)
    gap = 70 * scale
    total = a.width / S + gap * 2 + g.width / S
    x0 = cx - total / 2
    pa = ease_out_expo(prog(t, 0.0, 0.7))
    paste(img, a, x0 + a.width / S / 2 - (1 - pa) * 120, cy, opacity=prog(t, 0.0, 0.25))
    px = ease_out_back(prog(t, 0.25, 0.5), 2.2)
    xc = x0 + a.width / S + gap
    if px > 0:
        d = ImageDraw.Draw(img)
        k = 16 * scale * px
        col = cross_color or C["blanc"]
        lw = max(1, int(3 * scale * S))
        ang = math.radians((1 - px) * 180)
        for a0 in (math.pi / 4, -math.pi / 4):
            dx, dy = math.cos(a0 + ang) * k, math.sin(a0 + ang) * k
            d.line(P((xc - dx, cy - dy), (xc + dx, cy + dy)), fill=col, width=lw)
    pg = ease_out_expo(prog(t, 0.4, 0.7))
    paste(img, g, xc + gap + g.width / S / 2 + (1 - pg) * 120, cy, opacity=prog(t, 0.4, 0.25))


# --------------------------------------------------------------------------- #
# Fonds et habillage commun
# --------------------------------------------------------------------------- #
_BG = {}


def background(kind):
    if kind not in _BG:
        w, h = W * S, H * S
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        if kind == "bleu":
            k = np.clip((xx / w) * 0.75 + (1 - yy / h) * 0.45 - 0.25, 0, 1)[..., None] ** 1.3
            c0, c1 = np.array(C["marine"][:3], np.float32), np.array(C["bleu"][:3], np.float32)
        else:
            k = np.clip(np.sqrt(((xx - w) / w) ** 2 + ((yy - h) / h) ** 2) / 0.9, 0, 1)[..., None]
            c0, c1 = np.array(C["fond_clair"][:3], np.float32), np.array(C["blanc"][:3], np.float32)
        rgb = (c0 * (1 - k) + c1 * k).astype(np.uint8)
        _BG[kind] = Image.fromarray(np.dstack([rgb, np.full((h, w), 255, np.uint8)]), "RGBA")
    return _BG[kind]


def slashes(d, x, y, h, color, t, count=3, start=0.0, width=None, gap=None):
    """Les « /// » du logo AUTOPIEC, qui grandissent l'un après l'autre."""
    width = width or h * 0.42
    gap = gap or h * 0.18
    skew = h * 0.42
    for k in range(count):
        p = ease_out_expo(prog(t, start + k * 0.08, 0.6))
        if p <= 0:
            continue
        x0 = x + k * (width + gap)
        top = y + h * (1 - p)
        sk = skew * p
        d.polygon(P((x0 + sk, top), (x0 + sk + width, top), (x0 + width, y + h), (x0, y + h)),
                  fill=color)


def deco_stripes(d, t, color, alpha_k=0.12):
    """Grandes bandes diagonales translucides qui dérivent lentement (fonds bleus)."""
    col = mix(C["marine"], color, alpha_k)
    for k, (x, w) in enumerate(((1250, 160), (1500, 70), (1650, 260))):
        off = (t * 22 + k * 40) % 60
        x0 = x + off
        d.polygon(P((x0 + 520, 0), (x0 + 520 + w, 0), (x0 + w - 200, H), (x0 - 200, H)), fill=col)


def header(img, d, t, kicker):
    """En-tête des scènes claires : rubrique à gauche, bandeau bleu avec logos à droite."""
    p = ease_out_expo(prog(t, 0.05, 0.7))
    x = lerp(W + 40, 1190, p)
    d.polygon(P((x + 60, 0), (W + 10, 0), (W + 10, 112), (x, 112)), fill=C["bleu"])
    d.polygon(P((x - 34, 0), (x + 4, 0), (x - 56, 112), (x - 94, 112)), fill=C["citron"])
    if p > 0.05:
        a = asset("logo_autopiec.png", 0.5)
        g = asset("logo_groupama.png", 0.5)
        paste(img, a, x + 100 + a.width / S / 2, 56, opacity=prog(t, 0.35, 0.3))
        xc = x + 100 + a.width / S + 30
        k = 8
        for a0 in (1, -1):
            d.line(P((xc - k, 56 - k * a0), (xc + k, 56 + k * a0)), fill=C["blanc"], width=2 * S)
        paste(img, g, xc + 30 + g.width / S / 2, 56, opacity=prog(t, 0.45, 0.3))
    if kicker:
        kp = ease_out_expo(prog(t, 0.1, 0.5))
        rrect(d, 80, 62, 80 + 42 * kp, 68, 3, fill=C["citron"])
        reveal(img, kicker, "bold", 20, 140, 65, t - 0.15, C["bleu"], stagger=0.012,
               dur=0.4, tracking=0.16)


def footer(img, d, t, dark=True):
    col = C["texte"] if dark else C["blanc"]
    pin = C["citron"]
    op = prog(t, 0.3, 0.4)
    if op <= 0:
        return
    names = CFG["intro"]["territoires"]
    x = 80
    for i, name in enumerate(names):
        icon(d, "repere", x + 9, 1028, 11, pin, hole=C["blanc"] if dark else C["marine"])
        put_text(img, name, "medium", 19, col, x + 28, 1029, opacity=op)
        x += 28 + text_w(name, "medium", 19) + 22
        if i < len(names) - 1:
            d.line(P((x - 11, 1016), (x - 11, 1042)), fill=mix(C["blanc"] if dark else C["marine"], col, 0.4), width=S)
    rrect(d, 1730, 1026, 1730 + 110 * ease_out_expo(prog(t, 0.4, 0.6)), 1032, 3, fill=pin)


# --------------------------------------------------------------------------- #
# Scène 1 : logos
# --------------------------------------------------------------------------- #
def scene_intro(img, d, t):
    cfg = CFG["intro"]
    deco_stripes(d, t, C["bleu_vif"])

    # Grandes barres « /// » citron qui traversent l'écran puis s'effacent vers la droite
    p = ease_in_out_cubic(prog(t, 0.0, 1.3))
    if 0 < p < 1:
        for k in range(3):
            x0 = lerp(-900, W + 300, clamp(p * 1.15 - k * 0.07)) + k * 0
            d.polygon(P((x0 + 380, -10), (x0 + 560, -10), (x0 + 180, H + 10), (x0, H + 10)),
                      fill=C["citron"] if k != 1 else C["bleu_vif"])

    logos_lockup(img, W / 2, 470, 1.25, t - 0.9)

    lp = ease_out_expo(prog(t, 1.7, 0.6))
    tw = text_w(cfg["surtitre"], "bold", 26) + 0.22 * 26 * (len(cfg["surtitre"]) - 1)
    rrect(d, W / 2 - tw / 2 - 30 - 50 * lp, 600, W / 2 - tw / 2 - 30, 606, 3, fill=C["citron"])
    reveal(img, cfg["surtitre"], "bold", 26, W / 2, 603, t - 1.7, C["blanc"], align="c",
           stagger=0.018, dur=0.5, tracking=0.22)
    footer(img, d, t - 1.9, dark=False)


# --------------------------------------------------------------------------- #
# Scène 2 : titre + visuel de couverture
# --------------------------------------------------------------------------- #
def scene_title(img, d, t):
    cfg = CFG["titre"]
    white, lime = C["blanc"], C["citron"]

    # Visuel : entre en glissant, masqué par une bande diagonale, avec un léger zoom
    vp = ease_out_expo(prog(t, 0.1, 1.0))
    if vp > 0:
        zoom = 1.18 + 0.05 * t / 5.5
        vis = asset("visuel_couverture.png", zoom)
        vx = 1515 + (1 - vp) * 500
        paste(img, vis, vx, 560, opacity=prog(t, 0.1, 0.3))
    # Bande citron qui balaie le bord du visuel
    sp = ease_out_expo(prog(t, 0.25, 0.9))
    if sp > 0:
        x0 = lerp(W + 200, 1000, sp)
        d.polygon(P((x0 + 470, 0), (x0 + 505, 0), (x0 + 35, H), (x0, H)), fill=lime)

    slashes(d, 80, 138, 30, lime, t, start=0.1)
    put_text(img, "PROPOSITION DE PARTENARIAT", "bold", 18, white, 172, 153,
             opacity=prog(t, 0.3, 0.4), tracking=0.2)

    for i, line in enumerate(cfg["lignes"]):
        reveal(img, line, "black", 76, 80, 268 + i * 90, t - 0.3 - i * 0.22, white, lime,
               stagger=0.022)

    sub = wrap(cfg["sous_titre"], "medium", 30, 760)
    for i, line in enumerate(sub):
        op = ease_out_cubic(prog(t, 1.3 + i * 0.1, 0.5))
        put_text(img, line, "medium", 30, white, 80, 560 + i * 42 + (1 - op) * 18, opacity=op)

    for k, item in enumerate(cfg["atouts"]):
        st = 2.0 + k * 0.18
        ip = ease_out_back(prog(t, st, 0.5), 2.0)
        x = 80 + k * 290
        y = 800
        if ip > 0:
            ring(d, x + 34, y, 34 * ip, 3, lime)
            icon(d, item["icone"], x + 34, y, 17 * ip, lime)
        lines = wrap(item["texte"], "medium", 22, 190)
        op = prog(t, st + 0.15, 0.4)
        for j, l in enumerate(lines):
            put_text(img, l, "medium", 22, white, x + 84, y + (j - (len(lines) - 1) / 2) * 28, opacity=op)
        if k < 2:
            d.line(P((x + 270, y - 30), (x + 270, y - 30 + 60 * op)), fill=mix(C["marine"], white, 0.35), width=S)

    footer(img, d, t - 0.6, dark=False)


# --------------------------------------------------------------------------- #
# Scène 3 : qui sommes-nous
# --------------------------------------------------------------------------- #
def scene_who(img, d, t):
    cfg = CFG["qui"]
    header(img, d, t, cfg["rubrique"])
    for i, line in enumerate(cfg["lignes"]):
        reveal(img, line, "black", 62, 80, 175 + i * 74, t - 0.2 - i * 0.2, C["texte"], C["bleu"],
               stagger=0.02)

    cw, ch, gap = 540, 380, 70
    for k, item in enumerate(cfg["cartes"]):
        st = 0.7 + k * 0.18
        p = ease_out_back(prog(t, st, 0.7), 1.3)
        if p <= 0:
            continue
        x0 = 80 + k * (cw + gap)
        y0 = 340 + (1 - p) * 120
        op = prog(t, st, 0.25)
        if op < 1:
            y0 += (1 - op) * 40
        card(d, x0, y0, x0 + cw, y0 + ch, border=C["bordure"])
        bp = ease_out_back(prog(t, st + 0.25, 0.4), 2.5)
        disc(d, x0 + 46, y0 + 46, 24 * bp, C["citron"])
        if bp > 0.5:
            put_text(img, f"0{k + 1}", "black", 18, C["texte"], x0 + 46, y0 + 46, align="c")
        ip = ease_out_back(prog(t, st + 0.3, 0.5), 2.0)
        disc(d, x0 + cw / 2, y0 + 100, 56 * ip, C["fond_clair"])
        icon(d, item["icone"], x0 + cw / 2, y0 + 100, 30 * ip, C["bleu"])
        tp = ease_out_cubic(prog(t, st + 0.4, 0.4))
        size = fit_size(item["titre"], "black", 34, cw - 60)
        put_text(img, item["titre"], "black", size, C["bleu"], x0 + cw / 2, y0 + 205 + (1 - tp) * 14,
                 align="c", opacity=tp)
        dp = ease_out_cubic(prog(t, st + 0.5, 0.4))
        for j, l in enumerate(wrap(item["texte"], "medium", 25, cw - 90)):
            put_text(img, l, "medium", 25, C["texte_gris"], x0 + cw / 2, y0 + 262 + j * 34 + (1 - dp) * 14,
                     align="c", opacity=dp)

    # Bandeau « notre rôle »
    rp = ease_out_expo(prog(t, 2.0, 0.8))
    if rp > 0:
        x1 = 80 + (1840 - 80) * rp
        rrect(d, 80, 790, x1, 940, 24, fill=C["bleu"])
        ip = ease_out_back(prog(t, 2.35, 0.5), 2.0)
        ring(d, 160, 865, 44 * ip, 3, C["citron"])
        icon(d, "cible", 160, 865, 24 * ip, C["citron"])
        reveal(img, cfg["role_titre"], "bold", 19, 240, 832, t - 2.4, C["citron"], stagger=0.012,
               tracking=0.18, dur=0.4)
        size = fit_size(cfg["role_texte"], "bold", 38, 1540)
        reveal(img, cfg["role_texte"], "bold", size, 240, 885, t - 2.55, C["blanc"], stagger=0.01, dur=0.5)
    footer(img, d, t)


# --------------------------------------------------------------------------- #
# Scène 4 : processus en 6 étapes
# --------------------------------------------------------------------------- #
def scene_process(img, d, t):
    cfg = CFG["processus"]
    header(img, d, t, cfg["rubrique"])
    for i, line in enumerate(cfg["lignes"]):
        reveal(img, line, "black", 62, 80, 175 + i * 74, t - 0.2 - i * 0.2, C["texte"], C["bleu"],
               stagger=0.02)

    steps = cfg["etapes"]
    n = len(steps)
    x_first, x_last, y = 215, 1705, 520
    xs = [lerp(x_first, x_last, i / (n - 1)) for i in range(n)]

    # Ligne de fond + progression
    bp = ease_out_expo(prog(t, 0.5, 0.6))
    rrect(d, x_first, y - 4, lerp(x_first, x_last, bp), y + 4, 4, fill=C["bordure"])
    lp = ease_in_out_cubic(prog(t, 0.9, 3.6))
    head = lerp(x_first, x_last, lp)
    if lp > 0:
        rrect(d, x_first, y - 4, head, y + 4, 4, fill=C["bleu"])

    for i, step in enumerate(steps):
        reached = head >= xs[i] - 1 and lp > 0
        # instant où la tête atteint l'étape (inversion de la courbe par dichotomie)
        lo, hi = 0.0, 1.0
        target = i / (n - 1)
        for _ in range(18):
            mid = (lo + hi) / 2
            if ease_in_out_cubic(mid) < target:
                lo = mid
            else:
                hi = mid
        t_hit = 0.9 + 3.6 * lo
        p = ease_out_back(prog(t, t_hit - 0.05, 0.5), 2.2) if reached else 0
        x = xs[i]
        if p > 0:
            q = prog(t, t_hit, 0.6)
            if q < 1:
                ring(d, x, y, 60 + 40 * ease_out_cubic(q), 3 * (1 - q), mix(C["blanc"], C["bleu"], 0.6))
            disc(d, x, y, 60 * p, C["bleu"])
            icon(d, step["icone"], x, y, 28 * p, C["blanc"])
            disc(d, x - 44 * p, y - 44 * p, 20 * p, C["citron"])
            if p > 0.6:
                put_text(img, str(i + 1), "black", 20, C["texte"], x - 44 * p, y - 44 * p, align="c")
        op = ease_out_cubic(prog(t, t_hit + 0.1, 0.45))
        put_text(img, step["titre"], "black", fit_size(step["titre"], "black", 30, 270), C["texte"],
                 x, 640 + (1 - op) * 16, align="c", opacity=op)
        for j, l in enumerate(wrap(step["texte"], "medium", 22, 245)):
            put_text(img, l, "medium", 22, C["texte_gris"], x, 690 + j * 30 + (1 - op) * 16,
                     align="c", opacity=op * prog(t, t_hit + 0.2, 0.3))

    # Petite voiture qui roule en tête de progression
    if 0 < lp < 1:
        bob = math.sin(t * 18) * 1.5
        icon(d, "voiture", head, y - 50 + bob, 26, C["citron"], hole=C["texte"])
        d.line(P((head - 60, y - 44), (head - 36, y - 44)), fill=C["citron"], width=3 * S)
        d.line(P((head - 70, y - 54), (head - 42, y - 54)), fill=C["citron"], width=3 * S)

    cp = ease_out_cubic(prog(t, 4.6, 0.6))
    if cp > 0:
        tw = text_w(cfg["conclusion"], "semi", 28)
        rrect(d, W / 2 - tw / 2 - 40, 862, W / 2 + tw / 2 + 40, 932, 35, fill=C["fond_clair"],
              outline=C["bordure"], width=2)
        put_text(img, cfg["conclusion"], "semi", 28, C["bleu"], W / 2, 897 + (1 - cp) * 10,
                 align="c", opacity=cp)
    footer(img, d, t)


# --------------------------------------------------------------------------- #
# Scène 5 : déploiement
# --------------------------------------------------------------------------- #
def scene_deploy(img, d, t):
    cfg = CFG["deploiement"]
    white, lime = C["blanc"], C["citron"]
    deco_stripes(d, t, C["bleu_vif"])
    slashes(d, W / 2 - 52, 112, 34, lime, t, start=0.0)
    for i, line in enumerate(cfg["lignes"]):
        size = fit_size(line.replace("{", "").replace("}", ""), "black", 66, 1700)
        reveal(img, line, "black", size, W / 2, 222 + i * 80, t - 0.15 - i * 0.2, white, lime,
               align="c", stagger=0.016)
    sub = cfg["sous_titre"]
    op = ease_out_cubic(prog(t, 0.9, 0.5))
    put_text(img, sub, "medium", fit_size(sub, "medium", 28, 1600), mix(C["bleu"], white, 0.85),
             W / 2, 385 + (1 - op) * 12, align="c", opacity=op)

    cw, ch = 470, 360
    centers = [W / 2 - 600, W / 2, W / 2 + 600]
    for k, item in enumerate(cfg["etapes"]):
        st = 1.0 + k * 0.3
        p = ease_out_back(prog(t, st, 0.7), 1.3)
        if p <= 0:
            continue
        cx = centers[k]
        y0 = 500 + (1 - p) * 140
        rrect(d, cx - cw / 2 + 6, y0 + 14, cx + cw / 2 + 6, y0 + ch + 14, 28, fill=mix(C["marine"], (0, 0, 0), 0.25))
        rrect(d, cx - cw / 2, y0, cx + cw / 2, y0 + ch, 28, fill=white)
        ip = ease_out_back(prog(t, st + 0.25, 0.5), 2.2)
        disc(d, cx, y0 + 95, 58 * ip, C["fond_clair"])
        icon(d, item["icone"], cx, y0 + 95, 32 * ip, C["bleu"])
        tp = ease_out_cubic(prog(t, st + 0.35, 0.4))
        put_text(img, item["titre"], "black", 40, C["bleu"], cx, y0 + 200 + (1 - tp) * 12, align="c", opacity=tp)
        for j, l in enumerate(wrap(item["texte"], "medium", 25, cw - 80)):
            put_text(img, l, "medium", 25, C["texte_gris"], cx, y0 + 258 + j * 34 + (1 - tp) * 12,
                     align="c", opacity=tp * prog(t, st + 0.45, 0.3))
        if k < 2:
            ap = ease_out_back(prog(t, st + 0.45, 0.4), 2.5)
            ax, ay = cx + 300, y0 + ch / 2
            if ap > 0:
                disc(d, ax, ay, 26 * ap, lime)
                d.line(P((ax - 7 * ap, ay - 11 * ap), (ax + 6 * ap, ay), (ax - 7 * ap, ay + 11 * ap)),
                       fill=C["marine"], width=4 * S, joint="curve")
    footer(img, d, t - 0.3, dark=False)


# --------------------------------------------------------------------------- #
# Scène 6 : pilotage & KPI
# --------------------------------------------------------------------------- #
def mini_chart(d, kind, cx, cy, p, k):
    blue, lime, light = C["bleu"], C["citron"], C["bordure"]
    if kind == "barres":
        hs = [0.45, 0.7, 0.55, 0.9, 1.0]
        for i, h in enumerate(hs):
            hp = ease_out_back(prog(p, i * 0.08, 0.5), 1.6)
            x = cx - 50 + i * 22
            if hp > 0:
                rrect(d, x, cy + 30 - 60 * h * hp, x + 14, cy + 30, 4, fill=blue if i < 4 else lime)
    elif kind == "anneau":
        v = (0.76, 0.62, 0.84)[k % 3]
        r = 34
        ring(d, cx, cy, r, 12, light)
        sweep = 360 * v * ease_out_cubic(p)
        if sweep > 1:
            d.arc(P((cx - r, cy - r), (cx + r, cy + r)), -90, -90 + sweep, fill=blue, width=12 * S)
        if p > 0.95:
            disc(d, cx + r * math.cos(math.radians(-90 + sweep)), cy + r * math.sin(math.radians(-90 + sweep)), 8, lime)
    else:  # courbe
        pts = [(-55, 22), (-30, 8), (-8, 14), (15, -6), (35, -2), (55, -26)]
        n = len(pts)
        upto = ease_out_cubic(p) * (n - 1)
        line = []
        for i in range(n):
            if i <= upto:
                line.append((cx + pts[i][0], cy + pts[i][1]))
            else:
                j = int(upto)
                f = upto - j
                if j + 1 < n:
                    line.append((cx + lerp(pts[j][0], pts[j + 1][0], f), cy + lerp(pts[j][1], pts[j + 1][1], f)))
                break
        if len(line) > 1:
            d.line(P(*line), fill=blue, width=6 * S, joint="curve")
            disc(d, line[-1][0], line[-1][1], 8, lime)


def scene_kpi(img, d, t):
    cfg = CFG["kpi"]
    header(img, d, t, cfg["rubrique"])
    for i, line in enumerate(cfg["lignes"]):
        reveal(img, line, "black", 58, 80, 175 + i * 70, t - 0.2 - i * 0.2, C["texte"], C["bleu"],
               stagger=0.018)

    # Capture du tableau de bord à droite
    dp = ease_out_expo(prog(t, 0.6, 1.0))
    if dp > 0:
        dash = asset("tableau_de_bord.png", 1.32)
        dw, dh = dash.width / S, dash.height / S
        cx, cy = 1450 + (1 - dp) * 700, 625
        rrect(d, cx - dw / 2 - 18, cy - dh / 2 - 18, cx + dw / 2 + 18, cy + dh / 2 + 18, 30, fill=C["marine"])
        paste(img, dash, cx, cy - 4 * math.sin(t * 1.6) * prog(t, 1.6, 0.5))
        d.polygon(P((cx - dw / 2 - 70, cy + dh / 2 + 18), (cx - dw / 2 - 40, cy + dh / 2 + 18),
                    (cx - dw / 2 - 160, cy + dh / 2 + 140), (cx - dw / 2 - 190, cy + dh / 2 + 140)),
                  fill=C["citron"])

    tile_w, tile_h, gap = 270, 175, 18
    for b, bloc in enumerate(cfg["blocs"]):
        st = 0.9 + b * 0.7
        y = 380 + b * 330
        p = ease_out_expo(prog(t, st, 0.6))
        if p <= 0:
            continue
        x_off = (1 - p) * -60
        ip = ease_out_back(prog(t, st, 0.5), 2.2)
        disc(d, 112 + x_off, y, 32 * ip, C["bleu"] if b == 0 else C["citron"])
        icon(d, bloc["icone"], 112 + x_off, y, 18 * ip, C["blanc"] if b == 0 else C["texte"])
        put_text(img, bloc["titre"], "black", 24, C["bleu"], 162 + x_off, y - 14, opacity=p, tracking=0.08)
        put_text(img, bloc["texte"], "medium", 22, C["texte_gris"], 162 + x_off, y + 18, opacity=p)
        for k, ind in enumerate(bloc["indicateurs"]):
            tst = st + 0.2 + k * 0.12
            tp = ease_out_back(prog(t, tst, 0.55), 1.4)
            if tp <= 0:
                continue
            x0 = 80 + k * (tile_w + gap)
            y0 = y + 60 + (1 - tp) * 60
            card(d, x0, y0, x0 + tile_w, y0 + tile_h, r=20, border=C["bordure"], shadow=False)
            lines = wrap(ind["texte"], "semi", 20, tile_w - 36)
            for j, l in enumerate(lines[:2]):
                put_text(img, l, "semi", 20, C["texte"], x0 + 18, y0 + 32 + j * 25, opacity=prog(t, tst, 0.3))
            mini_chart(d, ind["graphe"], x0 + tile_w / 2, y0 + 118, prog(t, tst + 0.25, 0.9), k + b)
    footer(img, d, t)


# --------------------------------------------------------------------------- #
# Scène 7 : phase pilote
# --------------------------------------------------------------------------- #
def scene_pilot(img, d, t):
    cfg = CFG["pilote"]
    header(img, d, t, cfg["rubrique"])
    for i, line in enumerate(cfg["lignes"]):
        reveal(img, line, "black", 58, 80, 175 + i * 70, t - 0.2 - i * 0.2, C["texte"], C["bleu"],
               stagger=0.016)

    # Carte « 6 mois »
    p = ease_out_back(prog(t, 0.6, 0.7), 1.3)
    if p > 0:
        x0, y0, x1, y1 = 80, 330 + (1 - p) * 100, 700, 650 + (1 - p) * 100
        rrect(d, x0 + 6, y0 + 14, x1 + 6, y1 + 14, 30, fill=mix(C["fond_clair"], C["marine"], 0.15))
        rrect(d, x0, y0, x1, y1, 30, fill=C["bleu"])
        slashes(d, x0 + 44, y0 + 44, 28, C["citron"], t, start=0.8)
        put_text(img, "PHASE PILOTE", "bold", 22, C["blanc"], x0 + 140, y0 + 59,
                 opacity=prog(t, 0.9, 0.3), tracking=0.18)
        target = int(cfg["duree"]) if cfg["duree"].isdigit() else None
        cp = ease_out_cubic(prog(t, 1.0, 1.0))
        num = str(max(1, round(target * cp))) if target else cfg["duree"]
        big = text_layer(num, "black", 190, C["citron"])
        put_text(img, num, "black", 190, C["citron"], x0 + 40, y0 + 175, opacity=prog(t, 1.0, 0.2))
        nx = x0 + 40 + big.width / S - 2 * PAD / S + 20
        put_text(img, cfg["unite"], "black", 64, C["blanc"], nx, y0 + 205, opacity=prog(t, 1.1, 0.3))
        for j, l in enumerate(wrap(cfg["duree_texte"], "medium", 26, 540)):
            put_text(img, l, "medium", 26, mix(C["bleu"], C["blanc"], 0.85), x0 + 44, y0 + 268 + j * 34,
                     opacity=prog(t, 1.3, 0.4))

    # Critères de décision
    put_text(img, cfg["criteres_titre"], "black", 28, C["texte"], 790, 352, opacity=prog(t, 1.0, 0.4))
    for k, crit in enumerate(cfg["criteres"]):
        st = 1.2 + k * 0.15
        cp = ease_out_expo(prog(t, st, 0.6))
        if cp <= 0:
            continue
        col, row = k % 2, k // 2
        x0 = 790 + col * 530 + (1 - cp) * 50
        y0 = 400 + row * 130
        card(d, x0, y0, x0 + 505, y0 + 105, r=20, border=C["bordure"], shadow=False)
        ip = ease_out_back(prog(t, st + 0.1, 0.45), 2.2)
        disc(d, x0 + 54, y0 + 52, 32 * ip, C["citron"])
        icon(d, crit["icone"], x0 + 54, y0 + 52, 18 * ip, C["texte"])
        lines = wrap(crit["texte"], "semi", 25, 380)
        for j, l in enumerate(lines):
            put_text(img, l, "semi", 25, C["texte"], x0 + 106, y0 + 52 + (j - (len(lines) - 1) / 2) * 30,
                     opacity=cp)

    # Jalons
    ms = cfg["jalons"]
    y = 800
    xs = [lerp(340, 1660, i / (len(ms) - 1)) for i in range(len(ms))]
    lp = ease_in_out_cubic(prog(t, 2.0, 1.6))
    if lp > 0:
        rrect(d, xs[0], y - 3, lerp(xs[0], xs[-1], lp), y + 3, 3, fill=C["bleu"])
    for i, m in enumerate(ms):
        thr = i / (len(ms) - 1)
        if lp + 1e-6 < thr:
            continue
        st = 2.0 + 1.6 * thr
        q = ease_out_back(prog(t, st, 0.45), 2.4)
        disc(d, xs[i], y, 20 * q, C["bleu"])
        disc(d, xs[i], y, 8 * q, C["citron"])
        op = prog(t, st + 0.05, 0.35)
        put_text(img, m["titre"], "black", 26, C["texte"], xs[i], y + 52, align="c", opacity=op)
        put_text(img, m["texte"], "medium", 22, C["bleu"], xs[i], y + 86, align="c", opacity=op)
    put_text(img, "ÉTAPES CLÉS", "bold", 18, C["texte_gris"], 80, y, opacity=prog(t, 1.9, 0.4), tracking=0.18)
    footer(img, d, t)


# --------------------------------------------------------------------------- #
# Scène 8 : conclusion
# --------------------------------------------------------------------------- #
def scene_outro(img, d, t):
    cfg = CFG["fin"]
    white, lime = C["blanc"], C["citron"]
    deco_stripes(d, t, C["bleu_vif"])
    ip = ease_out_back(prog(t, 0.1, 0.6), 2.0)
    if ip > 0:
        disc(d, W / 2, 205, 62 * ip, lime)
        icon(d, "recyclage", W / 2, 205, 36 * ip, C["marine"])
        q = prog(t, 0.3, 1.0)
        if q < 1:
            ring(d, W / 2, 205, 62 + 60 * ease_out_cubic(q), 4 * (1 - q), lime)
    for i, line in enumerate(cfg["lignes"]):
        reveal(img, line, "black", 78, W / 2, 352 + i * 92, t - 0.35 - i * 0.2, white, lime,
               align="c", stagger=0.02)
    lines = wrap(cfg["texte"], "medium", 30, 1100)
    for j, l in enumerate(lines):
        op = ease_out_cubic(prog(t, 1.2 + j * 0.1, 0.5))
        put_text(img, l, "medium", 30, mix(C["bleu"], white, 0.85), W / 2, 560 + j * 42 + (1 - op) * 12,
                 align="c", opacity=op)
    lp = ease_out_expo(prog(t, 1.7, 0.8))
    if lp > 0:
        rrect(d, W / 2 - 520 * lp, 700, W / 2 + 520 * lp, 702, 1, fill=mix(C["marine"], white, 0.3))
    logos_lockup(img, W / 2, 820, 1.0, t - 1.9)
    footer(img, d, t - 2.3, dark=False)


# Début de chaque scène (secondes), fond, fonction de rendu
TIMELINE = [
    (0.0, "bleu", scene_intro),
    (4.0, "bleu", scene_title),
    (9.5, "clair", scene_who),
    (15.0, "clair", scene_process),
    (21.5, "bleu", scene_deploy),
    (26.5, "clair", scene_kpi),
    (32.0, "clair", scene_pilot),
    (37.5, "bleu", scene_outro),
]
DURATION = 43.0
SWITCHES = [s for s, _, _ in TIMELINE[1:]]


# --------------------------------------------------------------------------- #
# Transitions : bandes diagonales citron / bleu / marine qui balaient l'écran
# --------------------------------------------------------------------------- #
BAND_DUR, BAND_STAGGER = 0.8, 0.08


def draw_transitions(d, t):
    cols = [C["citron"], C["bleu_vif"], C["bleu"]]
    n = len(cols)
    bw, skew = 2.4 * W, 0.25 * W
    for switch in SWITCHES:
        # la dernière bande (au premier plan) est à mi-course pile au changement de scène
        ts = switch - (n - 1) * BAND_STAGGER - BAND_DUR / 2
        if not (ts <= t <= ts + (n - 1) * BAND_STAGGER + BAND_DUR):
            continue
        for k, col in enumerate(cols):
            p = ease_in_out_cubic(prog(t, ts + k * BAND_STAGGER, BAND_DUR))
            if 0 < p < 1:
                x = lerp(-bw, W + skew, p)
                d.polygon(P((x, 0), (x + bw, 0), (x + bw - skew, H), (x - skew, H)), fill=col)


# --------------------------------------------------------------------------- #
# Rendu d'une image
# --------------------------------------------------------------------------- #
def render_frame(i):
    t = i / FPS
    si = max(k for k, (s, _, _) in enumerate(TIMELINE) if t >= s)
    start, bg, func = TIMELINE[si]
    img = background(bg).copy()
    d = ImageDraw.Draw(img)
    func(img, d, t - start)
    draw_transitions(d, t)

    img = img.convert("RGB").resize((W, H), Image.LANCZOS)
    arr = np.asarray(img)
    f = min(prog(t, 0, 0.3), 1 - prog(t, DURATION - 0.8, 0.8))
    if f < 1:
        arr = (arr.astype(np.float32) * f).astype(np.uint8)
    return arr.tobytes()


def init_worker(cfg):
    CFG.clear()
    CFG.update(cfg)
    C.clear()
    C.update({k: hex2rgb(v) for k, v in cfg["couleurs"].items()})


# --------------------------------------------------------------------------- #
# Musique synthétisée (120 BPM, Do majeur : C - G - Am - F)
# --------------------------------------------------------------------------- #
def synth_music(path, sr=44100):
    n = int(DURATION * sr)
    out = np.zeros(n, np.float32)
    rng = np.random.default_rng(3)

    def add(sig, t0, gain=1.0):
        s = int(t0 * sr)
        if s >= n:
            return
        e = min(n, s + len(sig))
        out[s:e] += sig[:e - s] * gain

    def env(dur, decay):
        tt = np.arange(int(dur * sr)) / sr
        return tt, np.exp(-tt / decay)

    def midi(m):
        return 440.0 * 2 ** ((m - 69) / 12)

    def kick():
        tt, e = env(0.45, 0.12)
        f = 48 + 110 * np.exp(-tt / 0.03)
        return np.sin(2 * np.pi * np.cumsum(f) / sr) * e

    def clap():
        tt, e = env(0.25, 0.05)
        x = np.diff(rng.standard_normal(len(tt)).astype(np.float32), prepend=0)
        return x * e * 0.4

    def hat():
        tt, e = env(0.06, 0.012)
        x = rng.standard_normal(len(tt)).astype(np.float32)
        return np.diff(np.diff(x, prepend=0), prepend=0) * e * 0.1

    def tone(freq, dur, decay, harmonics=(1, 0.5, 0.25, 0.12), detune=0.0):
        tt, e = env(dur, decay)
        att = np.clip(tt / 0.005, 0, 1)
        sig = np.zeros_like(tt)
        for h, a in enumerate(harmonics, 1):
            sig += a * np.sin(2 * np.pi * freq * h * tt)
            if detune:
                sig += a * np.sin(2 * np.pi * freq * (1 + detune) * h * tt)
        return sig * e * att

    def pad(freqs, dur):
        tt = np.arange(int(dur * sr)) / sr
        e = np.clip(tt / 0.4, 0, 1) * np.clip((dur - tt) / 0.4, 0, 1)
        return sum(tone(f, dur, 99, (1, 0.3, 0.1), detune=0.004)[:len(tt)] for f in freqs) * e

    chords = [(60, 64, 67), (55, 59, 62), (57, 60, 64), (53, 57, 60)]   # C G Am F
    bar = 4 * BEAT
    music_end = DURATION - 2.5
    for b in range(int(math.ceil(music_end / bar))):
        ch = chords[b % 4]
        t0 = b * bar
        add(pad([midi(m) for m in ch], bar + 0.3), t0, 0.06)
        for k in range(16):                                   # arpège en doubles croches
            m = ch[(0, 1, 2, 1)[k % 4]] + 12 + (12 if k % 8 >= 4 else 0)
            add(tone(midi(m), 0.25, 0.07), t0 + k * BEAT / 4, 0.08 if b > 1 else 0.05)
        for k in range(8):                                    # basse en croches
            add(tone(midi(ch[0] - 24), 0.25, 0.12, (1, 0.6, 0.3, 0.2)), t0 + k * BEAT / 2, 0.2)

    drums_in = TIMELINE[1][0]
    for b in range(int(music_end / BEAT)):
        t0 = b * BEAT
        if t0 >= drums_in or b % 2 == 0:
            add(kick(), t0, 0.8)
        if t0 >= drums_in:
            if b % 2 == 1:
                add(clap(), t0, 0.6)
            add(hat(), t0 + BEAT / 2)
            if t0 >= TIMELINE[2][0]:
                add(hat(), t0 + BEAT / 4, 0.6)
                add(hat(), t0 + 3 * BEAT / 4, 0.6)

    add(kick(), 0.0, 1.1)
    tt, e = env(1.2, 0.35)
    add(rng.standard_normal(len(tt)).astype(np.float32) * e * 0.12, 0.0)

    def whoosh(dur=0.9):
        m = int(dur * sr)
        x = rng.standard_normal(m).astype(np.float32)
        tt = np.arange(m) / sr
        cutoff = 300 + 6000 * np.sin(np.pi * tt / dur) ** 2
        alpha = 1 - np.exp(-2 * np.pi * cutoff / sr)
        y = np.zeros(m, np.float32)
        acc = 0.0
        for i in range(m):
            acc += alpha[i] * (x[i] - acc)
            y[i] = acc
        return y * np.sin(np.pi * tt / dur) ** 2

    for s in SWITCHES:
        add(whoosh(), s - 0.6, 0.45)

    # Accord final tenu sur les logos
    add(kick(), music_end, 1.0)
    add(pad([midi(m) for m in (48, 60, 64, 67, 72)], 3.0), music_end, 0.08)

    tt = np.arange(n) / sr
    out *= np.clip((DURATION - tt) / 1.0, 0, 1)
    out = np.tanh(out * 1.2)
    out /= max(1e-6, np.abs(out).max()) / 0.8
    pcm = (out * 32767).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(np.stack([pcm, pcm], axis=1).tobytes())


# --------------------------------------------------------------------------- #
# Programme principal
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(ROOT, "config.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "output", "autopiec-groupama.mp4"))
    ap.add_argument("--preview", nargs="*", type=float, metavar="SEC",
                    help="exporte des images clés (instants en secondes, facultatif)")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    init_worker(cfg)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)

    if args.preview is not None:
        outdir = os.path.join(os.path.dirname(os.path.abspath(args.out)), "apercu")
        os.makedirs(outdir, exist_ok=True)
        secs = args.preview or [s + 3.4 if i else 3.4 for i, s in enumerate([0] + SWITCHES)]
        for sec in secs:
            buf = render_frame(int(sec * FPS))
            Image.frombytes("RGB", (W, H), buf).save(os.path.join(outdir, f"t{sec:05.2f}.png"))
        print("Aperçus écrits dans", outdir)
        return

    total = int(DURATION * FPS)
    wav = os.path.splitext(args.out)[0] + ".wav"
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
    if cfg.get("musique", True):
        synth_music(wav)
        cmd += ["-i", wav, "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-shortest", args.out]

    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    with Pool(args.jobs, initializer=init_worker, initargs=(cfg,)) as pool:
        for i, buf in enumerate(pool.imap(render_frame, range(total), chunksize=4)):
            ff.stdin.write(buf)
            if i % FPS == 0:
                print(f"\rRendu : {i * 100 // total:3d} %", end="", flush=True)
    ff.stdin.close()
    ret = ff.wait()
    if os.path.exists(wav):
        os.remove(wav)
    print("\rRendu : 100 %")
    if ret != 0:
        sys.exit("ffmpeg a échoué")
    print("Vidéo écrite :", args.out)


if __name__ == "__main__":
    main()
