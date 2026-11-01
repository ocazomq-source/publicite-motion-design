# Habillage dynamique — vidéo face caméra (WhatsApp, 1er nov. 2026)

Résultat : [`output/video-effets-dynamiques.mp4`](output/video-effets-dynamiques.mp4)
— 1080×1920, 30 i/s, 36,73 s, 1102 images (identique à la source image pour image).

Montage d'origine conservé : mêmes plans, mêmes coupes, même durée, voix intacte (aucune
image ajoutée ni retirée, donc synchro labiale parfaite). Seule une caméra virtuelle est
ajoutée par-dessus, plus trois whooshes discrets sous les transitions (−20 dB environ ;
la sonie globale reste à −13,3 LUFS contre −13,2 pour la source).

Rendu : `python3 outils/effets_dynamiques.py SOURCE.mp4 SORTIE.mp4` (≈ 2,5 min).
Tous les timecodes ci-dessous sont dans `SCALE`, `PAN_X`, `SHAKES`, `BLUR_WINDOWS` et
`WHOOSHES` en haut du script : modifiez une valeur, relancez, c'est tout.

## Structure analysée

| Plan | Temps | Cadrage d'origine | Contenu |
|---|---|---|---|
| P1 | 0,00–3,83 | large | accroche |
| P2 | 3,83–4,63 | serré | phrase courte |
| P3 | 4,63–10,57 | large | énumération sur les doigts, puis bras ouverts (≈ 9 s) |
| P4 | 10,57–17,80 | serré | explication, **silence 15,1–15,7**, reprise appuyée |
| P5 | 17,80–24,03 | large | nouveau bloc (pause 17,5–18,0), respiration à 21,5–21,7 |
| P6 | 24,03–32,17 | large (jump cut) | développement, pauses à 27,5 et 28,9, sourire à 29,1 |
| P7 | 32,17–36,73 | large | conclusion (pause 32,0–32,4 avant) |

## Effets appliqués

| Timecode | Effet | Durée | Intensité | Justification |
|---|---|---|---|---|
| 0,00 → 1,90 | Travelling avant | 1,9 s | léger (×1,00→1,06) | Installe la proximité dès l'accroche |
| 2,13 | Punch-in | 0,17 s | moyen (→×1,11) | Pic d'intensité de la voix dans l'accroche |
| 3,83 | Coupe franche conservée | — | — | Le plan serré d'origine suffit |
| 3,83 → 4,63 | Plan fixe (×1,00→1,02) | 0,8 s | très léger | Temps calme après l'accroche |
| 4,63 → 8,90 | Travelling latéral + poussée | 4,3 s | léger (×1,05, ±2 %) | Effet multicaméra pendant l'énumération, mains toujours visibles |
| 8,90 → 9,20 | Dézoom + recentrage | 0,3 s | moyen | Accompagne l'ouverture des bras, relâche la tension |
| 10,43 → 10,80 | **Transition zoom avant** avec flou de mouvement | 0,37 s | fort (×1,5 / ×1,45→1,0) | Passage vers le plan serré, raccord dans la direction du mouvement |
| 10,80 → 15,10 | Poussée lente | 4,3 s | léger (→×1,07) | Accompagne l'explication |
| 15,10 → 15,73 | Lente avancée dans le silence | 0,6 s | très léger | Fait monter la tension |
| 15,73 → 15,87 | **Hyperzoom d'impact** + micro-secousse | 0,14 s (+0,3 s) | fort (×1,08→1,25, secousse 5 px amortie) | Reprise de parole appuyée après le seul vrai silence de la vidéo |
| 17,66 → 17,97 | **Whip pan** (sortie gauche / entrée droite) | 0,3 s | fort, 4 images floues | Changement de bloc, placé exactement dans la pause : aucun mot masqué |
| 17,80 → 21,60 | Angle serré « caméra 2 » | 3,8 s | ×1,12→1,15 | Varie le cadrage du plan large d'origine |
| 21,75 | Coupe virtuelle vers le plan large | instantané | ×1,15→1,00 | Changement de phrase après la respiration 21,5–21,7 |
| 21,75 → 24,03 | Poussée lente | 2,3 s | léger | Garde le mouvement |
| 24,03 | Jump cut masqué par un changement d'échelle | instantané | ×1,14 | Rend le jump cut volontaire, coupe franche conservée |
| 24,03 → 27,40 | Dézoom d'aération + dérive latérale | 3,4 s | léger | Moment plus calme |
| 27,40 → 28,70 | Rapprochement | 1,3 s | léger (→×1,10) | Prépare le temps fort suivant |
| 29,06 → 29,20 | Punch-in | 0,14 s | moyen (→×1,20) | Sourire / chute après la pause 28,8–29,0 |
| 32,00 → 32,45 | **Transition zoom arrière** avec flou | 0,45 s | fort (×0,90 / ×1,30→1,0) | Ouvre la conclusion, placée dans la pause 32,0–32,4 |
| 32,45 → 36,30 | Travelling avant final | 3,9 s | léger (→×1,08) | Rapproche pour la conclusion |
| 10,57 / 17,80 / 32,17 | Whooshes | 0,4–0,5 s | discrets | Donnent du poids aux trois transitions |

Toutes les coupes non citées restent des coupes franches.

## Choix et limites

- **Zooms ancrés sur le visage** : le visage est détecté image par image (OpenCV), lissé
  sur environ 1 s dans chaque plan. Le sujet garde ainsi sa place à l'écran quand on zoome.
- **Résolution** : la source fait 576×1024. Le zoom maximal hors transition est plafonné à
  ×1,26 pour éviter un rendu flou. Une accentuation légère, proportionnelle au zoom, compense
  l'agrandissement. Le passage en 1080×1920 ne crée pas de détail, mais évite une double
  remise à l'échelle par les plateformes.
- **Pas d'arrêt sur image ni de changement de vitesse**, volontairement : sans couper de son,
  ils décaleraient la voix par rapport aux lèvres.
- **Pas de transcription** : les modèles de reconnaissance vocale n'étaient pas
  téléchargeables dans cet environnement. Le placement repose donc sur l'enveloppe de la voix
  (pauses, attaques, pics), les gestes, les expressions et les coupes, pas sur le sens des
  mots. Si vous fournissez le texte avec ses timecodes, les punch-ins peuvent être recalés au
  mot près en modifiant les clés de `SCALE`.
