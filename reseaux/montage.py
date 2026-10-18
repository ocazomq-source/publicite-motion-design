#!/usr/bin/env python3
"""Montage court pour les réseaux (9:16) à partir d'une vidéo face caméra.

Coupes, zooms « jump cut » alternés, sous-titres mot à mot surlignés, bandeau nom
en ouverture et appel à l'action en fin de vidéo. Tout se règle dans un fichier JSON :

    python3 reseaux/montage.py reseaux/cindy-mcv.json            # vidéo complète
    python3 reseaux/montage.py reseaux/cindy-mcv.json --preview  # quelques images clés

Transcription mot à mot (faster-whisper) d'une vidéo source vers un JSON de mots :

    python3 reseaux/montage.py --transcrire source.mp4 mots.json
"""
import json
import math
import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1080, 1920, 30
FONT_DIR = "/usr/share/fonts/opentype/inter"
FALLBACK = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_fonts = {}


def font(name, size):
    key = (name, size)
    if key not in _fonts:
        path = os.path.join(FONT_DIR, name)
        _fonts[key] = ImageFont.truetype(path if os.path.exists(path) else FALLBACK, size)
    return _fonts[key]


def ease_out_back(x, s=1.8):
    x = min(max(x, 0.0), 1.0) - 1
    return 1 + (s + 1) * x ** 3 + s * x ** 2


