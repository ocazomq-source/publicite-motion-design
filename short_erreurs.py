#!/usr/bin/env python3
"""Short vertical (≤ 30 s, 1080x1920) « 3 erreurs qui coûtent cher quand on débute en bourse » (O'MORGAN).

Format réseaux sociaux, pensé pour être compris du premier coup : trois erreurs seulement,
une scène d'environ 8 s par erreur avec une situation du quotidien (discussion de groupe,
alerte sur le téléphone, tous les œufs dans le même panier), un seul chiffre clé à l'écran, le bon réflexe
en fin de scène, sous-titres synchronisés avec la voix off et question finale pour les
commentaires. Voix off (Kokoro, plus naturelle), musique et bruitages reprennent le moteur
d'erreurs_bourse.py.

Usage :
    python3 short_erreurs.py              # -> output/short-3-erreurs.mp4
    python3 short_erreurs.py --preview    # images clés dans output/apercu-short/
    python3 short_erreurs.py --preview 7.2
"""
import argparse
import json
import math
import os
import subprocess
import sys

import numpy as np
from PIL import Image

import erreurs_bourse as eb
from erreurs_bourse import (C, CFG, NB, Cv, clamp, composite, ease_in_out_cubic, ease_out_back,
                            ease_out_cubic, ease_out_expo, fade, fit_size, fmt_n, lerp, markup_words,
                            mix, monotone, clip_path, prog, text_layer, text_w, word_layer)

ROOT = os.path.dirname(os.path.abspath(__file__))
W, H = 1080, 1920
S = eb.S
FPS = 30
TR = 0.45            # coupe en volet (s)
MAX_DUREE = 30.0
LEAD = 0.35          # la voix démarre peu après la coupe


# --------------------------------------------------------------------------- #
# Texte qui « pop » mot à mot
# --------------------------------------------------------------------------- #
def ink(bg):
    """Couleur de texte lisible sur un fond donné."""
    r, g, b = bg[:3]
    return C["marine"] if 0.299 * r + 0.587 * g + 0.114 * b > 150 else C["blanc"]


def pop_words(world, lines, kind, size, y_top, t, color, accent, stagger=0.08, lh=1.1, cx=None):
    """Lignes centrées ; chaque mot grossit avec un léger rebond. Renvoie le bas du bloc."""
    cx = W / 2 if cx is None else cx
    space = eb.font(kind, size).getlength(" ") / S
    i = 0
    for li, line in enumerate(lines):
        words = markup_words(line)
        widths = [text_w(w, kind, size) for w, _ in words]
        x = cx - (sum(widths) + space * (len(words) - 1)) / 2
        cy = y_top + li * size * lh + size * lh / 2
        for (w, acc), ww in zip(words, widths):
            p = prog(t, i * stagger, 0.55)
            if p > 0:
                if isinstance(acc, tuple):
                    lay = word_layer(acc, kind, size, color, accent)
                else:
                    lay = text_layer(w, kind, size, accent if acc else color)
                k = ease_out_back(p, 1.8) * 0.4 + 0.6 if p < 1 else 1.0
                if abs(k - 1) > 1e-3:
                    lay = lay.resize((max(1, int(lay.width * k)), max(1, int(lay.height * k))), Image.BICUBIC)
                composite(world, fade(lay, clamp(p * 2.5)), (x + ww / 2) * S - lay.width / 2,
                          (cy + (1 - ease_out_expo(p)) * 24) * S - lay.height / 2)
            x += ww + space
            i += 1
    return y_top + len(lines) * size * lh


def pop_layer(cv, lay, cx, cy, p, rot=0.0, overshoot=1.8):
    """Colle un calque avec effet tampon (grossit puis se pose)."""
    if p <= 0:
        return
    k = max(0.02, ease_out_back(clamp(p), overshoot) if p < 1 else 1.0)
    if abs(k - 1) > 1e-3:
        lay = lay.resize((max(1, int(lay.width * k)), max(1, int(lay.height * k))), Image.BICUBIC)
    if rot:
        lay = lay.rotate(rot, resample=Image.BICUBIC, expand=True)
    cv.paste(lay, cx, cy, opacity=clamp(p * 3))


def sticker(text, size, fg, bg, pad=0.6, kind="titre_b"):
    """Pastille arrondie (calque)."""
    tw = text_w(text, kind, size)
    w, h = tw + size * 2 * pad, size * 1.7
    im = Image.new("RGBA", (int((w + 8) * S), int((h + 8) * S)), (0, 0, 0, 0))
    cv = Cv(im, 4, 4)
    cv.rrect(0, 0, w, h, h / 2, fill=bg)
    cv.text(text, kind, size, fg, w / 2, h / 2, align="c")
    return im


