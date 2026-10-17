#!/usr/bin/env python3
"""Short vertical (≤ 30 s, 1080x1920) « 5 erreurs en bourse… et ce qu'elles coûtent en vrai » (O'MORGAN).

Format réseaux sociaux : accroche qui annonce l'erreur n°4, une scène très courte par erreur
avec un exemple du quotidien (Netflix, café, gâteau d'anniversaire…), fond de couleur qui
change à chaque coupe, textes qui « pop », secousses sur les chutes, barre de progression
façon story et question finale pour les commentaires. Voix off, musique et bruitages
reprennent le moteur d'erreurs_bourse.py.

Usage :
    python3 short_erreurs.py              # -> output/short-5-erreurs.mp4
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
from PIL import Image, ImageFilter

import erreurs_bourse as eb
from erreurs_bourse import (C, CFG, NB, Cv, clamp, composite, ease_in_out_cubic, ease_out_back,
                            ease_out_cubic, ease_out_expo, fade, fit_size, fmt_n, lerp, markup_words,
                            mix, monotone, clip_path, value_at, prog, text_layer, text_w, word_layer)

ROOT = os.path.dirname(os.path.abspath(__file__))
W, H = 1080, 1920
S = eb.S
FPS = 30
TR = 0.32            # coupe en volet (s)
MAX_DUREE = 30.0


# --------------------------------------------------------------------------- #
# Texte qui « pop » mot à mot
# --------------------------------------------------------------------------- #
def ink(bg):
    """Couleur de texte lisible sur un fond donné."""
    r, g, b = bg[:3]
    return C["marine"] if 0.299 * r + 0.587 * g + 0.114 * b > 150 else C["blanc"]


def pop_words(world, lines, kind, size, y_top, t, color, accent, stagger=0.06, lh=1.08, cx=None):
    """Lignes centrées ; chaque mot grossit avec un rebond. Renvoie le bas du bloc."""
    cx = W / 2 if cx is None else cx
    space = eb.font(kind, size).getlength(" ") / S
    i = 0
    for li, line in enumerate(lines):
        words = markup_words(line)
        widths = [text_w(w, kind, size) for w, _ in words]
        x = cx - (sum(widths) + space * (len(words) - 1)) / 2
        cy = y_top + li * size * lh + size * lh / 2
        for (w, acc), ww in zip(words, widths):
            p = prog(t, i * stagger, 0.42)
            if p > 0:
                if isinstance(acc, tuple):
                    lay = word_layer(acc, kind, size, color, accent)
                else:
                    lay = text_layer(w, kind, size, accent if acc else color)
                k = ease_out_back(p, 2.6) * 0.55 + 0.45 if p < 1 else 1.0
                if abs(k - 1) > 1e-3:
                    lay = lay.resize((max(1, int(lay.width * k)), max(1, int(lay.height * k))), Image.BICUBIC)
                cxw = x + ww / 2
                composite(world, fade(lay, clamp(p * 3)), cxw * S - lay.width / 2,
                          (cy + (1 - ease_out_expo(p)) * 30) * S - lay.height / 2)
            x += ww + space
            i += 1
    return y_top + len(lines) * size * lh


def pop_layer(cv, lay, cx, cy, p, rot=0.0, overshoot=2.4):
    """Colle un calque avec effet tampon (grossit puis se pose)."""
    if p <= 0:
        return
    k = ease_out_back(clamp(p), overshoot) if p < 1 else 1.0
    k = max(0.02, k)
    if abs(k - 1) > 1e-3:
        lay = lay.resize((max(1, int(lay.width * k)), max(1, int(lay.height * k))), Image.BICUBIC)
    if rot:
        lay = lay.rotate(rot, resample=Image.BICUBIC, expand=True)
    cv.paste(lay, cx, cy, opacity=clamp(p * 4))


def sticker(text, size, fg, bg, pad=0.6):
    """Pastille arrondie (calque)."""
    tw = text_w(text, "titre_b", size)
    w, h = tw + size * 2 * pad, size * 1.7
    im = Image.new("RGBA", (int((w + 8) * S), int((h + 8) * S)), (0, 0, 0, 0))
    cv = Cv(im, 4, 4)
    cv.rrect(0, 0, w, h, h / 2, fill=bg)
    cv.text(text, "titre_b", size, fg, w / 2, h / 2, align="c")
    return im


def counter(cv, text, size, color, x, y, align="c", kind="titre_b"):
    size = fit_size(text, kind, size, W - 120)
    cv.text(text, kind, size, color, x, y, align=align)


# --------------------------------------------------------------------------- #
# Pictos du quotidien (dessinés)
# --------------------------------------------------------------------------- #
def tv(cv, cx, cy, w, frame, screen, accent, label=None, label_size=120):
    h = w * 0.62
    for sgn in (-1, 1):                                       # antenne
        cv.line([(cx, cy - h / 2 - 6), (cx + sgn * w * 0.16, cy - h / 2 - w * 0.17)], frame, 9)
    cv.disc(cx, cy - h / 2 - 4, 12, frame)
    cv.rrect(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, 46, fill=frame)
    cv.rrect(cx - w / 2 + 26, cy - h / 2 + 26, cx + w / 2 - 26, cy + h / 2 - 26, 28, fill=screen)
    for sgn in (-1, 1):                                       # pieds
        cv.line([(cx + sgn * w * 0.22, cy + h / 2), (cx + sgn * w * 0.3, cy + h / 2 + 40)], frame, 12)
    if label:
        cv.text(label, "titre_b", fit_size(label, "titre_b", label_size, w - 90), accent, cx, cy, align="c")
    else:
        s = w * 0.14
        cv.poly([(cx - s * 0.6, cy - s), (cx - s * 0.6, cy + s), (cx + s, cy)], accent)


def phone(cv, cx, cy, w, h, body, screen):
    cv.rrect(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, 70, fill=body)
    cv.rrect(cx - w / 2 + 18, cy - h / 2 + 18, cx + w / 2 - 18, cy + h / 2 - 18, 54, fill=screen)
    cv.rrect(cx - 70, cy - h / 2 + 34, cx + 70, cy - h / 2 + 64, 15, fill=body)


def flame(cv, cx, cy, s, color, inner):
    pts = []
    for k in range(40):
        a = 2 * math.pi * k / 40
        r = s * (0.55 + 0.45 * max(0.0, -math.sin(a)) ** 2.5)
        pts.append((cx + math.cos(a) * s * 0.55, cy - math.sin(a) * r * (1.6 if math.sin(a) > 0 else 0.7)))
    cv.poly(pts, color)
    cv.disc(cx, cy + s * 0.12, s * 0.28, inner)


def bill(cv, cx, cy, w, color, dark):
    h = w * 0.5
    cv.rrect(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, 12, fill=color)
    cv.rrect(cx - w / 2 + 10, cy - h / 2 + 10, cx + w / 2 - 10, cy + h / 2 - 10, 8, outline=dark, width=3)
    cv.disc(cx, cy, h * 0.28, dark)
    cv.text("€", "titre_b", h * 0.34, color, cx, cy - 2, align="c")


def cup(cv, cx, cy, s, body, dark, steam_t=0.0, steam=None):
    cv.d.ellipse(cv.P((cx - s * 0.95, cy + s * 0.48), (cx + s * 0.95, cy + s * 0.72)), fill=dark)   # soucoupe
    cv.ring(cx + s * 0.62, cy, s * 0.24, s * 0.1, body)                                                # anse
    cv.rrect(cx - s * 0.62, cy - s * 0.5, cx + s * 0.62, cy + s * 0.58, s * 0.3, fill=body)
    cv.rrect(cx - s * 0.62, cy - s * 0.5, cx + s * 0.62, cy - s * 0.2, s * 0.15, fill=dark)
    if steam:
        for k in (-1, 0, 1):
            pts = [(cx + k * s * 0.3 + math.sin(steam_t * 5 + j * 0.7 + k) * s * 0.08, cy - s * 0.65 - j * s * 0.09)
                   for j in range(8)]
            cv.line(pts, steam, s * 0.06)


def cake(cv, cx, cy, w, n_candles, t_candles, base, cream, candle, fire):
    cv.rrect(cx - w / 2, cy, cx + w / 2, cy + w * 0.36, 26, fill=base)
    cv.rrect(cx - w / 2, cy - 4, cx + w / 2, cy + w * 0.09, 26, fill=cream)
    for k in range(6):                                         # coulures de crème
        x = cx - w / 2 + 50 + k * (w - 100) / 5
        cv.rrect(x - 16, cy, x + 16, cy + w * 0.09 + 26 + (k % 2) * 18, 16, fill=cream)
    cv.rrect(cx - w / 2 - 30, cy + w * 0.36 - 6, cx + w / 2 + 30, cy + w * 0.36 + 18, 10, fill=cream)
    for k in range(n_candles):
        p = t_candles[k]
        if p <= 0:
            continue
        x = cx - w / 2 + 44 + k * (w - 88) / max(1, n_candles - 1)
        hgt = 70 * ease_out_back(clamp(p * 1.4), 2)
        cv.rrect(x - 8, cy - hgt, x + 8, cy + 2, 5, fill=candle)
        if p > 0.4:
            flame(cv, x, cy - hgt - 16, 11 * ease_out_back(clamp((p - 0.4) * 2), 3), fire, C["blanc"])


def coin(cv, cx, cy, r, color, dark):
    cv.disc(cx, cy, r, dark)
    cv.disc(cx, cy - r * 0.08, r * 0.92, color)
    cv.text("€", "titre_b", r * 0.95, dark, cx, cy - r * 0.1, align="c")


def bubble(cv, x0, y0, x1, y1, fill):
    cv.rrect(x0, y0, x1, y1, 44, fill=fill)
    cv.poly([(x0 + 90, y1 - 2), (x0 + 70, y1 + 56), (x0 + 160, y1 - 2)], fill)


# --------------------------------------------------------------------------- #
# Scènes : (world, t local, D) -> caméra (zoom, dx, dy)
# --------------------------------------------------------------------------- #
def fill_bg(world, color):
    world.paste(color[:3] + (255,), (0, 0, world.width, world.height))


def punch(t, D, amount=0.07):
    """Zoom d'attaque à chaque coupe puis lente poussée."""
    return 1.0 + amount * (1 - ease_out_cubic(prog(t, 0, 0.45))) + 0.02 * prog(t, 0, D)


