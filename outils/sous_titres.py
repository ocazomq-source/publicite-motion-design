#!/usr/bin/env python3
"""Sous-titrage dynamique « chaud et jovial » par-dessus une vidéo 9:16.

Deux couches, inspirées des Reels à grosse typographie :
  1. TITRES : un mot clé en très grand (une ligne fine + une ligne extra-grasse en dégradé
     soleil), placé DERRIÈRE la personne (détourage image par image) ;
  2. SOUS-TITRES : la phrase apparaît mot à mot (flou → net, légère montée) ; les mots clés
     sont en jaune-orangé avec un petit rebond.

    python3 outils/sous_titres.py ENTREE.mp4 SORTIE.mp4 [--preview] [--image T]

Le texte et les timecodes de découpage sont dans PAROLES / TITRES ci-dessous.
Dépendances : numpy, opencv-python-headless, Pillow, mediapipe (+ libegl1 sous Linux),
modèle selfie_multiclass_256x256.tflite (téléchargé automatiquement), ffmpeg.
"""
import os
import subprocess
import sys
import urllib.request

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ICI = os.path.dirname(os.path.abspath(__file__))
POLICES = os.path.join(ICI, "..", "assets", "fonts")
MODELE = os.path.join(ICI, "..", "assets", "selfie_multiclass_256x256.tflite")
URL_MODELE = ("https://storage.googleapis.com/mediapipe-models/image_segmenter/"
              "selfie_multiclass_256x256/float32/latest/selfie_multiclass_256x256.tflite")
FPS, W, H = 30, 1080, 1920

# ---------------------------------------------------------------- palette chaude
CREME = (255, 244, 226)          # texte courant
SOLEIL_HAUT = (255, 214, 102)    # dégradé des titres (jaune mangue → orange corail)
SOLEIL_BAS = (255, 112, 59)
MOT_CLE = (255, 196, 61)         # mots clés des sous-titres
CONTOUR = (74, 33, 12)           # brun chaud (contour / ombre), plus doux que le noir

# ------------------------------------------------------------------- paroles
# Un bloc = un passage parlé (début, fin en s, détecté sur la voix). « / » sépare les
# groupes affichés ensemble, *…* marque les mots clés. Les mots sont répartis dans le bloc
# au prorata de leurs syllabes, uniquement sur les instants où la voix est active.
PAROLES = [
    (0.15, 4.85, "Vous avez créé / une *SCI* / pour protéger / votre *patrimoine,* / mais elle / "
                 "ne vous sert / peut-être *à rien.* / Je vous explique."),
    (5.10, 7.55, "Pour *vendre* le bien, / faire des *travaux,* / changer de *gérant*"),
    (7.75, 8.90, "ou même modifier / les *statuts,*"),
    (9.25, 11.95, "il faut donc que / *tout le monde* / soit *d'accord.* / Vous avez payé / *2\u00a0000\u00a0euros*"),
    (12.10, 13.10, "pour créer / votre SCI,"),
    (13.30, 15.15, "éviter de vous / retrouver / en *indivision* / sur le bien."),
    (15.75, 17.55, "Tout ça pour / vous retrouver / en *indivision* / sur les *parts\u00a0!*"),
    (18.15, 21.45, "Alors la SCI, / c'est vrai, / est un *excellent* / *outil* / pour préparer / "
                   "la *transmission,*"),
    (21.80, 27.35, "mais il faut faire / *très attention* / aux règles / de *décision.* / La *clé,* / "
                   "c'est d'avoir / des décisions / qui se prennent / à la *majorité*"),
    (27.70, 28.75, "plutôt qu'à / l'*unanimité,*"),
    (29.10, 31.95, "et idéalement / d'y *penser* / dès la rédaction / des *statuts.*"),
    (32.50, 33.70, "Et *vous,* / dans votre SCI,"),
    (33.85, 36.30, "les décisions / se prennent / à l'*unanimité* / ou à la *majorité\u00a0?*"),
]

# Grands titres derrière la personne : (mot d'ancrage, n-ième occurrence, fin en s,
# ligne fine, ligne grasse). Le titre apparaît quand le mot d'ancrage est prononcé.
TITRES = [
    ("Vous", 1, 3.80, "votre", "SCI"),
    ("payé", 1, 13.15, "payé", "2\u00a0000\u00a0€"),
    ("indivision", 2, 17.70, "en", "indivision"),
    ("règles", 1, 24.40, "règles de", "décision"),
    ("clé,", 1, 28.75, "la clé :", "majorité"),
    ("les", 3, 36.73, "unanimité ou", "majorité\u00a0?"),
]


