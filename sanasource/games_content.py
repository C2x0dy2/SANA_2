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


# Chasse aux pensées: CBT-style reframing. Each entry is a distorted thought,
# the realistic reframe (correct answer), and one plausible-but-wrong
# distractor — order is shuffled client-side.
THOUGHT_REFRAMES = [
    {
        'thought': "Je suis nul·le.",
        'correct': "Je fais des erreurs, comme tout le monde — ça ne définit pas ma valeur.",
        'wrong': "Je dois être parfait·e pour compter.",
    },
    {
        'thought': "Je vais échouer.",
        'correct': "Je ne sais pas encore ce qui va se passer, je peux essayer.",
        'wrong': "Si j'échoue une fois, je vais toujours échouer.",
    },
    {
        'thought': "Personne ne m'aime.",
        'correct': "Certaines personnes tiennent à moi, même si je ne le ressens pas toujours.",
        'wrong': "Je dois plaire à tout le monde pour être aimé·e.",
    },
    {
        'thought': "Je n'y arriverai jamais.",
        'correct': "Je peux apprendre et progresser avec du temps et de la pratique.",
        'wrong': "Si ce n'est pas facile maintenant, ça ne le sera jamais.",
    },
    {
        'thought': "Tout est de ma faute.",
        'correct': "Plusieurs facteurs jouent dans une situation, pas seulement moi.",
        'wrong': "Je dois tout contrôler pour que ça se passe bien.",
    },
    {
        'thought': "Je ne sers à rien.",
        'correct': "J'ai de la valeur même quand je ne suis pas productif·ve.",
        'wrong': "Ma valeur dépend de ce que je fais.",
    },
    {
        'thought': "Je suis un fardeau.",
        'correct': "Demander de l'aide fait partie d'une relation saine.",
        'wrong': "Je dois toujours me débrouiller seul·e.",
    },
    {
        'thought': "Rien ne va jamais changer.",
        'correct': "Les choses évoluent, même lentement — le changement est possible.",
        'wrong': "Si ça n'a pas changé avant, ça ne changera jamais.",
    },
]

# Memory des émotions: pairs to match, each revealed pair shows this
# definition so the game teaches something on every match.
EMOTION_CARDS = [
    {'emoji': '😊', 'name': 'Joie', 'definition': "Un sentiment de bonheur et de satisfaction."},
    {'emoji': '😢', 'name': 'Tristesse', 'definition': "Une réaction naturelle à une perte ou une déception."},
    {'emoji': '😠', 'name': 'Colère', 'definition': "Une réaction à une injustice ou une frustration."},
    {'emoji': '😨', 'name': 'Peur', 'definition': "Une réponse à un danger perçu, réel ou non."},
    {'emoji': '😲', 'name': 'Surprise', 'definition': "Une réaction à quelque chose d'inattendu."},
    {'emoji': '🤢', 'name': 'Dégoût', 'definition': "Un rejet face à quelque chose de désagréable."},
    {'emoji': '😳', 'name': 'Honte', 'definition': "Le sentiment d'avoir déçu une norme, la sienne ou celle des autres."},
    {'emoji': '😇', 'name': 'Sérénité', 'definition': "Un état de calme intérieur, apaisé."},
]