def shake(t, t0, amp=16, dur=0.45):
    q = t - t0
    if 0 <= q <= dur:
        k = (1 - q / dur) ** 2 * amp
        return math.sin(q * 70) * k, math.cos(q * 53) * k
    return 0.0, 0.0


def chip_index(cv, i, bg):
    fg = ink(bg)
    cv.chip(f"ERREUR {i}/5", W / 2, 300, "ui_bold", 26, bg if fg == C["blanc"] else C["blanc"],
            fg, align="c", tracking=0.14)


def big_index(cv, i, bg):
    """Grand chiffre de l'erreur en filigrane."""
    col = mix(bg, ink(bg), 0.07)
    cv.text(str(i), "titre_b", 900, col, W - 40, 1130, align="r")


def scene_accroche(world, t, D):
    cfg = CFG["accroche"]
    bg = C["marine"]
    fill_bg(world, bg)
    cv = Cv(world)
    # télé qui « allume » un compteur d'années
    p = prog(t, 0.0, 0.5)
    if p > 0:
        lay = Image.new("RGBA", (int(760 * S), int(640 * S)), (0, 0, 0, 0))
        lc = Cv(lay, 380, 300)
        n = int(round(21 * ease_out_cubic(prog(t, 0.35, 1.6))))
        tv(lc, 0, 0, 640, C["blanc"], C["rouge"], C["blanc"], f"{n}{NB}{cfg['compteur']}", 150)
        pop_layer(cv, lay, W / 2, 700, p, rot=-4 * (1 - ease_out_cubic(prog(t, 0, 0.9))))
    pop_words(world, cfg["lignes"], "titre_b", 96, 1060, t - 0.5, C["blanc"], C["or"])
    cv.text(cfg["note"], "ui", 27, mix(bg, C["blanc"], 0.6), W / 2, 1300, align="c",
            opacity=prog(t, 1.4, 0.4))
    # sticker « n°4 »
    q = prog(t, 2.6, 0.45)
    if q > 0:
        wig = math.sin((t - 2.6) * 9) * 4 * (1 - prog(t, 2.6, 1.2))
        pop_layer(cv, sticker(cfg["sticker"], 50, C["blanc"], C["rouge"]), W / 2, 1440, q, rot=-5 + wig)
    dx, dy = shake(t, 2.6, 10)
    return punch(t, D, 0.0) + 0.05 * (1 - ease_out_cubic(prog(t, 0, 0.8))), dx, dy


