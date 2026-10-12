#!/usr/bin/env python3
"""Vidéo « De la création du procès-verbal à la pièce livrée chez le réparateur ».

Style démo produit : interfaces épurées sur fond blanc, cartes qui basculent en
perspective, curseur qui clique, texte qui apparaît mot à mot en sortant du flou,
mouvements de caméra, transitions en travelling / zoom / flou et sons d'interface.

Usage :
    python3 parcours.py                    # -> output/parcours-autopiec.mp4
    python3 parcours.py --preview          # une image par scène dans output/apercu-parcours/
    python3 parcours.py --preview 15.2 22  # images à des instants précis
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
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.abspath(__file__))
W, H = 1920, 1080
S = 2                # supersampling
FPS = 30
TR = 0.8             # durée d'une transition entre scènes (s)

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
# Couleurs & polices
# --------------------------------------------------------------------------- #
def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (255,)


def mix(c1, c2, k):
    return tuple(int(lerp(a, b, k)) for a, b in zip(c1[:3], c2[:3])) + (255,)


def alpha(c, a):
    return c[:3] + (int(255 * clamp(a)),)


FONT_DIRS = [os.path.join(ROOT, "fonts"), "/usr/share/fonts/opentype/inter",
             "/usr/share/fonts/truetype/dejavu"]
FONTS = {
    "titre": ["InterDisplay-SemiBold.otf", "Inter-SemiBold.otf", "DejaVuSans-Bold.ttf"],
    "titre_b": ["InterDisplay-Bold.otf", "Inter-Bold.otf", "DejaVuSans-Bold.ttf"],
    "ui": ["Inter-Medium.otf", "Inter-Regular.otf", "DejaVuSans.ttf"],
    "ui_semi": ["Inter-SemiBold.otf", "Inter-Bold.otf", "DejaVuSans-Bold.ttf"],
    "ui_bold": ["Inter-Bold.otf", "DejaVuSans-Bold.ttf"],
    "mono": ["DejaVuSansMono.ttf", "LiberationMono-Regular.ttf"],
}


@lru_cache(maxsize=None)
def font(kind, size):
    for name in FONTS[kind]:
        for d in FONT_DIRS + ["/usr/share/fonts/truetype/liberation"]:
            p = os.path.join(d, name)
            if os.path.exists(p):
                return ImageFont.truetype(p, int(size * S))
    return ImageFont.load_default(int(size * S))


def text_w(text, kind, size, tracking=0.0):
    return (font(kind, size).getlength(text) + tracking * size * S * max(0, len(text) - 1)) / S


PAD = 12 * S


@lru_cache(maxsize=1024)
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
    """« L'expert {le procès-verbal.} » -> [(mot, accent?), ...]."""
    words = []
    for text, acc in segments(line, True):
        for w in text.split():
            words.append((w, bool(acc)))
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
        self.d.line(self.P(*pts), fill=color, width=max(1, int(width * S)), joint="curve")

    def poly(self, pts, fill):
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
        l1 = math.dist(a, b)
        l2 = math.dist(b, c)
        L = (l1 + l2) * p
        pts = [a]
        if L <= l1:
            k = L / l1
            pts.append((lerp(a[0], b[0], k), lerp(a[1], b[1], k)))
        else:
            k = (L - l1) / l2
            pts += [b, (lerp(b[0], c[0], k), lerp(b[1], c[1], k))]
        self.line(pts, color, width or max(1.5, s * 0.16))

    def check_badge(self, cx, cy, r, p, fill, color):
        q = ease_out_back(clamp(p * 1.6), 2.4)
        self.disc(cx, cy, r * q, fill)
        self.check(cx, cy, r * 1.05, color, ease_out_cubic(prog(p, 0.3, 0.7)), width=max(1.5, r * 0.2))

    def spinner(self, cx, cy, r, t, color):
        a0 = (t * 400) % 360
        self.d.arc(self.P((cx - r, cy - r), (cx + r, cy + r)), a0, a0 + 260, fill=color,
                   width=max(1, int(r * 0.3 * S)))

    def chip(self, text, x, y, kind, size, fg, bg, align="l", opacity=1.0, pad=14, h=None, icon_check=False):
        """Pastille arrondie ; retourne sa largeur."""
        tw = text_w(text, kind, size)
        extra = size * 0.95 if icon_check else 0
        w = tw + 2 * pad + extra
        h = h or size * 1.75
        x0 = x if align == "l" else (x - w / 2 if align == "c" else x - w)
        if opacity > 0.004:
            if opacity < 0.999:
                bg = mix(C["fond"], bg, opacity)
                fg = mix(C["fond"], fg, opacity)
            self.rrect(x0, y - h / 2, x0 + w, y + h / 2, h / 2, fill=bg)
            self.text(text, kind, size, fg, x0 + pad, y)
            if icon_check:
                self.check(x0 + pad + tw + extra * 0.55, y, size * 0.62, fg, width=size * 0.13)
        return w

    def icon(self, name, cx, cy, s, color, bg=None):
        icon(self, name, cx, cy, s, color, bg)


def icon(cv, name, cx, cy, s, color, bg=None):
    lw = max(1.2, s * 0.13)

    def p(x, y):
        return cx + x * s, cy + y * s

    def line(*pts, closed=False):
        pts = [p(*q) for q in pts]
        if closed:
            pts.append(pts[0])
        cv.line(pts, color, lw)

    def circ(x, y, r, fill=False):
        if fill:
            cv.disc(cx + x * s, cy + y * s, r * s, color)
        else:
            cv.ring(cx + x * s, cy + y * s, r * s, lw, color)

    def poly(*pts):
        cv.poly([p(*q) for q in pts], color)

    if name == "presse":           # porte-bloc de l'expert
        line((-0.55, -0.58), (0.55, -0.58), (0.55, 0.78), (-0.55, 0.78), closed=True)
        cv.rrect(*p(-0.26, -0.8), *p(0.26, -0.46), 0.08 * s, fill=color)
        for y in (-0.15, 0.12, 0.39):
            line((-0.3, y), (0.3, y))
    elif name == "autopiec":       # « /// »
        for k in range(3):
            x0 = -0.82 + k * 0.56
            poly((x0 + 0.3, -0.5), (x0 + 0.62, -0.5), (x0 + 0.32, 0.5), (x0, 0.5))
    elif name == "cle":            # clé à molette du réparateur
        line((-0.58, 0.58), (0.12, -0.12))
        circ(0.32, -0.32, 0.32)
        if bg:
            cv.disc(*p(0.52, -0.52), 0.17 * s, bg)
    elif name == "bouclier":       # assurance
        line((0, -0.78), (0.62, -0.52), (0.55, 0.18), (0, 0.8), (-0.55, 0.18), (-0.62, -0.52), closed=True)
        cv.check(cx, cy + 0.02 * s, s * 0.55, color, width=lw)
    elif name == "doc":
        line((-0.52, -0.72), (0.22, -0.72), (0.55, -0.4), (0.55, 0.72), (-0.52, 0.72), closed=True)
        for y in (-0.15, 0.12, 0.39):
            line((-0.28, y), (0.3, y))
    elif name == "camion":
        poly((-0.85, -0.45), (0.22, -0.45), (0.22, 0.35), (-0.85, 0.35))
        poly((0.28, -0.2), (0.58, -0.2), (0.85, 0.08), (0.85, 0.35), (0.28, 0.35))
        if bg:
            cv.poly([p(0.36, -0.1), p(0.55, -0.1), p(0.7, 0.06), p(0.36, 0.06)], bg)
        circ(-0.48, 0.45, 0.19, fill=True)
        circ(0.52, 0.45, 0.19, fill=True)
        if bg:
            cv.disc(*p(-0.48, 0.45), 0.08 * s, bg)
            cv.disc(*p(0.52, 0.45), 0.08 * s, bg)
    elif name == "repere":
        circ(0, -0.22, 0.5, fill=True)
        poly((-0.43, 0.02), (0.43, 0.02), (0, 0.85))
        if bg:
            cv.disc(*p(0, -0.22), 0.2 * s, bg)
    elif name == "garage":
        poly((-0.8, -0.1), (0, -0.7), (0.8, -0.1), (0.8, 0.75), (-0.8, 0.75))
        if bg:
            cv.rrect(*p(-0.45, 0.1), *p(0.45, 0.75), 0.04 * s, fill=bg)
            for y in (0.28, 0.46):
                cv.line([p(-0.45, y), p(0.45, y)], color, max(1, lw * 0.6))
    elif name == "cloche":
        cv.d.pieslice(cv.P(p(-0.52, -0.62), p(0.52, 0.42)), 180, 360, fill=color)
        poly((-0.52, -0.12), (0.52, -0.12), (0.62, 0.38), (-0.62, 0.38))
        circ(0, 0.56, 0.14, fill=True)
    else:
        circ(0, 0, 0.5, fill=True)


