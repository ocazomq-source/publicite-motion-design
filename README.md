# Vidéo motion design — AUTOPIEC × Groupama Antilles-Guyane

Vidéo de 43 s en 1920×1080 (30 i/s), avec une musique synthétisée. Elle reprend la présentation
« GROUPAMA x AUTOPIEC » : logos, titre, qui sommes-nous, processus en 6 étapes, déploiement,
pilotage & KPI, phase pilote, puis la conclusion.

**Vidéo prête :** [`output/autopiec-groupama.mp4`](output/autopiec-groupama.mp4)

| Temps | Scène |
|---|---|
| 0–4 s | Logos AUTOPIEC × Groupama, « Proposition de partenariat » |
| 4–9,5 s | Titre + visuel Martinique / véhicule |
| 9,5–15 s | Qui sommes-nous : 3 piliers + notre rôle |
| 15–21,5 s | Processus : du PV à la livraison (6 étapes) |
| 21,5–26,5 s | Déploiement : Référencer → Activer → Accompagner |
| 26,5–32 s | Pilotage & KPI + tableau de bord |
| 32–37,5 s | Phase pilote de 6 mois, critères, étapes clés |
| 37,5–43 s | Conclusion + logos |

## Modifier la vidéo

Tous les textes et les couleurs sont dans `config.json` (`{texte}` = mot mis en couleur d'accent).

```bash
python3 render.py --preview          # images clés dans output/apercu/ (quelques secondes)
python3 render.py --preview 12 18.5  # images à des instants précis
python3 render.py                    # vidéo complète (~2 min sur 4 cœurs)
```

Prérequis : Python 3 avec Pillow et numpy, ffmpeg, et la police Inter (sinon DejaVu est utilisée).
Pour changer la durée des scènes, modifiez `TIMELINE` et `DURATION` dans `render.py`.

Les logos et visuels de `assets/` sont extraits du PDF avec
`python3 outils/extraire_visuels.py GROUPAMA_x_AUTOPIEC_PRE.pdf`.