ORACLE = [(0.0, 0.18), (0.15, 0.3), (0.25, 0.25), (0.38, 0.55), (0.46, 0.5), (0.55, 1.0),
          (0.62, 0.72), (0.7, 0.8), (0.8, 0.4), (0.9, 0.3), (1.0, 0.0)]


def scene_mode(world, t, D):
    cfg = CFG["erreurs"][0]
    bg = C["fond"]
    fill_bg(world, bg)
    cv = Cv(world)
    big_index(cv, 1, bg)
    chip_index(cv, 1, bg)
    pop_words(world, cfg["titre"], "titre_b", 104, 350, t - 0.05, C["marine"], C["rouge"])
    # téléphone avec l'appli de bourse
    p = prog(t, 0.35, 0.55)
    crash = 1.75
    if p > 0:
        pw, ph = 600, 820
        lay = Image.new("RGBA", (int((pw + 40) * S), int((ph + 40) * S)), (0, 0, 0, 0))
        lc = Cv(lay, (pw + 40) / 2, (ph + 40) / 2)
        phone(lc, 0, 0, pw, ph, C["marine"], C["blanc"])
        lc.text(cfg["action"], "ui_bold", 30, C["marine"], -pw / 2 + 60, -ph / 2 + 120, tracking=0.1)
        # badge tendance avec flamme
        bw = text_w(cfg["badge"], "ui_bold", 22, 0.1) + 80
        lc.rrect(pw / 2 - 50 - bw, -ph / 2 + 98, pw / 2 - 50, -ph / 2 + 142, 22, fill=C["or"])
        flame(lc, pw / 2 - 50 - bw + 30, -ph / 2 + 124, 11, C["rouge"], C["or"])
        lc.text(cfg["badge"], "ui_bold", 22, C["blanc"], pw / 2 - 50 - bw + 52, -ph / 2 + 120, tracking=0.1)
        # cours
        k = ease_in_out_cubic(prog(t, crash, 1.0))
        price = lerp(cfg["de"], cfg["a"], k)
        col = C["marine"] if k == 0 else C["rouge"]
        lc.text(f"{fmt_n(price)}{NB}{cfg['unite']}", "titre_b", 96, col, -pw / 2 + 60, -ph / 2 + 230)
        # courbe : monte jusqu'au sommet, puis s'effondre
        path = monotone(ORACLE, 200)
        xc = 0.55 * ease_out_cubic(prog(t, 0.6, 1.1)) + 0.45 * ease_in_out_cubic(prog(t, crash, 1.0))
        x0, x1, y0, y1 = -pw / 2 + 50, pw / 2 - 50, -ph / 2 + 330, ph / 2 - 120
        pts = [(lerp(x0, x1, x), lerp(y1, y0, y)) for x, y in clip_path(path, 0, max(0.001, xc))]
        if len(pts) > 1:
            split = next((j for j, (x, _) in enumerate(clip_path(path, 0, xc)) if x >= 0.55), len(pts))
            lc.poly(pts + [(pts[-1][0], y1), (pts[0][0], y1)], mix(C["blanc"], col, 0.1))
            lc.line(pts[:split + 1], C["vert"], 9)
            if split < len(pts) - 1:
                lc.line(pts[split:], C["rouge"], 9)
            lc.disc(pts[-1][0], pts[-1][1], 14, col)
        lc.text(cfg["legende"], "ui_semi", 30, C["gris"], 0, ph / 2 - 70, align="c", opacity=prog(t, crash + 0.8, 0.3))
        tilt = 1 - ease_out_expo(p)
        lay = eb.tilt_layer(lay, tilt * 20)
        pop_layer(cv, lay, W / 2, 1090 + tilt * 80, min(1.0, p * 1.4), overshoot=1.4)
    q = prog(t, crash + 0.35, 0.4)
    pop_layer(cv, sticker(cfg["tampon"], 92, C["blanc"], C["rouge"]), W / 2 + 110, 1250, q, rot=-12)
    dx, dy = shake(t, crash + 0.35, 22)
    return punch(t, D), dx, dy