def reflexe(cv, text, y, p, bg):
    """Pastille verte « ✓ le bon réflexe » en bas de scène."""
    if p <= 0:
        return
    size = 34
    tw = text_w(text, "ui_bold", size)
    w = tw + 120
    e = ease_out_expo(p)
    x0, yy = W / 2 - w / 2, y + (1 - e) * 30
    cv.rrect(x0, yy - 44, x0 + w, yy + 44, 44, fill=mix(bg, C["vert"], clamp(p * 2.5)))
    cv.disc(x0 + 48, yy, 24, mix(bg, C["blanc"], clamp(p * 2.5)))
    cv.check(x0 + 48, yy, 26, C["vert"], ease_out_cubic(prog(p, 0.3, 0.7)), width=5)
    cv.text(text, "ui_bold", size, C["blanc"], x0 + 88, yy, opacity=clamp(p * 2.5))


# --------------------------------------------------------------------------- #
# Pictos du quotidien (dessinés)
# --------------------------------------------------------------------------- #
def phone(cv, cx, cy, w, h, body, screen):
    cv.rrect(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, 64, fill=body)
    cv.rrect(cx - w / 2 + 16, cy - h / 2 + 16, cx + w / 2 - 16, cy + h / 2 - 16, 50, fill=screen)
    cv.rrect(cx - 64, cy - h / 2 + 30, cx + 64, cy - h / 2 + 58, 14, fill=body)


def chat_bubble(cv, text, x, y, mine, size=30):
    """Bulle de messagerie ; (x, y) = bord extérieur et milieu vertical."""
    tw = text_w(text, "ui_semi", size)
    w, h = tw + 52, size * 2.1
    x0 = x - w if mine else x
    fill = C["vert"] if mine else mix(C["blanc"], C["trait"], 0.9)
    fg = C["blanc"] if mine else C["marine"]
    cv.rrect(x0, y - h / 2, x0 + w, y + h / 2, 28, fill=fill)
    cv.text(text, "ui_semi", size, fg, x0 + 26, y)


def coin(cv, cx, cy, r, color, dark):
    cv.disc(cx, cy, r, dark)
    cv.disc(cx, cy - r * 0.08, r * 0.92, color)
    cv.text("€", "titre_b", r * 0.95, dark, cx, cy - r * 0.1, align="c")


def bubble(cv, x0, y0, x1, y1, fill):
    cv.rrect(x0, y0, x1, y1, 44, fill=fill)
    cv.poly([(x0 + 90, y1 - 2), (x0 + 70, y1 + 56), (x0 + 160, y1 - 2)], fill)


# --------------------------------------------------------------------------- #
# Tirelire (illustration en aplats)
# --------------------------------------------------------------------------- #
def tirelire(cv, cx, cy, w, body, dark, t=0.0, fissure=0.0):
    """Tirelire cochon inquiète ; (cx, cy) = centre du corps, w = largeur du corps."""
    h = w * 0.72
    cy += math.sin(t * 2.4) * w * 0.01
    for k, dx in enumerate((-0.28, -0.1, 0.12, 0.3)):                               # pattes
        cv.rrect(cx + dx * w - w * 0.06, cy + h * 0.3, cx + dx * w + w * 0.06, cy + h * 0.62, w * 0.04,
                 fill=mix(body, dark, 0.25 if k % 2 else 0.0))
    cv.d.arc(cv.P((cx - w * 0.62, cy - h * 0.12), (cx - w * 0.44, cy + h * 0.08)), 60, 330, fill=dark,
             width=int(w * 0.02 * S))                                                  # queue
    cv.d.ellipse(cv.P((cx - w / 2, cy - h / 2), (cx + w / 2, cy + h / 2)), fill=body)
    cv.poly([(cx + w * 0.12, cy - h * 0.38), (cx + w * 0.2, cy - h * 0.66), (cx + w * 0.3, cy - h * 0.34)],
            mix(body, dark, 0.2))                                                      # oreille
    cv.rrect(cx - w * 0.12, cy - h * 0.5 - 6, cx + w * 0.08, cy - h * 0.5 + 8, 7, fill=dark)   # fente
    sx, sy = cx + w * 0.5, cy + h * 0.02                                               # groin
    cv.d.ellipse(cv.P((sx - w * 0.1, sy - h * 0.15), (sx + w * 0.08, sy + h * 0.15)), fill=mix(body, dark, 0.2))
    cv.disc(sx - w * 0.03, sy - h * 0.05, w * 0.017, dark)
    cv.disc(sx - w * 0.03, sy + h * 0.05, w * 0.017, dark)
    ex, ey = cx + w * 0.3, cy - h * 0.14                                               # œil inquiet
    cv.disc(ex, ey, w * 0.045, C["blanc"])
    cv.disc(ex + w * 0.01, ey + w * 0.012, w * 0.024, dark)
    cv.line([(ex - w * 0.06, ey - w * 0.1), (ex + w * 0.05, ey - w * 0.065)], dark, w * 0.016)
    if fissure > 0:
        pts = [(cx - w * 0.05, cy - h * 0.48), (cx + w * 0.03, cy - h * 0.25), (cx - w * 0.06, cy - h * 0.05),
               (cx + w * 0.04, cy + h * 0.15), (cx - w * 0.03, cy + h * 0.33), (cx + w * 0.02, cy + h * 0.48)]
        cv.line(pts[:max(2, int(len(pts) * fissure))], dark, w * 0.016)


