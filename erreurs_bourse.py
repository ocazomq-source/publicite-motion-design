#!/usr/bin/env python3
"""Vidéo « 5 erreurs qui coûtent cher quand on débute en bourse » (O'MORGAN).

Reprend le carrousel : couverture, une scène par erreur (le chiffre clé sous forme
de graphique animé, puis « le bon réflexe »), récapitulatif et conclusion avec les
mentions légales. Même technique que parcours.py : images dessinées avec Pillow
(supersampling x2), musique et bruitages synthétisés avec numpy, assemblage ffmpeg.
Voix off féminine française synthétisée hors ligne (Piper, voix « siwis ») : la durée
des scènes s'adapte au texte lu et la musique s'efface sous la voix.
Tous les textes, la voix off et les couleurs sont dans config_erreurs.json.

Usage :
    python3 erreurs_bourse.py                    # -> output/5-erreurs-bourse.mp4
    python3 erreurs_bourse.py --format vertical  # version mobile 1080x1920 -> output/5-erreurs-bourse-mobile.mp4
    python3 erreurs_bourse.py --preview          # quelques images par scène dans output/apercu-erreurs/
    python3 erreurs_bourse.py --preview 12.5     # image à un instant précis
"""
import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import wave
from functools import lru_cache
from multiprocessing import Pool

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.abspath(__file__))
W, H = 1920, 1080
VERT = False         # True pour la version mobile 1080x1920
S = 2                # supersampling
FPS = 30
TR = 0.8             # durée d'une transition entre scènes (s)
NB = " "        # espace fine insécable (10 000, 5 %, ?)

CFG = {}
C = {}


# --------------------------------------------------------------------------- #
# Courbes
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


def ease_in_out_sine(x):
    return -(math.cos(math.pi * clamp(x)) - 1) / 2


def ease_out_expo(x):
    return 1.0 if x >= 1 else 1 - 2 ** (-10 * x)


def ease_in_out_expo(x):
    if x <= 0 or x >= 1:
        return clamp(x)
    return 2 ** (20 * x - 10) / 2 if x < 0.5 else (2 - 2 ** (-20 * x + 10)) / 2


def ease_out_back(x, s=1.70158):
    x -= 1
    return 1 + (s + 1) * x ** 3 + s * x ** 2


def spring(x, damping=6.0, freq=2.2):
    """Ressort amorti : 0 -> 1 avec un léger rebond."""
    if x <= 0:
        return 0.0
    if x >= 1.5:
        return 1.0
    return 1 - math.exp(-damping * x) * math.cos(2 * math.pi * freq * x)


# --------------------------------------------------------------------------- #
# Couleurs, typographie, polices
# --------------------------------------------------------------------------- #
def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (255,)


def mix(c1, c2, k):
    return tuple(int(lerp(a, b, clamp(k))) for a, b in zip(c1[:3], c2[:3])) + (255,)


def typo(s):
    """Typographie française : apostrophe courbe et espaces insécables (10 000 €, 5 %, ?)."""
    s = s.replace("'", "’")
    s = re.sub(r"(?<=\d) (?=\d{3}(?!\d))", NB, s)
    s = re.sub(r" (?=[%€$?!:;»])", NB, s)
    s = re.sub(r"(?<=[«≈>−]) ", NB, s)
    return s


def typo_cfg(v, key=None):
    if key == "couleurs":
        return v
    if isinstance(v, dict):
        return {k: typo_cfg(x, k) for k, x in v.items()}
    if isinstance(v, list):
        return [typo_cfg(x) for x in v]
    return typo(v) if isinstance(v, str) else v


def fmt_n(v):
    return f"{int(round(v)):,}".replace(",", NB)


FONT_DIRS = [os.path.join(ROOT, "fonts"), "/usr/share/fonts/opentype/inter",
             "/usr/share/fonts/truetype/dejavu"]
FONTS = {
    "titre": ["InterDisplay-SemiBold.otf", "Inter-SemiBold.otf", "DejaVuSans-Bold.ttf"],
    "titre_b": ["InterDisplay-Bold.otf", "Inter-Bold.otf", "DejaVuSans-Bold.ttf"],
    "texte": ["Inter-Regular.otf", "DejaVuSans.ttf"],
    "ui": ["Inter-Medium.otf", "Inter-Regular.otf", "DejaVuSans.ttf"],
    "ui_semi": ["Inter-SemiBold.otf", "Inter-Bold.otf", "DejaVuSans-Bold.ttf"],
    "ui_bold": ["Inter-Bold.otf", "DejaVuSans-Bold.ttf"],
}


@lru_cache(maxsize=None)
def font(kind, size):
    for name in FONTS[kind]:
        for d in FONT_DIRS:
            p = os.path.join(d, name)
            if os.path.exists(p):
                return ImageFont.truetype(p, int(size * S))
    return ImageFont.load_default(int(size * S))


def text_w(text, kind, size, tracking=0.0):
    return (font(kind, size).getlength(text) + tracking * size * S * max(0, len(text) - 1)) / S


def fit_size(text, kind, size, max_w, tracking=0.0):
    while size > 12 and text_w(text, kind, size, tracking) > max_w:
        size -= 1
    return size


PAD = 12 * S


@lru_cache(maxsize=2048)
def text_layer(text, kind, size, color, tracking=0.0):
    f = font(kind, size)
    asc, desc = f.getmetrics()
    tr = tracking * size * S
    w = f.getlength(text) + tr * max(0, len(text) - 1)
    im = Image.new("RGBA", (int(w) + 2 * PAD, asc + desc + 2 * PAD), (0, 0, 0, 0))
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
    a = layer.getchannel("A").point(lambda v: int(v * opacity))
    layer = layer.copy()
    layer.putalpha(a)
    return layer


def segments(line, accent):
    out = []
    for part in re.split(r"(\{[^}]*\})", line):
        if part.startswith("{"):
            out.append((part[1:-1], accent))
        elif part:
            out.append((part, None))
    return out


def markup_words(line):
    """« Suivre {la mode} » -> [(mot, accent?), ...]. Seule l'espace normale sépare les mots
    (les espaces insécables gardent « 10 000 € » sur une même ligne).
    Un mot à cheval sur deux couleurs devient (mot, ((morceau, accent?), ...))."""
    words = []
    prev_end = ""
    for text, acc in segments(line, True):
        glue = bool(words) and prev_end not in ("", " ") and text[:1] != " "
        for k, w in enumerate(x for x in text.split(" ") if x):
            if k == 0 and glue:
                prev_w, prev_acc = words.pop()
                parts = prev_acc if isinstance(prev_acc, tuple) else ((prev_w, prev_acc),)
                words.append((prev_w + w, parts + ((w, bool(acc)),)))
            else:
                words.append((w, bool(acc)))
        prev_end = text[-1:]
    return words


# --------------------------------------------------------------------------- #
# Toile : dessin en unités 1920x1080 avec un décalage (pour les calques de cartes)
# --------------------------------------------------------------------------- #
class Cv:
    def __init__(self, img, ox=0.0, oy=0.0):
        self.img = img
        self.d = ImageDraw.Draw(img)
        self.ox, self.oy = ox, oy

    def P(self, *pts):
        return [((x + self.ox) * S, (y + self.oy) * S) for x, y in pts]

    def rrect(self, x0, y0, x1, y1, r, fill=None, outline=None, width=1.0):
        if x1 - x0 < 0.5 or y1 - y0 < 0.5:
            return
        r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
        self.d.rounded_rectangle(self.P((x0, y0), (x1, y1)), radius=int(r * S), fill=fill,
                                 outline=outline, width=max(1, int(width * S)) if outline else 0)

    def disc(self, cx, cy, r, fill):
        if r > 0.3:
            self.d.ellipse(self.P((cx - r, cy - r), (cx + r, cy + r)), fill=fill)

    def ring(self, cx, cy, r, w, color):
        if r > 0.3 and w > 0.05:
            self.d.ellipse(self.P((cx - r, cy - r), (cx + r, cy + r)), outline=color,
                           width=max(1, int(w * S)))

    def line(self, pts, color, width):
        if len(pts) >= 2:
            self.d.line(self.P(*pts), fill=color, width=max(1, int(width * S)), joint="curve")

    def dashed(self, a, b, color, width, dash=10, gap=9):
        L = math.dist(a, b)
        if L < 1:
            return
        ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        s = 0.0
        while s < L:
            e = min(L, s + dash)
            self.line([(a[0] + ux * s, a[1] + uy * s), (a[0] + ux * e, a[1] + uy * e)], color, width)
            s += dash + gap

    def poly(self, pts, fill):
        if len(pts) >= 3:
            self.d.polygon(self.P(*pts), fill=fill)

    def text(self, s, kind, size, color, x, y, align="l", opacity=1.0, tracking=0.0):
        """(x, y) = point d'ancrage horizontal (gauche/centre/droite) et milieu vertical."""
        if opacity <= 0.004 or not s:
            return
        lay = text_layer(s, kind, size, color, tracking)
        px = (x + self.ox) * S
        if align == "l":
            lx = px - PAD
        elif align == "c":
            lx = px - lay.width / 2
        else:
            lx = px - lay.width + PAD
        composite(self.img, fade(lay, opacity), lx, (y + self.oy) * S - lay.height / 2)

    def paste(self, layer, cx, cy, opacity=1.0):
        if opacity > 0.004:
            composite(self.img, fade(layer, opacity), (cx + self.ox) * S - layer.width / 2,
                      (cy + self.oy) * S - layer.height / 2)

    def check(self, cx, cy, s, color, p=1.0, width=None):
        """Coche dessinée progressivement (p de 0 à 1)."""
        if p <= 0:
            return
        a, b, c = (cx - 0.5 * s, cy + 0.02 * s), (cx - 0.14 * s, cy + 0.38 * s), (cx + 0.52 * s, cy - 0.34 * s)
        l1, l2 = math.dist(a, b), math.dist(b, c)
        L = (l1 + l2) * p
        pts = [a]
        if L <= l1:
            k = L / l1
            pts.append((lerp(a[0], b[0], k), lerp(a[1], b[1], k)))
        else:
            k = (L - l1) / l2
            pts += [b, (lerp(b[0], c[0], k), lerp(b[1], c[1], k))]
        self.line(pts, color, width or max(1.5, s * 0.16))

    def arrow(self, cx, cy, s, color, width=None, direction="droite"):
        """Flèche simple (→ ou ↓) centrée en (cx, cy)."""
        w = width or max(1.5, s * 0.14)
        if direction == "bas":
            self.line([(cx, cy - 0.5 * s), (cx, cy + 0.45 * s)], color, w)
            self.line([(cx - 0.36 * s, cy + 0.1 * s), (cx, cy + 0.48 * s), (cx + 0.36 * s, cy + 0.1 * s)], color, w)
        else:
            self.line([(cx - 0.5 * s, cy), (cx + 0.45 * s, cy)], color, w)
            self.line([(cx + 0.1 * s, cy - 0.36 * s), (cx + 0.48 * s, cy), (cx + 0.1 * s, cy + 0.36 * s)], color, w)

    def chip(self, text, x, y, kind, size, fg, bg, align="l", pad=16, h=None, tracking=0.0):
        """Pastille arrondie ; retourne sa largeur."""
        w = text_w(text, kind, size, tracking) + 2 * pad
        h = h or size * 1.9
        x0 = x if align == "l" else (x - w / 2 if align == "c" else x - w)
        self.rrect(x0, y - h / 2, x0 + w, y + h / 2, h / 2, fill=bg)
        self.text(text, kind, size, fg, x0 + pad, y, tracking=tracking)
        return w