# --------------------------------------------------------------------------- #
# Ombres, cartes, perspective
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=64)
def shadow_layer(w, h, r):
    """Ombre douce en deux couches (diffuse + contact) pour une carte w x h."""
    m = 90
    im = Image.new("RGBA", (int((w + 2 * m) * S), int((h + 2 * m) * S)), (0, 0, 0, 0))
    a = Image.new("L", im.size, 0)
    ImageDraw.Draw(a).rounded_rectangle(((m + 10) * S, (m + 34) * S, (m + w - 10) * S, (m + h + 18) * S),
                                        radius=int(r * S), fill=34)
    a = a.filter(ImageFilter.GaussianBlur(34 * S))
    b = Image.new("L", im.size, 0)
    ImageDraw.Draw(b).rounded_rectangle((m * S, (m + 3) * S, (m + w) * S, (m + h + 3) * S),
                                        radius=int(r * S), fill=22)
    b = b.filter(ImageFilter.GaussianBlur(4 * S))
    al = Image.fromarray(np.clip(np.asarray(a, np.int16) + np.asarray(b, np.int16), 0, 255).astype(np.uint8))
    im.putalpha(al)
    navy = Image.new("RGBA", im.size, C["marine"][:3] + (255,))
    navy.putalpha(al)
    return navy


def new_card(w, h, r=26, fill=None, border=True):
    """Calque vierge d'une carte (unités design) avec fond arrondi et liseré."""
    m = 4
    img = Image.new("RGBA", (int((w + 2 * m) * S), int((h + 2 * m) * S)), (0, 0, 0, 0))
    cv = Cv(img, m, m)
    cv.rrect(0, 0, w, h, r, fill=fill or C["fond"], outline=C["trait"] if border else None, width=1.5)
    return img, cv


def find_coeffs(dst, src):
    A, B = [], []
    for (x, y), (u, v) in zip(dst, src):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        B += [u, v]
    return np.linalg.solve(np.array(A, float), np.array(B, float))


def tilt_layer(layer, tilt):
    """Bascule autour de l'axe horizontal (bord haut qui s'éloigne) ; tilt en degrés."""
    if abs(tilt) < 0.3:
        return layer
    w, h = layer.size
    a = math.radians(tilt)
    inset = w * 0.5 * (1 - 1 / (1 + math.sin(a) * 0.55))
    drop = h * (1 - math.cos(a)) * 0.6
    dst = [(inset, drop), (w - inset, drop), (w, h), (0, h)]
    src = [(0, 0), (w, 0), (w, h), (0, h)]
    return layer.transform((w, h), Image.PERSPECTIVE, find_coeffs(dst, src), Image.BICUBIC)


def place_card(world, layer, cx, cy, t, t_in=0.0, w=None, h=None, r=26, rot=0.0, scale=1.0,
               opacity=1.0, dur=1.0, from_dy=90, tilt_from=24.0):
    """Entrée « démo produit » : la carte monte en se redressant (perspective + ressort)."""
    p = prog(t, t_in, dur)
    if p <= 0 or opacity <= 0:
        return
    e = ease_out_expo(p)
    sp = spring(p * 1.3, 7.0, 1.6)
    op = clamp(p * 3) * opacity
    sc = lerp(0.92, 1.0, e) * scale
    dy = (1 - sp) * from_dy
    tilt = (1 - e) * tilt_from
    if w is not None and op > 0.02:
        sh = shadow_layer(int(w), int(h), r)
        if abs(sc - 1) > 1e-3:
            sh = sh.resize((max(1, int(sh.width * sc)), max(1, int(sh.height * sc))), Image.BILINEAR)
        if rot:
            sh = sh.rotate(rot, resample=Image.BILINEAR, expand=True)
        composite(world, fade(sh, op * e), cx * S - sh.width / 2, (cy + dy + 10 * (1 - e)) * S - sh.height / 2)
    lay = tilt_layer(layer, tilt)
    if abs(sc - 1) > 1e-3:
        lay = lay.resize((max(1, int(lay.width * sc)), max(1, int(lay.height * sc))), Image.BICUBIC)
    if rot:
        lay = lay.rotate(rot, resample=Image.BICUBIC, expand=True)
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


def blur_words(world, lines, kind, size, x, y_top, t, color=None, accent=None, align="l",
               line_h=None, stagger=0.06, dur=0.7, opacity=1.0, max_w=None):
    """Affiche des lignes (listes de (mot, accent)) mot par mot, chaque mot sortant du flou.
    Si max_w est donné, `lines` est une liste de chaînes balisées à replier."""
    color = color or C["marine"]
    accent = accent or C["bleu"]
    line_h = line_h or size * 1.12
    if max_w is not None:
        words = []
        for ln in lines:
            words += markup_words(ln)
        lines = wrap_words(words, kind, size, max_w)
    else:
        lines = [markup_words(ln) if isinstance(ln, str) else ln for ln in lines]
    space = font(kind, size).getlength(" ") / S
    i = 0
    for li, ln in enumerate(lines):
        widths = [text_w(w, kind, size) for w, _ in ln]
        total = sum(widths) + space * (len(ln) - 1)
        cx = x if align == "l" else x - total / 2
        cy = y_top + li * line_h + line_h / 2
        for (w, acc), ww in zip(ln, widths):
            p = prog(t, i * stagger, dur)
            if p > 0 and opacity > 0:
                e = ease_out_expo(p)
                lay = text_layer(w, kind, size, accent if acc else color)
                blur = (1 - ease_out_cubic(p)) * 14 * S
                if blur > 0.4:
                    lay = lay.filter(ImageFilter.GaussianBlur(blur))
                op = clamp(p * 2.2) * opacity
                composite(world, fade(lay, op), cx * S - PAD, (cy + (1 - e) * 26) * S - lay.height / 2)
            cx += ww + space
            i += 1
    return y_top + len(lines) * line_h


# --------------------------------------------------------------------------- #
# Curseur
# --------------------------------------------------------------------------- #
def cursor_at(t, keys):
    """keys : [(temps, x, y), ...] ; interpolation lissée entre les points."""
    if t <= keys[0][0]:
        return keys[0][1], keys[0][2]
    for (t0, x0, y0), (t1, x1, y1) in zip(keys, keys[1:]):
        if t <= t1:
            k = ease_in_out_cubic((t - t0) / (t1 - t0))
            # léger arc pour un mouvement plus naturel
            arc = math.sin(k * math.pi) * 0.12 * math.dist((x0, y0), (x1, y1))
            return lerp(x0, x1, k), lerp(y0, y1, k) - arc * 0.35
    return keys[-1][1], keys[-1][2]


def draw_cursor(world, t, keys, clicks=(), t_show=None, t_hide=None):
    t_show = keys[0][0] if t_show is None else t_show
    op = prog(t, t_show, 0.25)
    if t_hide is not None:
        op *= 1 - prog(t, t_hide, 0.3)
    if op <= 0:
        return
    x, y = cursor_at(t, keys)
    cv = Cv(world)
    press = 1.0
    for tc in clicks:
        q = t - tc
        if -0.08 <= q <= 0.12:
            press = 0.82
        if 0 <= q <= 0.6:
            r = 10 + 46 * ease_out_cubic(q / 0.6)
            cv.ring(x, y, r, 3 * (1 - q / 0.6), alpha(C["bleu"], 0.9 * (1 - q / 0.6)))
    s = 34 * press
    pts = [(0, 0), (0, 1.0), (0.26, 0.76), (0.44, 1.12), (0.58, 1.05), (0.4, 0.7), (0.74, 0.7)]
    poly = [(x + px * s, y + py * s) for px, py in pts]
    shadow = [(px + 2, py + 3) for px, py in poly]
    cv.poly(shadow, alpha(C["marine"], 0.18 * op))
    cv.poly(poly, alpha(C["marine"], op))
    inner = [(x + px * s * 0.78 + s * 0.06, y + py * s * 0.78 + s * 0.13) for px, py in pts]
    cv.poly(inner, alpha(C["fond"], op))


def button(cv, x0, y0, x1, y1, t, t_click, label, label_ok, bg, fg, bg_ok=None, fg_ok=None, r=None):
    """Bouton qui s'enfonce au clic puis change d'état (avec coche)."""
    r = r if r is not None else (y1 - y0) / 2
    q = t - t_click
    press = 1 - 0.035 * math.exp(-((q - 0.05) / 0.07) ** 2) if -0.2 < q < 0.4 else 1
    done = q >= 0.06
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    hw, hh = (x1 - x0) / 2 * press, (y1 - y0) / 2 * press
    col = (bg_ok or C["marine"]) if done else bg
    cv.rrect(cx - hw, cy - hh, cx + hw, cy + hh, r * press, fill=col)
    if done:
        k = ease_out_back(prog(q, 0.06, 0.35), 2)
        fg2 = fg_ok or C["citron"]
        tw = text_w(label_ok, "ui_bold", 25)
        cv.text(label_ok, "ui_bold", 25, fg2, cx - 18, cy, align="c", opacity=prog(q, 0.06, 0.15))
        cv.check(cx + tw / 2 + 2, cy, 20 * k, fg2, ease_out_cubic(prog(q, 0.15, 0.3)), width=3.2)
    else:
        cv.text(label, "ui_bold", 25, fg, cx, cy, align="c")


