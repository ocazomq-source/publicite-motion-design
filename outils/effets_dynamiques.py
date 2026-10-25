"""Effets dynamiques sur une vidéo face caméra déjà montée.

Le montage d'origine (plans, coupes, son) est conservé tel quel : seul le cadrage de chaque image
change (zooms, punch-in, travellings virtuels, transitions zoom / whip pan, micro-secousse), avec un
vrai flou de mouvement calculé sur la trajectoire de la caméra virtuelle. La bande son est copiée
sans réencodage.

    python3 outils/effets_dynamiques.py source.mp4 sortie.mp4
    python3 outils/effets_dynamiques.py source.mp4 apercu.mp4 --debug   # timecodes incrustés

Prérequis : numpy, opencv-python-headless, ffmpeg.
"""
import argparse
import json
import math
import subprocess
import sys

import cv2
import numpy as np

FPS = 30
SRC_W, SRC_H = 576, 1024
OUT_W, OUT_H = 1080, 1920
SHUTTER = 0.5            # obturateur à 180° : le flou couvre une demi-image (360° en whip pan)
MAX_SUSTAINED_ZOOM = 1.18  # au-delà, la source 576 px montre ses limites

# Plans de la vidéo d'origine : (première image, point du visage à garder fixe à l'écran, en
# fraction de l'image). Les coupes sont celles du montage existant, détectées par différence
# d'images et toutes situées sur un silence.
SHOTS = [
    (0,    (0.58, 0.40)),
    (263,  (0.50, 0.40)),   # 8,77 s
    (450,  (0.56, 0.40)),   # 15,00 s
    (648,  (0.54, 0.40)),   # 21,60 s
    (908,  (0.50, 0.40)),   # 30,27 s
    (1168, (0.50, 0.40)),   # 38,93 s
    (1314, (0.46, 0.40)),   # 43,80 s
    (1489, (0.46, 0.36)),   # 49,63 s
]
FREEZE_FROM = 53.30      # arrêt sur image final, dans le silence de fin

# Images clés par plan : (temps en s, zoom, décalage x, décalage y, courbe d'arrivée).
# Décalages en fraction de la largeur / hauteur de l'image. La courbe décrit l'interpolation
# depuis la clé précédente.
KEYS = [
    # 1 — accroche : travelling avant lent, zoom léger sur la relance, punch-in sur le temps fort
    [(0.00, 1.00, 0, 0, None), (3.30, 1.05, 0, 0, "linear"), (3.60, 1.09, 0, 0, "out"),
     (6.90, 1.11, 0, 0, "linear"), (7.04, 1.18, 0, 0, "expo"), (8.70, 1.18, 0, 0, "linear")],
    # 2 — plan large (effet 2e caméra), léger déplacement latéral, zoom vers l'avant
    #     puis transition zoom avant pendant le silence de fin
    [(8.77, 1.03, -0.012, 0, None), (14.40, 1.08, 0.012, 0, "inout"),
     (14.78, 1.08, 0.012, 0, "linear"), (15.00, 1.32, 0.012, 0, "in")],
    # 3 — arrivée de la transition qui se pose, puis lente poussée
    [(15.00, 1.32, 0, 0, None), (15.30, 1.12, 0, 0, "expo"), (21.30, 1.16, 0, 0, "linear")],
    # 4 — retour large, travelling avant + glissement latéral, zoom moyen après la pause
    [(21.60, 1.00, 0, 0, None), (26.70, 1.07, 0.010, 0, "inout"),
     (26.83, 1.15, 0.010, 0, "expo"), (29.95, 1.17, 0.010, 0, "linear")],
    # 5 — whip pan d'entrée, cadrage moyen, punch-in après le silence, dézoom de respiration
    [(30.27, 1.08, 0.28, 0, None), (30.43, 1.08, 0, 0, "expo"), (35.20, 1.10, 0, 0, "linear"),
     (35.33, 1.17, 0, 0, "expo"), (37.85, 1.17, 0, 0, "linear"), (38.90, 1.06, 0, 0, "inout")],
    # 6 — coupe franche sur un cadrage serré, recul lent pour aérer
    [(38.93, 1.15, 0, 0.008, None), (43.70, 1.05, 0, -0.004, "inout")],
    # 7 — plan large, hyperzoom d'impact sur la reprise après le silence, puis maintien
    [(43.80, 1.00, 0, 0, None), (46.66, 1.03, 0, 0, "linear"), (46.78, 1.28, 0, 0, "expo"),
     (47.50, 1.17, 0, 0, "inout"), (49.55, 1.18, 0, 0, "linear")],
    # 8 — conclusion : dézoom progressif qui relâche, poussée douce sur l'image finale figée
    [(49.63, 1.12, 0, 0, None), (53.26, 1.00, 0, 0, "inout"), (53.60, 1.04, 0, 0, "out")],
]