# --------------------------------------------------------------------------- #
# Cartes : ombre douce et entrée en perspective
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=16)
def shadow_layer(w, h, r):
    m = 90
    im = Image.new("RGBA", (int((w + 2 * m) * S), int((h + 2 * m) * S)), (0, 0, 0, 0))
    a = Image.new("L", im.size, 0)
    ImageDraw.Draw(a).rounded_rectangle(((m + 10) * S, (m + 34) * S, (m + w - 10) * S, (m + h + 18) * S),
                                        radius=int(r * S), fill=30)
    a = a.filter(ImageFilter.GaussianBlur(34 * S))
    b = Image.new("L", im.size, 0)
    ImageDraw.Draw(b).rounded_rectangle((m * S, (m + 3) * S, (m + w) * S, (m + h + 3) * S),
                                        radius=int(r * S), fill=20)
    b = b.filter(ImageFilter.GaussianBlur(4 * S))
    al = Image.fromarray(np.clip(np.asarray(a, np.int16) + np.asarray(b, np.int16), 0, 255).astype(np.uint8))
    navy = Image.new("RGBA", im.size, C["marine"][:3] + (255,))
    navy.putalpha(al)
    return navy


def new_card(w, h, r=28, fill=None):
    m = 4
    img = Image.new("RGBA", (int((w + 2 * m) * S), int((h + 2 * m) * S)), (0, 0, 0, 0))
    cv = Cv(img, m, m)
    cv.rrect(0, 0, w, h, r, fill=fill or C["carte"], outline=C["trait"], width=1.5)
    return img, cv


def find_coeffs(dst, src):
    A, B = [], []
    for (x, y), (u, v) in zip(dst, src):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        B += [u, v]
    return np.linalg.solve(np.array(A, float), np.array(B, float))


def tilt_layer(layer, tilt):
    if abs(tilt) < 0.3:
        return layer
    w, h = layer.size
    a = math.radians(tilt)
    inset = w * 0.5 * (1 - 1 / (1 + math.sin(a) * 0.55))
    drop = h * (1 - math.cos(a)) * 0.6
    dst = [(inset, drop), (w - inset, drop), (w, h), (0, h)]
    src = [(0, 0), (w, 0), (w, h), (0, h)]
    return layer.transform((w, h), Image.PERSPECTIVE, find_coeffs(dst, src), Image.BICUBIC)


def place_card(world, layer, cx, cy, t, w, h, r=28, dur=1.0, from_dy=90, tilt_from=22.0):
    """Entrée « démo produit » : la carte monte en se redressant (perspective + ressort)."""
    p = prog(t, 0, dur)
    if p <= 0:
        return
    e = ease_out_expo(p)
    sp = spring(p * 1.3, 7.0, 1.6)
    op = clamp(p * 3)
    sc = lerp(0.92, 1.0, e)
    dy = (1 - sp) * from_dy
    sh = shadow_layer(int(w), int(h), r)
    if abs(sc - 1) > 1e-3:
        sh = sh.resize((max(1, int(sh.width * sc)), max(1, int(sh.height * sc))), Image.BILINEAR)
    composite(world, fade(sh, op * e), cx * S - sh.width / 2, (cy + dy + 10 * (1 - e)) * S - sh.height / 2)
    lay = tilt_layer(layer, (1 - e) * tilt_from)
    if abs(sc - 1) > 1e-3:
        lay = lay.resize((max(1, int(lay.width * sc)), max(1, int(lay.height * sc))), Image.BICUBIC)
    composite(world, fade(lay, op), cx * S - lay.width / 2, (cy + dy) * S - lay.height / 2)


# --------------------------------------------------------------------------- #
# Texte : mots qui sortent du flou
# --------------------------------------------------------------------------- #
def wrap_words(words, kind, size, max_w):
    space = font(kind, size).getlength(" ") / S
    lines, cur, cur_w = [], [], 0.0
    for w, acc in words:
        ww = text_w(w, kind, size)
        if cur and cur_w + space + ww > max_w:
            lines.append(cur)
            cur, cur_w = [], 0.0
        cur_w += (space if cur else 0) + ww
        cur.append((w, acc))
    if cur:
        lines.append(cur)
    return lines


@lru_cache(maxsize=256)
def word_layer(parts, kind, size, color, accent):
    lays = [text_layer(t, kind, size, accent if a else color) for t, a in parts]
    w = sum(l.width - 2 * PAD for l in lays) + 2 * PAD
    im = Image.new("RGBA", (w, lays[0].height), (0, 0, 0, 0))
    x = 0
    for l in lays:
        im.alpha_composite(l, (x, 0))
        x += l.width - 2 * PAD
    return im


def layout(lines, kind, size, wrap_w=None):
    """Chaque ligne de la config est repliée séparément (les retours à la ligne voulus restent)."""
    if isinstance(lines, str):
        lines = [lines]
    if wrap_w is None:
        return [markup_words(ln) for ln in lines]
    return [sub for ln in lines for sub in wrap_words(markup_words(ln), kind, size, wrap_w)]


def text_height(lines, kind, size, wrap_w=None, lh=1.12):
    return len(layout(lines, kind, size, wrap_w)) * size * lh


def blur_words(world, lines, kind, size, x, y_top, t, color=None, accent=None, align="l",
               lh=1.12, stagger=0.06, dur=0.7, opacity=1.0, wrap_w=None):
    """Affiche des lignes balisées mot par mot, chaque mot sortant du flou ; renvoie le bas du bloc."""
    color = color or C["marine"]
    accent = accent or C["or"]
    rows = layout(lines, kind, size, wrap_w)
    line_h = size * lh
    space = font(kind, size).getlength(" ") / S
    i = 0
    for li, ln in enumerate(rows):
        widths = [text_w(w, kind, size) for w, _ in ln]
        total = sum(widths) + space * (len(ln) - 1)
        cx = x if align == "l" else x - total / 2
        cy = y_top + li * line_h + line_h / 2
        for (w, acc), ww in zip(ln, widths):
            p = prog(t, i * stagger, dur)
            if p > 0 and opacity > 0.004:
                e = ease_out_expo(p)
                if isinstance(acc, tuple):
                    lay = word_layer(acc, kind, size, color, accent)
                else:
                    lay = text_layer(w, kind, size, accent if acc else color)
                blur = (1 - ease_out_cubic(p)) * 12 * S
                if blur > 0.4:
                    lay = lay.filter(ImageFilter.GaussianBlur(blur))
                composite(world, fade(lay, clamp(p * 2.2) * opacity), cx * S - PAD,
                          (cy + (1 - e) * 24) * S - lay.height / 2)
            cx += ww + space
            i += 1
    return y_top + len(rows) * line_h


def words_count(lines):
    return sum(len(markup_words(ln)) for ln in ([lines] if isinstance(lines, str) else lines))


# --------------------------------------------------------------------------- #
# Fond
# --------------------------------------------------------------------------- #
_BG = None


def background():
    """Fond ivoire avec une grille de points très discrète, plus présente vers les bords."""
    global _BG
    if _BG is None:
        img = Image.new("RGBA", (W * S, H * S), C["fond"])
        d = ImageDraw.Draw(img)
        step = 40
        for gy in range(0, H + step, step):
            for gx in range(0, W + step, step):
                dx, dy = (gx - W / 2) / (W / 2), (gy - H / 2) / (H / 2)
                k = clamp((math.sqrt(dx * dx * 0.8 + dy * dy) - 0.45) / 0.7)
                if k > 0:
                    col = mix(C["fond"], C["trait"], k * 0.9)
                    d.ellipse((gx * S - 2.4, gy * S - 2.4, gx * S + 2.4, gy * S + 2.4), fill=col)
        _BG = img
    return _BG


# --------------------------------------------------------------------------- #
# Graphiques
# --------------------------------------------------------------------------- #
def monotone(pts, n=260):
    """Interpolation cubique monotone (Fritsch-Carlson) : passe par les points, sans dépassement."""
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    m = len(xs)
    d = [(ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]) for i in range(m - 1)]
    tg = [d[0]] + [0.0 if d[i - 1] * d[i] <= 0 else (d[i - 1] + d[i]) / 2 for i in range(1, m - 1)] + [d[-1]]
    for i in range(m - 1):
        if d[i] == 0:
            tg[i] = tg[i + 1] = 0.0
            continue
        a, b = tg[i] / d[i], tg[i + 1] / d[i]
        s = a * a + b * b
        if s > 9:
            k = 3 / math.sqrt(s)
            tg[i], tg[i + 1] = k * a * d[i], k * b * d[i]
    out = []
    span = xs[-1] - xs[0]
    for i in range(m - 1):
        h = xs[i + 1] - xs[i]
        steps = max(3, int(n * h / span))
        for k in range(steps):
            s = k / steps
            h00, h10 = 2 * s ** 3 - 3 * s ** 2 + 1, s ** 3 - 2 * s ** 2 + s
            h01, h11 = -2 * s ** 3 + 3 * s ** 2, s ** 3 - s ** 2
            out.append((xs[i] + s * h, h00 * ys[i] + h10 * h * tg[i] + h01 * ys[i + 1] + h11 * h * tg[i + 1]))
    out.append(tuple(pts[-1]))
    return out