def scene_action(world, t, D):
    cfg = CFG["erreurs"][1]
    bg = C["rouge"]
    fill_bg(world, bg)
    cv = Cv(world)
    big_index(cv, 2, bg)
    chip_index(cv, 2, bg)
    pop_words(world, cfg["titre"], "titre_b", 96, 350, t - 0.05, C["blanc"], C["marine"])
    cv.text(cfg["contexte"], "ui_semi", 38, mix(bg, C["blanc"], 0.85), W / 2, 640, align="c",
            opacity=prog(t, 0.4, 0.3))
    # le compteur fond, les billets s'envolent
    melt = prog(t, 0.9, 1.7)
    k = ease_in_out_cubic(melt)
    v = cfg["de"] * (cfg["a"] / cfg["de"]) ** k
    txt = (fmt_n(v) if v >= 10 else f"{v:.2f}".replace(".", ",")) + NB + "€"
    counter(cv, txt, 170, C["blanc"], W / 2, 800)
    for j in range(7):                                         # billets qui s'envolent
        d = clamp(melt * 1.6 - j * 0.09)
        if d >= 1:
            continue
        a = j * 1.3
        x = W / 2 + math.cos(a) * 210 * d + (j - 3) * 46
        y = 1080 - j * 22 - 520 * d * d
        lay = Image.new("RGBA", (int(260 * S), int(150 * S)), (0, 0, 0, 0))
        bill(Cv(lay, 130, 75), 0, 0, 240, mix(C["vert"], C["blanc"], 0.55), C["vert"])
        lay = lay.rotate((j - 3) * 6 + d * 160 * (1 if j % 2 else -1), resample=Image.BICUBIC, expand=True)
        cv.paste(lay, x, y, opacity=1 - d)
    # … et il reste un café
    q = prog(t, 2.5, 0.5)
    if q > 0:
        lay = Image.new("RGBA", (int(420 * S), int(420 * S)), (0, 0, 0, 0))
        cup(Cv(lay, 210, 230), 0, 0, 150, C["blanc"], C["marine"], t, mix(bg, C["blanc"], 0.6))
        pop_layer(cv, lay, W / 2, 1110, q)
        pop_words(world, [cfg["chute"]], "titre", 50, 1300, t - 2.75, C["blanc"], C["or"], stagger=0.05)
    dx, dy = shake(t, 0.9, 8, 1.6)
    return punch(t, D), dx, dy


