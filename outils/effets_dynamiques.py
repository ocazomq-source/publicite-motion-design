#!/usr/bin/env python3
"""Habillage dynamique d'une vidéo déjà montée (face caméra, 9:16).

Ne modifie ni l'ordre des plans, ni les coupes, ni la durée, ni la voix :
chaque image source donne exactement une image de sortie (synchro labiale intacte).
Ajoute une caméra virtuelle par-dessus : zooms ancrés sur le visage, punch-in,
hyperzoom avec micro-secousse, transitions zoom / whip pan avec vrai flou de
mouvement, recadrages « multicaméra », puis de légers whooshes sous les transitions.

    python3 outils/effets_dynamiques.py SOURCE.mp4 SORTIE.mp4 [--preview]

Dépendances : numpy, opencv-python-headless (<5, pour les cascades de Haar), ffmpeg.
Le plan d'effets (timecodes) est dans PLAN ci-dessous ; les coupes sont détectées
automatiquement et doivent correspondre à CUTS (vérification au lancement).
"""
import json
import subprocess
import sys

import cv2
import numpy as np

FPS = 30
OUT_W, OUT_H = 1080, 1920

# Coupes existantes (secondes) — détectées dans la vidéo source, conservées telles quelles.
def F(n):
    """Numéro d'image → secondes (clés exactement calées sur les coupes)."""
    return n / FPS


CUT_FRAMES = [115, 139, 317, 534, 721, 965]  # 3,83 · 4,63 · 10,57 · 17,80 · 24,03 · 32,17 s
CUTS = [F(n) for n in CUT_FRAMES]

# --------------------------------------------------------------------------------------
# Courbes d'animation : liste de clés (temps, valeur, easing vers la clé suivante).
# easing : "lin", "io" (ease-in-out), "out" (ease-out cubique), "in" (ease-in cubique),
#          "hold" (palier, saut net à la clé suivante = recadrage sec / coupe virtuelle)
# --------------------------------------------------------------------------------------
SCALE = [
    # P1 0–3,83 accroche : travelling avant léger, puis punch-in sur le pic de voix à 2,15 s
    (0.00, 1.00, "io"), (1.90, 1.06, "hold"), (2.13, 1.06, "out"), (2.30, 1.11, "lin"),
    (3.80, 1.12, "hold"),
    # P2 3,83–4,63 plan serré natif : on laisse respirer
    (F(115), 1.00, "lin"), (4.60, 1.02, "hold"),
    # P3 4,63–10,57 énumération aux doigts : poussée très légère (mains visibles)
    (F(139), 1.00, "io"), (8.90, 1.05, "out"),
    # 8,95 s : ouverture des bras → dézoom de relâchement
    (9.20, 1.00, "hold"), (10.40, 1.00, "in"),
    # transition ZOOM AVANT à travers la coupe 10,57
    (F(316), 1.50, "hold"),
    (F(317), 1.45, "out"), (10.80, 1.00, "io"),
    # P4 : poussée lente pendant l'explication, tension dans le silence 15,1–15,7
    (15.10, 1.07, "lin"), (15.73, 1.08, "out"),
    # 15,73 s : reprise de parole après le silence → HYPERZOOM d'impact
    (15.87, 1.25, "lin"), (F(533), 1.26, "hold"),
    # P5 (après whip pan) : angle serré « caméra 2 »
    (F(534), 1.12, "lin"), (21.60, 1.15, "hold"),
    # 21,75 s : changement de phrase → coupe virtuelle vers un angle large
    (21.75, 1.00, "lin"), (F(720), 1.06, "hold"),
    # P6 24,03 : jump cut masqué par un changement d'échelle, puis dézoom d'aération
    (F(721), 1.14, "io"), (27.40, 1.06, "io"), (28.70, 1.10, "hold"),
    # 29,08 s : sourire / chute → punch-in moyen
    (29.06, 1.10, "out"), (29.20, 1.20, "lin"), (31.95, 1.18, "in"),
    # transition ZOOM ARRIÈRE à travers la coupe 32,17
    (F(964), 0.90, "hold"),
    (F(965), 1.30, "out"), (32.45, 1.00, "io"),
    # P7 conclusion : rapprochement progressif jusqu'à la fin
    (36.30, 1.08, "hold"),
]

# Décalage horizontal de la caméra virtuelle, en fraction de la largeur visible.
PAN_X = [
    (0.0, 0.0, "hold"),
    (F(139), -0.02, "io"), (8.90, 0.02, "out"),     # léger travelling latéral sur P3
    (9.20, 0.0, "hold"),                             # recentrage avec le dézoom
    (F(530), 0.0, "in"), (F(533), 0.38, "hold"),      # WHIP PAN : sortie vers la gauche…
    (F(534), -0.38, "out"), (F(539), 0.0, "hold"),    # …entrée depuis la droite
    (F(721), 0.015, "io"), (27.40, -0.015, "io"),     # dérive douce pendant le dézoom
    (29.00, 0.0, "hold"),
]