def police(nom, taille):
    return ImageFont.truetype(os.path.join(POLICES, f"Poppins-{nom}.ttf"), taille)


def ease_out(u):
    return 1 - (1 - min(max(u, 0), 1)) ** 3


def ease_back(u):
    u = min(max(u, 0), 1)
    c = 1.9
    return 1 + (c + 1) * (u - 1) ** 3 + c * (u - 1) ** 2


# ------------------------------------------------------------------ alignement
def syllabes(mot):
    import re
    n = len(re.findall(r"[aeiouyàâäéèêëîïôöùûü]+", mot.lower()))
    if mot.lower().endswith("e") and n > 1:
        n -= 0.5
    return max(n, 0.6) + 0.15 * len(mot) / 4


def voix_active(audio_wav):
    import wave
    w = wave.open(audio_wav)
    sr = w.getframerate()
    x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    hop = sr // 100
    db = np.array([20 * np.log10(np.sqrt((x[i:i + hop] ** 2).mean()) + 1e-6)
                   for i in range(0, len(x) - hop, hop)])
    return db > -30  # pas de 10 ms


def aligner(actif):
    mots, groupes = [], []
    for t0, t1, texte in PAROLES:
        brut = []
        for gi, g in enumerate(texte.split("/")):
            for m in g.split():
                brut.append([m, gi])
        # état mot-clé : *…* peut couvrir plusieurs mots
        cle = False
        for b in brut:
            m = b[0]
            debut = m.startswith("*") or (m.startswith("l'*") or m.startswith("d'*"))
            if debut:
                cle = True
            b.append(cle)
            if m.endswith("*") or m.rstrip(",.!?").endswith("*"):
                cle = False
            b[0] = m.replace("*", "")
        a, b_ = int(t0 * 100), int(t1 * 100)
        idx = [i for i in range(a, min(b_, len(actif))) if actif[i]] or list(range(a, b_))
        poids = np.array([syllabes(m) for m, _, _ in brut])
        bornes = np.concatenate([[0], np.cumsum(poids)]) / poids.sum() * (len(idx) - 1)
        base = len(groupes)
        for k, (m, gi, c) in enumerate(brut):
            d = idx[int(round(bornes[k]))] / 100
            f = idx[int(round(bornes[k + 1]))] / 100
            mots.append({"mot": m, "t": d, "fin": f, "cle": c, "groupe": base + gi})
        groupes.extend(range(len(texte.split("/"))))
    # bornes des groupes
    gr = {}
    for m in mots:
        g = gr.setdefault(m["groupe"], {"debut": m["t"], "fin": m["fin"], "mots": []})
        g["fin"] = max(g["fin"], m["fin"])
        g["mots"].append(m)
    ordre = sorted(gr)
    for i, k in enumerate(ordre):
        suivant = gr[ordre[i + 1]]["debut"] if i + 1 < len(ordre) else 1e9
        gr[k]["sortie"] = min(suivant, gr[k]["fin"] + 0.7)
    return mots, [gr[k] for k in ordre]


# ------------------------------------------------------------------ rendu texte
def sprite_mot(mot, cle):
    f = police("ExtraBold" if cle else "Bold", 92 if cle else 82)
    couleur = MOT_CLE if cle else CREME
    l, t, r, b = f.getbbox(mot, stroke_width=7)
    pad = 30
    im = Image.new("RGBA", (r - l + 2 * pad, b - t + 2 * pad), (0, 0, 0, 0))
    ombre = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(ombre).text((pad - l + 4, pad - t + 8), mot, font=f, fill=CONTOUR + (150,),
                               stroke_width=7, stroke_fill=CONTOUR + (150,))
    im = Image.alpha_composite(im, ombre.filter(ImageFilter.GaussianBlur(9)))
    ImageDraw.Draw(im).text((pad - l, pad - t), mot, font=f, fill=couleur + (255,),
                            stroke_width=6, stroke_fill=CONTOUR + (255,))
    return np.array(im).astype(np.float32) / 255, f.getlength(mot), pad, -t