def ease_in_out(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# ---------------------------------------------------------------- transcription

def transcrire(source, sortie, modele="small"):
    from faster_whisper import WhisperModel
    m = WhisperModel(modele, device="cpu", compute_type="int8")
    segments, _ = m.transcribe(source, language="fr", word_timestamps=True, vad_filter=True)
    mots = []
    for seg in segments:
        for w in seg.words:
            mots.append({"w": w.word.strip(), "t0": round(w.start, 3), "t1": round(w.end, 3)})
        print(f"[{seg.start:6.1f}–{seg.end:6.1f}] {seg.text.strip()}")
    with open(sortie, "w", encoding="utf-8") as f:
        json.dump(mots, f, ensure_ascii=False, indent=0)


# ---------------------------------------------------------------- timeline

class Montage:
    def __init__(self, cfg, base):
        self.cfg = cfg
        self.base = base
        self.source = os.path.join(base, cfg["source"])
        self.segments = [tuple(s) for s in cfg["segments"]]
        # début de chaque segment dans la vidéo montée
        self.offsets, t = [], 0.0
        for a, b in self.segments:
            self.offsets.append(t)
            t += b - a
        self.duree_parole = t
        self.cta = cfg.get("cta", {})
        self.duree_cta = self.cta.get("duree", 3.0) if self.cta else 0.0
        self.duree = self.duree_parole + self.duree_cta
        mots = cfg.get("mots", [])
        if isinstance(mots, str):
            with open(os.path.join(base, mots), encoding="utf-8") as f:
                mots = json.load(f)
        self.mots = self._mots_montes(mots)
        self.lignes = self._grouper(self.mots)
        self.blocs = self._blocs(self.mots)
        self.forts = {w.lower().strip(".,!?;:«»\"'") for w in cfg.get("mots_forts", [])}

    def vers_monte(self, ts):
        for (a, b), off in zip(self.segments, self.offsets):
            if a <= ts < b:
                return off + ts - a
        return None

    def _mots_montes(self, mots):
        res = []
        for m in mots:
            t0 = self.vers_monte(m["t0"])
            if t0 is None:
                continue
            t1 = self.vers_monte(min(m["t1"], m["t0"] + 1.2) - 1e-3)
            res.append({"w": m["w"], "t0": t0, "t1": t1 if t1 is not None else t0 + 0.25})
        return res

    def _grouper(self, mots):
        """Groupes de 1 à 3 mots, coupés sur la ponctuation, les pauses et la largeur."""
        st = self.cfg.get("sous_titres", {})
        taille, maxw, maxn = st.get("taille", 92), st.get("largeur_max", 900), st.get("mots_max", 3)
        f = font("Inter-Black.otf", taille)
        lignes, cur = [], []
        for i, m in enumerate(mots):
            cur.append(m)
            texte = " ".join(x["w"] for x in cur).upper()
            suivant = mots[i + 1] if i + 1 < len(mots) else None
            fin = (suivant is None or len(cur) >= maxn or m["w"][-1:] in ".,!?;:"
                   or suivant["t0"] - m["t1"] > 0.35
                   or f.getlength(texte + " " + suivant["w"].upper()) > maxw)
            if fin:
                lignes.append(cur)
                cur = []
        for i, l in enumerate(lignes):
            fin = lignes[i + 1][0]["t0"] - 0.05 if i + 1 < len(lignes) else l[-1]["t1"] + 0.4
            l[-1]["fin_ligne"] = min(fin, l[-1]["t1"] + 0.6)
        return lignes

    def _blocs(self, mots):
        """Style « reel » : blocs de 2 à 4 lignes, coupés en fin de phrase ou sur une pause."""
        st = self.cfg.get("sous_titres", {})
        f = font(st.get("police", "InterDisplay-Bold.otf"), st.get("taille", 98))
        maxw, maxl = st.get("largeur_max", 820), st.get("lignes_max", 4)
        blocs, lignes, cur = [], [], []
        for i, m in enumerate(mots):
            if cur and f.getlength(" ".join(x["w"] for x in cur + [m])) > maxw:
                lignes.append(cur)
                cur = []
            cur.append(m)
            suivant = mots[i + 1] if i + 1 < len(mots) else None
            fin = (suivant is None or suivant["t0"] - m["t1"] > 0.6
                   or (m["w"][-1:] in ".!?" and len(lignes) >= 1)
                   or (len(lignes) == maxl - 1 and suivant is not None
                       and f.getlength(" ".join(x["w"] for x in cur + [suivant])) > maxw))
            if fin:
                lignes.append(cur)
                cur = []
                blocs.append(lignes)
                lignes = []
        for i, b in enumerate(blocs):
            dernier = b[-1][-1]
            suite = blocs[i + 1][0][0]["t0"] if i + 1 < len(blocs) else dernier["t1"] + 0.8
            b_fin = min(suite - 0.02, dernier["t1"] + 0.8)
            for l in b:
                for m in l:
                    m["fin_bloc"] = b_fin
        return blocs

    # ------------------------------------------------------------ cadrage

    def plans(self):
        """Plans de cadrage [début, zoom, centre_y, centre_x] : donnés dans le JSON, sinon un par segment."""
        z = self.cfg.get("zoom", {})
        defaut = (z.get("centre_y", 0.36), z.get("centre_x", 0.5))
        if "plans" in z:
            return [tuple(p[:2]) + tuple(p[2:]) + defaut[len(p) - 2:] for p in z["plans"]]
        return [(o, z.get("serre", 1.18) if i % 2 else 1.0) + defaut for i, o in enumerate(self.offsets)]

    def zoom(self, t):
        """Zoom par plan + entrée « whip » à chaque coupe + poussée continue + « punchs »."""
        z = self.cfg.get("zoom", {})
        if t >= self.duree_parole:
            return 1.0, 0.5, 0.5
        plans = self.plans()
        idx = max(i for i, p in enumerate(plans) if p[0] <= t)
        debut, base, cy, cx = plans[idx]
        fin = plans[idx + 1][0] if idx + 1 < len(plans) else self.duree_parole
        local = t - debut
        base *= 1 + z.get("poussee", 0.035) * local / max(fin - debut, 1)
        tr = self.transition(t)
        if tr and tr[0] in ("whip", "flou", "choc"):
            base *= 1 + z.get("entree", 0.1) * (1 - ease_in_out(tr[1]))
        punchs = list(z.get("punchs", [])) + [m["t0"] for m in self.mots if self._fort(m["w"])]
        for tp in punchs:
            if tp - 0.05 <= t < tp + 0.6:
                x = (t - tp + 0.05) / 0.65
                base *= 1 + z.get("punch", 0.07) * math.sin(math.pi * min(x, 1)) ** 0.6
                break
        return base, cy, cx

    def transition(self, t):
        """(type, avancement 0→1) si t est dans une transition, sinon None.

        "transitions" : [[temps, type], …] avec type whip (zoom + éclair), glisse (filé
        horizontal), flou (flou de zoom), choc (secousse + éclair)."""
        for i, (tt, typ) in enumerate(self.cfg.get("transitions", [])):
            d = 0.22 if typ == "glisse" else 0.28
            if tt <= t < tt + d and t < self.duree_parole:
                return typ, (t - tt) / d, i
        return None

    def _fort(self, w):
        return w.lower().strip(".,!?;:«»\"'") in self.forts


# ---------------------------------------------------------------- dessin

def texte_contour(img, xy, texte, f, fill, contour=9, ombre=True, anchor="mm"):
    d = ImageDraw.Draw(img)
    if ombre:
        # ombre floutée sur une vignette autour du texte seulement (bien plus rapide que plein cadre)
        x0, y0, x1, y1 = d.textbbox((xy[0], xy[1] + 8), texte, font=f, anchor=anchor, stroke_width=contour)
        m = 30
        x0, y0 = int(x0) - m, int(y0) - m
        sh = Image.new("RGBA", (int(x1) - x0 + m, int(y1) - y0 + m), (0, 0, 0, 0))
        ImageDraw.Draw(sh).text((xy[0] - x0, xy[1] + 8 - y0), texte, font=f, fill=(0, 0, 0, 150),
                                anchor=anchor, stroke_width=contour, stroke_fill=(0, 0, 0, 150))
        sh = sh.filter(ImageFilter.GaussianBlur(10))
        cx0, cy0 = max(x0, 0), max(y0, 0)
        cx1, cy1 = min(x0 + sh.width, img.width), min(y0 + sh.height, img.height)
        if cx1 > cx0 and cy1 > cy0:
            img.alpha_composite(sh.crop((cx0 - x0, cy0 - y0, cx1 - x0, cy1 - y0)), (cx0, cy0))
    d.text(xy, texte, font=f, fill=fill, anchor=anchor, stroke_width=contour, stroke_fill=(0, 0, 0, 255))


def dessiner_sous_titres_reel(img, mt, t):
    """Blocs de plusieurs lignes, mots qui apparaissent au fil de la voix, mots-clés en couleur."""
    st = mt.cfg.get("sous_titres", {})
    taille = st.get("taille", 98)
    f = font(st.get("police", "InterDisplay-Bold.otf"), taille)
    interligne = int(taille * st.get("interligne", 0.95))
    cle = hex_rgb(st.get("couleur", "#FFC21A"))
    cx, y_bas = st.get("x", 0.47) * W, st.get("y", 1500)
    esp = f.getlength(" ")
    for bloc in mt.blocs:
        if not (bloc[0][0]["t0"] - 0.03 <= t < bloc[-1][-1]["fin_bloc"]):
            continue
        y0 = y_bas - interligne * len(bloc)
        calque = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ombre = Image.new("RGBA", img.size, (0, 0, 0, 0))
        dc, do = ImageDraw.Draw(calque), ImageDraw.Draw(ombre)
        for k, ligne in enumerate(bloc):
            larg = sum(f.getlength(m["w"]) for m in ligne) + esp * (len(ligne) - 1)
            # lignes légèrement décalées les unes des autres, comme dans les reels
            x = cx - larg / 2 + (k % 2) * st.get("decalage", 18)
            y = y0 + k * interligne
            for m in ligne:
                a = (t - m["t0"] + 0.03) / 0.12
                if a > 0:
                    a = min(a, 1)
                    lev = int(10 * (1 - ease_in_out(a)))
                    col = cle if mt._fort(m["w"]) else (255, 255, 255)
                    dc.text((x, y + lev), m["w"], font=f, fill=col + (int(255 * a),))
                    do.text((x, y + lev + 4), m["w"], font=f, fill=(0, 0, 0, int(170 * a)))
                x += f.getlength(m["w"]) + esp
        bande = (0, max(int(y0) - 30, 0), W, min(int(y_bas + taille * 0.6) + 30, H))
        img.alpha_composite(ombre.crop(bande).filter(ImageFilter.GaussianBlur(7)), bande[:2])
        img.alpha_composite(calque)


def dessiner_sous_titres(img, mt, t):
    st = mt.cfg.get("sous_titres", {})
    if st.get("style") == "reel":
        return dessiner_sous_titres_reel(img, mt, t)
    taille = st.get("taille", 92)
    y = st.get("y", 1330)
    surl = hex_rgb(st.get("couleur", "#FFE03A")) + (255,)
    for ligne in mt.lignes:
        debut, fin = ligne[0]["t0"] - 0.05, ligne[-1]["fin_ligne"]
        if not (debut <= t < fin):
            continue
        # apparition du groupe : petit rebond
        app = ease_out_back((t - debut) / 0.18)
        f = font("Inter-Black.otf", taille)
        mots = [m["w"].upper() for m in ligne]
        esp = f.getlength(" ")
        larg = [f.getlength(w) for w in mots]
        total = sum(larg) + esp * (len(mots) - 1)
        x = W / 2 - total / 2
        actifs = []
        for m, w, lw in zip(ligne, mots, larg):
            cx, x = x + lw / 2, x + lw + esp
            if m["t0"] <= t < max(m["t1"], m["t0"] + 0.12):
                actifs.append((m, w, cx))  # dessiné en dernier, par-dessus ses voisins
                continue
            s = max(1, int(taille * (0.6 + 0.4 * app)))
            texte_contour(img, (cx, y), w, font("Inter-Black.otf", s), (255, 255, 255, 255),
                          contour=max(4, s // 10))
        for m, w, cx in actifs:
            pop = 1 + 0.08 * (1 - ease_in_out((t - m["t0"]) / 0.15))
            s = max(1, int(taille * pop * (0.6 + 0.4 * app)))
            texte_contour(img, (cx, y), w, font("Inter-Black.otf", s), surl, contour=max(4, s // 10))


def dessiner_bandeau(img, mt, t):
    b = mt.cfg.get("bandeau")
    if not b:
        return
    t0, t1 = b.get("debut", 0.4), b.get("fin", 4.0)
    if not (t0 <= t < t1 + 0.4):
        return
    entree = ease_out_back((t - t0) / 0.45, 1.2)
    sortie = ease_in_out((t - t1) / 0.35) if t > t1 else 0
    nom = b["nom"].upper()
    f = font("Inter-Black.otf", 54)
    fs = font("Inter-SemiBold.otf", 34)
    sous = b.get("sous_titre", "")
    lw = max(f.getlength(nom), fs.getlength(sous) if sous else 0)
    pw, ph = int(lw + 90), 150 if sous else 104
    calque = Image.new("RGBA", (pw + 40, ph + 40), (0, 0, 0, 0))
    d = ImageDraw.Draw(calque)
    d.rounded_rectangle((20, 20, 20 + pw, 20 + ph), 30, fill=(255, 255, 255, 245))
    d.rounded_rectangle((20, 20, 34, 20 + ph), 7, fill=hex_rgb(b.get("couleur", "#FFE03A")) + (255,))
    d.text((60, 20 + (52 if sous else ph // 2)), nom, font=f, fill=(15, 15, 20), anchor="lm")
    if sous:
        d.text((60, 20 + 112), sous, font=fs, fill=(80, 80, 90), anchor="lm")
    largeur_vis = int(calque.width * min(1, max(0.0, entree)))
    if largeur_vis <= 0:
        return
    calque = calque.crop((0, 0, largeur_vis, calque.height))
    a = int(255 * (1 - sortie))
    if a < 255:
        calque.putalpha(calque.getchannel("A").point(lambda v: v * a // 255))
    x = int(60 - 60 * sortie)
    img.alpha_composite(calque, (x, b.get("y", 230)))


def bulle(d, cx, cy, r, fill):
    d.rounded_rectangle((cx - r, cy - r * 0.78, cx + r, cy + r * 0.62), int(r * 0.45), fill=fill)
    d.polygon([(cx - r * 0.45, cy + r * 0.5), (cx - r * 0.62, cy + r * 1.0), (cx - r * 0.05, cy + r * 0.55)],
              fill=fill)
    for k in (-1, 0, 1):
        d.ellipse((cx + k * r * 0.42 - r * 0.11, cy - r * 0.19, cx + k * r * 0.42 + r * 0.11, cy + r * 0.03),
                  fill=(20, 20, 25))


def dessiner_cta(img, mt, t):
    if not mt.cta or t < mt.duree_parole - 0.3:
        return img
    c = mt.cta
    x = ease_in_out((t - mt.duree_parole + 0.3) / 0.5)
    fond = img.convert("RGB").filter(ImageFilter.GaussianBlur(int(2 + 22 * x))).convert("RGBA")
    voile = Image.new("RGBA", img.size, (0, 0, 0, int(140 * x)))
    fond.alpha_composite(voile)
    tc = t - mt.duree_parole
    jaune = hex_rgb(c.get("couleur", "#FFE03A")) + (255,)
    calque = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(calque)
    # bulle de commentaire qui saute
    eb = ease_out_back(tc / 0.4, 2.2)
    if eb > 0:
        saut = 18 * math.sin(max(tc - 0.4, 0) * 6) * math.exp(-max(tc - 0.4, 0) * 1.5)
        bulle(d, W / 2, 640 - saut, 120 * eb, jaune)
    e1 = ease_out_back((tc - 0.25) / 0.35)
    if e1 > 0:
        texte_contour(calque, (W / 2, 900), c.get("ligne1", "COMMENTE").upper(),
                      font("Inter-Black.otf", max(1, int(110 * e1))), (255, 255, 255, 255), 10)
    e2 = ease_out_back((tc - 0.5) / 0.4, 2.5)
    if e2 > 0:
        pulse = 1 + 0.05 * math.sin(max(tc - 0.9, 0) * 7)
        s = max(1, int(230 * e2 * pulse))
        texte_contour(calque, (W / 2, 1110), c.get("mot", "MCV"), font("Inter-Black.otf", s), jaune, 16)
    e3 = ease_in_out((tc - 0.9) / 0.4)
    if e3 > 0 and c.get("ligne3"):
        f3 = font("Inter-Bold.otf", 60)
        lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        texte_contour(lay, (W / 2, 1290 + 30 * (1 - e3)), c["ligne3"], f3, (255, 255, 255, 255), 5)
        lay.putalpha(lay.getchannel("A").point(lambda v: int(v * e3)))
        calque.alpha_composite(lay)
    fond.alpha_composite(calque)
    return fond


def dessiner_progression(img, mt, t):
    if not mt.cfg.get("barre_progression", True):
        return
    d = ImageDraw.Draw(img)
    p = min(t / mt.duree, 1)
    d.rectangle((0, 0, W, 10), fill=(255, 255, 255, 70))
    d.rectangle((0, 0, int(W * p), 10), fill=hex_rgb(mt.cfg.get("sous_titres", {}).get("couleur", "#FFE03A")))


# ---------------------------------------------------------------- rendu

def cadrer(frame, z, cy, cx=0.5):
    """frame : image source (déjà en W×H) ; zoom qui garde fixe le point (cx, cy)."""
    if abs(z - 1) < 1e-3:
        return frame
    cw, ch = W / z, H / z
    x0 = min(max(cx * W - cw * cx, 0), W - cw)
    y0 = min(max(cy * H - ch * cy, 0), H - ch)
    return frame.resize((W, H), Image.BICUBIC, box=(x0, y0, x0 + cw, y0 + ch))


def flou_rapide(img, rayon, horizontal=False):
    """Flou approximatif calculé en basse résolution (filé horizontal si demandé)."""
    if rayon < 0.5:
        return img
    k = 4
    petit = img.resize((W // k, H // k), Image.BILINEAR)
    if horizontal:
        a = np.asarray(petit).astype(np.float32)
        n = max(1, int(rayon / k * 2))
        a = sum(np.roll(a, s, axis=1) for s in range(-n, n + 1)) / (2 * n + 1)
        petit = Image.fromarray(a.astype(np.uint8), petit.mode)
    else:
        petit = petit.filter(ImageFilter.GaussianBlur(rayon / k))
    return petit.resize((W, H), Image.BILINEAR)


def appliquer_transition(img, mt, t):
    tr = mt.transition(t)
    if not tr:
        return img
    typ, x, i = tr
    flash = mt.cfg.get("zoom", {}).get("flash", 0.35)
    if typ == "glisse":
        sens = 1 if i % 2 else -1
        e = ease_in_out(x)
        dx = int(sens * W * 0.22 * (1 - e))
        img = Image.fromarray(np.roll(np.asarray(img), dx, axis=1), img.mode)
        img = Image.blend(img, flou_rapide(img, 60 * (1 - e), horizontal=True), 1 - e)
    elif typ == "flou":
        img = flou_rapide(img, 26 * (1 - ease_in_out(x)))
    elif typ == "choc":
        amp = 26 * (1 - x) ** 2
        dx, dy = int(amp * math.sin(x * 47)), int(amp * math.cos(x * 39))
        img = Image.fromarray(np.roll(np.roll(np.asarray(img), dx, axis=1), dy, axis=0), img.mode)
    if typ in ("whip", "choc") and x < 0.45:
        a = int(255 * flash * (1 - x / 0.45))
        img.alpha_composite(Image.new("RGBA", img.size, (255, 255, 255, a)))
    return img


def image(mt, src, t):
    z, cy, cx = mt.zoom(t)
    img = cadrer(src, z, cy, cx).convert("RGBA")
    img = appliquer_transition(img, mt, t)
    dessiner_bandeau(img, mt, t)
    if t < mt.duree_parole:
        dessiner_sous_titres(img, mt, t)
    img = dessiner_cta(img, mt, t)
    dessiner_progression(img, mt, t)
    return img.convert("RGB")


def filtre_coupes(mt, piste):
    """Graphe ffmpeg qui enchaîne les segments retenus, pour la piste « v » ou « a »."""
    parts, n = [], len(mt.segments)
    for i, (a, b) in enumerate(mt.segments):
        if piste == "v":
            parts.append(f"[0:v]trim={a}:{b},setpts=PTS-STARTPTS[s{i}];")
        else:
            parts.append(f"[0:a]atrim={a}:{b},asetpts=PTS-STARTPTS,"
                         f"afade=t=in:d=0.02,afade=t=out:st={b - a - 0.03:.3f}:d=0.03[s{i}];")
    v, a = (1, 0) if piste == "v" else (0, 1)
    parts.append("".join(f"[s{i}]" for i in range(n)) + f"concat=n={n}:v={v}:a={a}")
    if piste == "v" and mt.cfg.get("etalonnage"):
        parts.append("," + mt.cfg["etalonnage"])
    if piste == "v":
        parts.append(f",scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
                     f"crop={W}:{H},fps={FPS},setsar=1")
    return "".join(parts) + "[out]"


def traiter_voix(mt, voix, sr, tmp):
    """Débruitage spectral doux (noisereduce), puis chaîne ffmpeg « audio_filtre » (EQ, compression…)."""
    force = mt.cfg.get("debruitage", 0)
    if force:
        import noisereduce
        voix = noisereduce.reduce_noise(y=voix, sr=sr, stationary=True, prop_decrease=force,
                                        n_fft=2048, freq_mask_smooth_hz=300, time_mask_smooth_ms=60)
        voix = voix.astype(np.float32)
    filtre = mt.cfg.get("audio_filtre")
    if filtre:
        voix.tofile(tmp)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", tmp,
                        "-af", filtre + f",aresample={sr}", "-f", "f32le", tmp + ".2"], check=True)
        voix = np.fromfile(tmp + ".2", dtype=np.float32)
        os.remove(tmp + ".2")
    return voix


def audio_final(mt, tmp):
    """Voix coupée + silence pendant l'appel à l'action + « pop » à l'apparition de MCV."""
    sr = 48000
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", mt.source, "-filter_complex", filtre_coupes(mt, "a"),
                    "-map", "[out]", "-ac", "1", "-ar", str(sr), "-f", "f32le", tmp], check=True)
    voix = np.fromfile(tmp, dtype=np.float32)
    voix = traiter_voix(mt, voix, sr, tmp)
    n = int(mt.duree * sr)
    out = np.zeros(n, dtype=np.float32)
    out[:min(len(voix), n)] = voix[:n]
    gain = mt.cfg.get("gain_voix", 1.0)
    out *= gain
    if mt.cta:
        for dt, f0 in ((0.0, 520), (0.5, 780)):
            i0 = int((mt.duree_parole + dt) * sr)
            k = np.arange(int(0.12 * sr)) / sr
            pop = 0.35 * np.sin(2 * np.pi * (f0 + 900 * k) * k) * np.exp(-k * 38)
            out[i0:i0 + len(pop)] += pop[:max(0, n - i0)]
    np.clip(out, -1, 1).tofile(tmp)
    return tmp, sr


def rendre(mt, sortie, apercu=None):
    os.makedirs(os.path.dirname(sortie) or ".", exist_ok=True)
    lecture = subprocess.Popen(["ffmpeg", "-v", "error", "-i", mt.source, "-filter_complex", filtre_coupes(mt, "v"),
                                "-map", "[out]", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                               stdout=subprocess.PIPE)
    n_total = int(round(mt.duree * FPS))
    if apercu is not None:
        voulues = {int(round(t * FPS)): t for t in apercu}
        derniere = None
        for i in range(n_total):
            raw = lecture.stdout.read(W * H * 3) if i < int(mt.duree_parole * FPS) else b""
            if raw:
                derniere = Image.frombytes("RGB", (W, H), raw)
            if i in voulues:
                image(mt, derniere, i / FPS).save(f"{sortie}-{voulues[i]:06.2f}.jpg", quality=88)
            if i > max(voulues):
                break
        lecture.kill()
        return
    tmp = sortie + ".voix.f32"
    tmp, sr = audio_final(mt, tmp)
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                            "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                            "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", tmp,
                            "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
                            "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-shortest",
                            "-movflags", "+faststart", sortie], stdin=subprocess.PIPE)
    derniere = None
    n_parole = int(mt.duree_parole * FPS)
    for i in range(n_total):
        if i < n_parole:
            raw = lecture.stdout.read(W * H * 3)
            if len(raw) == W * H * 3:
                derniere = Image.frombytes("RGB", (W, H), raw)
        enc.stdin.write(image(mt, derniere, i / FPS).tobytes())
        if i % (FPS * 5) == 0:
            print(f"  {i / FPS:5.1f} / {mt.duree:.1f} s", flush=True)
    enc.stdin.close()
    enc.wait()
    lecture.kill()
    os.remove(tmp)
    print("→", sortie)


def main():
    args = sys.argv[1:]
    if args and args[0] == "--transcrire":
        transcrire(*args[1:])
        return
    cfg_path = args[0]
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    base = os.path.dirname(os.path.abspath(cfg_path))
    mt = Montage(cfg, base)
    sortie = os.path.join(base, cfg.get("sortie", "output/montage.mp4"))
    print(f"Durée montée : {mt.duree:.1f} s ({len(mt.segments)} plans, {len(mt.mots)} mots)")
    if "--preview" in args:
        i = args.index("--preview")
        ts = [float(x) for x in args[i + 1:]] or [0.6, 1.5, mt.duree_parole / 2,
                                                   mt.duree_parole + 0.3, mt.duree - 0.2]
        rendre(mt, os.path.splitext(sortie)[0] + "-apercu", ts)
    else:
        rendre(mt, sortie)


if __name__ == "__main__":
    main()
