# Sous-titrage dynamique « chaud et jovial »

Résultat : [`output/video-sous-titree.mp4`](output/video-sous-titree.mp4). C'est la vidéo
avec les effets dynamiques, à laquelle s'ajoutent les sous-titres (1080×1920, 36,73 s,
1102 images, son d'origine).

Rendu : `python3 outils/sous_titres.py output/video-effets-dynamiques.mp4 output/video-sous-titree.mp4`
(≈ 3,5 min). Pour un aperçu image par image : `--image 1.6,12.4,26.6`.

## Style (inspiré de la référence « Martinique / trophy »)

- **Grands titres derrière la personne** : une ligne fine crème (Poppins Light) au-dessus
  d'une ligne extra-grasse (Poppins Black) remplie d'un dégradé soleil, du jaune mangue
  `#FFD666` à l'orange corail `#FF703B`. La personne est détourée image par image (MediaPipe) :
  le bas des lettres passe derrière les cheveux. La position est calculée automatiquement à
  partir du haut de la tête. Entrée : apparition depuis le flou avec une légère réduction
  d'échelle. Sortie : fondu vers le haut.
- **Sous-titres mot à mot** : Poppins Bold crème avec un contour brun chaud `#4A210C` (pas de
  noir, pour garder la chaleur). Chaque mot apparaît depuis le flou en montant légèrement. Les
  mots clés sont en jaune-orangé `#FFC43D`, plus gras, avec un petit rebond. Les sous-titres
  sont placés à 66 % de la hauteur, au-dessus de la zone de légende Instagram et TikTok.

| Titre | Temps |
|---|---|
| votre / **SCI** | 0,16–3,80 |
| payé / **2 000 €** | 11,38–13,15 |
| en / **indivision** (punchline, avec l'hyperzoom) | 16,71–17,70 |
| règles de / **décision** | 23,45–24,40 |
| la clé : / **majorité** | 24,45–28,75 |
| unanimité ou / **majorité ?** (question finale) | 33,85–36,73 |

## Texte (transcription automatique corrigée)

Vous avez créé une SCI pour protéger votre patrimoine, mais elle ne vous sert peut-être à rien.
Je vous explique. Pour vendre le bien, faire des travaux, changer de gérant ou même modifier
les statuts, il faut donc que tout le monde soit d'accord. Vous avez payé 2 000 euros pour
créer votre SCI, éviter de vous retrouver en indivision sur le bien. Tout ça pour vous
retrouver en indivision sur les parts ! Alors la SCI, c'est vrai, est un excellent outil pour
préparer la transmission, mais il faut faire très attention aux règles de décision. La clé,
c'est d'avoir des décisions qui se prennent à la majorité plutôt qu'à l'unanimité, et
idéalement d'y penser dès la rédaction des statuts. Et vous, dans votre SCI, les décisions se
prennent à l'unanimité ou à la majorité ?

**À vérifier** (passages incertains de la reconnaissance vocale) : « éviter de vous
retrouver » (peut-être « pour éviter »), « la SCI, c'est vrai, » et « d'y penser dès la
rédaction ». Pour corriger, modifiez `PAROLES` dans le script et relancez.

Synchronisation : les mots sont calés sur les passages où la voix est active, au prorata des
syllabes (précision d'environ ±0,1 s). Les titres et les mots clés se règlent dans `TITRES`
et avec les `*…*` de `PAROLES`.