# --------------------------------------------------------------------------- #
# Visuels
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=None)
def asset(name, scale=1.0, tint=None):
    im = Image.open(os.path.join(ROOT, "assets", name)).convert("RGBA")
    if tint:
        col = Image.new("RGBA", im.size, tint[:3] + (255,))
        # on écarte les résidus quasi transparents du détourage
        col.putalpha(im.getchannel("A").point(lambda v: 0 if v < 70 else v))
        im = col
    return im.resize((max(1, int(im.width * scale * S)), max(1, int(im.height * scale * S))), Image.LANCZOS)


def logo_bleu(scale):
    return asset("logo_autopiec.png", scale, C["bleu"])


def logo_groupama(scale):
    return asset("logo_groupama.png", scale * 0.8, C["bleu"])


def lockup(world, cx, cy, scale, t=10.0, opacity=1.0):
    """AUTOPIEC  ×  Groupama Antilles-Guyane (version bleue pour fond blanc), entrée animée."""
    cv = Cv(world)
    a, g = logo_bleu(scale), logo_groupama(scale)
    gap = 48 * scale
    aw, gw = a.width / S, g.width / S
    x0 = cx - (aw + 2 * gap + gw) / 2
    p = ease_out_expo(prog(t, 0.0, 0.9))
    if p > 0:
        lay = Image.new("RGBA", a.size, (0, 0, 0, 0))
        lay.paste(a.crop((0, 0, max(1, int(a.width * p)), a.height)), (0, 0))
        b = (1 - p) * 8 * S
        if b > 0.4:
            lay = lay.filter(ImageFilter.GaussianBlur(b))
        cv.paste(lay, x0 + aw / 2, cy, opacity=opacity)
    q = ease_out_back(prog(t, 0.35, 0.45), 2.4)
    if q > 0:
        xc, k = x0 + aw + gap, 13 * scale * q
        for sgn in (1, -1):
            cv.line([(xc - k, cy - k * sgn), (xc + k, cy + k * sgn)], alpha(C["bleu"], opacity),
                    max(1.5, 3 * scale))
    r = prog(t, 0.5, 0.7)
    if r > 0:
        lay = g
        b = (1 - ease_out_cubic(r)) * 10 * S
        if b > 0.4:
            lay = lay.filter(ImageFilter.GaussianBlur(b))
        cv.paste(lay, x0 + aw + 2 * gap + gw / 2 + (1 - ease_out_expo(r)) * 30, cy,
                 opacity=clamp(r * 2) * opacity)


_BG = None


def background():
    """Fond blanc avec une grille de points très discrète, plus présente vers les bords."""
    global _BG
    if _BG is None:
        w, h = W * S, H * S
        img = Image.new("RGBA", (w, h), C["fond"])
        d = ImageDraw.Draw(img)
        step = 40
        for gy in range(0, H + step, step):
            for gx in range(0, W + step, step):
                dx, dy = (gx - W / 2) / (W / 2), (gy - H / 2) / (H / 2)
                k = clamp((math.sqrt(dx * dx * 0.8 + dy * dy) - 0.45) / 0.7)
                if k <= 0:
                    continue
                col = mix(C["fond"], (210, 218, 232), k * 0.75)
                d.ellipse((gx * S - 2.4, gy * S - 2.4, gx * S + 2.4, gy * S + 2.4), fill=col)
        _BG = img
    return _BG


# --------------------------------------------------------------------------- #
# Éléments communs aux étapes
# --------------------------------------------------------------------------- #
CARD_CX, CARD_CY = 575, 520
TEXT_X = 1120
NSTEPS = 7


def actor_chip(world, t, actor, x, y):
    a = CFG["acteurs"][actor]
    cv = Cv(world)
    p = ease_out_expo(prog(t, 0, 0.6))
    if p <= 0:
        return
    tw = text_w(a["nom"], "mono", 19, 0.14)
    w = 64 + tw + 22
    x0 = x - (1 - p) * 20
    op = clamp(p * 2)
    cv.rrect(x0, y - 24, x0 + w, y + 24, 24, fill=mix(C["fond"], C["bleu_clair"], op))
    cv.disc(x0 + 24, y, 17 * p, mix(C["fond"], C["bleu"], op))
    cv.icon(a["icone"], x0 + 24, y, 9.5 * p, C["fond"], bg=C["bleu"])
    cv.text(a["nom"], "mono", 19, C["bleu"], x0 + 52, y, opacity=op, tracking=0.14)


def step_text(world, t, idx, step):
    cv = Cv(world)
    label = f"ÉTAPE {idx + 1} / {NSTEPS}"
    op = prog(t, 0.15, 0.4)
    cv.text(label, "mono", 21, C["bleu"], TEXT_X, 330, opacity=op, tracking=0.2)
    actor_chip(world, t - 0.3, step["acteur"], TEXT_X, 392)
    bottom = blur_words(world, [step["titre"]], "titre", 66, TEXT_X, 440, t - 0.5,
                        max_w=700, line_h=74, stagger=0.055)
    lines = wrap_words([(w, False) for w in step["texte"].split()], "ui", 28, 680)
    for j, ln in enumerate(lines):
        s = " ".join(w for w, _ in ln)
        q = ease_out_cubic(prog(t, 1.3 + j * 0.08, 0.6))
        cv.text(s, "ui", 28, C["texte_gris"], TEXT_X, bottom + 34 + j * 38 + (1 - q) * 12, opacity=q)


def step_progress(world, t, idx):
    cv = Cv(world)
    n = NSTEPS
    sw, gap = 54, 10
    x0 = W / 2 - (n * sw + (n - 1) * gap) / 2
    y = 1010
    for k in range(n):
        x = x0 + k * (sw + gap)
        cv.rrect(x, y - 3, x + sw, y + 3, 3, fill=C["trait"])
        if k < idx:
            f = 1.0
        elif k == idx:
            f = ease_in_out_cubic(prog(t, 0.4, 4.6))
        else:
            f = 0
        if f > 0:
            cv.rrect(x, y - 3, x + sw * f, y + 3, 3, fill=C["bleu"])
    a, g = logo_bleu(0.3), logo_groupama(0.3)
    lockup(world, 80 + (a.width / S + g.width / S + 2 * 48 * 0.3) / 2, 64, 0.3)


def card_header(cv, w, left, right):
    cv.text(left, "mono", 18, C["texte_gris"], 32, 38, tracking=0.14)
    cv.text(right, "mono", 18, C["texte_gris"], w - 32, 38, align="r", tracking=0.14)
    cv.line([(0, 76), (w, 76)], C["trait"], 1.5)


def typed(s, p):
    n = int(round(len(s) * clamp(p)))
    return s[:n]


# --------------------------------------------------------------------------- #
# Scènes d'introduction
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=16)
def mini_card(kind):
    """Petites cartes décoratives (reprises des étapes) pour l'accroche et la fin."""
    if kind == "pv":
        img, cv = new_card(420, 200, 20)
        card_header(cv, 420, "PROCÈS-VERBAL", "PV")
        for j, (a, b) in enumerate((("Assureur", "Groupama"), ("Réparateur", "Garage KERN"), ("Pièce", "Pare-chocs"))):
            cv.text(a, "ui", 17, C["texte_gris"], 30, 102 + j * 34)
            cv.text(b, "ui_semi", 17, C["marine"], 390, 102 + j * 34, align="r")
    elif kind == "valide":
        img, cv = new_card(330, 120, 20)
        cv.text("ESPACE RÉPARATEUR", "mono", 14, C["texte_gris"], 26, 30, tracking=0.12)
        cv.rrect(26, 56, 304, 98, 21, fill=C["citron"])
        cv.text("Pièce validée", "ui_bold", 18, C["marine"], 150, 77, align="c")
        cv.check(225, 77, 15, C["marine"], width=2.4)
    elif kind == "commande":
        img, cv = new_card(380, 160, 20)
        card_header(cv, 380, "BON DE COMMANDE", "GROUPAMA")
        cv.text("Règlement", "ui", 17, C["texte_gris"], 30, 110)
        cv.text("Virement", "ui_semi", 17, C["marine"], 350, 110, align="r")
    elif kind == "livraison":
        img, cv = new_card(360, 150, 20)
        card_header(cv, 360, "LIVRAISON", "SUIVI")
        cv.check(40, 112, 16, C["vert"], width=2.6)
        cv.text("Livrée au réparateur", "ui_semi", 18, C["marine"], 62, 112)
    else:  # notification
        img, cv = new_card(400, 96, 20)
        cv.rrect(22, 22, 74, 74, 14, fill=C["bleu"])
        cv.text("G", "ui_bold", 24, C["fond"], 48, 48, align="c")
        cv.text("NOTIFICATION · ASSURANCE", "mono", 13, C["texte_gris"], 90, 36, tracking=0.1)
        cv.text("Pièce acceptée", "ui_semi", 19, C["marine"], 90, 64)
    return img