def goutte(cv, x, y, s, color):
    cv.poly([(x, y - s * 1.3), (x - s * 0.62, y), (x + s * 0.62, y)], color)
    cv.disc(x, y + s * 0.1, s * 0.66, color)


def rayons(cv, cx, cy, r, n, t, color):
    """Rayons de soleil qui tournent lentement (fond festif)."""
    for k in range(n):
        a0 = 2 * math.pi * k / n + t * 0.15
        a1 = a0 + math.pi / n
        cv.poly([(cx, cy), (cx + math.cos(a0) * r, cy + math.sin(a0) * r),
                 (cx + math.cos(a1) * r, cy + math.sin(a1) * r)], color)


# --------------------------------------------------------------------------- #
# Scènes : (world, t local, D) -> caméra (zoom, dx, dy)
# --------------------------------------------------------------------------- #
def punch(t, D, amount=0.04):
    """Petit zoom d'attaque à chaque coupe puis lente poussée."""
    return 1.0 + amount * (1 - ease_out_cubic(prog(t, 0, 0.6))) + 0.015 * prog(t, 0, D)


def shake(t, t0, amp=10, dur=0.4):
    q = t - t0
    if 0 <= q <= dur:
        k = (1 - q / dur) ** 2 * amp
        return math.sin(q * 60) * k, math.cos(q * 47) * k
    return 0.0, 0.0


def header(world, cv, i, title, bg, t):
    """« ERREUR i/3 » + titre de l'erreur."""
    fg = ink(bg)
    p = prog(t, 0.0, 0.4)
    if p > 0:
        cv.chip(f"ERREUR {i}/3", W / 2, 290, "ui_bold", 26, C["blanc"] if fg == C["marine"] else C["marine"],
                fg, align="c", tracking=0.14)
    pop_words(world, [title], "titre_b", 92, 335, t - 0.1, fg, C["rouge"] if bg != C["rouge"] else C["or"])


def scene_accroche(world, t, D):
    cfg = CFG["accroche"]
    bg = C["marine"]
    cv = Cv(world)
    # courbe qui dégringole derrière la tirelire
    zig = [(90, 600), (260, 700), (360, 650), (520, 860), (640, 800), (800, 1050), (990, 1180)]
    k = ease_in_out_cubic(prog(t, 0.2, 1.3))
    if k > 0:
        L = sum(math.dist(p, q) for p, q in zip(zig, zig[1:])) * k
        pts = [zig[0]]
        for p, q in zip(zig, zig[1:]):
            seg = math.dist(p, q)
            if L >= seg:
                pts.append(q)
                L -= seg
            else:
                pts.append((lerp(p[0], q[0], L / seg), lerp(p[1], q[1], L / seg)))
                break
        col = mix(bg, C["rouge"], 0.75)
        cv.line(pts, col, 18)
        if k >= 1:
            cv.poly([(1010, 1190), (930, 1195), (985, 1125)], col)
    for j in range(5):                                       # pièces qui tombent
        tj = (t - 0.6 - j * 0.45) % 2.4
        if t > 0.6 + j * 0.45:
            coin(cv, 150 + j * 190, 560 + 700 * (tj / 2.4) ** 1.6, 30, mix(bg, C["or"], 0.9), mix(bg, C["marine"], 0.6))
    # la tirelire inquiète se fissure et perd ses pièces
    p = prog(t, 0.15, 0.6)
    if p > 0:
        lay = Image.new("RGBA", (int(700 * S), int(560 * S)), (0, 0, 0, 0))
        tirelire(Cv(lay, 330, 280), 0, 0, 520, C["or"], C["marine"], t, ease_out_cubic(prog(t, 0.9, 0.6)))
        pop_layer(cv, lay, W / 2, 920, p, overshoot=1.6)
        g = prog(t, 1.0, 1.2)
        if 0 < g < 1:
            goutte(cv, W / 2 + 205, 700 + 60 * g, 18, mix(bg, (120, 190, 255), 0.95 * (1 - g * 0.6)))
    pop_words(world, cfg["lignes"][:2], "titre_b", 100, 200, t - 0.05, C["blanc"], C["or"], stagger=0.07)
    pop_words(world, cfg["lignes"][2:], "titre", 52, 445, t - 0.5, mix(bg, C["blanc"], 0.8), C["or"])
    q = prog(t, 1.6, 0.45)
    if q > 0:
        wig = math.sin((t - 1.6) * 8) * 3 * (1 - prog(t, 1.6, 1.0))
        pop_layer(cv, sticker(cfg["sticker"], 40, C["blanc"], C["rouge"]), W / 2, 1440, q, rot=-4 + wig)
    return punch(t, D), 0, 0