def sprite_titre(fin_txt, gras_txt, haut_tete):
    """Calque plein cadre (RGBA) du grand titre, aligné à gauche comme la référence.

    Le bas de la ligne grasse passe derrière le haut de la tête (≈ 20 % de sa hauteur),
    le reste du mot reste lisible au-dessus."""
    marge = 92
    largeur_max = W - 2 * marge
    t_gras = 260
    while police("Black", t_gras).getlength(gras_txt) > largeur_max and t_gras > 120:
        t_gras -= 6
    fg = police("Black", t_gras)
    ff = police("Light", int(t_gras * 0.5))
    l, t, r, b = fg.getbbox(gras_txt)
    haut_glyphes = b - t
    y_gras = int(haut_tete + 0.20 * haut_glyphes - b)
    y_gras = max(y_gras, 150 + int(t_gras * 0.62) - t)
    y_fin = y_gras + t - int(t_gras * 0.62)
    calque = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    # ligne fine crème
    ImageDraw.Draw(calque).text((marge + 6, y_fin), fin_txt, font=ff, fill=CREME + (235,))
    # ligne grasse : masque de texte rempli d'un dégradé vertical soleil
    masque = Image.new("L", (W, H), 0)
    ImageDraw.Draw(masque).text((marge, y_gras), gras_txt, font=fg, fill=255)
    bb = masque.getbbox()
    grad = np.zeros((H, W, 4), np.uint8)
    y0, y1 = bb[1], bb[3]
    u = np.clip((np.arange(H) - y0) / max(1, y1 - y0), 0, 1)[:, None]
    for c in range(3):
        grad[..., c] = (SOLEIL_HAUT[c] * (1 - u) + SOLEIL_BAS[c] * u).astype(np.uint8)
    grad[..., 3] = (np.array(masque) * 0.94).astype(np.uint8)
    calque = Image.alpha_composite(calque, Image.fromarray(grad, "RGBA"))
    # halo brun chaud très doux pour détacher le titre du décor
    halo = Image.new("RGBA", (W, H), CONTOUR + (0,))
    a = Image.fromarray((np.array(calque)[..., 3] * 0.45).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(16))
    halo.putalpha(a)
    calque = Image.alpha_composite(halo, calque)
    return np.array(calque).astype(np.float32) / 255


def composer(fond, rgba, x, y, alpha=1.0, masque_inverse=None):
    """Colle rgba (float 0-1) sur fond (float 0-255) à (x, y)."""
    h, w = rgba.shape[:2]
    x0, y0 = max(int(x), 0), max(int(y), 0)
    x1, y1 = min(int(x) + w, W), min(int(y) + h, H)
    if x1 <= x0 or y1 <= y0:
        return
    s = rgba[y0 - int(y):y1 - int(y), x0 - int(x):x1 - int(x)]
    a = s[..., 3:4] * alpha
    if masque_inverse is not None:
        a = a * (1 - masque_inverse[y0:y1, x0:x1, None])
    zone = fond[y0:y1, x0:x1]
    zone[:] = zone * (1 - a) + s[..., 2::-1] * 255 * a  # sprites RGB → image BGR


def transformer(rgba, echelle, flou):
    if echelle != 1.0:
        rgba = cv2.resize(rgba, None, fx=echelle, fy=echelle, interpolation=cv2.INTER_LINEAR)
    if flou > 0.4:
        rgba = cv2.GaussianBlur(rgba, (0, 0), flou)
    return rgba


# ------------------------------------------------------------------ détourage
class Detoureur:
    def __init__(self):
        if not os.path.exists(MODELE):
            urllib.request.urlretrieve(URL_MODELE, MODELE)
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision
        self.mp = mp
        self.seg = vision.ImageSegmenter.create_from_options(vision.ImageSegmenterOptions(
            base_options=BaseOptions(model_asset_path=MODELE), output_confidence_masks=True))
        self.prec = None

    def __call__(self, rgb):
        r = self.seg.segment(self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb))
        fond = np.squeeze(r.confidence_masks[0].numpy_view())
        m = cv2.resize(1 - fond, (W, H), interpolation=cv2.INTER_LINEAR)
        m = np.clip((m - 0.35) / 0.4, 0, 1)
        m = cv2.GaussianBlur(m, (0, 0), 2.5)
        if self.prec is not None:
            m = 0.6 * m + 0.4 * self.prec
        self.prec = m
        return m