COVID = [(0, 0.86), (0.06, 0.8), (0.12, 0.62), (0.17, 0.66), (0.24, 0.2), (0.38, 0.5), (0.55, 0.62),
         (0.75, 0.78), (0.9, 0.84), (1.0, 0.88)]


def scene_panique(world, t, D):
    cfg = CFG["erreurs"][2]
    bg = C["fond"]
    fill_bg(world, bg)
    cv = Cv(world)
    big_index(cv, 3, bg)
    chip_index(cv, 3, bg)
    pop_words(world, cfg["titre"], "titre_b", 104, 350, t - 0.05, C["marine"], C["rouge"])
    cv.text(cfg["contexte"], "ui_semi", 36, C["gris"], W / 2, 630, align="c", opacity=prog(t, 0.4, 0.3))
    # montagnes russes : la chute, puis la remontée
    path = monotone(COVID, 200)
    x0, x1, y0, y1 = 90, W - 90, 720, 1240
    xc = 0.24 * ease_in_out_cubic(prog(t, 0.5, 0.8)) + 0.76 * ease_in_out_cubic(prog(t, 2.0, 1.4))
    lvl = lerp(y1, y0, 0.86)
    cv.dashed((x0, lvl), (x1, lvl), mix(bg, C["gris_clair"], 0.8), 3, 16, 12)
    if xc > 0:
        part = clip_path(path, 0, xc)
        pts = [(lerp(x0, x1, x), lerp(y1, y0, y)) for x, y in part]
        lo = next((j for j, (x, _) in enumerate(part) if x >= 0.24), len(pts))
        cv.line(pts[:lo + 1], C["rouge"], 14)
        if lo < len(pts) - 1:
            cv.line(pts[lo:], C["vert"], 14)
        hx, hy = pts[-1]
        cv.disc(hx, hy, 22, C["marine"])
        cv.disc(hx, hy, 10, C["blanc"])
    lowx, lowy = lerp(x0, x1, 0.24), lerp(y1, y0, 0.2)
    q = prog(t, 1.2, 0.35)
    pop_layer(cv, sticker(cfg["creux"], 40, C["blanc"], C["rouge"]), lowx + 150, lowy + 90, q, rot=-6)
    # bouton « VENDRE » qu'on écrase… (la perte devient réelle)
    b = prog(t, 1.4, 0.35) * (1 - prog(t, 3.0, 0.3))
    if b > 0:
        press = 1 - 0.12 * math.exp(-((t - 1.75) / 0.08) ** 2)
        r = 120 * ease_out_back(clamp(b), 2) * press
        bx, by = W - 230, 1430
        cv.disc(bx, by + 18, r, mix(C["rouge"], C["marine"], 0.45))
        cv.disc(bx, by + 18 * (1 - press) * 3, r, C["rouge"])
        cv.text(cfg["bouton"], "titre_b", 40 * press, C["blanc"], bx, by + 18 * (1 - press) * 3, align="c")
    ok = prog(t, 3.3, 0.4)
    if ok > 0:
        w = text_w(cfg["retour"], "ui_bold", 34) + 110
        cx, cy = W / 2, 1430
        cv.rrect(cx - w / 2, cy - 44, cx + w / 2, cy + 44, 44, fill=mix(bg, C["vert"], clamp(ok * 2)))
        cv.check(cx - w / 2 + 48, cy, 30, C["blanc"], ease_out_cubic(prog(t, 3.45, 0.3)), width=6)
        cv.text(cfg["retour"], "ui_bold", 34, C["blanc"], cx - w / 2 + 84, cy, opacity=clamp(ok * 2))
    dx, dy = shake(t, 1.0, 18)
    return punch(t, D), dx, dy


