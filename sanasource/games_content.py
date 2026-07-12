"""Content bank for the Jeux thérapeutiques (therapeutic games) space.
Plain Python data, not DB tables — same convention as
reflection_questions.py / sensibilisation_content.py.
"""

POSITIVE_THOUGHTS = [
    "Je suis capable.",
    "J'ai le droit de me reposer.",
    "Je progresse chaque jour.",
    "Je mérite d'être écouté·e.",
    "Mes efforts comptent.",
    "Je peux demander de l'aide.",
    "Chaque jour est une nouvelle chance.",
    "Je suis fier·ère de moi.",
    "J'ai le droit de me tromper.",
    "Je fais de mon mieux, et c'est suffisant.",
]

NEGATIVE_THOUGHTS = [
    "Je suis nul·le.",
    "Je vais échouer.",
    "Personne ne m'aime.",
    "Je n'y arriverai jamais.",
    "Tout est de ma faute.",
    "Je ne sers à rien.",
    "Je suis un fardeau.",
    "Rien ne va jamais changer.",
    "Je ne mérite pas d'être heureux·se.",
    "Je suis toujours en retard sur tout.",
]

# Jardin intérieur: growth stages, keyed by a cumulative "wellness actions"
# count (mood entries + daily challenges completed + auto-évaluations faites).
GARDEN_STAGES = [
    (0, 2, '🌰', 'Une graine', "Ton jardin commence tout juste — chaque petite action compte."),
    (3, 7, '🌱', 'Une pousse', "Ça germe ! Continue à prendre soin de toi."),
    (8, 15, '🌿', 'Une jeune plante', "Ton jardin prend forme, jour après jour."),
    (16, 30, '🌸', 'Une fleur', "Ton jardin fleurit — c'est le reflet de tes efforts."),
    (31, None, '🌳🌸', 'Un jardin épanoui', "Un vrai jardin, riche et vivant — comme ton cheminement."),
]


def get_garden_stage(actions_count):
    for lo, hi, emoji, label, msg in GARDEN_STAGES:
        if hi is None or actions_count <= hi:
            if actions_count >= lo:
                return {'emoji': emoji, 'label': label, 'message': msg, 'count': actions_count}
    return GARDEN_STAGES[-1]