ORACLE = [(0.0, 0.25), (0.15, 0.4), (0.25, 0.33), (0.38, 0.62), (0.46, 0.55), (0.55, 1.0),
          (0.62, 0.74), (0.7, 0.8), (0.8, 0.42), (0.9, 0.3), (1.0, 0.04)]


def scene_mode(world, t, D):
    cfg = CFG["erreurs"][0]
    bg = C["fond"]
    cv = Cv(world)
    header(world, cv, 1, cfg["titre"], bg, t)
    p = prog(t, 0.3, 0.6)
    if p <= 0:
        return punch(t, D), 0, 0
    pw, ph = 600, 760
    lay = Image.new("RGBA", (int((pw + 40) * S), int((ph + 40) * S)), (0, 0, 0, 0))
    lc = Cv(lay, (pw + 40) / 2, (ph + 40) / 2)
    phone(lc, 0, 0, pw, ph, C["marine"], C["blanc"])
    swap = ease_in_out_cubic(prog(t, 3.6, 0.5))           # discussion -> appli de bourse
    # 1) la discussion de groupe : tout le monde en parle
    ox = -swap * pw
    if swap < 1:
        lc.text("Groupe « Famille »", "ui_bold", 24, C["gris"], ox, -ph / 2 + 110, align="c")
        for k, msg in enumerate(cfg["messages"]):
            q = prog(t, 0.8 + k * 0.75, 0.35)
            if q > 0:
                mine = k == len(cfg["messages"]) - 1
                y = -ph / 2 + 200 + k * 110 + (1 - ease_out_expo(q)) * 30
                chat_bubble(lc, msg, ox + (pw / 2 - 40 if mine else -pw / 2 + 40), y, mine)
    # 2) l'appli de bourse : le cours s'effondre
    if swap > 0:
        ox = (1 - swap) * pw
        lc.text(cfg["action"], "ui_bold", 30, C["marine"], ox - pw / 2 + 56, -ph / 2 + 110, tracking=0.1)
        k = ease_in_out_cubic(prog(t, 4.3, 1.4))
        price = lerp(cfg["de"], cfg["a"], k)
        col = C["marine"] if k == 0 else C["rouge"]
        lc.text(f"{fmt_n(price)}{NB}{cfg['unite']}", "titre_b", 92, col, ox - pw / 2 + 56, -ph / 2 + 200)
        path = monotone(ORACLE, 200)
        xc = 0.55 + 0.45 * ease_in_out_cubic(prog(t, 4.3, 1.4))
        x0, x1, y0, y1 = ox - pw / 2 + 50, ox + pw / 2 - 50, -ph / 2 + 300, ph / 2 - 110
        part = clip_path(path, 0, xc)
        pts = [(lerp(x0, x1, x), lerp(y1, y0, y)) for x, y in part]
        split = next((j for j, (x, _) in enumerate(part) if x >= 0.55), len(pts))
        lc.poly(pts + [(pts[-1][0], y1), (pts[0][0], y1)], mix(C["blanc"], col, 0.1))
        lc.line(pts[:split + 1], C["vert"], 8)
        if split < len(pts) - 1:
            lc.line(pts[split:], C["rouge"], 8)
        lc.disc(pts[-1][0], pts[-1][1], 13, col)
        lc.text(cfg["legende"], "ui_semi", 26, C["gris"], ox, ph / 2 - 62, align="c")
    tilt = 1 - ease_out_expo(p)
    pop_layer(cv, eb.tilt_layer(lay, tilt * 18), W / 2, 940 + tilt * 60, min(1.0, p * 1.4), overshoot=1.2)
    q = prog(t, 5.8, 0.45)
    pop_layer(cv, sticker(cfg["tampon"], 84, C["blanc"], C["rouge"]), W / 2 + 200, 1190, q, rot=-10)
    reflexe(cv, cfg["reflexe"], 1370, prog(t, D - 2.2, 0.5), bg)
    dx, dy = shake(t, 5.8, 14)
    return punch(t, D), dx, dy


COVID = [(0, 0.86), (0.06, 0.8), (0.12, 0.62), (0.17, 0.66), (0.24, 0.22), (0.38, 0.5), (0.55, 0.64),
         (0.75, 0.8), (0.9, 0.85), (1.0, 0.9)]