def value_at(path, x):
    if x <= path[0][0]:
        return path[0][1]
    for (x0, y0), (x1, y1) in zip(path, path[1:]):
        if x <= x1:
            return lerp(y0, y1, (x - x0) / (x1 - x0))
    return path[-1][1]


def clip_path(path, a, b):
    """Portion de la courbe entre les abscisses a et b (bornes interpolées)."""
    if b <= a:
        return []
    out = [(a, value_at(path, a))]
    out += [p for p in path if a < p[0] < b]
    out.append((b, value_at(path, b)))
    return out


class Plot:
    """Zone de tracé dans une carte : conversion données -> pixels et éléments communs."""

    def __init__(self, cv, box, xr, yr):
        self.cv = cv
        self.x0, self.y0, self.x1, self.y1 = box
        self.xr, self.yr = xr, yr

    def X(self, x):
        return lerp(self.x0, self.x1, (x - self.xr[0]) / (self.xr[1] - self.xr[0]))

    def Y(self, y):
        return lerp(self.y1, self.y0, (y - self.yr[0]) / (self.yr[1] - self.yr[0]))

    def pt(self, x, y):
        return self.X(x), self.Y(y)

    def grid(self, n=4):
        for k in range(n + 1):
            y = lerp(self.y0, self.y1, k / n)
            self.cv.line([(self.x0 - 10, y), (self.x1 + 10, y)], C["trait"] if k == n else mix(C["carte"], C["trait"], 0.55), 1.2)

    def curve(self, path, a, b, color, width=5.0, area=True):
        part = clip_path(path, a, b)
        if len(part) < 2:
            return
        pts = [self.pt(x, y) for x, y in part]
        if area:
            self.cv.poly(pts + [(pts[-1][0], self.y1), (pts[0][0], self.y1)], mix(C["carte"], color, 0.08))
        self.cv.line(pts, color, width)

    def axis(self, labels, op=1.0):
        """labels : [(x, texte)] ; le premier est calé à gauche, le dernier à droite."""
        for k, (x, s) in enumerate(labels):
            al = "l" if k == 0 and x <= self.xr[0] else ("r" if x >= self.xr[1] else "c")
            px = self.X(x)
            if al == "c":
                self.cv.line([(px, self.y1), (px, self.y1 + 8)], C["trait"], 1.5)
            self.cv.text(s, "ui", 18, C["gris"], px, self.y1 + 30, align=al, opacity=op)

    def head(self, x, y, color):
        px, py = self.pt(x, y)
        self.cv.disc(px, py, 13, mix(C["carte"], color, 0.22))
        self.cv.disc(px, py, 7, color)


def marker(cv, plot, x, y, value, sub, color, p, side="haut", align="c", dy=0):
    """Point + étiquette (valeur en gras, précision dessous) qui apparaît avec un rebond."""
    if p <= 0:
        return
    px, py = plot.pt(x, y)
    q = ease_out_back(clamp(p * 1.4), 2.2)
    cv.disc(px, py, 15 * q, mix(C["carte"], color, 0.22))
    cv.disc(px, py, 9 * q, C["carte"])
    cv.disc(px, py, 6 * q, color)
    op = clamp(p * 2.5)
    vw = text_w(value, "ui_bold", 25)
    sw = text_w(sub, "ui", 17) if sub else 0
    bw, bh = max(vw, sw) + 28, (66 if sub else 44)
    bx = {"c": px - bw / 2, "l": px - 22, "r": px - bw + 22}[align]
    bx = clamp(bx, 14, cv.img.width / S - 8 - 14 - bw)
    by = py - 26 - bh + dy if side == "haut" else py + 26 + dy
    by += (1 - ease_out_cubic(clamp(p * 1.6))) * (12 if side == "haut" else -12)
    line, col, gr = (mix(C["carte"], c, op) for c in (C["trait"], color, C["gris"]))
    cv.rrect(bx, by, bx + bw, by + bh, 12, fill=C["carte"], outline=line, width=1.5)
    cv.text(value, "ui_bold", 25, col, bx + 14, by + 23)
    if sub:
        cv.text(sub, "ui", 17, gr, bx + 14, by + 49)


@lru_cache(maxsize=32)
def pill_layer(text, size, fg, bg):
    tw = text_w(text, "titre_b", size)
    w, h = tw + size * 1.0, size * 1.6
    im = Image.new("RGBA", (int((w + 4) * S), int((h + 4) * S)), (0, 0, 0, 0))
    cv = Cv(im, 2, 2)
    cv.rrect(0, 0, w, h, h / 2, fill=bg)
    cv.text(text, "titre_b", size, fg, w / 2, h / 2, align="c")
    return im


def badge(cv, text, x, y, p, color, size=34):
    if p <= 0:
        return
    lay = pill_layer(text, size, C["carte"], color)
    k = max(0.01, ease_out_back(clamp(p), 2.6))
    if abs(k - 1) > 1e-3:
        lay = lay.resize((max(1, int(lay.width * k)), max(1, int(lay.height * k))), Image.BICUBIC)
    cv.paste(lay, x, y, opacity=clamp(p * 3))


def card_header(cv, w, g):
    cv.text(g.get("titre", ""), "ui_bold", fit_size(g.get("titre", ""), "ui_bold", 18, w * 0.62, 0.08),
            C["marine"], 36, 46, tracking=0.08)
    if g.get("note"):
        cv.text(g["note"], "ui", 16, C["gris_clair"], w - 36, 46, align="r")


def card_footer(cv, w, h, label, value, color, op=1.0):
    cv.line([(36, h - 118), (w - 36, h - 118)], C["trait"], 1.5)
    cv.text(label, "ui", 21, C["gris"], 40, h - 60)
    if value:
        size = fit_size(value, "titre_b", 52, w * 0.42)
        cv.text(value, "titre_b", size, color, w - 40, h - 62, align="r", opacity=op)


# Chaque graphique : t = temps local depuis le début du tracé ; la courbe se dessine
# pendant CHART_DUR secondes (progression lissée), puis les dernières étiquettes arrivent.
CHART_T0 = 1.7
CHART_DUR = 4.4


def reveal(t):
    return ease_in_out_sine(prog(t, 0, CHART_DUR))


def t_at(frac):
    """Instant (local au tracé) où la courbe atteint la fraction frac de sa longueur."""
    return CHART_DUR * math.acos(clamp(1 - 2 * frac, -1, 1)) / math.pi


def plot_box(w, h):
    return (64, 150, w - 64, h - 196)


ORACLE = [(0.0, 196), (0.1, 232), (0.18, 222), (0.27, 262), (0.33, 248), (0.4, 345), (0.47, 300),
          (0.53, 318), (0.62, 252), (0.7, 228), (0.78, 178), (0.85, 190), (0.93, 138), (1.0, 115)]


def chart_oracle(cv, w, h, t, g):
    card_header(cv, w, g)
    pl = Plot(cv, plot_box(w, h), (0, 1), (60, 400))
    pl.grid()
    path = monotone(ORACLE)
    xc = reveal(t)
    peak = 0.4
    if xc > peak:
        pl.cv.dashed(pl.pt(peak, 345), (pl.x1 + 10, pl.Y(345)), mix(C["carte"], C["gris_clair"], 0.8), 1.6)
    pl.curve(path, 0, min(xc, peak), C["marine"])
    pl.curve(path, peak, xc, C["rouge"])
    if 0 < xc < 1:
        pl.head(xc, value_at(path, xc), C["marine"] if xc <= peak else C["rouge"])
    pl.axis([(peak, g["axe"][0]), (1.0, g["axe"][1])], op=clamp((xc - peak) / 0.1))
    marker(cv, pl, peak, 345, g["sommet"], "", C["marine"], prog(t, t_at(peak), 0.5), "haut")
    p_end = prog(t, CHART_DUR, 0.5)
    marker(cv, pl, 1.0, 115, g["creux"], "", C["rouge"], p_end, "haut", align="r")
    badge(cv, g["badge"], pl.X(0.74), pl.Y(330), prog(t, CHART_DUR + 0.3, 0.5), C["rouge"])
    cpt = g["compteur"]
    if xc <= peak:
        val, col = fmt_n(cpt["debut"]), C["marine"]
    elif xc < 1:
        k = (345 - value_at(path, xc)) / (345 - 115)
        val, col = fmt_n(round(lerp(cpt["debut"], cpt["fin"], clamp(k)) / 100) * 100), C["rouge"]
    else:
        val, col = cpt["texte_fin"], C["rouge"]
    card_footer(cv, w, h, cpt["label"], val, col)


ATOS = [(0.0, 107), (0.08, 96), (0.16, 84), (0.24, 89), (0.33, 72), (0.42, 58), (0.5, 40),
        (0.58, 30), (0.66, 14), (0.74, 6), (0.82, 2.2), (0.9, 0.6), (1.0, 0.005)]


def chart_atos(cv, w, h, t, g):
    card_header(cv, w, g)
    pl = Plot(cv, plot_box(w, h), (0, 1), (0, 125))
    pl.grid()
    path = monotone(ATOS)
    xc = reveal(t)
    p0 = prog(t, -0.2, 0.5)
    pl.curve(path, 0, xc, C["rouge"])
    if 0 < xc < 1:
        pl.head(xc, value_at(path, xc), C["rouge"])
    pl.axis([(0, g["axe"][0]), (1.0, g["axe"][1])], op=p0)
    marker(cv, pl, 0, 107, g["depart"], "", C["marine"], p0, "bas", align="l", dy=4)
    marker(cv, pl, 1.0, 0.005, g["arrivee"], "", C["rouge"], prog(t, CHART_DUR, 0.5), "haut", align="r")
    cpt = g["compteur"]
    if xc < 1:
        v = cpt["debut"] * value_at(path, xc) / 107
        val = fmt_n(round(v / 10) * 10 if v > 100 else v) + NB + "€"
        col = C["marine"] if xc < 0.05 else C["rouge"]
    else:
        val, col = cpt["texte_fin"], C["rouge"]
    card_footer(cv, w, h, cpt["label"], val, col)