# ------------------------------------------------------------------ image par image
def dessiner(img, t, mots, groupes, titres, detoureur):
    out = img.astype(np.float32)
    # 1. grands titres derrière la personne
    actifs = [ti for ti in titres if ti["debut"] - 0.01 <= t < ti["fin"]]
    masque = detoureur(np.ascontiguousarray(img[..., ::-1])) if actifs else None
    if not actifs and detoureur is not None:
        detoureur.prec = None
    for ti in actifs:
        u_in = (t - ti["debut"]) / 0.35
        u_out = (ti["fin"] - t) / 0.3
        a = ease_out(min(u_in, u_out, 1))
        ech = 1.10 - 0.10 * ease_out(u_in) + 0.025 * (t - ti["debut"]) / max(ti["fin"] - ti["debut"], 1)
        flou = 14 * (1 - ease_out(min(u_in, u_out, 1)))
        dy = -24 * (1 - ease_out(min(u_out, 1)))
        spr = transformer(ti["sprite"], ech, flou)
        h, w = spr.shape[:2]
        composer(out, spr, (W - w) / 2, (H - h) / 2 + dy, a, masque)
    # 2. sous-titres mot à mot
    for g in groupes:
        if not (g["debut"] - 0.01 <= t < g["sortie"]):
            continue
        sortie = ease_out((g["sortie"] - t) / 0.12)
        visibles = [m for m in g["mots"] if t >= m["t"] - 0.03]
        if not visibles:
            continue
        espace = 24
        larg = sum(m["larg"] for m in g["mots"]) + espace * (len(g["mots"]) - 1)
        x = (W - larg) / 2
        y_base = 1265
        for m in g["mots"]:
            if t >= m["t"] - 0.03:
                u = (t - m["t"] + 0.03) / 0.18
                p = ease_out(u)
                ech = (0.7 + 0.3 * ease_back(u)) if m["cle"] else 1.0
                spr = transformer(m["sprite"], ech, 7 * (1 - p))
                h, w = spr.shape[:2]
                cx = x + m["larg"] / 2
                composer(out, spr, cx - w / 2, y_base - h / 2 + 16 * (1 - p), p * sortie)
            x += m["larg"] + espace
    return np.clip(out, 0, 255).astype(np.uint8)


def preparer(entree):
    wav = "/tmp/_sous_titres_audio.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", entree, "-ac", "1", "-ar", "16000", wav],
                   check=True)
    mots, groupes = aligner(voix_active(wav))
    for m in mots:
        m["sprite"], m["larg"], _, _ = sprite_mot(m["mot"], m["cle"])
    titres = []
    det = Detoureur()
    for ancre, n, fin, fin_txt, gras_txt in TITRES:
        occ = [m for m in mots if m["mot"] == ancre]
        debut = occ[n - 1]["t"]
        haut = min(haut_tete(entree, t, det) for t in np.linspace(debut + 0.05, fin - 0.05, 4))
        titres.append({"debut": debut, "fin": fin, "sprite": sprite_titre(fin_txt, gras_txt, haut),
                       "texte": f"{fin_txt} / {gras_txt}"})
    return mots, groupes, titres


def haut_tete(entree, t, det):
    """Ordonnée du haut de la tête (pixels) à l'instant t, d'après le détourage."""
    b = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", entree,
                                 "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    det.prec = None
    m = det(np.frombuffer(b, np.uint8).reshape(H, W, 3).copy())
    lignes = np.where((m[:, W // 4:3 * W // 4] > 0.5).sum(axis=1) > 40)[0]
    return int(lignes[0]) if len(lignes) else 420


def main():
    entree, sortie = sys.argv[1], sys.argv[2]
    preview = "--preview" in sys.argv
    mots, groupes, titres = preparer(entree)
    for g in groupes:
        print(f"{g['debut']:6.2f}–{g['sortie']:6.2f}  " + " ".join(
            ("[" + m["mot"] + "]") if m["cle"] else m["mot"] for m in g["mots"]))
    for ti in titres:
        print(f"TITRE {ti['debut']:6.2f}–{ti['fin']:6.2f}  {ti['texte']}")
    detoureur = Detoureur()
    if "--image" in sys.argv:
        for tt in sys.argv[sys.argv.index("--image") + 1].split(","):
            t = float(tt)
            b = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", str(t), "-i", entree,
                                         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"])
            img = np.frombuffer(b, np.uint8).reshape(H, W, 3)
            cv2.imwrite(f"{sortie}_{t:05.2f}.png", dessiner(img, t, mots, groupes, titres, detoureur))
        os._exit(0)
    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", entree, "-f", "rawvideo",
                            "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)
    enc = subprocess.Popen([
        "ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
        "-r", str(FPS), "-i", "-", "-i", entree, "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "ultrafast" if preview else "slow",
        "-crf", "28" if preview else "19", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-c:a", "copy", sortie], stdin=subprocess.PIPE)
    i = 0
    while True:
        b = dec.stdout.read(W * H * 3)
        if len(b) < W * H * 3:
            break
        img = np.frombuffer(b, np.uint8).reshape(H, W, 3)
        enc.stdin.write(dessiner(img, i / FPS, mots, groupes, titres, detoureur).tobytes())
        i += 1
        if i % 150 == 0:
            print(f"  {i} images", flush=True)
    enc.stdin.close()
    enc.wait()
    print("terminé :", sortie)
    os._exit(0)  # évite le message d'erreur de mediapipe à la fermeture


if __name__ == "__main__":
    main()