def scene_panique(world, t, D):
    cfg = CFG["erreurs"][1]
    bg = C["fond"]
    cv = Cv(world)
    header(world, cv, 2, cfg["titre"], bg, t)
    # notification d'alerte qui tombe sur l'écran
    a = prog(t, 1.4, 0.5)
    if a > 0:
        w, h = 900, 150
        y = 560 + (1 - ease_out_back(a, 1.6)) * -160
        cv.rrect(W / 2 - w / 2, y, W / 2 + w / 2, y + h, 36, fill=C["blanc"], outline=C["trait"], width=2)
        cv.rrect(W / 2 - w / 2 + 30, y + 35, W / 2 - w / 2 + 110, y + 115, 22, fill=C["rouge"])
        cv.line([(W / 2 - w / 2 + 48, y + 60), (W / 2 - w / 2 + 70, y + 82), (W / 2 - w / 2 + 92, y + 96)],
                C["blanc"], 6)
        cv.text(f"ALERTE · {cfg['alerte_quand']}", "ui_bold", 24, C["gris"], W / 2 - w / 2 + 135, y + 50,
                tracking=0.08)
        al = cfg["alerte"]
        cv.text(al, "ui_bold", fit_size(al, "ui_bold", 40, w - 170), C["marine"], W / 2 - w / 2 + 135, y + 100)
    # la courbe : la chute, puis la remontée
    path = monotone(COVID, 200)
    x0, x1, y0, y1 = 110, W - 110, 800, 1250
    xc = 0.24 * ease_in_out_cubic(prog(t, 1.6, 1.0)) + 0.76 * ease_in_out_cubic(prog(t, 3.8, 1.6))
    lvl = lerp(y1, y0, 0.86)
    cv.dashed((x0, lvl), (x1, lvl), mix(bg, C["gris_clair"], 0.8), 3, 16, 12)
    if xc > 0:
        part = clip_path(path, 0, xc)
        pts = [(lerp(x0, x1, x), lerp(y1, y0, y)) for x, y in part]
        lo = next((j for j, (x, _) in enumerate(part) if x >= 0.24), len(pts))
        cv.line(pts[:lo + 1], C["rouge"], 12)
        if lo < len(pts) - 1:
            cv.line(pts[lo:], C["vert"], 12)
        cv.disc(pts[-1][0], pts[-1][1], 18, C["marine"])
        cv.disc(pts[-1][0], pts[-1][1], 8, C["blanc"])
    lowx, lowy = lerp(x0, x1, 0.24), lerp(y1, y0, 0.22)
    # « ceux qui ont vendu » : la perte reste au plus bas
    v = prog(t, 5.6, 0.5)
    if v > 0:
        cv.dashed((lowx, lowy), (lowx + (x1 - lowx) * ease_out_cubic(v), lowy), C["rouge"], 5, 14, 10)
        pop_layer(cv, sticker(cfg["vendu"], 28, C["blanc"], C["rouge"], kind="ui_bold"), W / 2 + 90, lowy + 60, v)
    ok = prog(t, 4.6, 0.4)
    if ok > 0:
        pop_layer(cv, sticker(cfg["retour"], 28, C["blanc"], C["vert"], kind="ui_bold"), W / 2 + 120, lvl - 60, ok)
    reflexe(cv, cfg["reflexe"], 1370, prog(t, D - 2.2, 0.5), bg)
    dx, dy = shake(t, 1.6, 12)
    return punch(t, D), dx, dy


def panier(cv, cx, cy, w, body, dark, t_shake=0.0):
    """Panier en osier (vu de face) ; (cx, cy) = milieu du bord supérieur."""
    h = w * 0.55
    cv.d.arc(cv.P((cx - w * 0.36, cy - w * 0.5), (cx + w * 0.36, cy + w * 0.2)), 180, 360, fill=dark,
             width=int(w * 0.045 * S))                                               # anse
    cv.poly([(cx - w / 2, cy), (cx + w / 2, cy), (cx + w * 0.38, cy + h), (cx - w * 0.38, cy + h)], body)
    for k in range(1, 4):                                                            # tressage
        y = cy + h * k / 4
        x = w / 2 - (w / 2 - w * 0.38) * k / 4
        cv.line([(cx - x + 10, y), (cx + x - 10, y)], dark, 5)
    for k in range(-3, 4):
        cv.line([(cx + k * w * 0.13, cy + 6), (cx + k * w * 0.1, cy + h - 6)], dark, 4)
    cv.rrect(cx - w / 2 - 14, cy - 16, cx + w / 2 + 14, cy + 16, 16, fill=dark)       # bord


def oeuf(cv, cx, cy, r, fill, crack, dark):
    cv.d.ellipse(cv.P((cx - r * 0.78, cy - r), (cx + r * 0.78, cy + r * 0.92)), fill=fill)
    if crack > 0:
        pts = [(cx - r * 0.7, cy - r * 0.1), (cx - r * 0.35, cy + r * 0.12), (cx - r * 0.1, cy - r * 0.18),
               (cx + r * 0.2, cy + r * 0.1), (cx + r * 0.45, cy - r * 0.15), (cx + r * 0.72, cy + r * 0.05)]
        n = max(2, int(len(pts) * crack))
        cv.line(pts[:n], dark, max(3, r * 0.07))