# S&P 500 en « 10 000 investis » : x = jours depuis le 19 février 2020
COVID_DROP = [(0, 10000), (6, 9650), (13, 8900), (19, 7550), (23, 8100), (28, 7300), (33, 6600)]
COVID_UP = [(33, 6600), (45, 8250), (60, 8500), (85, 9000), (115, 9400), (150, 9550), (181, 10000),
            (230, 10150), (260, 10600), (320, 11300), (380, 11600), (420, 12400), (480, 12900),
            (530, 13100), (575, 12800), (628, 13900)]


def chart_covid(cv, w, h, t, g):
    card_header(cv, w, g)
    x0, y0, x1, y1 = plot_box(w, h)
    pl = Plot(cv, (x0, y0 + 10, x1, y1), (0, 628), (3800, 15000))
    pl.grid()
    drop, up = monotone(COVID_DROP, 60), monotone(COVID_UP, 300)
    xc = reveal(t) * 628
    pl.curve(drop, 0, min(xc, 33), C["marine"])
    if xc > 33:
        pl.curve(up, 33, xc, C["vert"])
        cv.dashed(pl.pt(33, 6600), pl.pt(xc, 6600), C["rouge"], 4.5, dash=14, gap=10)
    lvl = mix(C["carte"], C["gris_clair"], 0.9)
    cv.dashed((pl.x0 - 10, pl.Y(10000)), (pl.x1 + 10, pl.Y(10000)), lvl, 1.6)
    cv.text(g["avant_crise"], "ui", 16, C["gris"], pl.X(400), pl.Y(10000) + 20, align="c",
            opacity=prog(t, 0, 0.5))
    if 0 < xc < 628:
        if xc > 33:
            pl.head(xc, 6600, C["rouge"])
            pl.head(xc, value_at(up, xc), C["vert"])
        else:
            pl.head(xc, value_at(drop, xc), C["marine"])
    pl.axis([(0, g["axe"][0]), (181, g["axe"][1]), (628, g["axe"][2])], op=prog(t, 0, 0.5))
    badge(cv, g["creux"], pl.X(33) + 150, pl.Y(6600) + 56, prog(t, t_at(33 / 628) + 0.15, 0.5), C["rouge"], 24)
    marker(cv, pl, 181, 10000, g["retour"], "", C["vert"], prog(t, t_at(181 / 628), 0.5), "haut")
    p_end = prog(t, CHART_DUR, 0.5)
    marker(cv, pl, 628, value_at(up, 628), g["reste"]["valeur"], "", C["vert"], p_end, "haut", align="r")
    marker(cv, pl, 628, 6600, g["vendu"]["valeur"], "", C["rouge"], p_end, "haut", align="r")
    # pied : les deux issues côte à côte
    cv.line([(36, h - 118), (w - 36, h - 118)], C["trait"], 1.5)
    half = (w - 72) / 2
    for k, (key, col) in enumerate((("vendu", C["rouge"]), ("reste", C["vert"]))):
        o = g[key]
        x = 40 + k * (half + 10)
        if k:
            cv.line([(x - 18, h - 96), (x - 18, h - 24)], C["trait"], 1.5)
        cv.text(o["label"], "ui_semi", fit_size(o["label"], "ui_semi", 19, half - 20), col, x, h - 88)
        if xc > 33:
            if k == 0:
                val = o["valeur"]
            else:
                val = o["valeur"] if xc >= 628 else fmt_n(round(value_at(up, xc) / 100) * 100)
        else:
            val = fmt_n(round(value_at(drop, xc) / 100) * 100)
        cv.text(val, "titre_b", 36, col, x, h - 48)
        sw = text_w(val, "titre_b", 36)
        cv.text(o["sous"], "ui", 16, C["gris"], x + sw + 14, h - 46, opacity=p_end)


def chart_frais(cv, w, h, t, g):
    card_header(cv, w, g)
    n = g["annees"]
    vals = [[g["capital"] * (1 + (g["rendement"] - f) / 100) ** y for y in range(n + 1)] for f in g["frais"]]
    hi = max(max(v) for v in vals)
    pl = Plot(cv, plot_box(w, h), (0, n), (g["capital"] * 0.85, hi * 1.12))
    pl.grid()
    paths = [[(y, v) for y, v in enumerate(vs)] for vs in vals]
    paths = [monotone(p, 200) for p in paths]
    xc = reveal(t) * n
    cols = [C["vert"], C["rouge"]]
    # écart entre les deux courbes
    if xc > 0:
        top = [pl.pt(x, y) for x, y in clip_path(paths[0], 0, xc)]
        bot = [pl.pt(x, y) for x, y in clip_path(paths[1], 0, xc)]
        cv.poly(top + bot[::-1], mix(C["carte"], C["rouge"], 0.10))
    for p, col in zip(paths, cols):
        pl.curve(p, 0, xc, col, area=False)
        if 0 < xc < n:
            pl.head(xc, value_at(p, xc), col)
    pl.axis([(0, g["axe"][0]), (n / 2, g["axe"][1]), (n, g["axe"][2])], op=prog(t, 0, 0.5))
    p_end = prog(t, CHART_DUR, 0.5)
    marker(cv, pl, n, vals[0][-1], g["valeurs"][0], g["legendes"][0], cols[0], p_end, "haut", align="r")
    marker(cv, pl, n, vals[1][-1], g["valeurs"][1], g["legendes"][1], cols[1],
           prog(t, CHART_DUR + 0.25, 0.5), "bas", align="r")
    cpt = g["compteur"]
    gap = value_at(paths[0], xc) - value_at(paths[1], xc)
    val = cpt["texte_fin"] if xc >= n else "−" + fmt_n(round(gap / 10) * 10) + NB + "€"
    card_footer(cv, w, h, cpt["label"], val, C["rouge"])


NASDAQ = [(1998.5, 1750), (1999.0, 2250), (1999.5, 2600), (1999.9, 3900), (2000.19, 5048),
          (2000.45, 3800), (2000.8, 2700), (2001.25, 1850), (2001.6, 2050), (2001.85, 1650),
          (2002.2, 1900), (2002.77, 1114), (2003.5, 1750), (2004.1, 2050), (2005.3, 2000),
          (2006.4, 2350), (2007.8, 2800), (2008.5, 2250), (2009.2, 1300), (2010.0, 2250),
          (2011.3, 2800), (2011.8, 2500), (2012.7, 3050), (2013.6, 3700), (2014.5, 4400),
          (2015.31, 5048)]


def chart_nasdaq(cv, w, h, t, g):
    card_header(cv, w, g)
    pl = Plot(cv, plot_box(w, h), (1998.5, 2015.31), (-900, 6200))
    pl.grid()
    path = monotone(NASDAQ, 400)
    x_a, x_b = pl.xr
    xc = lerp(x_a, x_b, reveal(t))
    peak, low = 2000.19, 2002.77
    if xc > peak:
        cv.dashed(pl.pt(peak, 5048), pl.pt(min(xc, x_b), 5048), mix(C["carte"], C["or"], 0.7), 2.2)
    pl.curve(path, x_a, min(xc, peak), C["marine"])
    pl.curve(path, peak, min(xc, low), C["rouge"])
    pl.curve(path, low, xc, C["marine"])
    if x_a < xc < x_b:
        pl.head(xc, value_at(path, xc), C["rouge"] if peak < xc <= low else C["marine"])
    pl.axis([(2000, "2000"), (2005, "2005"), (2010, "2010"), (2015.31, g["retour"])], op=prog(t, 0, 0.5))
    f = lambda x: (x - x_a) / (x_b - x_a)
    marker(cv, pl, peak, 5048, g["sommet"], g["sommet_date"], C["marine"], prog(t, t_at(f(peak)), 0.5),
           "haut", align="l")
    marker(cv, pl, low, 1114, g["creux"], g["creux_date"], C["rouge"], prog(t, t_at(f(low)), 0.5),
           "bas", align="l", dy=-4)
    badge(cv, g["badge"], pl.X(2004.9), pl.Y(4300), prog(t, t_at(f(low)) + 0.3, 0.5), C["rouge"])
    p_end = prog(t, CHART_DUR, 0.5)
    if p_end > 0:
        mid = pl.X((peak + x_b) / 2)
        cv.chip(g["compteur"]["texte_fin"], mid, pl.Y(5048) - 26, "ui_bold", 20,
                C["carte"], C["or"], align="c")
    cpt = g["compteur"]
    yrs = clamp(xc - peak, 0, 15)
    val = cpt["texte_fin"] if xc >= x_b else (f"{int(yrs)}{NB}an" + ("s" if yrs >= 2 else "") if xc > peak else "")
    card_footer(cv, w, h, cpt["label"], val, C["or"])


# graphique, puis sons [(fraction de la courbe ou « fin », type)]
CHARTS = [
    (chart_oracle, [(0.4, "tic"), (0.47, "chute"), ("fin", "pop")]),
    (chart_atos, [(0.15, "chute"), ("fin", "tic")]),
    (chart_covid, [(0.02, "chute"), (33 / 628, "pop"), (181 / 628, "tic"), ("fin", "succes")]),
    (chart_frais, [(0.5, "tic"), ("fin", "pop")]),
    (chart_nasdaq, [(0.1, "tic"), (0.15, "chute"), (0.25, "pop"), ("fin", "tic")]),
]


# --------------------------------------------------------------------------- #
# Mise en page (horizontal / vertical)
# --------------------------------------------------------------------------- #
L = {}


def setup_format(fmt):
    global W, H, VERT, _BG
    VERT = fmt == "vertical"
    _BG = None
    if VERT:
        W, H = 1080, 1920
        L.update(mx=70, kick_y=150, title_y=200, title_size=78, title_w=940, col_w=940,
                 card=(540, 735, 940, 640), text_y=1110, body=33, res=36)
    else:
        W, H = 1920, 1080
        L.update(mx=110, kick_y=150, title_y=192, title_size=76, title_w=780, col_w=780,
                 card=(1395, 548, 800, 740), text_y=None, body=30, res=33)


def ctitle(world, lines, size, y, t, wrap=None, lh=1.12, **kw):
    return blur_words(world, lines, kw.pop("kind", "titre"), size, W / 2, y, t, align="c",
                      wrap_w=wrap or W - 160, lh=lh, **kw)


def progress_bar(cv, x, y, idx, t):
    for k in range(5):
        x0 = x + k * 44
        cv.rrect(x0, y - 3, x0 + 34, y + 3, 3, fill=C["trait"])
        if k < idx:
            cv.rrect(x0, y - 3, x0 + 34, y + 3, 3, fill=mix(C["fond"], C["marine"], 0.55))
        elif k == idx:
            p = ease_out_cubic(prog(t, 0.3, 0.6))
            cv.rrect(x0, y - 3, x0 + 34 * p, y + 3, 3, fill=C["rouge"])