# Micro-secousses : (début, durée, amplitude en px source)
SHAKES = [(15.73, 0.30, 5.0)]

# Fenêtres où l'on calcule un flou de mouvement (sous-échantillonnage de la caméra)
# (début, fin, obturateur en images : 0,5 = 180°, > 1 = traînée stylisée)
BLUR_WINDOWS = [(10.43, 10.80, 1.0), (15.72, 15.88, 0.6), (17.66, 17.97, 1.8), (32.00, 32.45, 1.2)]

# Whooshes sous les transitions : (centre en s, durée, gain linéaire, "up"/"down")
WHOOSHES = [(10.567, 0.45, 0.10, "up"), (17.80, 0.40, 0.11, "side"), (32.167, 0.50, 0.09, "down")]


def ease(u, kind):
    if kind == "io":
        return u * u * (3 - 2 * u)
    if kind == "out":
        return 1 - (1 - u) ** 3
    if kind == "in":
        return u ** 3
    if kind == "hold":
        return 0.0
    return u


def track(keys, t):
    t += 1e-6
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, v0, e), (t1, v1, _) in zip(keys, keys[1:]):
        if t0 <= t < t1:
            return v0 + (v1 - v0) * ease((t - t0) / (t1 - t0), e)
    return keys[-1][1]


def detect_faces(src, w, h):
    cc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", src, "-f", "rawvideo",
                          "-pix_fmt", "gray", "-"], stdout=subprocess.PIPE)
    pts, i = {}, 0
    while True:
        b = p.stdout.read(w * h)
        if len(b) < w * h:
            break
        if i % 2 == 0:
            g = np.frombuffer(b, np.uint8).reshape(h, w)
            f = cc.detectMultiScale(g, 1.1, 6, minSize=(80, 80))
            if len(f):
                x, y, fw, fh = max(f, key=lambda r: r[2] * r[3])
                pts[i] = (x + fw / 2, y + fh / 2)
        i += 1
    return pts, i


def anchors(faces, n, w, h):
    """Point d'ancrage du zoom par image : visage lissé (≈1 s) à l'intérieur de chaque plan."""
    cut_frames = [round(c * FPS) for c in CUTS]
    bounds = [0] + cut_frames + [n]
    out = np.zeros((n, 2))
    for a, b in zip(bounds, bounds[1:]):
        idx = [i for i in range(a, b) if i in faces]
        if not idx:
            out[a:b] = (w / 2, h * 0.45)
            continue
        xs = np.interp(range(a, b), idx, [faces[i][0] for i in idx])
        ys = np.interp(range(a, b), idx, [faces[i][1] for i in idx])
        k = np.exp(-0.5 * (np.arange(-45, 46) / 15.0) ** 2)
        for arr, col in ((xs, 0), (ys, 1)):
            pad = np.pad(arr, 45, mode="edge")
            out[a:b, col] = np.convolve(pad, k / k.sum(), mode="valid")
    return out


def shake(t):
    dx = dy = 0.0
    for t0, d, amp in SHAKES:
        if t0 <= t < t0 + d:
            u = (t - t0) / d
            a = amp * (1 - u) ** 2
            dx += a * np.sin(2 * np.pi * 23 * (t - t0))
            dy += a * 0.7 * np.cos(2 * np.pi * 17 * (t - t0) + 1.3)
    return dx, dy


def matrix(t, anchor, w, h, clamp=True):
    s = track(SCALE, t)
    ax, ay = anchor
    cw, ch = w / s, h / s
    # zoom autour du visage : le visage garde sa place à l'écran
    cx = ax + (w / 2 - ax) / s
    cy = ay + (h / 2 - ay) / s
    cx += track(PAN_X, t) * cw
    sx, sy = shake(t)
    cx, cy = cx + sx, cy + sy
    if clamp and s >= 1.0:
        cx = min(max(cx, cw / 2), w - cw / 2)
        cy = min(max(cy, ch / 2), h - ch / 2)
    k = OUT_W / cw
    return np.array([[k, 0, OUT_W / 2 - k * cx], [0, k, OUT_H / 2 - k * cy]], np.float32), s


def in_blur(t):
    for a, b, sh in BLUR_WINDOWS:
        if a <= t <= b:
            return sh
    return 0.0


def frame_shot(i):
    return sum(i >= round(c * FPS) for c in CUTS)