# Fin du plan 4 : whip pan de sortie (glissement vers la gauche) pendant le silence 29,94–30,36 s
WHIP_OUT = (30.03, 30.27, -0.28)
# Micro-secousse sur l'impact du plan 7 : (début, durée, amplitude en fraction de largeur)
SHAKES = [(46.70, 0.30, 0.010)]


def ease(kind, u):
    if kind == "linear":
        return u
    if kind == "in":
        return u ** 3
    if kind == "out":
        return 1 - (1 - u) ** 3
    if kind == "inout":
        return 3 * u * u - 2 * u ** 3 if u < 1 else 1.0
    if kind == "expo":   # attaque très rapide, arrivée amortie : le punch-in
        return 1.0 if u >= 1 else 1 - 2 ** (-10 * u)
    raise ValueError(kind)


def shot_index(frame):
    i = 0
    for k, (start, _) in enumerate(SHOTS):
        if frame >= start:
            i = k
    return i


def camera(shot, t):
    """Zoom et décalages de la caméra virtuelle à l'instant t, dans le plan donné."""
    keys = KEYS[shot]
    if t <= keys[0][0]:
        z, dx, dy = keys[0][1:4]
    elif t >= keys[-1][0]:
        z, dx, dy = keys[-1][1:4]
    else:
        for a, b in zip(keys, keys[1:]):
            if a[0] <= t <= b[0]:
                u = ease(b[4], (t - a[0]) / (b[0] - a[0]))
                z, dx, dy = (a[j] + (b[j] - a[j]) * u for j in (1, 2, 3))
                break
    if shot == 3 and t > WHIP_OUT[0]:
        u = min(1.0, (t - WHIP_OUT[0]) / (WHIP_OUT[1] - WHIP_OUT[0]))
        dx += WHIP_OUT[2] * u ** 2
    for start, dur, amp in SHAKES:
        if start <= t <= start + dur:
            s = t - start
            decay = math.exp(-s * 12)
            dx += amp * decay * math.sin(s * 2 * math.pi * 11)
            dy += amp * 0.7 * decay * math.sin(s * 2 * math.pi * 13 + 1.3)
    return z, dx, dy


def matrix(shot, t):
    """Transformation source -> sortie : zoom autour du visage, décalage, cadre borné."""
    z, dx, dy = camera(shot, t)
    ax, ay = SHOTS[shot][1]
    ax, ay = ax * SRC_W, ay * SRC_H
    # centre de la fenêtre visible tel que le point du visage reste fixe à l'écran
    cx = ax + (SRC_W / 2 - ax) / z
    cy = ay + (SRC_H / 2 - ay) / z
    half_w, half_h = SRC_W / (2 * z), SRC_H / (2 * z)
    cx = min(max(cx, half_w), SRC_W - half_w)
    cy = min(max(cy, half_h), SRC_H - half_h)
    # les décalages (whip pan, secousse) peuvent sortir du cadre : le bord est alors prolongé,
    # ce qui se fond dans le flou de mouvement
    cx -= dx * SRC_W / z
    cy -= dy * SRC_H / z
    s = OUT_W / SRC_W * z
    return np.array([[s, 0, OUT_W / 2 - s * cx], [0, s, OUT_H / 2 - s * cy]], np.float32)


