"""Content bank for the Sensibilisation (mental health awareness) space:
validated screening questionnaires, a knowledge quiz, and wellness
challenges. Plain Python data, not DB tables — same convention as
reflection_questions.py.

PHQ-9 and GAD-7 are public-domain, freely usable screening instruments
(Spitzer, Kroenke, Williams — developed with Pfizer, released for free
clinical and public use). They are NOT diagnostic tools; every result
screen must say so explicitly.
"""

SCREENING_ANSWER_CHOICES = [
    (0, 'Pas du tout'),
    (1, 'Plusieurs jours'),
    (2, 'Plus de la moitié des jours'),
    (3, 'Presque tous les jours'),
]

SCREENING_TOOLS = {
    'phq9': {
        'name': 'PHQ-9',
        'title': 'Dépistage — symptômes dépressifs',
        'intro': "Au cours des 2 dernières semaines, à quelle fréquence as-tu été gêné·e par les problèmes suivants ?",
        'questions': [
            "Peu d'intérêt ou de plaisir à faire les choses",
            "Te sentir triste, déprimé·e, ou sans espoir",
            "Difficultés à t'endormir, à rester endormi·e, ou trop dormir",
            "Te sentir fatigué·e ou manquer d'énergie",
            "Peu d'appétit ou trop manger",
            "Avoir une mauvaise image de toi-même — ou le sentiment d'être un échec, d'avoir déçu ta famille",
            "Difficultés à te concentrer, par exemple pour lire ou regarder la télévision",
            "Bouger ou parler si lentement que d'autres l'auraient remarqué — ou au contraire, être si agité·e que tu bouges beaucoup plus que d'habitude",
            "Avoir des pensées comme quoi tu serais mieux mort·e, ou penser à te faire du mal d'une manière ou d'une autre",
        ],
        # Index (0-based) of the item that signals possible self-harm/suicide
        # risk — a positive answer here must trigger crisis resources
        # immediately, independent of the total score.
        'risk_question_index': 8,
        'max_score': 27,
        'bands': [
            (0, 4, 'Minimal'),
            (5, 9, 'Léger'),
            (10, 14, 'Modéré'),
            (15, 19, 'Modérément sévère'),
            (20, 27, 'Sévère'),
        ],
    },
    'gad7': {
        'name': 'GAD-7',
        'title': 'Dépistage — symptômes anxieux',
        'intro': "Au cours des 2 dernières semaines, à quelle fréquence as-tu été gêné·e par les problèmes suivants ?",
        'questions': [
            "Te sentir nerveux·se, anxieux·se, ou à cran",
            "Ne pas arriver à arrêter ou contrôler tes inquiétudes",
            "T'inquiéter excessivement à propos de tout et de rien",
            "Difficultés à te détendre",
            "Être si agité·e qu'il est difficile de rester en place",
            "Devenir facilement irritable ou agacé·e",
            "Avoir peur que quelque chose d'horrible puisse arriver",
        ],
        'risk_question_index': None,
        'max_score': 21,
        'bands': [
            (0, 4, 'Minimal'),
            (5, 9, 'Léger'),
            (10, 14, 'Modéré'),
            (15, 21, 'Sévère'),
        ],
    },
}


def score_band(tool_key, score):
    for lo, hi, label in SCREENING_TOOLS[tool_key]['bands']:
        if lo <= score <= hi:
            return label
    return ''