def result_block(world, text, x, y, t, wrap, size, opacity=1.0):
    """Ligne « 👉 » : pastille avec flèche puis texte en gras (accents en rouge)."""
    cv = Cv(world)
    p = prog(t, 0, 0.5)
    if p > 0 and opacity > 0.004:
        k = ease_out_back(p, 2.2)
        cy = y + size * 0.62
        cv.disc(x + 20, cy, 20 * k, mix(C["fond"], C["or"], opacity))
        cv.arrow(x + 20, cy, 18 * k, mix(C["or"], C["carte"], opacity), width=2.6)
    return blur_words(world, [text], "ui_semi", size, x + 58, y, t, accent=C["rouge"], lh=1.32,
                      stagger=0.035, wrap_w=wrap - 58, opacity=opacity)


def reflexe_block(world, text, x, y, t, w, size, measure=False):
    """Carte « Le bon réflexe » : fond vert pâle, coche animée, texte."""
    inner = w - 2 * 34
    th = text_height([text], "ui", size, inner, 1.36)
    hgt = 34 + 30 + 24 + th + 30
    if measure:
        return hgt
    p = prog(t, 0, 0.7)
    if p <= 0:
        return hgt
    e = ease_out_expo(p)
    cv = Cv(world)
    oy = (1 - e) * 40
    cv.rrect(x, y + oy, x + w, y + hgt + oy, 26, fill=mix(C["fond"], C["vert_clair"], clamp(p * 2.5)))
    cx, cy = x + 34 + 18, y + oy + 34 + 15
    q = prog(t, 0.2, 0.6)
    cv.disc(cx, cy, 20 * ease_out_back(clamp(q * 1.6), 2.4), C["vert"])
    cv.check(cx, cy, 20, C["carte"], ease_out_cubic(prog(q, 0.3, 0.7)), width=3.4)
    cv.text(CFG["reflexe_titre"], "ui_bold", 20, C["vert"], cx + 36, cy, opacity=prog(t, 0.25, 0.4),
            tracking=0.14)
    blur_words(world, [text], "ui", size, x + 34, y + oy + 34 + 30 + 24, t - 0.45, color=C["marine"],
               accent=C["vert"], lh=1.36, stagger=0.03, wrap_w=inner)
    return hgt


# --------------------------------------------------------------------------- #
# Scènes
# --------------------------------------------------------------------------- #
COVER_LINE = [(0.0, 0.18), (0.12, 0.22), (0.2, 0.15), (0.3, 0.3), (0.38, 0.12), (0.45, 0.28),
              (0.55, 0.42), (0.62, 0.36), (0.7, 0.58), (0.78, 0.5), (0.86, 0.72), (1.0, 0.86)]


def scene_couverture(world, t, D):
    cfg = CFG["couverture"]
    cv = Cv(world)
    # courbe descendante en arrière-plan
    path = monotone(COVER_LINE, 200)
    xc = ease_in_out_sine(prog(t, 0.0, 3.2))
    top, bot = (0.52, 0.94) if not VERT else (0.6, 0.93)
    if xc > 0:
        pts = [(x * W, lerp(H * top, H * bot, y)) for x, y in clip_path(path, 0, xc)]
        cv.poly(pts + [(pts[-1][0], H + 10), (pts[0][0], H + 10)], mix(C["fond"], C["rouge"], 0.05))
        cv.line(pts, mix(C["fond"], C["rouge"], 0.32), 5)
        if xc < 1:
            cv.disc(pts[-1][0], pts[-1][1], 9, mix(C["fond"], C["rouge"], 0.6))
    y = (340 if not VERT else 560)
    p = prog(t, 0.1, 0.6)
    if p > 0:
        w = text_w(cfg["surtitre"], "ui_bold", 22, 0.18) + 40
        k = ease_out_back(p, 1.8)
        cv.rrect(W / 2 - w / 2 * k, y - 22, W / 2 + w / 2 * k, y + 22, 22, fill=C["marine"])
        cv.text(cfg["surtitre"], "ui_bold", 22, C["fond"], W / 2, y, align="c", opacity=prog(t, 0.3, 0.3),
                tracking=0.18)
    size = 118 if not VERT else 104
    bottom = ctitle(world, cfg["lignes"], size, y + 50, t - 0.4, wrap=W - 140, lh=1.08, kind="titre_b",
                    accent=C["rouge"], stagger=0.07)
    q = prog(t, 1.9, 0.7)
    if q > 0:
        s = 32 if not VERT else 34
        tw = text_w(cfg["accroche"], "ui", s)
        if tw > W - 200:
            s = fit_size(cfg["accroche"], "ui", s, W - 200)
            tw = text_w(cfg["accroche"], "ui", s)
        e = ease_out_expo(q)
        yy = bottom + 70 + (1 - e) * 20
        w = tw + 110
        cv.rrect(W / 2 - w / 2, yy - 38, W / 2 + w / 2, yy + 38, 38,
                 fill=mix(C["fond"], C["carte"], clamp(q * 2)), outline=mix(C["fond"], C["trait"], q), width=1.5)
        cv.disc(W / 2 - w / 2 + 40, yy, 14, mix(C["fond"], C["or"], clamp(q * 2)))
        cv.text("!", "ui_bold", 20, C["carte"], W / 2 - w / 2 + 40, yy, align="c", opacity=clamp(q * 2))
        cv.text(cfg["accroche"], "ui", s, C["marine"], W / 2 - w / 2 + 70, yy, opacity=clamp(q * 2))
    return 1.0 + 0.03 * ease_in_out_cubic(prog(t, 0, D)), 0, 0


def make_erreur(idx):
    def scene(world, t, D):
        cfg = CFG["erreurs"][idx]
        g = cfg["graphique"]
        cv = Cv(world)
        mx = L["mx"]
        # grand numéro en filigrane
        num = text_layer(str(idx + 1), "titre_b", 640 if not VERT else 560, mix(C["fond"], C["marine"], 0.028))
        if VERT:
            cv.paste(num, W - 200, 330, opacity=prog(t, 0, 0.8))
        else:
            cv.paste(num, 760, 760, opacity=prog(t, 0, 0.8))
        # numéro de l'erreur + progression
        p = prog(t, 0.05, 0.5)
        if p > 0:
            cw = cv.chip(cfg["numero"], mx, L["kick_y"], "ui_bold", 19, mix(C["fond"], C["rouge"], p),
                         mix(C["fond"], C["rouge_clair"], p), tracking=0.12)
            progress_bar(cv, mx + cw + 26, L["kick_y"], idx, t)
        bottom = blur_words(world, [cfg["titre"]], "titre", L["title_size"], mx, L["title_y"], t - 0.2,
                            accent=C["rouge"], stagger=0.07, wrap_w=L["title_w"], lh=1.08)
        # graphique
        ccx, ccy, cw_, ch_ = L["card"]
        t_card = t - 0.9
        if t_card > 0:
            lay, ccv = new_card(cw_, ch_)
            CHARTS[idx][0](ccv, cw_, ch_, t - CHART_T0, g)
            place_card(world, lay, ccx, ccy, t_card, cw_, ch_)
        # textes : contexte + résultat, puis le bon réflexe
        t2 = REFLEXE[idx]
        y0 = L["text_y"] or bottom + 44
        out = prog(t, t2 - 0.55, 0.5)
        op = 1 - ease_in_out_cubic(out)
        dy = -30 * ease_in_out_cubic(out)
        if op > 0:
            yb = blur_words(world, [cfg["contexte"]], "texte", L["body"], mx, y0 + dy, t - 1.0,
                            lh=1.42, stagger=0.022, dur=0.6,
                            wrap_w=L["col_w"], opacity=op)
            result_block(world, cfg["resultat"], mx, yb + 30 + dy, t - result_at(idx), L["col_w"], L["res"],
                         opacity=op)
        reflexe_block(world, cfg["reflexe"], mx, y0, t - t2, L["col_w"], L["body"] + 3)
        return 1.0 + 0.025 * ease_in_out_cubic(prog(t, 0, D)), 0, 0
    return scene


def result_at(idx):
    """Le résultat arrive quand le contexte a fini d'apparaître (et pas avant la fin du tracé)."""
    n = words_count(CFG["erreurs"][idx]["contexte"])
    return max(1.0 + n * 0.022 + 0.9, CHART_T0 + CHART_DUR - 0.6)


def scene_recap(world, t, D):
    cfg = CFG["recap"]
    cv = Cv(world)
    y = 120 if not VERT else 300
    bottom = ctitle(world, [cfg["titre"]], 80 if not VERT else 84, y, t - 0.1, accent=C["rouge"])
    size = 44 if not VERT else 46
    step = 104 if not VERT else 150
    tw = max(text_w(s, "titre", size) for s in cfg["lignes"])
    x0 = W / 2 - (tw + 96) / 2
    y = bottom + (56 if not VERT else 120)
    for k, s in enumerate(cfg["lignes"]):
        tk = t - RECAP_T0 - k * RECAP_STEP
        p = prog(tk, 0, 0.6)
        if p <= 0:
            continue
        e = ease_out_expo(p)
        cy = y + k * step + step / 2
        xo = (1 - e) * 40
        q = ease_out_back(clamp(p * 1.5), 2)
        cv.disc(x0 + 34 + xo, cy, 32 * q, C["marine"])
        cv.text(str(k + 1), "titre_b", 30, C["fond"], x0 + 34 + xo, cy - 1, align="c", opacity=clamp(p * 3))
        cv.text(s, "titre", size, C["marine"], x0 + 96 + xo, cy, opacity=clamp(p * 2.5))
        # l'erreur est barrée
        r = ease_in_out_cubic(prog(tk, 1.8, 0.45))
        if r > 0:
            sw = text_w(s, "titre", size)
            cv.line([(x0 + 90, cy + 5), (x0 + 90 + (sw + 12) * r, cy + 5)], C["rouge"], 4.5)
    yq = y + 5 * step + (70 if not VERT else 140)
    tq = t - RECAP_Q
    end = ctitle(world, [cfg["question"]], 50 if not VERT else 58, yq, tq, color=C["or"], stagger=0.06)
    if tq > 0.6:
        b = math.sin((tq - 0.6) * 5.5) * 8
        cv.arrow(W / 2, end + 44 + b, 34, mix(C["fond"], C["or"], prog(tq, 0.6, 0.4)), width=4,
                 direction="bas")
    return 1.0 + 0.02 * ease_in_out_cubic(prog(t, 0, D)), 0, 0