def floating_cards(world, t, items, opacity=1.0, blur=0.0):
    for kind, x, y, rot, t_in, vx, vy in items:
        lay = mini_card(kind)
        if blur:
            lay = blurred_mini(kind, blur)
        w = lay.width / S - 8
        h = lay.height / S - 8
        place_card(world, lay, x + vx * t, y + vy * t, t, t_in, w=w, h=h, r=20, rot=rot,
                   opacity=opacity, dur=1.3, from_dy=60, tilt_from=30)


@lru_cache(maxsize=16)
def blurred_mini(kind, blur):
    return mini_card(kind).filter(ImageFilter.GaussianBlur(blur * S))


def scene_accroche(world, t):
    cfg = CFG["accroche"]
    floating_cards(world, t, [
        ("pv", 300, 250, 6, 0.6, 6, -4), ("valide", 1610, 260, -7, 0.8, -5, 3),
        ("commande", 330, 840, -5, 1.0, 5, -3), ("livraison", 1590, 830, 6, 1.2, -6, -2)],
        opacity=0.55, blur=1.5)
    blur_words(world, cfg["lignes"], "titre", 84, W / 2, 440, t - 0.25, align="c", line_h=98, stagger=0.07)
    return 1.0 + 0.035 * ease_in_out_cubic(prog(t, 0, 4.5)), 0, 0


def scene_probleme(world, t):
    cfg = CFG["probleme"]
    blur_words(world, cfg["lignes"], "titre", 70, W / 2, 210, t - 0.15, align="c", line_h=82, stagger=0.055)
    cv = Cv(world)
    y = 720
    p_in = ease_out_expo(prog(t, 1.3, 0.8))
    if p_in > 0:
        # La pièce de réemploi rejoint les réparateurs agréés par la plateforme
        xl, xr = 680, 1240
        cv.rrect(xl - 110, y - 64, xl + 110, y + 64, 26, fill=mix(C["fond"], C["gris_clair"], p_in))
        cv.paste(asset("pare_chocs.png", 0.27), xl, y - 6, opacity=p_in)
        cv.text(cfg["piece"], "ui_semi", 22, C["marine"], xl, y + 98, align="c", opacity=p_in)
        x = xl + 130
        while x < xr - 70:
            cv.disc(x, y, 3.2, mix(C["fond"], C["texte_gris"], 0.45 * p_in))
            x += 18
        link = ease_in_out_cubic(prog(t, 1.9, 1.1))
        if link > 0:
            xe = lerp(xl + 120, xr - 66, link)
            cv.rrect(xl + 120, y - 3, xe, y + 3, 3, fill=C["bleu"])
            if link < 1:
                cv.disc(xe, y, 9, C["bleu"])
        cv.disc(xr, y, 62 * p_in, C["bleu_clair"])
        cv.icon("garage", xr, y, 30 * p_in, C["bleu"], bg=C["bleu_clair"])
        if t > 3.0:
            cv.check_badge(xr + 44, y - 44, 15, prog(t, 3.0, 0.5), C["vert"], C["fond"])
        cv.text(cfg["garage"], "ui_semi", 22, C["marine"], xr, y + 98, align="c", opacity=p_in)
    return 1.0 + 0.03 * ease_in_out_cubic(prog(t, 0, 4.5)), 0, 0


def scene_logo(world, t):
    cv = Cv(world)
    lockup(world, W / 2, 490, 1.05, t - 0.3)
    cv.text(CFG["logo"]["baseline"], "mono", 21, C["bleu"], W / 2, 600, align="c",
            opacity=ease_out_cubic(prog(t, 1.1, 0.6)), tracking=0.2)
    return 1.0 + 0.05 * ease_in_out_cubic(prog(t, 0, 3.8)), 0, 0


def scene_titre(world, t):
    cfg = CFG["titre"]
    cv = Cv(world)
    if cfg.get("surtitre"):
        cv.text(cfg["surtitre"], "mono", 21, C["bleu"], W / 2, 300, align="c",
                opacity=prog(t, 0.1, 0.4), tracking=0.24)
    blur_words(world, cfg["lignes"], "titre", 72, W / 2, 330 if cfg.get("surtitre") else 300, t - 0.25, align="c", line_h=84, stagger=0.06)

    # Aperçu du parcours : 7 acteurs reliés
    steps = CFG["etapes"]
    n = len(steps)
    xs = [lerp(330, 1590, i / (n - 1)) for i in range(n)]
    y = 660
    lp = ease_in_out_cubic(prog(t, 1.1, 1.8))
    if lp > 0:
        cv.line([(xs[0], y), (lerp(xs[0], xs[-1], lp), y)], C["trait"], 3)
    for i, st in enumerate(steps):
        th = 1.1 + 1.8 * (i / (n - 1))
        q = ease_out_back(prog(t, th - 0.1, 0.5), 2.2)
        if q <= 0:
            continue
        a = CFG["acteurs"][st["acteur"]]
        cv.disc(xs[i], y, 34 * q, C["fond"])
        cv.ring(xs[i], y, 34 * q, 2.5, C["bleu"])
        cv.icon(a["icone"], xs[i], y, 16 * q, C["bleu"], bg=C["fond"])
        op = prog(t, th, 0.4)
        cv.text(a["court"], "ui_semi", 17, C["marine"], xs[i], y + 62, align="c", opacity=op, tracking=0.04)
        cv.text(str(i + 1), "mono", 16, C["texte_gris"], xs[i], y - 58, align="c", opacity=op)
    q = ease_out_cubic(prog(t, 2.6, 0.6))
    cv.text(cfg["sous_titre"], "ui", 28, C["texte_gris"], W / 2, 850 + (1 - q) * 10, align="c", opacity=q)
    return 1.0 + 0.03 * ease_in_out_cubic(prog(t, 0, 4.3)), 0, 0


# --------------------------------------------------------------------------- #
# Étapes
# --------------------------------------------------------------------------- #
def card_origin(w, h):
    return CARD_CX - w / 2, CARD_CY - h / 2


def step1(world, t, step):
    c = step["carte"]
    w, h = 820, 580
    img, cv = new_card(w, h)
    card_header(cv, w, *c["entete"])
    for j, (lab, val) in enumerate(c["lignes"]):
        y = 112 + j * 66
        op = prog(t, 0.55 + j * 0.28, 0.3)
        cv.text(lab, "ui", 25, C["texte_gris"], 36, y, opacity=op)
        pv = prog(t, 0.65 + j * 0.28, 0.32)
        s = typed(val, pv)
        cv.text(s, "ui_semi", 25, C["marine"], 784, y, align="r")
        if 0 < pv < 1 or (pv >= 1 and j == len(c["lignes"]) - 1 and t < 2.1 and int(t * 4) % 2 == 0):
            cv.rrect(786, y - 15, 789, y + 15, 1, fill=C["bleu"])
        cv.line([(36, y + 33), (784, y + 33)], C["trait"], 1.2)
    y = 112 + 4 * 66
    q = prog(t, 1.95, 0.4)
    cv.text(c["eligible"][0], "ui", 25, C["texte_gris"], 36, y, opacity=q)
    if q > 0:
        cv.chip(c["eligible"][1], 784, y, "ui_bold", 21, C["marine"], C["citron"], align="r",
                opacity=q, icon_check=True)
    button(cv, 36, 470, 784, 542, t, 3.0, c["bouton"], c["bouton_ok"], C["bleu"], C["fond"])
    place_card(world, img, CARD_CX, CARD_CY - 30, t, 0.2, w=w, h=h)

    x0, y0 = card_origin(w, h)
    bx, by = x0 + 410, y0 - 30 + 506
    draw_cursor(world, t, [(2.1, 980, 900), (2.9, bx + 60, by + 8)], clicks=[3.0], t_hide=4.4)

    # Toast : Autopiec reçoit le PV
    tq = prog(t, 3.45, 0.7)
    if tq > 0:
        tw = 560
        timg, tcv = new_card(tw, 66, 33, fill=C["gris_clair"], border=False)
        done = t > 4.4
        if done:
            pulse = (t - 4.4) % 1.0
            tcv.disc(36, 33, 13 + 9 * pulse, mix(C["gris_clair"], C["bleu"], 0.35 * (1 - pulse)))
            tcv.disc(36, 33, 9, C["bleu"])
            tcv.text(c["toast_ok"], "ui_semi", 23, C["marine"], 62, 33)
        else:
            tcv.spinner(36, 33, 11, t, C["bleu"])
            tcv.text(c["toast"], "ui", 23, C["texte_gris"], 62, 33)
        place_card(world, timg, CARD_CX - 130, CARD_CY + 330, t, 3.45, w=tw, h=66, r=33, dur=0.8, from_dy=40)
    return 1.0 + 0.04 * ease_in_out_cubic(prog(t, 0, 6.3)), -20, 0