def render_frame(img, i, anc, w, h):
    t = i / FPS
    shot = frame_shot(i)
    shutter = in_blur(t)
    if shutter:
        # flou de mouvement : 17 sous-échantillons de la caméra, sans franchir la coupe
        acc = np.zeros((OUT_H, OUT_W, 3), np.float32)
        bounds = [0.0] + CUTS + [1e9]
        lo, hi = bounds[shot], bounds[shot + 1] - 1e-4
        ns = 17
        subs = [min(max(t + (k / (ns - 1) - 0.5) * (shutter / FPS), lo), hi) for k in range(ns)]
        for st in subs:
            free = abs(track(PAN_X, st)) > 0.05 or track(SCALE, st) < 1.0
            m, _ = matrix(st, anc, w, h, clamp=not free)
            acc += cv2.warpAffine(img, m, (OUT_W, OUT_H), flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_REFLECT)
        out = acc / len(subs)
        s = track(SCALE, t)
        pan = abs(track(PAN_X, t))
        if pan > 0.08:
            # traînée horizontale du whip pan (masque aussi les bords en miroir)
            L = int(40 + 360 * min(pan / 0.38, 1.0)) | 1
            out = cv2.blur(out, (L, 1), borderType=cv2.BORDER_REFLECT)
    else:
        m, s = matrix(t, anc, w, h)
        out = cv2.warpAffine(img, m, (OUT_W, OUT_H), flags=cv2.INTER_LANCZOS4,
                             borderMode=cv2.BORDER_REFLECT).astype(np.float32)
    # légère accentuation proportionnelle au grossissement (compense l'agrandissement)
    amt = 0.25 + 0.6 * max(0.0, s - 1.0)
    blur = cv2.GaussianBlur(out, (0, 0), 1.6)
    out = cv2.addWeighted(out, 1 + amt, blur, -amt, 0)
    return np.clip(out, 0, 255).astype(np.uint8)


def whoosh_track(duration, sr=48000):
    rng = np.random.default_rng(7)
    out = np.zeros(int(duration * sr) + sr)
    for c, d, g, kind in WHOOSHES:
        n = int(d * sr)
        x = rng.standard_normal(n)
        u = np.linspace(0, 1, n)
        env = np.sin(np.pi * u) ** 2 * (u ** 0.6)  # montée, pic juste avant la coupe
        # filtre passe-bande glissant (balayage de fréquence) par filtrage à un pôle variable
        f0, f1 = {"up": (400, 3000), "down": (3000, 350), "side": (900, 2500)}[kind]
        fc = f0 * (f1 / f0) ** u
        a = np.exp(-2 * np.pi * fc / sr)
        y = np.zeros(n)
        lp = 0.0
        for j in range(n):
            lp = a[j] * lp + (1 - a[j]) * x[j]
            y[j] = lp
        y = y - np.convolve(y, np.ones(64) / 64, mode="same")  # retire le grave
        y = y / (np.abs(y).max() + 1e-9) * env * g
        start = int((c - d * 0.7) * sr)
        out[start:start + n] += y
    return out, sr


def charger_plan():
    """--plan FICHIER.py : remplace les réglages en MAJUSCULES par ceux d'une autre vidéo."""
    if "--plan" not in sys.argv:
        return
    chemin = sys.argv[sys.argv.index("--plan") + 1]
    ns = {"F": F, "FPS": FPS}
    exec(open(chemin, encoding="utf-8").read(), ns)
    g = globals()
    for k, v in ns.items():
        if k.isupper() and k in g:
            g[k] = v
    if "CUT_FRAMES" in ns and "CUTS" in g:
        g["CUTS"] = [n / FPS for n in ns["CUT_FRAMES"]]
    print("plan :", chemin)


def main():
    charger_plan()
    src, dst = sys.argv[1], sys.argv[2]
    preview = "--preview" in sys.argv
    info = json.loads(subprocess.check_output(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:format=duration", "-of", "json", src]))
    w, h = info["streams"][0]["width"], info["streams"][0]["height"]
    duration = float(info["format"]["duration"])
    print("détection du visage…", flush=True)
    faces, n = detect_faces(src, w, h)
    anc = anchors(faces, n, w, h)

    wav = dst + ".whoosh.wav"
    wh, sr = whoosh_track(duration)
    import wave
    with wave.open(wav, "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(sr)
        f.writeframes((np.clip(wh, -1, 1) * 32767).astype(np.int16).tobytes())

    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", src, "-f", "rawvideo",
                            "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)
    enc = subprocess.Popen([
        "ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{OUT_W}x{OUT_H}", "-r", str(FPS), "-i", "-", "-i", src, "-i", wav,
        "-filter_complex",
        "[1:a]aresample=48000[v];[2:a]aformat=channel_layouts=stereo[w];"
        "[v][w]amix=inputs=2:normalize=0:duration=first[a]",
        "-map", "0:v", "-map", "[a]",
        "-c:v", "libx264", "-preset", "ultrafast" if preview else "slow",
        "-crf", "28" if preview else "20", "-pix_fmt", "yuv420p", "-profile:v", "high",
        "-movflags", "+faststart", "-c:a", "aac", "-b:a", "192k", "-shortest", dst],
        stdin=subprocess.PIPE)
    i = 0
    while True:
        b = dec.stdout.read(w * h * 3)
        if len(b) < w * h * 3:
            break
        img = np.frombuffer(b, np.uint8).reshape(h, w, 3)
        enc.stdin.write(render_frame(img, i, anc[min(i, n - 1)], w, h).tobytes())
        i += 1
        if i % 150 == 0:
            print(f"  {i}/{n}", flush=True)
    enc.stdin.close()
    enc.wait()
    print("terminé :", dst)


if __name__ == "__main__":
    main()