def scene_frais(world, t, D):
    cfg = CFG["erreurs"][3]
    bg = C["or"]
    fill_bg(world, bg)
    cv = Cv(world)
    big_index(cv, 4, bg)
    chip_index(cv, 4, bg)
    pop_words(world, cfg["titre"], "titre_b", 104, 350, t - 0.05, C["marine"], C["blanc"])
    cv.text(cfg["contexte"], "ui_semi", 32, mix(bg, C["marine"], 0.75), W / 2, 630, align="c",
            opacity=prog(t, 0.4, 0.3))
    # pièces qui fuient goutte à goutte, compteur qui grimpe
    fill = prog(t, 0.6, 1.8)
    counter(cv, "−" + fmt_n(round(cfg["montant"] * ease_out_cubic(fill) / 10) * 10) + NB + "€", 150,
            C["marine"], W / 2, 790)
    for j in range(9):
        tj = (t - 0.6 - j * 0.2)
        if 0 < tj < 0.9:
            y = 900 + 380 * (tj / 0.9) ** 2
            coin(cv, W / 2 + ((j * 37) % 5 - 2) * 70, y, 44, C["blanc"], C["marine"])
    # … qui se transforment en 21 ans de Netflix
    q = prog(t, 2.5, 0.5)
    if q > 0:
        lay = Image.new("RGBA", (int(620 * S), int(520 * S)), (0, 0, 0, 0))
        tv(Cv(lay, 310, 250), 0, 0, 520, C["marine"], C["rouge"], C["blanc"], f"21{NB}ans", 120)
        pop_layer(cv, lay, W / 2, 1150, q)
        pop_words(world, [cfg["egal"]], "titre_b", 64, 1400, t - 2.75, C["marine"], C["blanc"])
    dx, dy = shake(t, 2.5, 10)
    return punch(t, D), dx, dy