QUIZ_QUESTIONS = [
    {
        'question': "La dépression, c'est...",
        'choices': ["Un manque de volonté", "Une vraie maladie médicale", "Une phase qui passe toute seule", "Un signe de faiblesse"],
        'correct': 1,
        'explanation': "La dépression a des causes biologiques, psychologiques et sociales reconnues. Elle se traite, comme n'importe quelle autre maladie.",
    },
    {
        'question': "Que faire si un·e proche parle d'idées suicidaires ?",
        'choices': ["Changer de sujet pour ne pas l'encourager", "L'écouter, prendre au sérieux, et l'orienter vers de l'aide", "Attendre que ça passe", "Lui dire de se ressaisir"],
        'correct': 1,
        'explanation': "En parler ne \"donne\" pas l'idée à la personne — au contraire, l'écoute sans jugement et l'orientation vers une aide professionnelle sauvent des vies.",
    },
    {
        'question': "L'anxiété chronique peut avoir des effets physiques réels (maux de tête, troubles digestifs, fatigue) ?",
        'choices': ["Vrai", "Faux"],
        'correct': 0,
        'explanation': "Le corps et l'esprit sont liés : le stress chronique a des effets physiologiques mesurables, pas seulement \"dans la tête\".",
    },
    {
        'question': "Consulter un psychologue signifie...",
        'choices': ["Qu'on est \"fou\"", "Qu'on prend soin de sa santé, comme pour le corps", "Qu'on a échoué dans la vie", "Que la famille va avoir honte"],
        'correct': 1,
        'explanation': "Consulter est un acte de courage et de responsabilité envers soi-même — exactement comme aller chez le médecin pour un problème physique.",
    },
    {
        'question': "Le sommeil a un impact direct sur la santé mentale ?",
        'choices': ["Vrai", "Faux"],
        'correct': 0,
        'explanation': "Le manque de sommeil aggrave l'anxiété, la dépression et la gestion des émotions — c'est l'un des premiers leviers à travailler.",
    },
    {
        'question': "Un enfant peut-il souffrir d'anxiété ou de dépression ?",
        'choices': ["Non, ce sont des problèmes d'adultes", "Oui, à tout âge", "Seulement à l'adolescence", "Seulement après un traumatisme"],
        'correct': 1,
        'explanation': "Les troubles mentaux peuvent toucher n'importe quel âge, y compris les enfants — souvent sous des formes différentes de celles des adultes.",
    },
    {
        'question': "Le burn-out est reconnu comme...",
        'choices': ["Une invention moderne", "Un phénomène lié à l'épuisement professionnel chronique", "Juste de la fatigue normale", "Un problème uniquement individuel"],
        'correct': 1,
        'explanation': "Le burn-out est un épuisement physique et émotionnel lié à un stress professionnel prolongé, reconnu par l'OMS.",
    },
    {
        'question': "Prendre soin de sa santé mentale profite...",
        'choices': ["Qu'à soi-même", "À soi-même et à son entourage (famille, travail, relations)", "À personne, c'est égoïste", "Uniquement en cas de crise"],
        'correct': 1,
        'explanation': "Une personne qui va bien est plus disponible, patiente et présente pour les autres — prendre soin de soi n'est jamais égoïste.",
    },
]

CHALLENGES = [
    {
        'id': 'gratitude-3j',
        'icon': '🙏',
        'title': '3 jours de gratitude',
        'description': "Chaque jour, note une chose — même petite — pour laquelle tu es reconnaissant·e.",
        'duration_days': 3,
    },
    {
        'id': 'sommeil-7j',
        'icon': '😴',
        'title': 'Une semaine de sommeil régulier',
        'description': "Couche-toi à heure fixe pendant 7 jours et observe l'effet sur ton humeur.",
        'duration_days': 7,
    },
    {
        'id': 'ecrans-5j',
        'icon': '📵',
        'title': '5 jours sans écran avant de dormir',
        'description': "Pas de téléphone dans la dernière heure avant le coucher, pendant 5 jours.",
        'duration_days': 5,
    },
    {
        'id': 'respiration-5j',
        'icon': '🌬️',
        'title': '5 jours de respiration consciente',
        'description': "5 minutes de respiration lente et profonde chaque jour, matin ou soir.",
        'duration_days': 5,
    },
    {
        'id': 'marche-7j',
        'icon': '🚶',
        'title': 'Une semaine de marche quotidienne',
        'description': "15 minutes de marche par jour, dehors si possible, pendant 7 jours.",
        'duration_days': 7,
    },
]

CHALLENGES_BY_ID = {c['id']: c for c in CHALLENGES}