def scene_conclusion(world, t, D):
    cfg = CFG["conclusion"]
    y = 330 if not VERT else 640
    size = 86 if not VERT else 80
    bottom = ctitle(world, cfg["lignes"], size, y, t - 0.2, wrap=W - 160, stagger=0.07)
    ctitle(world, [cfg["texte"]], 40 if not VERT else 44, bottom + 50, t - 1.5, kind="ui",
           color=C["gris"], wrap=1200 if not VERT else W - 160, lh=1.35, stagger=0.04)
    return 1.0 + 0.03 * ease_in_out_cubic(prog(t, 0, D)), 0, 0


def wordmark(world, cx, cy, t, scale=1.0):
    """O'MORGAN en capitales espacées, filet doré et activité."""
    cfg = CFG["fin"]
    cv = Cv(world)
    size = 104 * scale
    lay = text_layer(cfg["marque"], "titre_b", size, C["marine"], 0.12)
    p = ease_out_expo(prog(t, 0, 1.0))
    if p > 0:
        part = Image.new("RGBA", lay.size, (0, 0, 0, 0))
        part.paste(lay.crop((0, 0, max(1, int(lay.width * p)), lay.height)), (0, 0))
        b = (1 - p) * 8 * S
        if b > 0.4:
            part = part.filter(ImageFilter.GaussianBlur(b))
        cv.paste(part, cx, cy)
    q = ease_in_out_cubic(prog(t, 0.5, 0.7))
    lw = 150 * scale * q
    cv.line([(cx - lw, cy + 74 * scale), (cx + lw, cy + 74 * scale)], C["or"], 2.5)
    cv.text(cfg["activite"], "ui_semi", 23 * scale, C["or"], cx, cy + 116 * scale, align="c",
            opacity=prog(t, 0.9, 0.6), tracking=0.22)


def scene_fin(world, t, D):
    cfg = CFG["fin"]
    cv = Cv(world)
    y = 170 if not VERT else 420
    bottom = ctitle(world, cfg["lignes"], 66 if not VERT else 68, y, t - 0.2, stagger=0.06, wrap=W - 140)
    wordmark(world, W / 2, bottom + (150 if not VERT else 260), t - 1.4, 1.0 if not VERT else 0.92)
    # mentions légales : visibles jusqu'à la fin
    size = 17 if not VERT else 22
    wrap = 1500 if not VERT else W - 140
    rows = layout([cfg["mentions"]], "texte", size, wrap)
    lh = size * 1.5
    y0 = H - 70 - len(rows) * lh if not VERT else H - 170 - len(rows) * lh
    op = prog(t, 0.6, 0.8)
    if op > 0:
        cv.line([(W / 2 - 60, y0 - 26), (W / 2 + 60, y0 - 26)], mix(C["fond"], C["trait"], op), 1.5)
        for k, ln in enumerate(rows):
            s = " ".join(w for w, _ in ln)
            cv.text(s, "texte", size, C["gris"], W / 2, y0 + k * lh + lh / 2, align="c", opacity=op)
    return 1.0 + 0.02 * ease_in_out_cubic(prog(t, 0, D)), 0, 0


# --------------------------------------------------------------------------- #
# Montage : (durée, scène, transition d'entrée, sons [(t local, type)])
# --------------------------------------------------------------------------- #
ERR_DUR = [13.5, 13.5, 13.5, 13.0, 16.0]
RECAP_T0, RECAP_STEP, RECAP_Q = 1.3, 0.95, 6.2    # récapitulatif : 1re ligne, écart, question
TIMELINE = []
STARTS, DURATION = [], 0.0
REFLEXE = []      # début du « bon réflexe » dans chaque scène d'erreur (s)
VO = []           # voix off : [(t absolu, clé)]


def erreur_sounds(idx, t2):
    snd = [(0.15, "pop"), (0.95, "souffle")]
    for frac, kind in CHARTS[idx][1]:
        tt = CHART_DUR + 0.05 if frac == "fin" else t_at(frac)
        snd.append((CHART_T0 + tt, kind))
    snd.append((result_at(idx), "ping"))
    snd.append((t2 + 0.3, "succes"))
    return snd


def build_timeline():
    """Durées des scènes (multipliées par « rythme.duree », allongées si la voix off le demande),
    sons associés et instants de la voix off."""
    global STARTS, DURATION
    k = max(0.85, float(CFG.get("rythme", {}).get("duree", 1.0))) if CFG else 1.0
    dv = CFG.get("_durees_voix", {}) if CFG else {}
    TIMELINE.clear()
    REFLEXE.clear()
    voix = []                                    # par scène : [(t local, clé)]

    def lu(key, t0, base, apres):
        """Durée de scène : au moins `base`, et assez pour lire la clé à t0 puis `apres` s de silence."""
        if key in dv:
            voix[-1].append((t0, key))
            return max(base, t0 + dv[key] + apres)
        return base

    voix.append([])
    TIMELINE.append((lu("couverture", 0.6, 6.5 * k, 1.0), scene_couverture, None,
                     [(0.1, "pop"), (0.5, "souffle"), (2.0, "pop")]))
    for i, d in enumerate(ERR_DUR):
        voix.append([])
        t2 = lu(f"erreur{i + 1}a", 0.5, d * k * 0.6, 0.6)
        REFLEXE.append(t2)
        D = lu(f"erreur{i + 1}b", t2 + 0.35, d * k, 1.2)
        TIMELINE.append((D, make_erreur(i), "zoom" if i == 0 else "travelling",
                         erreur_sounds(i, t2) if CFG else []))
    voix.append([])
    rows = [RECAP_T0 + j * RECAP_STEP for j in range(5)]
    TIMELINE.append((lu("recap", 0.4, 9.5 * k, 1.3), scene_recap, "zoom",
                     [(r, "tic") for r in rows] + [(r + 1.8, "barre") for r in rows] + [(RECAP_Q, "pop")]))
    voix.append([])
    TIMELINE.append((lu("conclusion", 0.3, 6.0 * k, 1.2), scene_conclusion, "flou", [(0.2, "pop")]))
    voix.append([])
    TIMELINE.append((lu("fin", 0.3, 10.0 * k, 4.0), scene_fin, "flou", [(0.2, "pop"), (1.4, "impact")]))
    STARTS, acc = [], 0.0
    for d, *_ in TIMELINE:
        STARTS.append(acc)
        acc += d
    DURATION = acc
    VO.clear()
    for i, evs in enumerate(voix):
        VO.extend((STARTS[i] - (TR / 2 if i else 0) + lt, key) for lt, key in evs)


# --------------------------------------------------------------------------- #
# Voix off : synthèse neuronale hors ligne (Piper), voix française féminine
# --------------------------------------------------------------------------- #
VOIX_DIR = os.path.join(ROOT, "voix")
VOIX_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/{}.tar.bz2"
MODELES = {   # nom : (archive, modèle, locuteur) ; « kokoro » = voix plus naturelle (sherpa-onnx)
    "siwis": ("vits-piper-fr_FR-siwis-medium", "fr_FR-siwis-medium", None),
    "jessica": ("vits-piper-fr_FR-upmc-medium", "fr_FR-upmc-medium", 0),
    "kokoro": ("kokoro-multi-lang-v1_0", None, 30),          # 30 = ff_siwis, voix féminine française
}
VOIX = {}     # clé -> signal mono 44,1 kHz (processus principal uniquement)
PHRASES = {}  # clé -> début de chaque phrase dans le fichier (s), pour caler l'image sur la voix
KICK_EVERY = 2  # pulsation grave : un temps sur deux (1 = chaque temps, plus nerveux)
KICK_STOP = -2  # scène où la pulsation s'arrête (None : jusqu'à la fin)


def voix_textes(cfg):
    v = cfg.get("voix_off", {})
    out = [("couverture", v.get("couverture"))]
    for i, (a, b) in enumerate(v.get("erreurs", [])):
        out += [(f"erreur{i + 1}a", a), (f"erreur{i + 1}b", b)]
    out += [(key, v.get(key)) for key in ("recap", "conclusion", "fin")]
    return [(key, t) for key, t in out if t]


def telecharger_modele(archive, modele):
    """Récupère le modèle de voix (≈ 70 Mo, une seule fois) dans voix/."""
    os.makedirs(VOIX_DIR, exist_ok=True)
    print(f"Téléchargement de la voix {modele or archive}…")
    with tempfile.NamedTemporaryFile(suffix=".tar.bz2") as tmp:
        urllib.request.urlretrieve(VOIX_URL.format(archive), tmp.name)
        with tarfile.open(tmp.name, "r:bz2") as tar:
            for m in tar.getmembers():
                name = os.path.basename(m.name)
                if modele is None:                  # Kokoro : on garde tout le dossier du modèle
                    if m.isfile():
                        dst = os.path.join(VOIX_DIR, m.name)
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        with tar.extractfile(m) as src, open(dst, "wb") as out:
                            out.write(src.read())
                elif name in (modele + ".onnx", modele + ".onnx.json"):
                    with tar.extractfile(m) as src, open(os.path.join(VOIX_DIR, name), "wb") as dst:
                        dst.write(src.read())