def step2(world, t, step):
    c = step["carte"]
    w, h = 820, 668
    img, cv = new_card(w, h)
    card_header(cv, w, *c["entete"])
    # PV reçu
    q = ease_out_expo(prog(t, 0.5, 0.6))
    if q > 0:
        cv.rrect(32, 98, 788, 196, 20, fill=mix(C["fond"], C["bleu_clair"], q))
        cv.disc(84, 147, 28 * q, C["bleu"])
        cv.icon("doc", 84, 147, 14 * q, C["fond"])
        cv.text(c["recu"][0], "ui_semi", 25, C["marine"], 128, 133, opacity=q)
        cv.text(c["recu"][1], "ui", 20, C["texte_gris"], 128, 164, opacity=q)
        cq = prog(t, 0.95, 0.4)
        if cq > 0:
            cv.chip(c["recu"][2], 764, 147, "ui_bold", 19, C["fond"], C["bleu"], align="r",
                    opacity=cq, icon_check=True)
    # Sourcing
    sup = c["fournisseurs"]
    results = [1.75, 2.1, 2.45, 2.8]
    nres = sum(1 for k in range(len(sup)) if t >= results[k])
    lq = prog(t, 1.05, 0.4)
    cv.text(c["sourcing"], "mono", 17, C["texte_gris"], 36, 236, opacity=lq, tracking=0.14)
    cv.text(f"{nres} / {len(sup)}", "mono", 17, C["bleu"], 784, 236, align="r", opacity=lq, tracking=0.1)
    for k, (name, loc, ok) in enumerate(sup):
        y = 290 + k * 70
        rq = ease_out_expo(prog(t, 1.15 + k * 0.1, 0.5))
        if rq <= 0:
            continue
        xo = (1 - rq) * 30
        cv.disc(64 + xo, y, 22, mix(C["fond"], C["gris_clair"], rq))
        cv.text(f"F{k + 1}", "ui_bold", 17, C["marine"], 64 + xo, y, align="c", opacity=rq)
        cv.text(name, "ui_semi", 23, C["marine"], 100 + xo, y, opacity=rq)
        cv.text("· " + loc, "ui", 23, C["texte_gris"], 108 + xo + text_w(name, "ui_semi", 23), y, opacity=rq)
        if t < results[k]:
            cv.spinner(766, y, 11, t + k * 0.3, C["bleu"])
        else:
            rp = ease_out_back(prog(t, results[k], 0.4), 2)
            if ok:
                cv.chip(c["disponible"], 784, y, "ui_bold", 19, C["vert"], C["vert_clair"], align="r",
                        opacity=clamp(rp), icon_check=True)
            else:
                cv.chip(c["indisponible"], 784, y, "ui_semi", 19, C["texte_gris"], C["gris_clair"],
                        align="r", opacity=clamp(rp))
        if k < len(sup) - 1:
            cv.line([(36, y + 35), (784, y + 35)], C["trait"], 1.2)
    # Bilan
    bq = ease_out_expo(prog(t, 3.25, 0.6))
    if bq > 0:
        cv.rrect(32, 592, 32 + 756 * bq, 644, 26, fill=C["marine"])
        if bq > 0.6:
            o = prog(t, 3.5, 0.3)
            cv.check(70, 618, 18, C["citron"], o, width=3)
            cv.text(c["bilan"], "ui_bold", 22, C["citron"], 96, 618, opacity=o)
    place_card(world, img, CARD_CX, CARD_CY, t, 0.2, w=w, h=h)

    # onde « radar » derrière la carte pendant le sourcing
    cvw = Cv(world)
    for k in range(3):
        q = (t - 1.2 - k * 0.5)
        if 0 < q < 1.4 and t < 3.0:
            r = 200 + 360 * ease_out_cubic(q / 1.4)
            cvw.ring(CARD_CX + 380, CARD_CY - 40, r, 2.5, alpha(C["bleu"], 0.25 * (1 - q / 1.4)))
    return 1.0 + 0.04 * ease_in_out_cubic(prog(t, 0, 6.3)), -20, 0


def step3(world, t, step):
    c = step["carte"]
    w, h = 820, 704
    img, cv = new_card(w, h)
    card_header(cv, w, *c["entete"])
    cv.rrect(32, 96, 788, 330, 20, fill=C["gris_clair"])
    bp = ease_out_expo(prog(t, 0.55, 0.9))
    if bp > 0:
        bump = asset("pare_chocs.png", 0.88)
        cv.paste(bump, 410 + (1 - bp) * 60, 213 + math.sin(t * 1.8) * 3, opacity=clamp(bp * 1.5))
    q = ease_out_cubic(prog(t, 0.8, 0.5))
    cv.text(c["piece"], "titre", 38, C["marine"], 36, 372 + (1 - q) * 10, opacity=q)
    x = 36
    for k, tag in enumerate(c["etiquettes"]):
        tq = prog(t, 0.95 + k * 0.1, 0.35)
        fg, bg = (C["bleu"], C["bleu_clair"]) if k == 0 else (C["vert"], C["vert_clair"])
        x += cv.chip(tag, x, 424, "ui_semi", 19, fg, bg, opacity=tq) + 10
    for k, ctl in enumerate(c["controles"]):
        y = 484 + k * 40
        tk = 1.35 + k * 0.3
        cq = prog(t, tk, 0.3)
        if cq > 0:
            cv.check_badge(48, y, 12, prog(t, tk + 0.1, 0.5), C["vert"], C["fond"])
            cv.text(ctl, "ui", 22, C["marine"], 72, y, opacity=cq)
    t_click = 3.25
    button(cv, 32, 604, 402, 676, t, t_click, c["valider"], c["valide"], C["citron"], C["marine"])
    cv.rrect(418, 604, 788, 676, 36, outline=C["marine"], width=2)
    cv.text(c["refuser"], "ui_bold", 25, C["marine"], 603, 640, align="c")
    place_card(world, img, CARD_CX, CARD_CY, t, 0.2, w=w, h=h)

    x0, y0 = card_origin(w, h)
    draw_cursor(world, t, [(2.2, 1000, 880), (3.15, x0 + 230, y0 + 646)], clicks=[t_click], t_hide=4.6)
    return 1.0 + 0.04 * ease_in_out_cubic(prog(t, 0, 6.3)), -20, 0


STATUS_STYLE = {
    "Livrée": ("vert", "vert_clair"), "Commandée": ("bleu", "bleu_clair"),
    "Acceptée": ("vert", "vert_clair"), "Proposée": ("texte_gris", "gris_clair"),
    "En attente": ("texte_gris", "gris_clair"),
}


def status_chip(cv, label, x, y, opacity=1.0, check=False):
    fg, bg = STATUS_STYLE.get(label, ("texte_gris", "gris_clair"))
    return cv.chip(label, x, y, "ui_semi", 18, C[fg], C[bg], align="r", opacity=opacity, icon_check=check)