def render(src, shot, t, first_t, last_t):
    """Image de sortie, avec flou de mouvement si la caméra bouge vite pendant l'obturation."""
    whip = WHIP_OUT[0] <= t <= KEYS[4][1][0]
    half = (1.0 if whip else SHUTTER) / FPS / 2
    t0, t1 = max(first_t, t - half), min(last_t, t + half)
    m0, m1 = matrix(shot, t0), matrix(shot, t1)
    corners = np.array([[0, 0, 1], [SRC_W, 0, 1], [0, SRC_H, 1], [SRC_W, SRC_H, 1]], np.float32).T
    travel = float(np.abs(m1 @ corners - m0 @ corners).max())
    n = int(min(24, max(1, math.ceil(travel / 2.0))))
    if n == 1:
        return cv2.warpAffine(src, matrix(shot, t), (OUT_W, OUT_H), flags=cv2.INTER_LANCZOS4,
                              borderMode=cv2.BORDER_REPLICATE)
    acc = np.zeros((OUT_H, OUT_W, 3), np.float32)
    for k in range(n):
        tk = t0 + (t1 - t0) * (k + 0.5) / n
        acc += cv2.warpAffine(src, matrix(shot, tk), (OUT_W, OUT_H), flags=cv2.INTER_CUBIC,
                              borderMode=cv2.BORDER_REPLICATE)
    return (acc / n).clip(0, 255).astype(np.uint8)


def effects_list():
    """Contrôles : zoom tenu trop fort, effets trop rapprochés."""
    issues = []
    for shot, keys in enumerate(KEYS):
        for (t, z, *_), (t2, z2, *_) in zip(keys, keys[1:]):
            if min(z, z2) > MAX_SUSTAINED_ZOOM + 1e-6 and t2 - t > 0.3:
                issues.append(f"plan {shot + 1} : zoom {min(z, z2)} tenu de {t:.2f} à {t2:.2f} s")
        for (t, *_), (t2, *_) in zip(keys, keys[1:]):
            if t2 < t:
                issues.append(f"plan {shot + 1} : clés dans le désordre à {t2:.2f} s")
    return issues


def main():
    p = argparse.ArgumentParser()
    p.add_argument("source")
    p.add_argument("sortie")
    p.add_argument("--debug", action="store_true", help="incruste temps, plan et zoom")
    p.add_argument("--debut", type=float, default=0.0)
    p.add_argument("--fin", type=float, default=None)
    a = p.parse_args()

    for issue in effects_list():
        print("ATTENTION", issue, file=sys.stderr)

    info = json.loads(subprocess.check_output(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
         "stream=width,height,nb_read_packets", "-of", "json", a.source]))["streams"][0]
    assert (info["width"], info["height"]) == (SRC_W, SRC_H), info
    total = int(info["nb_read_packets"])
    first = int(a.debut * FPS)
    last = total if a.fin is None else min(total, int(a.fin * FPS))

    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", f"{first / FPS:.4f}", "-i", a.source,
                            "-frames:v", str(last - first), "-f", "rawvideo", "-pix_fmt", "bgr24",
                            "-"], stdout=subprocess.PIPE)
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                            "-s", f"{OUT_W}x{OUT_H}", "-r", str(FPS), "-i", "-",
                            "-ss", f"{first / FPS:.4f}", "-t", f"{(last - first) / FPS:.4f}",
                            "-i", a.source, "-map", "0:v", "-map", "1:a",
                            "-vf", "unsharp=5:5:0.35:5:5:0", "-c:v", "libx264", "-preset", "slow",
                            "-crf", "19", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                            "-c:a", "copy", a.sortie], stdin=subprocess.PIPE)

    frozen = None
    for i in range(first, last):
        buf = dec.stdout.read(SRC_W * SRC_H * 3)
        if len(buf) < SRC_W * SRC_H * 3:
            break
        src = np.frombuffer(buf, np.uint8).reshape(SRC_H, SRC_W, 3)
        t = i / FPS
        if t >= FREEZE_FROM:
            frozen = src if frozen is None else frozen
            src = frozen
        shot = shot_index(i)
        first_t = SHOTS[shot][0] / FPS
        last_t = (SHOTS[shot + 1][0] - 1) / FPS if shot + 1 < len(SHOTS) else total / FPS
        out = render(src, shot, t, first_t, last_t)
        if a.debug:
            z = camera(shot, t)[0]
            cv2.putText(out, f"{t:6.2f}s  plan {shot + 1}  x{z:.2f}", (30, 70),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 255, 255), 3)
        enc.stdin.write(out.tobytes())
        if i % 150 == 0:
            print(f"{t:5.1f} s", file=sys.stderr)
    enc.stdin.close()
    enc.wait()
    dec.wait()


if __name__ == "__main__":
    main()