def moteur_voix(nom, vitesse):
    """Renvoie une fonction texte -> (échantillons int16, fréquence)."""
    archive, modele, speaker = MODELES[nom]
    if modele is None:
        d = os.path.join(VOIX_DIR, archive)
        if not os.path.exists(os.path.join(d, "model.onnx")):
            telecharger_modele(archive, None)
        import sherpa_onnx
        tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(model=sherpa_onnx.OfflineTtsModelConfig(
            kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
                model=f"{d}/model.onnx", voices=f"{d}/voices.bin", tokens=f"{d}/tokens.txt",
                data_dir=f"{d}/espeak-ng-data", dict_dir=f"{d}/dict", lexicon=f"{d}/lexicon-us-en.txt",
                lang="fr"),
            num_threads=os.cpu_count() or 2)))

        def synth(texte):
            """Phrase par phrase, avec une vraie respiration entre elles et un débit qui varie un peu :
            le rythme d'une personne qui parle plutôt qu'une lecture d'un seul souffle."""
            phrases = [p for p in re.split(r"(?<=[.!?…])\s+", texte.strip()) if p]
            parts, rate, debuts, n = [], 24000, [], 0
            for i, ph in enumerate(phrases):
                a = tts.generate(ph, sid=speaker, speed=vitesse * (1.0, 0.97, 1.02)[i % 3])
                rate = a.sample_rate
                sig = np.clip(np.array(a.samples, np.float32), -1, 1)
                debuts.append(n / rate)
                parts.append(sig)
                n += len(sig)
                if i < len(phrases) - 1:
                    pause = 0.36 if ph.endswith(("?", "!", ":")) else (0.45 if ph.endswith("…") else 0.3)
                    parts.append(np.zeros(int(pause * rate), np.float32))
                    n += int(pause * rate)
            return (np.concatenate(parts) * 32767).astype(np.int16), rate, debuts
        return synth
    onnx = os.path.join(VOIX_DIR, modele + ".onnx")
    if not os.path.exists(onnx):
        telecharger_modele(archive, modele)
    from piper import PiperVoice, SynthesisConfig
    voice = PiperVoice.load(onnx, config_path=onnx + ".json")
    syn = SynthesisConfig(speaker_id=speaker, length_scale=1 / vitesse)

    def synth(texte):
        chunks = list(voice.synthesize(texte, syn_config=syn))
        return np.concatenate([c.audio_int16_array for c in chunks]), voice.config.sample_rate, [0.0]
    return synth


def prepare_voix(cfg, textes=None, sr=44100):
    """Synthétise chaque phrase de la voix off (avec cache) ; renvoie {clé: durée en s}.
    `textes` : [(clé, texte)], par défaut ceux de la rubrique « voix_off » de la config."""
    opts = cfg.get("voix", {})
    if not opts.get("active", True):
        return {}
    textes = voix_textes(cfg) if textes is None else textes
    if not textes:
        return {}
    nom = opts.get("modele", "siwis")
    vitesse = float(opts.get("vitesse", 1.0))
    cache = os.path.join(ROOT, "output", "voix-cache")
    os.makedirs(cache, exist_ok=True)
    voice = None
    durees = {}
    for key, texte in textes:
        h = hashlib.sha1(f"{nom}|{vitesse}|{texte}|v4".encode()).hexdigest()[:16]
        path = os.path.join(cache, f"{key}-{h}.wav")
        if not os.path.exists(path):
            if voice is None:
                voice = moteur_voix(nom, vitesse)
            pcm, rate, debuts = voice(texte)
            with open(path + ".json", "w") as f:
                json.dump(debuts, f)
            with tempfile.NamedTemporaryFile(suffix=".wav") as raw:
                with wave.open(raw.name, "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(rate)
                    w.writeframes(pcm.tobytes())
                # 44,1 kHz, coupe des graves, légère compression et niveau homogène
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw.name, "-af",
                                "silenceremove=start_periods=1:start_threshold=-50dB,areverse,"
                                "silenceremove=start_periods=1:start_threshold=-50dB,areverse,"
                                "highpass=f=70,equalizer=f=220:t=q:w=1:g=2.5,"         # chaleur
                                "equalizer=f=3800:t=q:w=1.4:g=-2.5,deesser,"           # moins métallique
                                "aecho=0.9:0.5:23|41:0.10|0.06,"                       # petite pièce
                                "acompressor=threshold=-20dB:ratio=3:attack=5:release=90,"
                                "loudnorm=I=-16:TP=-1.5:LRA=7,aresample=44100",
                                "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le", path], check=True)
        with wave.open(path, "rb") as w:
            sig = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        VOIX[key] = sig
        durees[key] = len(sig) / sr
        if os.path.exists(path + ".json"):
            with open(path + ".json") as f:
                PHRASES[key] = json.load(f)
    return durees


def scene_local_time(k, t):
    return t - STARTS[k] + (TR / 2 if k > 0 else 0)


# --------------------------------------------------------------------------- #
# Rendu
# --------------------------------------------------------------------------- #
def render_scene(k, t):
    world = background().copy()
    d, f = TIMELINE[k][0], TIMELINE[k][1]
    zoom, dx, dy = f(world, scene_local_time(k, t), d) or (1.0, 0, 0)
    cw, ch = W * S / zoom, H * S / zoom
    cx = clamp(W * S / 2 + dx * S, cw / 2, W * S - cw / 2)
    cy = clamp(H * S / 2 + dy * S, ch / 2, H * S - ch / 2)
    box = (cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2)
    return np.asarray(world.convert("RGB").resize((W, H), Image.LANCZOS, box=box), dtype=np.float32)


def blur_arr(arr, r):
    if r < 0.4:
        return arr
    return np.asarray(Image.fromarray(arr.astype(np.uint8)).filter(ImageFilter.GaussianBlur(r)), np.float32)


