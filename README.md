# Vidéos motion design — AUTOPIEC × Groupama Antilles-Guyane

## 1. Le parcours : du procès-verbal à la pièce livrée — [`output/parcours-autopiec.mp4`](output/parcours-autopiec.mp4)

71 s, 1920×1080, 30 i/s, musique et bruitages d'interface. Style démo produit : interfaces
épurées sur fond blanc, cartes qui se redressent en perspective, curseur qui clique, texte qui
sort du flou mot à mot, caméra en léger travelling, transitions travelling / zoom / flou.

| Temps | Scène |
|---|---|
| 0–7,5 s | Accroche : « Chaque sinistre carrosserie mérite la bonne pièce. » puis le problème |
| 7,5–14,5 s | Logo AUTOPIEC, titre du parcours et aperçu des 7 acteurs |
| 14,5–53 s | Les 7 étapes (une interface animée par étape) |
| 53–62 s | « Tout est tracé, en temps réel. » + bénéfices pour Groupama |
| 62–71,5 s | Pilote de 6 mois, puis logo et contact |

Les 7 étapes : l'expert transmet le PV → Autopiec centralise et source → le réparateur valide la
conformité → l'assurance est notifiée → l'expert est notifié → l'assurance passe le bon de
commande → Autopiec livre la pièce chez le réparateur.

Version mobile (1080×1920, 9:16 — Instagram, TikTok, WhatsApp, Stories) :
[`output/parcours-autopiec-mobile.mp4`](output/parcours-autopiec-mobile.mp4), mêmes textes et animations.

```bash
python3 parcours.py                  # vidéo complète (~5 min sur 4 cœurs)
python3 parcours.py --format vertical  # version mobile
python3 parcours.py --preview        # une image par scène dans output/apercu-parcours/
python3 parcours.py --preview 22.5   # image à un instant précis
```

Pour modifier vous-même les textes sans rien installer : voir **[MODIFIER.md](MODIFIER.md)**.

Tous les textes (étapes, contenus des interfaces, contact, offre) et les couleurs sont dans
`config_parcours.json` (`{texte}` = mots en bleu). Les durées des scènes et les bruitages se
règlent dans `TIMELINE` en bas de `parcours.py`.

## 2. Présentation du partenariat — [`output/autopiec-groupama.mp4`](output/autopiec-groupama.mp4)

43 s, reprend la présentation PDF (titre, qui sommes-nous, processus, déploiement, KPI, pilote).
Textes dans `config_presentation.json`, rendu avec `python3 presentation.py`.

## 3. O'MORGAN : « 5 erreurs qui coûtent cher » — [`output/5-erreurs-bourse.mp4`](output/5-erreurs-bourse.mp4)

≈ 100 s, 1920×1080, adaptée du carrousel PDF « 5 erreurs qui coûtent cher à un investisseur
débutant ». Version mobile 9:16 : [`output/5-erreurs-bourse-mobile.mp4`](output/5-erreurs-bourse-mobile.mp4).

| Temps | Scène |
|---|---|
| 0–6,5 s | Couverture : « 5 erreurs qui coûtent cher quand on débute en bourse » |
| 6,5–76 s | Les 5 erreurs : texte + graphique animé (Oracle, Atos, S&P 500 Covid, frais, Nasdaq), puis « Le bon réflexe » |
| 76–84,5 s | Récapitulatif (les 5 erreurs barrées) et « Laquelle avez-vous déjà faite ? » |
| 84,5–100,5 s | Conclusion, signature O'MORGAN et mentions légales |

```bash
python3 erreurs_bourse.py                    # vidéo horizontale
python3 erreurs_bourse.py --format vertical  # version mobile
python3 erreurs_bourse.py --preview          # images clés dans output/apercu-erreurs/
```

Tous les textes, chiffres affichés et couleurs sont dans `config_erreurs.json` (`{texte}` = mots en
rouge, ou en or dans la conclusion). Les courbes sont des illustrations schématiques passant par
les points chiffrés du document (indiqué sur chaque graphique) ; la courbe des frais est calculée.
Les sources du PDF (« à ne pas publier ») ne figurent pas dans la vidéo. Une modification de
`config_erreurs.json` sur GitHub régénère automatiquement les deux vidéos.

## Prérequis et visuels

Python 3 avec Pillow et numpy, ffmpeg, police Inter (sinon DejaVu est utilisée).

`assets/` contient les logos et visuels extraits du PDF (`outils/extraire_visuels.py`) et la photo
du pare-chocs reprise de la première version de la vidéo.
