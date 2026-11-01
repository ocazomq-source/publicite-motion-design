# Plan de la vidéo « SCI : unanimité ou majorité ? » — version longue, 46,4 s
# (WhatsApp Video 2026-11-01 at 16.40.41). Utilisé par les deux scripts :
#   python3 outils/effets_dynamiques.py SOURCE.mp4 output/sci-unanimite-effets.mp4 --plan outils/plans/sci-unanimite-46s.py
#   python3 outils/sous_titres.py output/sci-unanimite-effets.mp4 output/sci-unanimite-sous-titree.mp4 --plan outils/plans/sci-unanimite-46s.py

# ------------------------------------------------------------------ texte
PAROLES = [
    (0.15, 4.60, "Vous avez créé / une *SCI* / pour protéger / votre *patrimoine,* / mais elle / "
                 "ne vous sert / peut-être *à rien.* / Je vous explique."),
    (4.90, 8.70, "Si vos *statuts* / prévoient que / toutes les décisions / se prennent / "
                 "à l'*unanimité,* / alors"),
    (8.85, 10.25, "un *seul* associé / n'est pas d'accord"),
    (10.50, 11.25, "et il peut / *tout bloquer.*"),
    (12.10, 14.50, "Pour *vendre* le bien, / faire des *travaux,* / changer de *gérant*"),
    (14.70, 15.90, "ou même modifier / les *statuts,*"),
    (16.25, 18.90, "il faut donc que / *tout le monde* / soit *d'accord.* / Vous avez payé / *2 000 euros*"),
    (19.05, 20.00, "pour créer / votre SCI,"),
    (20.25, 22.10, "éviter de vous / retrouver / en *indivision* / sur le bien."),
    (22.75, 24.50, "Tout ça pour / vous retrouver / en *indivision* / sur les *parts !*"),
    (25.10, 28.45, "Alors la SCI, / c'est vrai, / est un *excellent* / *outil* / pour préparer / "
                   "la *transmission,*"),
    (28.75, 33.00, "mais il faut faire / *très attention* / aux règles / de *décision.* / La *clé,* / "
                   "c'est d'avoir / des décisions / qui se prennent"),
    (33.15, 34.35, "à la *majorité*"),
    (34.65, 35.70, "plutôt qu'à / l'*unanimité,*"),
    (36.05, 38.90, "et idéalement / d'y *penser* / dès la rédaction / des *statuts.*"),
    (39.35, 41.65, "Ensuite, / il faut *définir* / les *règles* / de *majorité.*"),
    (42.15, 43.30, "Et *vous,* / dans votre SCI,"),
    (43.45, 45.95, "les décisions / se prennent / à l'*unanimité* / ou à la *majorité ?*"),
]

TITRES = [
    ("Vous", 1, 3.80, "votre", "SCI"),
    ("l'unanimité,", 1, 8.80, "décisions à", "l'unanimité"),
    ("et", 1, 11.55, "il peut tout", "bloquer"),
    ("payé", 1, 20.00, "payé", "2 000 €"),
    ("Tout", 1, 24.60, "en", "indivision"),
    ("clé,", 1, 35.70, "la clé :", "majorité"),
    ("définir", 1, 41.70, "définir les", "règles"),
    ("décisions", 3, 46.37, "unanimité ou", "majorité ?"),
]

# ------------------------------------------------------------------ effets (caméra virtuelle)
# Coupes d'origine (n° d'image) : 3,83 · 4,63 · 11,60 · 17,53 · 24,77 · 31,00 · 39,13 · 41,80 s
CUT_FRAMES = [115, 139, 348, 526, 743, 930, 1174, 1254]

SCALE = [
    # P1 accroche : travelling avant léger + punch-in sur le pic de voix
    (0.00, 1.00, "io"), (1.90, 1.06, "hold"), (2.13, 1.06, "out"), (2.30, 1.11, "lin"),
    (3.80, 1.12, "hold"),
    # P2 « Je vous explique » : plan serré natif, on respire
    (F(115), 1.00, "lin"), (4.60, 1.02, "hold"),
    # P3 la règle de l'unanimité : poussée lente qui fait monter la tension…
    (F(139), 1.00, "io"), (8.70, 1.08, "lin"), (10.85, 1.10, "out"),
    # …« il peut tout BLOQUER » → hyperzoom d'impact + micro-secousse
    (10.98, 1.26, "lin"), (F(347), 1.27, "hold"),
    # P4 (après whip pan) énumération aux doigts : dézoom léger, mains visibles
    (F(348), 1.04, "io"), (16.20, 1.00, "io"), (17.50, 1.08, "hold"),
    # P5 jump cut 17,53 masqué par un cadrage large, punch-in sur « 2 000 euros »
    (F(526), 1.00, "hold"), (18.45, 1.00, "out"), (18.60, 1.12, "lin"),
    (22.10, 1.07, "lin"), (22.75, 1.08, "out"),
    # « Tout ça pour vous retrouver en indivision sur les parts ! » → hyperzoom
    (22.89, 1.25, "lin"), (24.60, 1.26, "in"),
    # transition ZOOM AVANT à travers la coupe 24,77 (dans la pause)
    (F(742), 1.65, "hold"),
    (F(743), 1.45, "out"), (25.00, 1.00, "io"),
    # P6 « excellent outil » : calme, puis coupe virtuelle serrée sur « très attention »
    (28.45, 1.06, "hold"), (28.72, 1.14, "lin"), (F(929), 1.17, "hold"),
    # P7 jump cut 31,00 masqué (plan large), punch-in sur « à la MAJORITÉ »
    (F(930), 1.00, "io"), (33.40, 1.04, "out"), (33.56, 1.15, "lin"),
    (36.05, 1.16, "io"), (38.85, 1.10, "in"),
    # transition ZOOM ARRIÈRE à travers la coupe 39,13 (dans la pause)
    (F(1173), 0.90, "hold"),
    (F(1174), 1.30, "out"), (39.45, 1.00, "io"),
    # P8 « définir les règles de majorité » : poussée lente
    (F(1253), 1.06, "hold"),
    # P9 coupe franche, punch-in sur « Et VOUS » puis rapprochement jusqu'à la fin
    (F(1254), 1.00, "hold"), (42.13, 1.00, "out"), (42.30, 1.12, "lin"), (46.30, 1.17, "hold"),
]

PAN_X = [
    (0.0, 0.0, "hold"),
    (F(344), 0.0, "in"), (F(347), 0.38, "hold"),      # WHIP PAN à 11,60 (dans la pause)
    (F(348), -0.38, "out"), (F(353), 0.0, "io"),
    (16.20, 0.02, "io"), (17.50, 0.0, "hold"),         # léger travelling pendant l'énumération
    (25.00, 0.0, "io"), (28.45, -0.015, "hold"), (28.72, 0.0, "hold"),
]

SHAKES = [(10.98, 0.30, 5.0)]

BLUR_WINDOWS = [(10.96, 11.12, 0.6), (11.46, 11.78, 1.8), (22.74, 22.90, 0.6),
                (24.60, 25.00, 1.0), (38.98, 39.45, 1.2)]

WHOOSHES = [(11.60, 0.40, 0.11, "side"), (24.767, 0.45, 0.10, "up"), (39.133, 0.50, 0.09, "down")]