def scene_diversifier(world, t, D):
    cfg = CFG["erreurs"][4]
    bg = C["fond"]
    fill_bg(world, bg)
    cv = Cv(world)
    big_index(cv, 5, bg)
    chip_index(cv, 5, bg)
    pop_words(world, cfg["titre"], "titre_b", 104, 350, t - 0.05, C["marine"], C["rouge"])
    cv.text(cfg["contexte"], "ui_semi", 34, C["gris"], W / 2, 630, align="c", opacity=prog(t, 0.4, 0.3))
    # compteur d'années et bougies d'anniversaire
    n = cfg["annees"]
    run = prog(t, 0.7, 1.9)
    yrs = int(n * ease_in_out_cubic(run))
    counter(cv, f"{yrs}{NB}ans", 170, C["rouge"] if run < 1 else C["marine"], W / 2, 800)
    cv.text(cfg["retour"], "ui_semi", 34, C["gris"], W / 2, 920, align="c", opacity=prog(t, 2.6, 0.3))
    tc = [prog(t, 0.7 + 1.9 * (k / n), 0.35) for k in range(n)]
    lay = Image.new("RGBA", (int(900 * S), int(640 * S)), (0, 0, 0, 0))
    cake(Cv(lay, 450, 260), 0, 0, 760, n, tc, C["marine"], C["blanc"], C["or"], C["rouge"])
    pop_layer(cv, lay, W / 2, 1120, prog(t, 0.3, 0.5))
    pop_words(world, [cfg["chute"]], "titre", 52, 1405, t - 2.8, C["marine"], C["rouge"], stagger=0.05)
    return punch(t, D), 0, 0


def scene_fin(world, t, D):
    cfg = CFG["fin"]
    bg = C["marine"]
    fill_bg(world, bg)
    cv = Cv(world)
    q = prog(t, 0.0, 0.4)
    if q > 0:
        lay = Image.new("RGBA", (int(880 * S), int(380 * S)), (0, 0, 0, 0))
        lc = Cv(lay, 0, 0)
        bubble(lc, 20, 20, 860, 300, C["blanc"])
        pop_layer(cv, lay, W / 2, 520, q)
        bottom = pop_words(world, cfg["question"], "titre_b", 74, 400, t - 0.15, C["marine"], C["rouge"])
    # les 5 réponses possibles
    for k, s in enumerate(cfg["choix"]):
        p = prog(t, 0.9 + k * 0.1, 0.35)
        x = W / 2 + (k - 2) * 150
        if p > 0:
            lay = sticker(s, 54, C["marine"], C["or"], pad=0.75)
            pop_layer(cv, lay, x, 840 + math.sin(t * 6 + k) * 6 * clamp(t - 1.4), p)
    # signature
    r = prog(t, 1.4, 0.6)
    if r > 0:
        lay = text_layer(eb.typo(cfg["marque"]), "titre_b", 92, C["blanc"], 0.12)
        e = ease_out_expo(r)
        part = Image.new("RGBA", lay.size, (0, 0, 0, 0))
        part.paste(lay.crop((0, 0, max(1, int(lay.width * e)), lay.height)), (0, 0))
        cv.paste(part, W / 2, 1110)
        lw = 170 * ease_in_out_cubic(prog(t, 1.8, 0.5))
        cv.line([(W / 2 - lw, 1180), (W / 2 + lw, 1180)], C["or"], 3)
        cv.text(cfg["activite"], "ui_semi", 24, C["or"], W / 2, 1225, align="c", opacity=prog(t, 2.0, 0.4),
                tracking=0.2)
    return punch(t, D, 0.04), 0, 0


SCENES = [  # (scène, clé de voix, durée minimale, fond, sons [(t local, type)])
    (scene_accroche, "accroche", 4.2, "marine", [(0.05, "impact"), (0.6, "tic"), (1.1, "tic"), (1.6, "tic"),
                                                 (2.6, "pop")]),
    (scene_mode, "e1", 3.8, "fond", [(0.4, "pop"), (2.0, "chute"), (2.1, "pop")]),
    (scene_action, "e2", 4.0, "rouge", [(0.9, "chute"), (2.5, "pop"), (2.75, "ping")]),
    (scene_panique, "e3", 4.0, "fond", [(0.6, "chute"), (1.2, "pop"), (1.75, "barre"), (3.3, "succes")]),
    (scene_frais, "e4", 4.2, "or", [(0.6 + 0.2 * j, "tic") for j in range(6)] + [(2.5, "pop"), (2.75, "ping")]),
    (scene_diversifier, "e5", 4.4, "fond", [(0.7 + 0.38 * j, "tic") for j in range(5)] + [(2.8, "succes")]),
    (scene_fin, "fin", 3.4, "marine", [(0.05, "pop"), (0.9, "tic"), (1.0, "tic"), (1.1, "tic"), (1.2, "tic"),
                                       (1.3, "tic"), (1.4, "impact")]),
]
TIMELINE, STARTS, DURATION = [], [], 0.0