def scene_diversifier(world, t, D):
    cfg = CFG["erreurs"][2]
    bg = C["fond"]
    cv = Cv(world)
    header(world, cv, 3, cfg["titre"], bg, t)
    # tous les œufs dans le même panier « 100 % tech »
    crash = 3.3
    sx, sy = shake(t, crash, 16, 0.6)
    tilt = 7 * math.sin(max(0.0, t - crash) * 7) * math.exp(-max(0.0, t - crash) * 2.5) if t > crash else 0.0
    p = prog(t, 0.3, 0.5)
    if p > 0:
        lay = Image.new("RGBA", (int(760 * S), int(640 * S)), (0, 0, 0, 0))
        lc = Cv(lay, 380, 300)
        for k, (ex, ey) in enumerate([(-150, -95), (-55, -130), (55, -120), (150, -90), (0, -70)]):
            q = prog(t, 0.6 + k * 0.18, 0.35)
            if q > 0:
                oeuf(lc, ex, ey - (1 - ease_out_back(q, 2)) * 60, 62, mix(C["fond"], C["blanc"], 0.6),
                     prog(t, crash + 0.15 + k * 0.08, 0.3), C["marine"])
        panier(lc, 0, 0, 500, C["brun"], C["marine"])
        lc.chip(cfg["panier"], 0, 135, "ui_bold", 34, C["blanc"], C["chaud"], align="c", tracking=0.08)
        if tilt:
            lay = lay.rotate(tilt, resample=Image.BICUBIC)
        pop_layer(cv, lay, W / 2, 830, p, overshoot=1.4)
    q = prog(t, crash + 0.35, 0.45)
    pop_layer(cv, sticker(cfg["tampon"], 84, C["blanc"], C["rouge"]), W / 2 + 250, 600, q, rot=-10)
    # le temps qu'il a fallu pour revenir au niveau de 2000
    run = prog(t, crash + 1.0, 2.0)
    if run > 0:
        yrs = int(cfg["annees"] * ease_in_out_cubic(run))
        cv.text(f"{yrs}{NB}ans", "titre_b", 120, C["rouge"] if run < 1 else C["marine"], W / 2, 1175, align="c")
        cv.text(cfg["legende"], "ui_semi", 32, C["gris"], W / 2, 1262, align="c", opacity=clamp(run * 3))
    reflexe(cv, cfg["reflexe"], 1370, prog(t, D - 2.2, 0.5), bg)
    return punch(t, D), sx, sy


def scene_fin(world, t, D):
    cfg = CFG["fin"]
    bg = C["or"]
    cv = Cv(world)
    rayons(cv, W / 2, 900, 1400, 18, t, mix(bg, C["blanc"], 0.13))
    pop_words(world, cfg["titre"], "titre_b", 96, 200, t - 0.05, C["marine"], C["blanc"], stagger=0.07)
    # l'échange avec le conseiller, en bulles de messagerie
    for k, (txt, mine) in enumerate(cfg["echange"]):
        p = prog(t, 0.35 + k * 0.55, 0.45)
        if p <= 0:
            continue
        size = 42
        tw = text_w(txt, "ui_semi", size)
        w, h = tw + 80, 110
        lay = Image.new("RGBA", (int((w + 70) * S), int((h + 70) * S)), (0, 0, 0, 0))
        lc = Cv(lay, 35, 20)
        fill, fg = (C["chaud"], C["blanc"]) if mine else (C["blanc"], C["marine"])
        lc.rrect(0, 0, w, h, 40, fill=fill)
        tx = w - 70 if mine else 70
        lc.poly([(tx - 30, h - 4), (tx + 30, h - 4), (tx + (40 if mine else -40), h + 40)], fill)
        lc.text(txt, "ui_semi", size, fg, 40, h / 2)
        x = W - 80 - w / 2 if mine else 80 + w / 2
        pop_layer(cv, lay, x, 650 + k * 230, p, overshoot=1.6)
    # pastille « ✓ » : le plan est clair
    c = prog(t, 1.5, 0.5)
    if c > 0:
        k = ease_out_back(c, 2.4)
        cv.disc(W / 2, 1110, 62 * k, C["vert"])
        cv.check(W / 2, 1110, 62, C["blanc"], ease_out_cubic(prog(c, 0.3, 0.7)), width=10)
    # le message du cabinet
    pop_words(world, cfg["message"], "titre", 50, 1235, t - 1.2, C["marine"], C["blanc"], stagger=0.06)
    cv.text(cfg["commentaire"], "ui_semi", 30, C["blanc"], W / 2, 1535, align="c", opacity=prog(t, 2.2, 0.4))
    return punch(t, D, 0.02), 0, 0


SCENES = [  # (scène, clé de voix, durée minimale, fond, sons [(t local, type)])
    (scene_accroche, "accroche", 3.4, "marine", [(0.05, "impact"), (0.2, "chute"), (1.6, "pop")]),
    (scene_mode, "e1", 7.5, "fond", [(0.3, "pop"), (0.8, "tic"), (1.55, "tic"), (2.3, "tic"), (3.6, "souffle"),
                                     (4.3, "chute"), (5.8, "pop")]),
    (scene_panique, "e2", 7.5, "fond", [(1.4, "ping"), (1.6, "chute"), (4.6, "succes"), (5.6, "pop")]),
    (scene_diversifier, "e3", 7.0, "fond", [(0.3, "pop")] + [(0.6 + 0.18 * j, "tic") for j in range(5)]
     + [(3.3, "chute"), (3.65, "pop")]),
    (scene_fin, "fin", 4.5, "or", [(0.35, "pop"), (0.9, "pop"), (1.5, "succes"), (1.6, "impact")]),
]
TIMELINE, STARTS, DURATION = [], [], 0.0


