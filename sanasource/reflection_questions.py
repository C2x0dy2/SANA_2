"""Question bank for the Burn After Writing journal.

Plain Python data, not a DB table — see the choice-list convention in
models.py. One question is shown per page; the app picks randomly while
avoiding repetition within a single journal (see views._pick_prompt).
"""

REFLECTION_QUESTIONS = [
    "Qu'est-ce que tu n'as jamais dit à personne ?",
    "De quoi es-tu encore en colère aujourd'hui ?",
    "Qu'aurait besoin d'entendre ton toi plus jeune, en ce moment ?",
    "Quelle relation te fait encore mal ?",
    "Qu'est-ce que tu portes en silence depuis trop longtemps ?",
    "Qui as-tu encore besoin de pardonner ?",
    "Qu'est-ce que tu ne t'es jamais pardonné à toi-même ?",
    "De quoi as-tu le plus peur en ce moment ?",
    "Qu'est-ce que tu aimerais laisser partir ce soir ?",
    "Quel souvenir revient sans cesse, même quand tu ne le veux pas ?",
    "Qu'est-ce que tu regrettes de ne pas avoir dit à temps ?",
    "Qu'est-ce qui te pèse le plus, en ce moment précis ?",
    "Quelle vérité évites-tu de regarder en face ?",
    "Qu'est-ce que tu voudrais dire à quelqu'un que tu as perdu de vue ?",
    "Quelle attente n'as-tu jamais osé exprimer ?",
    "Qu'est-ce que tu fais semblant d'aller bien alors que ce n'est pas le cas ?",
    "Quel rêve as-tu abandonné, et pourquoi ?",
    "Qu'est-ce que tu voudrais que l'on comprenne enfin de toi ?",
    "De quoi as-tu honte, même si tu sais que tu ne devrais pas ?",
    "Qu'est-ce qui t'empêche de dormir, certains soirs ?",
    "Quelle décision continues-tu de remettre en question ?",
    "Qu'est-ce que tu aimerais pouvoir recommencer autrement ?",
    "Qui aimerais-tu être, si tu n'avais peur du regard de personne ?",
    "Qu'est-ce que tu attends encore que l'on te dise ?",
    "Quelle blessure d'enfance résonne encore aujourd'hui ?",
    "Qu'est-ce que tu voudrais enfin accepter de toi-même ?",
    "De quoi as-tu besoin, là, maintenant, que tu ne t'accordes pas ?",
    "Qu'est-ce que tu voudrais crier, si personne ne pouvait t'entendre ?",
    "Quelle promesse envers toi-même n'as-tu jamais tenue ?",
    "Qu'est-ce qui serait différent si tu arrêtais d'avoir peur ?",
]