def zoom_arr(arr, z):
    if abs(z - 1) < 1e-3:
        return arr
    if z < 1:
        small = Image.fromarray(arr.astype(np.uint8)).resize((int(W * z), int(H * z)), Image.BILINEAR)
        canvas = Image.new("RGB", (W, H), C["fond"][:3])
        canvas.paste(small, ((W - small.width) // 2, (H - small.height) // 2))
        return np.asarray(canvas, np.float32)
    cw, ch = W / z, H / z
    box = ((W - cw) / 2, (H - ch) / 2, (W + cw) / 2, (H + ch) / 2)
    return np.asarray(Image.fromarray(arr.astype(np.uint8)).resize((W, H), Image.BILINEAR, box=box), np.float32)


def render_frame(i):
    t = i / FPS
    k = max(j for j, s in enumerate(STARTS) if t >= s)
    nxt = k + 1 if k + 1 < len(TIMELINE) else None
    prev = k - 1 if k > 0 else None
    pair = None
    if nxt is not None and t >= STARTS[nxt] - TR / 2:
        pair = (k, nxt, (t - (STARTS[nxt] - TR / 2)) / TR)
    elif prev is not None and t < STARTS[k] + TR / 2:
        pair = (prev, k, (t - (STARTS[k] - TR / 2)) / TR)

    if pair is None:
        out = render_scene(k, t)
    else:
        a_i, b_i, u = pair
        kind = TIMELINE[b_i][2]
        A = render_scene(a_i, t)
        B = render_scene(b_i, t)
        if kind == "travelling":
            p = ease_in_out_expo(u)
            off = p * W
            strip = np.concatenate([A, B], axis=1)
            speed = abs(ease_in_out_expo(min(1, u + 0.02)) - ease_in_out_expo(max(0, u - 0.02))) / 0.04
            n = 11 if speed > 0.5 else 1
            spread = min(55, speed * 16)
            acc = np.zeros_like(A)
            for s in range(n):
                o = int(round(clamp(off + (s - (n - 1) / 2) * spread / max(1, n - 1) * 2, 0, W)))
                acc += strip[:, o:o + W]
            out = acc / n
        elif kind == "zoom":
            p = ease_in_out_cubic(u)
            A2 = blur_arr(zoom_arr(A, 1 + 0.35 * p), 10 * p)
            B2 = blur_arr(zoom_arr(B, 0.86 + 0.14 * p), 10 * (1 - p))
            out = A2 * (1 - p) + B2 * p
        else:  # flou
            p = ease_in_out_cubic(u)
            out = blur_arr(A, 14 * p) * (1 - p) + blur_arr(B, 14 * (1 - p)) * p

    f = min(prog(t, 0, 0.4), 1 - prog(t, DURATION - 0.9, 0.9))
    if f < 1:
        bg = np.array(C["fond"][:3], np.float32)
        out = bg - (bg - out) * f   # fondu depuis / vers le fond
    return np.clip(out, 0, 255).astype(np.uint8).tobytes()


def init_worker(cfg, fmt="horizontal"):
    setup_format(fmt)
    CFG.clear()
    CFG.update(typo_cfg(cfg))
    C.clear()
    C.update({k: hex2rgb(v) for k, v in cfg["couleurs"].items()})
    build_timeline()


# --------------------------------------------------------------------------- #
# Son : musique posée (Am7 - Fmaj7 - Cmaj7 - G6) + bruitages d'interface
# --------------------------------------------------------------------------- #
def synth_audio(path, sr=44100):
    n = int(DURATION * sr)
    mus = np.zeros(n, np.float32)
    sfx = np.zeros(n, np.float32)
    rng = np.random.default_rng(7)
    beat = 0.5

    def add(buf, sig, t0, gain=1.0):
        s = int(t0 * sr)
        if s >= n or s + len(sig) <= 0:
            return
        if s < 0:
            sig, s = sig[-s:], 0
        e = min(n, s + len(sig))
        buf[s:e] += sig[:e - s] * gain

    def tt(dur):
        return np.arange(int(dur * sr)) / sr

    def midi(m):
        return 440.0 * 2 ** ((m - 69) / 12)

    def keys(freq, dur, decay=0.6):
        x = tt(dur)
        env = np.exp(-x / decay) * np.clip(x / 0.004, 0, 1)
        sig = np.sin(2 * np.pi * freq * x) + 0.35 * np.sin(4 * np.pi * freq * x) * np.exp(-x / 0.15) \
            + 0.12 * np.sin(6 * np.pi * freq * x) * np.exp(-x / 0.08)
        return sig * env

    def pad(freqs, dur):
        x = tt(dur)
        env = np.clip(x / 0.6, 0, 1) * np.clip((dur - x) / 0.6, 0, 1)
        sig = np.zeros_like(x)
        for f in freqs:
            for det in (-0.003, 0.003):
                sig += np.sin(2 * np.pi * f * (1 + det) * x + 0.3 * np.sin(2 * np.pi * 0.2 * x))
        return sig * env / len(freqs)

    def kick():
        x = tt(0.35)
        f = 45 + 80 * np.exp(-x / 0.025)
        return np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-x / 0.1)

    chords = [(45, 57, 60, 64, 67), (41, 57, 60, 64, 69), (48, 60, 64, 67, 71), (43, 55, 59, 62, 64)]
    bar = 4 * beat
    end = DURATION - 3.0
    beats_in = STARTS[1]                # la pulsation démarre avec la 1re erreur
    beats_out = STARTS[KICK_STOP] if KICK_STOP else DURATION - 1.5   # et s'arrête pour la conclusion
    off = set(CFG.get("sons", {}).get("desactiver", []))
    for b in range(int(math.ceil(end / bar))):
        ch = chords[b % 4]
        t0 = b * bar
        add(mus, pad([midi(m) for m in ch[1:]], bar + 0.6), t0, 0.10)
        add(mus, keys(midi(ch[0]), 1.6, 0.8) * 0.9, t0, 0.18)
        add(mus, keys(midi(ch[0] - 12), 1.8, 1.0), t0, 0.15)
        for k8, idx in enumerate([0, 2, 3, 1, 2, 4, 3, 2]):
            add(mus, keys(midi(ch[1 + idx % 4] + 12), 0.6, 0.25), t0 + k8 * beat / 2, 0.06)
    for k in range(int(end / beat)):
        t0 = k * beat
        if beats_in <= t0 < beats_out and k % KICK_EVERY == 0 and "batterie" not in off:
            add(mus, kick(), t0, 0.45)
    add(mus, pad([midi(m) for m in (57, 60, 64, 67, 72)], 3.5), end, 0.12)
    add(mus, keys(midi(45), 3.0, 1.4), end, 0.25)
    x = np.arange(n) / sr
    mus *= np.clip((DURATION - x) / 1.2, 0, 1)

    def fft_band(x, lo, hi, soft=0.25):
        n_ = len(x)
        spec = np.fft.rfft(x)
        f = np.fft.rfftfreq(n_, 1 / sr)
        lo_g = 1 / (1 + (lo / np.maximum(f, 1)) ** (2 / soft)) if lo > 0 else 1
        hi_g = 1 / (1 + (f / hi) ** (2 / soft)) if hi else 1
        return np.fft.irfft(spec * lo_g * hi_g, n_)

    def reverb(x, mix_=0.25, room=0.82, tail=1.2):
        x = np.concatenate([x, np.zeros(int(tail * sr))])
        wet = np.zeros_like(x)
        for d_ms, g in ((29.7, room), (37.1, room * 0.98), (41.1, room * 0.96), (43.7, room * 0.94)):
            d = int(d_ms * sr / 1000)
            y = x.copy()
            for i in range(d, len(y), d):
                y[i:i + d] += g * y[i - d:i - d + len(y[i:i + d])]
            wet += y
        wet /= 4
        wet = fft_band(wet, 200, 7000)
        return x * (1 - mix_) + wet * mix_

    def bell(freq, dur, decay):
        x = tt(dur)
        sig = np.zeros_like(x)
        for ratio, amp, dk in ((1.0, 1.0, 1.0), (2.76, 0.35, 0.45), (5.4, 0.12, 0.25), (8.93, 0.05, 0.15)):
            sig += amp * np.sin(2 * np.pi * freq * ratio * x) * np.exp(-x / (decay * dk))
        return sig * np.clip(x / 0.002, 0, 1)

    def marimba(freq, dur=0.35):
        x = tt(dur)
        body = np.sin(2 * np.pi * freq * x) * np.exp(-x / 0.09)
        over = 0.25 * np.sin(2 * np.pi * freq * 4 * x) * np.exp(-x / 0.02)
        return (body + over) * np.clip(x / 0.0015, 0, 1)

    def tic(note=76):
        return marimba(midi(note))

    def pop():
        x = tt(0.09)
        f = 320 + 520 * (1 - np.exp(-x / 0.02))
        return np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-x / 0.028) * np.clip(x / 0.003, 0, 1)

    def ping():
        a = bell(midi(88), 1.2, 0.35)
        b = bell(midi(93), 1.2, 0.45)
        out = np.zeros(int(1.4 * sr))
        out[:len(a)] += a
        o = int(0.11 * sr)
        out[o:o + len(b)] += 0.8 * b[:len(out) - o]
        return reverb(out * 0.6, mix_=0.3)

    def succes():
        out = np.zeros(int(1.3 * sr))
        for j, m in enumerate((69, 72, 76, 81)):
            s_ = marimba(midi(m), 0.6) * (0.9 if j < 3 else 0.6) + 0.25 * bell(midi(m + 12), 0.6, 0.2)
            o = int(j * 0.06 * sr)
            out[o:o + len(s_)] += s_[:len(out) - o]
        return reverb(out * 0.55, mix_=0.28)

    def chute():                                # baisse : glissando descendant feutré
        x = tt(0.9)
        f = 300 + 420 * np.exp(-x / 0.25)
        sig = np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-x / 0.3) * np.clip(x / 0.01, 0, 1)
        sig += 0.3 * np.sin(2 * np.pi * np.cumsum(f * 2.01) / sr) * np.exp(-x / 0.15)
        return reverb(sig * 0.7, mix_=0.3, tail=0.6)

    def barre():                                # trait de feutre
        x = tt(0.22)
        return fft_band(rng.standard_normal(len(x)), 1500, 6000) * np.sin(np.pi * x / 0.22) ** 2 * 0.5

    def souffle(dur=0.8):
        m = int(dur * sr)
        noise = rng.standard_normal(m)
        out = np.zeros(m)
        hop, win = 1024, 2048
        w = np.hanning(win)
        for i in range(0, m - win, hop):
            ph = (i + win / 2) / m
            centre = 250 + 1300 * math.sin(math.pi * ph) ** 2
            out[i:i + win] += fft_band(noise[i:i + win] * w, centre * 0.5, centre * 1.6, soft=0.5) * w
        x = np.arange(m) / sr
        return out * np.sin(np.pi * x / dur) ** 3 * 0.9

    def impact():
        x = tt(2.2)
        f = 45 + 60 * np.exp(-x / 0.06)
        low = np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-x / 0.5) * np.clip(x / 0.004, 0, 1)
        halo = sum(bell(midi(m), 2.2, 0.9) for m in (81, 84, 88)) * 0.18
        return reverb(low * 0.9 + halo, mix_=0.3, tail=0.8)

    gen = {"tic": (tic, 0.2), "pop": (pop, 0.3), "ping": (ping, 0.26), "succes": (succes, 0.3),
           "chute": (chute, 0.22), "barre": (barre, 0.16), "souffle": (souffle, 0.28), "impact": (impact, 0.5)}
    scale = [69, 72, 74, 76, 79, 81, 84, 86]   # pentatonique de la mineur
    cache = {}
    for k, (dur, _f, trans, sounds) in enumerate(TIMELINE):
        origin = STARTS[k] - (TR / 2 if k > 0 else 0)
        n_tic = 0
        for lt, kind in sounds:
            if kind in off:
                continue
            key = kind
            if kind == "tic":
                key = ("tic", scale[n_tic % len(scale)])
                n_tic += 1
            if key not in cache:
                cache[key] = (tic(key[1]) if kind == "tic" else gen[kind][0]()).astype(np.float32)
            add(sfx, cache[key], origin + lt, gen[kind][1])
        if trans and "souffle" not in off:
            if "souffle_tr" not in cache:
                cache["souffle_tr"] = souffle(0.9).astype(np.float32)
            add(sfx, cache["souffle_tr"], STARTS[k] - 0.5, 0.12 if trans == "travelling" else 0.09)

    sons = CFG.get("sons", {})
    vol_fx = float(sons.get("volume_effets", 1.0)) if sons.get("effets", True) else 0.0
    mus *= float(sons.get("volume_musique", 1.0))
    sfx *= vol_fx
    # voix off : la musique s'efface (≈ -8 dB) pendant qu'elle parle
    voix = np.zeros(n, np.float32)
    duck = np.ones(n, np.float32)
    for t0, key in VO:
        sig = VOIX.get(key)
        if sig is None:
            continue
        add(voix, sig, t0)
        ramp = np.clip(np.minimum((x - t0 + 0.35) / 0.35, (t0 + len(sig) / sr + 0.5 - x) / 0.5), 0, 1)
        duck = np.minimum(duck, 1 - 0.6 * ramp)
    ref = np.abs(np.tanh((mus * 0.9 + sfx) * 1.1)).max()
    out = np.tanh((mus * 0.9 * duck + sfx * (0.6 + 0.4 * duck)) * 1.1) * (0.8 / max(1e-6, ref))
    if VO and VOIX:
        vol_voix = float(CFG.get("voix", {}).get("volume", 1.0))
        out = out * 0.6 + voix * (0.9 / max(1e-6, np.abs(voix).max())) * vol_voix
        out /= max(1.0, np.abs(out).max() / 0.95)
    pcm = (out * 32767).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(np.stack([pcm, pcm], axis=1).tobytes())


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(ROOT, "config_erreurs.json"))
    ap.add_argument("--format", choices=["horizontal", "vertical"], default="horizontal",
                    help="horizontal 1920x1080 (défaut) ou vertical 1080x1920 pour mobile")
    ap.add_argument("--out")
    ap.add_argument("--preview", nargs="*", type=float, metavar="SEC")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["_durees_voix"] = prepare_voix(cfg)
    init_worker(cfg, args.format)
    suffix = "-mobile" if VERT else ""
    args.out = args.out or os.path.join(ROOT, "output", f"5-erreurs-bourse{suffix}.mp4")
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)

    if args.preview is not None:
        outdir = os.path.join(os.path.dirname(os.path.abspath(args.out)), "apercu-erreurs" + suffix)
        os.makedirs(outdir, exist_ok=True)
        secs = args.preview
        if not secs:
            for k, (s, (d, *_)) in enumerate(zip(STARTS, TIMELINE)):
                if 1 <= k <= len(REFLEXE):          # erreurs : avant et après « le bon réflexe »
                    secs.append(s + REFLEXE[k - 1] - 0.9)
                secs.append(s + d - 0.6)
        for sec in secs:
            buf = render_frame(int(round(sec * FPS)))
            Image.frombytes("RGB", (W, H), buf).save(os.path.join(outdir, f"t{sec:06.2f}.png"))
        print(f"Durée : {DURATION:.1f} s — aperçus dans {outdir}")
        return

    total = int(round(DURATION * FPS))
    wav = os.path.splitext(args.out)[0] + ".wav"
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
    if cfg.get("musique", True):
        synth_audio(wav)
        cmd += ["-i", wav, "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-shortest", args.out]
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    with Pool(args.jobs, initializer=init_worker, initargs=(cfg, args.format)) as pool:
        for i, buf in enumerate(pool.imap(render_frame, range(total), chunksize=2)):
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