def step4(world, t, step):
    c = step["carte"]
    w, h = 840, 470
    img, cv = new_card(w, h)
    card_header(cv, w, *c["entete"])
    cols = c["colonnes"]
    cv.text(cols[0], "mono", 15, C["texte_gris"], 36, 112, tracking=0.12)
    cv.text(cols[1], "mono", 15, C["texte_gris"], 250, 112, tracking=0.12)
    cv.text(cols[2], "mono", 15, C["texte_gris"], 804, 112, align="r", tracking=0.12)
    t_flip = 1.55
    for k, (num, piece, statut) in enumerate(c["dossiers"]):
        y = 168 + k * 72
        rq = ease_out_expo(prog(t, 0.5 + k * 0.1, 0.5))
        if rq <= 0:
            continue
        focus = statut == "En attente"
        if focus and t >= t_flip:
            hq = prog(t, t_flip, 0.3) * (1 - 0.6 * prog(t, 3.5, 1.0))
            cv.rrect(20, y - 32, w - 20, y + 32, 14, fill=mix(C["fond"], C["bleu_clair"], hq))
        cv.text(num, "ui_bold", 22, C["marine"], 36, y, opacity=rq)
        cv.text(piece, "ui", 22, C["marine"], 250, y, opacity=rq)
        if focus and t >= t_flip:
            fp = ease_out_back(prog(t, t_flip, 0.4), 2.2)
            status_chip(cv, c["statut_final"], 804, y, opacity=clamp(fp), check=True)
        else:
            status_chip(cv, statut, 804, y, opacity=rq)
        if k < len(c["dossiers"]) - 1:
            cv.line([(36, y + 36), (804, y + 36)], C["trait"], 1.2)
    place_card(world, img, CARD_CX, 420, t, 0.2, w=w, h=h)

    # Notification
    nw, nh = 640, 108
    nimg, ncv = new_card(nw, nh, 24)
    ncv.rrect(22, 22, 86, 86, 16, fill=C["bleu"])
    ncv.text("G", "ui_bold", 30, C["fond"], 54, 54, align="c")
    ncv.text(c["notif_app"], "mono", 15, C["texte_gris"], 106, 38, tracking=0.12)
    ncv.text("à l'instant", "ui", 17, C["texte_gris"], nw - 26, 38, align="r")
    ncv.text(c["notif_texte"], "ui_semi", 24, C["marine"], 106, 72)
    place_card(world, nimg, CARD_CX + 90, 735, t, t_flip, w=nw, h=nh, r=24, dur=0.9, from_dy=-60, tilt_from=-18)
    # Rappel du cas de refus
    rq = ease_out_cubic(prog(t, 2.5, 0.6))
    if rq > 0:
        cvw = Cv(world)
        tw = text_w(c["refus"], "ui", 21)
        x0 = CARD_CX + 90 - nw / 2
        cvw.rrect(x0, 822 + (1 - rq) * 10, x0 + tw + 70, 876 + (1 - rq) * 10, 27,
                  fill=mix(C["fond"], C["gris_clair"], rq))
        cvw.disc(x0 + 28, 849 + (1 - rq) * 10, 9 * rq, C["rouge"])
        cvw.text(c["refus"], "ui", 21, C["texte_gris"], x0 + 48, 849 + (1 - rq) * 10, opacity=rq)
    return 1.0 + 0.04 * ease_in_out_cubic(prog(t, 0, 6.3)), -20, 0


def step5(world, t, step):
    c = step["carte"]
    pw, ph = 410, 800
    img = Image.new("RGBA", (int((pw + 8) * S), int((ph + 8) * S)), (0, 0, 0, 0))
    cv = Cv(img, 4, 4)
    cv.rrect(0, 0, pw, ph, 66, fill=C["marine"])
    # écran dégradé
    sw, sh = pw - 28, ph - 28
    grad = np.linspace(0, 1, int(sh * S))[:, None, None]
    top = np.array(hex2rgb("#0B3FA8")[:3], float)
    bot = np.array(hex2rgb("#5B9BF8")[:3], float)
    arr = (top * (1 - grad) + bot * grad).repeat(int(sw * S), axis=1).astype(np.uint8)
    scr = Image.fromarray(np.dstack([arr, np.full(arr.shape[:2], 255, np.uint8)]), "RGBA")
    m = Image.new("L", scr.size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, scr.width - 1, scr.height - 1), radius=int(54 * S), fill=255)
    scr.putalpha(m)
    img.alpha_composite(scr, (int(18 * S), int(18 * S)))
    cv.rrect(pw / 2 - 62, 30, pw / 2 + 62, 62, 16, fill=C["marine"])
    cv.text(c["date"], "ui_semi", 20, alpha(C["fond"], 0.85), pw / 2, 128, align="c")
    cv.text(c["heure"], "titre_b", 92, C["fond"], pw / 2, 196, align="c")
    for k, msg in enumerate(c["notifs"]):
        tn = 0.95 + k * 1.3
        q = prog(t, tn, 0.7)
        if q <= 0:
            continue
        e = spring(q * 1.2, 6.5, 1.8)
        y0 = 300 + k * 140 + (1 - e) * -50
        nimg, ncv = new_card(354, 124, 24, fill=mix(C["fond"], C["bleu_clair"], 0.2), border=False)
        ncv.rrect(18, 18, 58, 58, 12, fill=C["bleu"])
        ncv.icon("autopiec", 38, 38, 13, C["fond"])
        ncv.text(c["app"], "mono", 13, C["texte_gris"], 70, 30, tracking=0.1)
        ncv.text("à l'instant", "ui", 14, C["texte_gris"], 336, 30, align="r")
        lines = wrap_words([(x, False) for x in msg.split()], "ui_semi", 19, 300)
        for j, ln in enumerate(lines[:3]):
            ncv.text(" ".join(x for x, _ in ln), "ui_semi", 19, C["marine"], 20, 72 + j * 25)
        composite(img, fade(nimg, clamp(q * 2.5)), (pw / 2 - 177 + 4) * S - 4 * S, (y0 + 4) * S - 4 * S)
    place_card(world, img, CARD_CX, CARD_CY + math.sin(t * 1.4) * 4 * prog(t, 1.2, 0.5), t, 0.2,
               w=pw, h=ph, r=66, dur=1.1, tilt_from=28)
    return 1.0 + 0.04 * ease_in_out_cubic(prog(t, 0, 6.3)), -20, 0


def step6(world, t, step):
    c = step["carte"]
    w, h = 820, 580
    img, cv = new_card(w, h)
    card_header(cv, w, *c["entete"])
    for j, (lab, val) in enumerate(c["lignes"]):
        y = 112 + j * 66
        op = ease_out_cubic(prog(t, 0.55 + j * 0.12, 0.4))
        cv.text(lab, "ui", 25, C["texte_gris"], 36, y, opacity=op)
        cv.text(val, "ui_semi", 25, C["marine"], 784 + (1 - op) * 12, y, align="r", opacity=op)
        cv.line([(36, y + 33), (784, y + 33)], C["trait"], 1.2)
    y = 112 + 4 * 66
    q = prog(t, 1.2, 0.4)
    cv.text(c["validation"][0], "ui", 25, C["texte_gris"], 36, y, opacity=q)
    if q > 0:
        cv.chip(c["validation"][1], 784, y, "ui_bold", 20, C["vert"], C["vert_clair"], align="r",
                opacity=q, icon_check=True)
    t_click = 2.85
    button(cv, 36, 470, 784, 542, t, t_click, c["bouton"], c["bouton_ok"], C["bleu"], C["fond"])
    place_card(world, img, CARD_CX, CARD_CY, t, 0.2, w=w, h=h)
    x0, y0 = card_origin(w, h)
    draw_cursor(world, t, [(1.9, 1000, 920), (2.75, x0 + 470, y0 + 512)], clicks=[t_click], t_hide=4.5)

    # Le bon de commande « part » vers Autopiec
    fq = prog(t, 3.3, 1.0)
    if 0 < fq < 1:
        cvw = Cv(world)
        e = ease_in_out_cubic(fq)
        x = lerp(CARD_CX + 300, CARD_CX + 520, e)
        y = lerp(CARD_CY + 260, CARD_CY - 330, e) - math.sin(e * math.pi) * 60
        r = 30 * (1 - 0.4 * e)
        cvw.disc(x, y, r, alpha(C["bleu"], 1 - fq ** 3))
        cvw.icon("doc", x, y, r * 0.5, alpha(C["fond"], 1 - fq ** 3))
    return 1.0 + 0.04 * ease_in_out_cubic(prog(t, 0, 6.3)), -20, 0


def step7(world, t, step):
    c = step["carte"]
    w, h = 820, 580
    img, cv = new_card(w, h)
    card_header(cv, w, *c["entete"])
    y = 176
    xa, xb = 96, 724
    a_in = ease_out_expo(prog(t, 0.45, 0.6))
    move = ease_in_out_cubic(prog(t, 0.95, 2.4))
    arrived = t >= 3.35
    # pointillés + progression
    x = xa + 40
    while x < xb - 40:
        cv.disc(x, y, 3, mix(C["fond"], C["trait"], a_in))
        x += 16
    xt = lerp(xa + 30, xb - 70, move)
    if move > 0:
        cv.rrect(xa + 30, y - 3, xt, y + 3, 3, fill=C["bleu"])
    cv.disc(xa, y, 30 * a_in, C["bleu_clair"])
    cv.icon("autopiec", xa, y, 14 * a_in, C["bleu"])
    cv.text(c["depart"], "ui_semi", 20, C["marine"], xa, y + 54, align="c", opacity=a_in)
    gcol = C["vert_clair"] if arrived else C["gris_clair"]
    cv.disc(xb, y, 30 * a_in, gcol)
    cv.icon("garage", xb, y, 15 * a_in, C["vert"] if arrived else C["texte_gris"], bg=gcol)
    cv.text(c["arrivee"], "ui_semi", 20, C["marine"], xb, y + 54, align="c", opacity=a_in)
    if arrived:
        cv.check_badge(xb + 24, y - 24, 13, prog(t, 3.35, 0.5), C["vert"], C["fond"])
    if a_in > 0 and not arrived:
        bob = math.sin(t * 16) * 1.2 if 0 < move < 1 else 0
        cv.icon("camion", xt + 8, y - 4 + bob, 26, C["bleu"], bg=C["fond"])
    cv.line([(36, 272), (784, 272)], C["trait"], 1.2)
    marks = [0.0, 0.3, 0.65, 1.0]
    for k, label in enumerate(c["jalons"]):
        yy = 320 + k * 60
        tk = 0.95 + 2.4 * marks[k] + (0.45 if k == 3 else 0.1)
        q = prog(t, tk, 0.4)
        cv.ring(50, yy, 13, 1.8, C["trait"])
        if q > 0:
            cv.check_badge(50, yy, 13, q, C["vert"], C["fond"])
        bold = k == 3 and q > 0
        cv.text(label, "ui_semi" if bold else "ui", 23, C["marine"] if q > 0 else C["texte_gris"], 78, yy)
    place_card(world, img, CARD_CX, CARD_CY, t, 0.2, w=w, h=h)
    return 1.0 + 0.04 * ease_in_out_cubic(prog(t, 0, 6.3)), -20, 0


