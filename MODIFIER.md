# Modifier la vidéo vous-même

Tous les textes de la vidéo du parcours sont dans **`config_parcours.json`**.
Quand vous enregistrez ce fichier sur GitHub, la vidéo est **régénérée automatiquement**
(environ 6 à 8 minutes).

## 1. Ouvrir le fichier

1. Allez sur https://github.com/ocazomq-source/publicite-motion-design
2. En haut à gauche, dans le menu des branches, choisissez **`claude/motion-design-video-edms2m`**.
3. Cliquez sur **`config_parcours.json`**, puis sur l'icône **crayon** ✏️ (« Edit this file »).

## 2. Modifier les textes

Changez uniquement le texte **entre guillemets**. Exemple :

```json
"titre": "L'expert transmet {le procès-verbal} sans efforts.",
```

Règles à respecter :

- **`{ }`** : les mots entre accolades s'affichent **en bleu**. Les accolades n'apparaissent pas à l'écran.
- Gardez les **guillemets** `"` au début et à la fin de chaque texte, et les **virgules** entre les éléments.
- Pour mettre un guillemet dans un texte, utilisez plutôt « » ou l'apostrophe '.
- `"lignes": ["ligne 1", "ligne 2"]` : chaque élément est une ligne à l'écran.
- Un titre d'étape peut aussi être écrit en liste pour choisir les retours à la ligne :
  `"titre": ["L'expert transmet le", "{procès-verbal},", "sans efforts."]`.
  Les accolades `{ }` doivent alors s'ouvrir et se fermer sur la même ligne.
- Ne revenez **jamais** à la ligne à l'intérieur des guillemets : le fichier ne serait plus valide.
- Les couleurs se changent dans la rubrique `"couleurs"` (codes du type `#005EE9`).

Repères dans le fichier :

| Rubrique | Ce que c'est à l'écran |
|---|---|
| `accroche` | 1re phrase de la vidéo |
| `probleme` | « Une plateforme, un parcours plus fluide… » + schéma |
| `logo` | phrase sous les logos AUTOPIEC × Groupama |
| `titre` | titre du parcours et sous-titre |
| `acteurs` | noms des acteurs (badges et frise) |
| `etapes` | les 7 étapes, dans l'ordre : `titre`, `texte`, puis le contenu de la `carte` (interface) |
| `suivi` | tableau de bord « Tout est tracé » |
| `benefices` | les 3 bénéfices pour Groupama |
| `pilote` | phrase sur le pilote, offre, nombre de mois |
| `fin` | phrase finale et contact |

## 3. Enregistrer

Cliquez sur **« Commit changes… »** (bouton vert), puis encore sur **« Commit changes »**.

## 4. Récupérer la vidéo

- Onglet **Actions** : une tâche « Générer la vidéo » démarre (rond jaune). Elle est terminée quand le rond est vert ✅.
- La nouvelle vidéo remplace alors **`output/parcours-autopiec.mp4`** dans le dépôt (cliquez dessus, puis sur le bouton de téléchargement).
- Elle est aussi téléchargeable en bas de la page de la tâche, rubrique **Artifacts**.

Si le rond devient **rouge ❌**, il y a le plus souvent une erreur de syntaxe dans le fichier
(guillemet ou virgule oubliés) : cliquez sur la tâche pour lire le message, corrigez, puis
enregistrez à nouveau.

## Modifications plus importantes

Changer la durée des scènes, l'animation ou la mise en page se fait dans `parcours.py`
(section `TIMELINE` en bas du fichier pour les durées). Pour ce type de changement, vous
pouvez aussi simplement le demander à Claude.