def build_timeline():
    """Durée des scènes : au moins le minimum, et assez pour la phrase de voix off plus
    un temps de respiration. Le total est plafonné à 30 s (on rogne les respirations)."""
    global STARTS, DURATION
    dv = CFG.get("_durees_voix", {})
    tail = [0.3, 0.6, 0.6, 0.6, 1.8]          # respiration après la voix ; la fin laisse lire la signature
    durs = [max(m, LEAD + dv.get(key, 0) + tl) for (_, key, m, _, _), tl in zip(SCENES, tail)]
    over = sum(durs) - MAX_DUREE
    if over > 0:
        slack = [d - (LEAD + dv.get(key, 0) + (1.0 if key == "fin" else 0.2)) for d, (_, key, *_r) in zip(durs, SCENES)]
        k = min(1.0, over / max(1e-6, sum(slack)))
        durs = [d - s * k for d, s in zip(durs, slack)]
    TIMELINE[:] = []
    for i, (d, (f, _, _, _, snd)) in enumerate(zip(durs, SCENES)):
        snd = list(snd) + ([(d - 2.2, "succes")] if 1 <= i <= 3 else [])
        TIMELINE.append((d, f, "volet" if i else None, snd))
    STARTS, acc = [], 0.0
    for d in durs:
        STARTS.append(acc)
        acc += d
    DURATION = acc
    eb.VO[:] = [(STARTS[i] - (TR / 2 if i else 0) + LEAD, key) for i, (_, key, *_r) in enumerate(SCENES) if key in dv]


def scene_local_time(k, t):
    return t - STARTS[k] + (TR / 2 if k > 0 else 0)


# --------------------------------------------------------------------------- #
# Sous-titres, barre de progression, mentions
# --------------------------------------------------------------------------- #
def caption_chunks(text):
    """Une phrase = un sous-titre."""
    chunks, cur = [], ""
    for w in text.split(" "):
        cur = f"{cur} {w}" if cur else w
        if cur.endswith((".", "?", "!")):
            chunks.append(cur)
            cur = ""
    if cur:
        chunks.append(cur)
    return chunks


def caption(cv, k, t_local, bg):
    if not CFG.get("sous_titres", True):
        return
    key = SCENES[k][1]
    d = CFG.get("_durees_voix", {}).get(key)
    src = CFG["accroche"] if key == "accroche" else (CFG["fin"] if key == "fin" else CFG["erreurs"][int(key[1]) - 1])
    text = src.get("sous_titre")
    if not d or not text:
        return
    u = (t_local - LEAD) / d
    if u < 0 or u > 1.08:
        return
    chunks = caption_chunks(text)
    total = sum(len(c) + 4 for c in chunks)
    acc = 0
    for c in chunks:
        a, acc = acc, acc + len(c) + 4
        if u <= acc / total or c is chunks[-1]:
            break
    size, lh = 38, 50
    rows = [" ".join(w for w, _ in ln) for ln in eb.layout([c], "ui_bold", size, W - 200)]
    tw = max(text_w(r, "ui_bold", size) for r in rows)
    dark = ink(bg) == C["marine"]
    fill, fg = (C["marine"], C["blanc"]) if dark else (C["blanc"], C["marine"])
    y0 = 1500 - (len(rows) - 1) * lh / 2
    cv.rrect(W / 2 - tw / 2 - 28, y0 - 38, W / 2 + tw / 2 + 28, y0 + (len(rows) - 1) * lh + 38, 20, fill=fill)
    for r, row in enumerate(rows):
        cv.text(row, "ui_bold", size, fg, W / 2, y0 + r * lh, align="c")


def overlays(world, k, t_local, bg):
    """Barre de progression, sous-titres et mentions légales (fixes, hors caméra)."""
    cv = Cv(world)
    fg = ink(bg)
    n = len(SCENES)
    gap, x0, x1 = 10, 60, W - 60
    seg = (x1 - x0 - gap * (n - 1)) / n
    for j in range(n):
        xa = x0 + j * (seg + gap)
        cv.rrect(xa, 120, xa + seg, 128, 4, fill=mix(bg, fg, 0.25))
        f = 1.0 if j < k else (clamp(t_local / TIMELINE[k][0]) if j == k else 0.0)
        if f > 0:
            cv.rrect(xa, 120, xa + seg * f, 128, 4, fill=fg)
    caption(cv, k, t_local, bg)
    rows = eb.layout([CFG["mentions"]], "texte", 21, W - 140)
    for r, ln in enumerate(rows):
        cv.text(" ".join(w for w, _ in ln), "texte", 21, mix(bg, fg, 0.62), W / 2, 1620 + r * 30, align="c")