STEP_FUNCS = [step1, step2, step3, step4, step5, step6, step7]


def make_step(idx):
    def scene(world, t):
        step = CFG["etapes"][idx]
        cam = STEP_FUNCS[idx](world, t, step)
        step_text(world, t, idx, step)
        step_progress(world, t, idx)
        return cam
    return scene


# --------------------------------------------------------------------------- #
# Scènes de conclusion
# --------------------------------------------------------------------------- #
def scene_suivi(world, t):
    cfg = CFG["suivi"]
    blur_words(world, [cfg["titre"]], "titre", 80, W / 2, 170, t - 0.15, align="c", line_h=90, stagger=0.06)
    w, h = 1060, 500
    img, cv = new_card(w, h, 28, fill=C["marine"], border=False)
    cv.text(cfg["entete"][0], "mono", 18, C["citron"], 36, 40, tracking=0.16)
    cv.text(cfg["entete"][1], "mono", 18, alpha(C["fond"], 0.55), w - 36, 40, align="r", tracking=0.16)
    cv.line([(0, 80), (w, 80)], mix(C["marine"], C["fond"], 0.12), 1.5)
    statuts = cfg["statuts"]
    for k, (num, piece, final) in enumerate(cfg["dossiers"]):
        y = 126 + k * 76
        rq = ease_out_expo(prog(t, 0.7 + k * 0.08, 0.5))
        if rq <= 0:
            continue
        cv.text(f"Dossier {num}", "ui_semi", 22, mix(C["marine"], C["fond"], 0.95 * rq), 36, y)
        cv.text(piece, "ui", 22, mix(C["marine"], C["fond"], 0.6 * rq), 260, y)
        idx = int(clamp((t - 1.0 - k * 0.12) / 0.5, 0, final))
        # mini-progression en 5 segments
        for s in range(len(statuts)):
            x = 600 + s * 40
            col = C["citron"] if s <= idx else mix(C["marine"], C["fond"], 0.15)
            cv.rrect(x, y - 3, x + 32, y + 3, 3, fill=mix(C["marine"], col, rq))
        lab = statuts[idx]
        last = idx == len(statuts) - 1
        fg = C["marine"] if last else C["fond"]
        bg = C["citron"] if last else mix(C["marine"], C["fond"], 0.14)
        cv.chip(lab, w - 36, y, "ui_semi", 19, fg, bg, align="r", opacity=rq, icon_check=last)
        if k < len(cfg["dossiers"]) - 1:
            cv.line([(36, y + 38), (w - 36, y + 38)], mix(C["marine"], C["fond"], 0.08), 1.2)
    place_card(world, img, W / 2, 610, t, 0.35, w=w, h=h, r=28, dur=1.1)
    return 1.0 + 0.04 * ease_in_out_cubic(prog(t, 0, 5.3)), 0, 0


def scene_benefices(world, t):
    cfg = CFG["benefices"]
    cv = Cv(world)
    cv.text(cfg["surtitre"], "mono", 21, C["bleu"], W / 2, 300, align="c", opacity=prog(t, 0.1, 0.4),
            tracking=0.24)
    starts = [0.3, 1.25, 2.2]
    for k, line in enumerate(cfg["lignes"]):
        dim = 1 - 0.55 * prog(t, starts[k + 1], 0.5) if k + 1 < len(starts) else 1
        blur_words(world, [line], "titre", 84, W / 2, 350 + k * 112, t - starts[k], align="c",
                   line_h=100, stagger=0.07, opacity=dim)
    return 1.0 + 0.04 * ease_in_out_cubic(prog(t, 0, 5.3)), 0, 0


def scene_pilote(world, t):
    cfg = CFG["pilote"]
    cv = Cv(world)
    blur_words(world, cfg["lignes"], "titre", 88, W / 2, 300, t - 0.15, align="c", line_h=100, stagger=0.07)
    q = ease_out_back(prog(t, 1.2, 0.6), 1.8)
    if q > 0:
        tw = text_w(cfg["offre"], "ui_semi", 26)
        cv.rrect(W / 2 - tw / 2 - 30, 568 - 30 * q, W / 2 + tw / 2 + 30, 568 + 30 * q, 30 * q,
                 fill=C["gris_clair"])
        cv.text(cfg["offre"], "ui_semi", 26, C["marine"], W / 2, 568, align="c", opacity=prog(t, 1.35, 0.3))
    n = cfg["mois"]
    sw, gap = 120, 14
    x0 = W / 2 - (n * sw + (n - 1) * gap) / 2
    for k in range(n):
        x = x0 + k * (sw + gap)
        st = 1.7 + k * 0.18
        f = ease_out_expo(prog(t, st, 0.5))
        cv.rrect(x, 700, x + sw, 708, 4, fill=C["trait"])
        if f > 0:
            cv.rrect(x, 700, x + sw * f, 708, 4, fill=C["bleu"])
        cv.text(f"Mois {k + 1}", "mono", 16, C["texte_gris"], x + sw / 2, 738, align="c",
                opacity=prog(t, st, 0.4), tracking=0.08)
    return 1.0 + 0.04 * ease_in_out_cubic(prog(t, 0, 4.8)), 0, 0


def scene_fin(world, t):
    cfg = CFG["fin"]
    floating_cards(world, t, [
        ("pv", 250, 170, 8, 0.4, 5, -3), ("valide", 1650, 150, -8, 0.55, -4, 2),
        ("commande", 270, 930, -6, 0.7, 4, -3), ("livraison", 1660, 920, 7, 0.85, -5, -2),
        ("notif", 1790, 560, -4, 1.0, -3, 2), ("valide", 150, 580, 5, 1.1, 3, -2)])
    cv = Cv(world)
    lockup(world, W / 2, 345, 0.7, t - 0.2)
    blur_words(world, cfg["lignes"], "titre", 84, W / 2, 425, t - 0.5, align="c", line_h=98, stagger=0.07)
    cv.text(cfg["contact"], "mono", 22, C["bleu"], W / 2, 690, align="c",
            opacity=ease_out_cubic(prog(t, 1.6, 0.6)), tracking=0.18)
    return 1.0 + 0.05 * ease_in_out_cubic(prog(t, 0, 6.0)), 0, 0


# --------------------------------------------------------------------------- #
# Montage : (durée, scène, transition d'entrée, sons [(t local, type)])
# --------------------------------------------------------------------------- #
STEP_DUR = 5.5
TIMELINE = [
    (3.5, scene_accroche, None, [(0.3, "pop")]),
    (4.0, scene_probleme, "flou", [(1.0, "pop"), (1.7, "souffle")]),
    (3.0, scene_logo, "zoom", [(0.3, "impact")]),
    (4.0, scene_titre, "flou", [(1.2, "tic"), (1.5, "tic"), (1.8, "tic"), (2.1, "tic"), (2.4, "tic"), (2.7, "tic"), (3.0, "tic")]),
    (STEP_DUR, make_step(0), "zoom", [(0.7, "frappe"), (0.98, "frappe"), (1.26, "frappe"), (1.54, "frappe"),
                                       (2.0, "pop"), (3.0, "clic"), (3.05, "succes"), (4.4, "ping")]),
    (STEP_DUR, make_step(1), "travelling", [(0.95, "pop"), (1.75, "tic"), (2.1, "tic"), (2.45, "tic"),
                                             (2.8, "tic"), (3.3, "succes")]),
    (STEP_DUR, make_step(2), "travelling", [(1.45, "tic"), (1.75, "tic"), (2.05, "tic"), (3.25, "clic"),
                                             (3.3, "succes")]),
    (STEP_DUR, make_step(3), "travelling", [(1.55, "ping"), (2.5, "pop")]),
    (STEP_DUR, make_step(4), "travelling", [(0.95, "ping"), (2.25, "ping")]),
    (STEP_DUR, make_step(5), "travelling", [(1.2, "pop"), (2.85, "clic"), (2.9, "succes"), (3.3, "souffle")]),
    (STEP_DUR, make_step(6), "travelling", [(1.0, "souffle"), (3.35, "succes")]),
    (4.5, scene_suivi, "zoom", [(1.5, "tic"), (2.0, "tic"), (2.5, "tic"), (3.0, "succes")]),
    (4.5, scene_benefices, "flou", [(0.3, "pop"), (1.25, "pop"), (2.2, "pop")]),
    (4.0, scene_pilote, "flou", [(1.2, "pop")]),
    (5.5, scene_fin, "zoom", [(0.2, "impact")]),
]
STARTS = []
_acc = 0.0
for _d, *_ in TIMELINE:
    STARTS.append(_acc)
    _acc += _d
