#!/usr/bin/env python3
"""Extrait logos et visuels de la présentation PDF « GROUPAMA x AUTOPIEC » vers assets/.

Usage : python3 outils/extraire_visuels.py chemin/vers/GROUPAMA_x_AUTOPIEC_PRE.pdf
(nécessite pdfimages, fourni par poppler-utils)
"""
import os
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")


def key_on_blue(im):
    """Détoure un logo clair posé sur un fond bleu dégradé (fond estimé colonne par colonne)."""
    a = np.asarray(im.convert("RGB")).astype(np.float32)
    bg = np.median(np.concatenate([a[:4], a[-4:]], axis=0), axis=0)[None, :, :]
    diff = np.clip(a - bg, 0, None) / np.clip(255 - bg, 1, None)
    alpha = np.clip((diff.max(axis=2, keepdims=True) - 0.14) / 0.8, 0, 1)
    rgb = np.clip(bg + (a - bg) / np.clip(alpha, 1e-3, None), 0, 255)
    out = np.concatenate([rgb, alpha * 255], axis=2).astype(np.uint8)
    img = Image.fromarray(out, "RGBA")
    return img.crop(img.getbbox())


def main(pdf):
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdfimages", "-png", "-f", "1", "-l", "1", pdf, os.path.join(tmp, "p1")], check=True)
        subprocess.run(["pdfimages", "-png", "-f", "5", "-l", "5", pdf, os.path.join(tmp, "p5")], check=True)
        cover = Image.open(os.path.join(tmp, "p1-000.png")).convert("RGB")
        kpi = Image.open(os.path.join(tmp, "p5-000.png")).convert("RGB")

    os.makedirs(ASSETS, exist_ok=True)
    key_on_blue(cover.crop((40, 88, 575, 168))).save(os.path.join(ASSETS, "logo_autopiec.png"))
    key_on_blue(cover.crop((702, 76, 1058, 192))).save(os.path.join(ASSETS, "logo_groupama.png"))

    # Visuel de couverture (paysage + véhicule), détouré en parallélogramme le long de la bande citron
    photo = cover.crop((900, 0, 1672, 941))
    mask = Image.new("L", photo.size, 0)
    top, bottom = 75, 830
    x_at = lambda y: 335 - 0.55 * (y - 300) - 28
    ImageDraw.Draw(mask).polygon([(x_at(top), top), (photo.width, top),
                                  (photo.width, bottom), (x_at(bottom), bottom)], fill=255)
    photo.putalpha(mask.filter(ImageFilter.GaussianBlur(1)))
    photo.crop((0, top, photo.width, bottom)).save(os.path.join(ASSETS, "visuel_couverture.png"))

    # Capture du tableau de bord (page KPI)
    kpi.crop((975, 240, 1600, 760)).save(os.path.join(ASSETS, "tableau_de_bord.png"))
    print("Visuels écrits dans", ASSETS)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