# --------------------------------------------------------------------------- #
# Rendu
# --------------------------------------------------------------------------- #
def render_scene(k, t):
    tl = scene_local_time(k, t)
    bg = C[SCENES[k][3]]
    world = Image.new("RGBA", (W * S, H * S), bg)
    zoom, dx, dy = TIMELINE[k][1](world, tl, TIMELINE[k][0]) or (1.0, 0, 0)
    cw, ch = W * S / zoom, H * S / zoom
    cx = clamp(W * S / 2 + dx * S, cw / 2, W * S - cw / 2)
    cy = clamp(H * S / 2 + dy * S, ch / 2, H * S - ch / 2)
    img = world.resize((W * S, H * S), Image.BILINEAR, box=(cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2))
    overlays(img, k, tl, bg)
    return np.asarray(img.convert("RGB").resize((W, H), Image.LANCZOS), dtype=np.float32)


def render_frame(i):
    t = i / FPS
    k = max(j for j, s in enumerate(STARTS) if t >= s)
    pair = None
    if k + 1 < len(TIMELINE) and t >= STARTS[k + 1] - TR / 2:
        pair = (k, k + 1, (t - (STARTS[k + 1] - TR / 2)) / TR)
    elif k > 0 and t < STARTS[k] + TR / 2:
        pair = (k - 1, k, (t - (STARTS[k] - TR / 2)) / TR)
    if pair is None:
        out = render_scene(k, t)
    else:
        # volet diagonal : la scène suivante arrive par la droite, en biais, avec un liseré doré
        a_i, b_i, u = pair
        A, B = render_scene(a_i, t), render_scene(b_i, t)
        p = eb.ease_in_out_cubic(u)
        yy, xx = np.mgrid[0:H, 0:W]
        edge = (1 - p) * (W + H * 0.35) - H * 0.35 * (1 - yy / H)
        m = np.clip((xx - edge) / 3.0, 0, 1)[..., None]
        out = A * (1 - m) + B * m
        band = (np.abs(xx - edge) < 8)[..., None] & (0 < p < 1)
        out = np.where(band, np.array(C["or"][:3], np.float32), out)
    f = min(prog(t, 0, 0.2), 1 - prog(t, DURATION - 0.5, 0.5))
    if f < 1:
        out = out * f
    return np.clip(out, 0, 255).astype(np.uint8).tobytes()


def voix_textes(cfg):
    out = [("accroche", cfg["accroche"].get("voix"))]
    out += [(f"e{i + 1}", e.get("voix")) for i, e in enumerate(cfg["erreurs"])]
    out.append(("fin", cfg["fin"].get("voix")))
    return [(k, v) for k, v in out if v]


def init_worker(cfg):
    eb.setup_format("vertical")
    CFG.clear()
    CFG.update(eb.typo_cfg(cfg))
    C.clear()
    C.update({k: eb.hex2rgb(v) for k, v in cfg["couleurs"].items()})
    build_timeline()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(ROOT, "config_short.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "output", "short-3-erreurs.mp4"))
    ap.add_argument("--preview", nargs="*", type=float, metavar="SEC")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["_durees_voix"] = eb.prepare_voix(cfg, voix_textes(cfg))
    init_worker(cfg)
    print("Scènes :", " | ".join(f"{d:.1f}" for d, *_ in TIMELINE), f"→ {DURATION:.1f} s")

    if args.preview is not None:
        outdir = os.path.join(ROOT, "output", "apercu-short")
        os.makedirs(outdir, exist_ok=True)
        secs = args.preview or [s + d * f for s, (d, *_) in zip(STARTS, TIMELINE) for f in (0.55, 0.93)]
        for sec in secs:
            Image.frombytes("RGB", (W, H), render_frame(int(round(sec * FPS)))).save(
                os.path.join(outdir, f"t{sec:05.2f}.png"))
        print("Aperçus dans", outdir)
        return

    # musique : pulsation régulière (un temps sur deux) de la première erreur à la signature
    eb.TIMELINE[:] = TIMELINE
    eb.STARTS, eb.DURATION, eb.TR = STARTS, DURATION, TR
    eb.KICK_EVERY, eb.KICK_STOP = 2, -1
    total = int(round(DURATION * FPS))
    wav = os.path.splitext(args.out)[0] + ".wav"
    eb.synth_audio(wav)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-i", wav, "-c:a", "aac", "-b:a", "192k",
           "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", "-shortest", args.out]
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    from multiprocessing import Pool
    with Pool(args.jobs, initializer=init_worker, initargs=(cfg,)) as pool:
        for i, buf in enumerate(pool.imap(render_frame, range(total), chunksize=2)):
            ff.stdin.write(buf)
            if i % FPS == 0:
                print(f"\rRendu : {i * 100 // total:3d} %", end="", flush=True)
    ff.stdin.close()
    ret = ff.wait()
    os.remove(wav)
    print("\rRendu : 100 %")
    if ret != 0:
        sys.exit("ffmpeg a échoué")
    print("Vidéo écrite :", args.out)


if __name__ == "__main__":
    main()