DURATION = _acc


def scene_local_time(k, t):
    return t - STARTS[k] + (TR / 2 if k > 0 else 0)


# --------------------------------------------------------------------------- #
# Rendu
# --------------------------------------------------------------------------- #
def render_scene(k, t):
    world = background().copy()
    zoom, dx, dy = TIMELINE[k][1](world, scene_local_time(k, t)) or (1.0, 0, 0)
    # Caméra : recadrage puis réduction à la taille finale (anticrénelage)
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
    if z < 1:   # zoom arrière : image réduite au centre d'un fond blanc
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
    # transition en cours ?
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
            A2 = blur_arr(A, 14 * p)
            B2 = blur_arr(B, 14 * (1 - p))
            out = A2 * (1 - p) + B2 * p

    f = min(prog(t, 0, 0.4), 1 - prog(t, DURATION - 0.9, 0.9))
    if f < 1:
        out = 255 - (255 - out) * f   # fondu depuis / vers le blanc
    return np.clip(out, 0, 255).astype(np.uint8).tobytes()


def init_worker(cfg):
    CFG.clear()
    CFG.update(cfg)
    C.clear()
    C.update({k: hex2rgb(v) for k, v in cfg["couleurs"].items()})


# --------------------------------------------------------------------------- #
# Son : musique douce (Cmaj7 - Am7 - Fmaj7 - G6) + bruitages d'interface
# --------------------------------------------------------------------------- #
def synth_audio(path, sr=44100):
    n = int(DURATION * sr)
    mus = np.zeros(n, np.float32)
    sfx = np.zeros(n, np.float32)
    rng = np.random.default_rng(11)
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

    def shaker():
        x = tt(0.08)
        s = rng.standard_normal(len(x)).astype(np.float32)
        s = np.diff(s, prepend=0)
        return s * np.exp(-x / 0.02) * 0.08

    chords = [(48, 60, 64, 67, 71), (45, 57, 60, 64, 67), (41, 57, 60, 64, 69), (43, 55, 59, 62, 64)]
    bar = 4 * beat
    end = DURATION - 3.0
    beats_in = STARTS[4] - 0.0          # le rythme démarre avec les étapes
    for b in range(int(math.ceil(end / bar))):
        ch = chords[b % 4]
        t0 = b * bar
        add(mus, pad([midi(m) for m in ch[1:]], bar + 0.6), t0, 0.10)
        add(mus, keys(midi(ch[0]), 1.6, 0.8) * 0.9, t0, 0.20)
        add(mus, keys(midi(ch[0] - 12), 1.8, 1.0), t0, 0.16)
        pattern = [0, 2, 3, 1, 2, 4, 3, 2]
        for k8, idx in enumerate(pattern):
            add(mus, keys(midi(ch[1 + idx % 4] + 12), 0.6, 0.25), t0 + k8 * beat / 2, 0.07)
    for k in range(int(end / beat)):
        t0 = k * beat
        if t0 >= beats_in:
            if k % 2 == 0:
                add(mus, kick(), t0, 0.55)
            add(mus, shaker(), t0 + beat / 2, 1.0)
            add(mus, shaker(), t0, 0.5)
    add(mus, pad([midi(m) for m in (60, 64, 67, 71, 76)], 3.5), end, 0.12)
    add(mus, keys(midi(48), 3.0, 1.4), end, 0.25)
    x = np.arange(n) / sr
    mus *= np.clip((DURATION - x) / 1.2, 0, 1)

    # --- bruitages
    def clic():
        x = tt(0.05)
        s = rng.standard_normal(len(x)) * np.exp(-x / 0.004)
        return (np.sin(2 * np.pi * 2200 * x) * np.exp(-x / 0.01) * 0.6 + s * 0.4)

    def tic():
        x = tt(0.04)
        return np.sin(2 * np.pi * 1800 * x) * np.exp(-x / 0.008)

    def frappe():
        out = np.zeros(int(0.35 * sr))
        for j in range(6):
            x = tt(0.03)
            s = rng.standard_normal(len(x)) * np.exp(-x / 0.004) * 0.5
            o = int(j * 0.05 * sr)
            out[o:o + len(s)] += s
        return out

    def pop():
        x = tt(0.12)
        f = 900 * np.exp(-x / 0.03) + 300
        return np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-x / 0.035)

    def ping():
        x = tt(0.9)
        return (np.sin(2 * np.pi * 1318.5 * x) * np.exp(-x / 0.25) +
                0.7 * np.sin(2 * np.pi * 1975.5 * x) * np.exp(-(x - 0.09).clip(0) / 0.3) * (x > 0.09))

    def succes():
        out = np.zeros(int(1.0 * sr))
        for j, m in enumerate((72, 76, 79)):
            s = keys(midi(m), 0.8, 0.3)
            o = int(j * 0.07 * sr)
            out[o:o + len(s)] += s[:len(out) - o]
        return out * 0.6

    def souffle(dur=0.8):
        m = int(dur * sr)
        noise = rng.standard_normal(m)
        x = np.arange(m) / sr
        cutoff = 400 + 5000 * np.sin(np.pi * x / dur) ** 2
        a = 1 - np.exp(-2 * np.pi * cutoff / sr)
        y = np.zeros(m)
        acc = 0.0
        for i in range(m):
            acc += a[i] * (noise[i] - acc)
            y[i] = acc
        return y * np.sin(np.pi * x / dur) ** 2

    def impact():
        x = tt(1.5)
        f = 40 + 90 * np.exp(-x / 0.05)
        low = np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-x / 0.4)
        return low + souffle(1.5) * 0.3

    gen = {"clic": (clic, 0.5), "tic": (tic, 0.18), "frappe": (frappe, 0.25), "pop": (pop, 0.35),
           "ping": (ping, 0.22), "succes": (succes, 0.3), "souffle": (souffle, 0.35), "impact": (impact, 0.55)}
    cache = {}
    for k, (dur, _f, trans, sounds) in enumerate(TIMELINE):
        origin = STARTS[k] - (TR / 2 if k > 0 else 0)
        for lt, kind in sounds:
            if kind not in cache:
                cache[kind] = gen[kind][0]().astype(np.float32)
            add(sfx, cache[kind], origin + lt, gen[kind][1])
        if trans:
            if "souffle_tr" not in cache:
                cache["souffle_tr"] = souffle(0.9).astype(np.float32)
            add(sfx, cache["souffle_tr"], STARTS[k] - 0.5, 0.22 if trans == "travelling" else 0.16)

    out = np.tanh((mus * 0.9 + sfx) * 1.1)
    out /= max(1e-6, np.abs(out).max()) / 0.8
    pcm = (out * 32767).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(np.stack([pcm, pcm], axis=1).tobytes())


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(ROOT, "config_parcours.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "output", "parcours-autopiec.mp4"))
    ap.add_argument("--preview", nargs="*", type=float, metavar="SEC")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    init_worker(cfg)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)

    if args.preview is not None:
        outdir = os.path.join(os.path.dirname(os.path.abspath(args.out)), "apercu-parcours")
        os.makedirs(outdir, exist_ok=True)
        secs = args.preview or [s + d - 0.6 for s, (d, *_) in zip(STARTS, TIMELINE)]
        for sec in secs:
            buf = render_frame(int(round(sec * FPS)))
            Image.frombytes("RGB", (W, H), buf).save(os.path.join(outdir, f"t{sec:05.2f}.png"))
        print(f"Durée : {DURATION:.1f} s — aperçus dans {outdir}")
        return

    total = int(round(DURATION * FPS))
    wav = os.path.splitext(args.out)[0] + ".wav"
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
    if cfg.get("musique", True):
        synth_audio(wav)
        cmd += ["-i", wav, "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", "17", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-shortest", args.out]
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    with Pool(args.jobs, initializer=init_worker, initargs=(cfg,)) as pool:
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