def build_timeline():
    """Durée des scènes : au moins le minimum, et assez pour la phrase de voix off.
    Si le total dépasse 30 s, on raccourcit les marges de fin de scène."""
    global STARTS, DURATION
    dv = CFG.get("_durees_voix", {})
    lead, tail = 0.2, 0.3
    durs = [max(m, lead + dv.get(key, 0) + tail) for _, key, m, _, _ in SCENES]
    durs[-1] = max(durs[-1], lead + dv.get("fin", 0) + 1.6)       # le temps de lire la signature
    over = sum(durs) - MAX_DUREE
    if over > 0:
        slack = [d - (lead + dv.get(key, 0) + 0.12) for d, (_, key, *_r) in zip(durs, SCENES)]
        k = min(1.0, over / max(1e-6, sum(slack)))
        durs = [d - s * k for d, s in zip(durs, slack)]
    TIMELINE[:] = [(d, f, "volet" if i else None, snd) for i, (d, (f, _, _, _, snd)) in enumerate(zip(durs, SCENES))]
    STARTS, acc = [], 0.0
    for d in durs:
        STARTS.append(acc)
        acc += d
    DURATION = acc
    eb.VO[:] = [(STARTS[i] - (TR / 2 if i else 0) + lead + (0.0 if i else 0.15), key)
                for i, (_, key, *_r) in enumerate(SCENES) if key in dv]


def scene_local_time(k, t):
    return t - STARTS[k] + (TR / 2 if k > 0 else 0)


# --------------------------------------------------------------------------- #
# Rendu
# --------------------------------------------------------------------------- #
def overlays(world, k, t_local, bg):
    """Barre de progression façon story et mentions légales, sur chaque image."""
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
    rows = eb.layout([CFG["mentions"]], "texte", 21, W - 140)
    for r, ln in enumerate(rows):
        s = " ".join(w for w, _ in ln)
        cv.text(s, "texte", 21, mix(bg, fg, 0.62), W / 2, 1610 + r * 30, align="c")


def render_scene(k, t):
    tl = scene_local_time(k, t)
    bg = C[SCENES[k][3]]
    world = Image.new("RGBA", (W * S, H * S), bg)
    zoom, dx, dy = TIMELINE[k][1](world, tl, TIMELINE[k][0]) or (1.0, 0, 0)
    cw, ch = W * S / zoom, H * S / zoom
    cx = clamp(W * S / 2 + dx * S, cw / 2, W * S - cw / 2)
    cy = clamp(H * S / 2 + dy * S, ch / 2, H * S - ch / 2)
    img = world.resize((W * S, H * S), Image.BILINEAR, box=(cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2)) \
        if zoom != 1.0 or dx or dy else world
    overlays(img, k, tl, bg)          # la barre et les mentions ne bougent pas avec la caméra
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
        p = eb.ease_in_out_expo(u)
        yy, xx = np.mgrid[0:H, 0:W]
        edge = (1 - p) * (W + H * 0.35) - H * 0.35 * (1 - yy / H)
        m = np.clip((xx - edge) / 3.0, 0, 1)[..., None]
        out = A * (1 - m) + B * m
        band = (np.abs(xx - edge) < 9)[..., None] & (p > 0) & (p < 1)
        out = np.where(band, np.array(C["or"][:3], np.float32), out)
    f = min(prog(t, 0, 0.15), 1 - prog(t, DURATION - 0.4, 0.4))
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
    ap.add_argument("--out", default=os.path.join(ROOT, "output", "short-5-erreurs.mp4"))
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
        secs = args.preview or [s + d - 0.4 for s, (d, *_) in zip(STARTS, TIMELINE)]
        for sec in secs:
            Image.frombytes("RGB", (W, H), render_frame(int(round(sec * FPS)))).save(
                os.path.join(outdir, f"t{sec:05.2f}.png"))
        print("Aperçus dans", outdir)
        return

    # musique plus nerveuse : pulsation à chaque temps, de la première coupe jusqu'à la signature
    eb.TIMELINE[:] = TIMELINE
    eb.STARTS, eb.DURATION, eb.TR = STARTS, DURATION, TR
    eb.KICK_EVERY, eb.KICK_STOP = 1, -1
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
