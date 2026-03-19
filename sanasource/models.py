from django.db import models
from django.contrib.auth.models import User


class UserProfile(models.Model):
    GENRE_CHOICES = [
        ('homme',       'Homme'),
        ('femme',       'Femme'),
        ('autre',       'Autre'),
        ('prefere_pas', 'Préfère ne pas préciser'),
    ]
    SITUATION_CHOICES = [
        ('etudiant',      'Étudiant·e'),
        ('professionnel', 'Professionnel·le'),
        ('sans_emploi',   'Sans emploi'),
        ('retraite',      'Retraité·e'),
        ('autre',         'Autre'),
    ]
    THEME_CHOICES = [
        ('rose',   'Rose doux'),
        ('ocean',  'Océan calme'),
        ('foret',  'Forêt apaisante'),
        ('soleil', 'Soleil chaud'),
        ('nuit',   'Nuit étoilée'),
    ]
    SENTIMENT_CHOICES = [
        ('tres_bien', 'Très bien'),
        ('bien',      'Bien'),
        ('moyen',     'Moyen'),
        ('pas_bien',  'Pas très bien'),
        ('tres_mal',  'Vraiment mal'),
    ]
    URGENCE_CHOICES = [
        (1, "Je vais bien, je veux juste progresser"),
        (2, "Un peu d'anxiété ou de stress"),
        (3, "Des difficultés modérées au quotidien"),
        (4, "Des difficultés importantes"),
        (5, "En grande détresse, j'ai besoin d'aide maintenant"),
    ]

    user               = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    username_anonyme   = models.CharField(max_length=50, unique=True)
    age                = models.PositiveIntegerField(null=True, blank=True)
    genre              = models.CharField(max_length=20, choices=GENRE_CHOICES, blank=True)
    ville              = models.CharField(max_length=100, blank=True)
    situation          = models.CharField(max_length=20, choices=SITUATION_CHOICES, blank=True)
    theme_couleur      = models.CharField(max_length=20, choices=THEME_CHOICES, default='rose')
    comment_tu_te_sens = models.CharField(max_length=20, choices=SENTIMENT_CHOICES, blank=True)
    principales_difficultes = models.JSONField(default=list, blank=True)
    objectif_principal = models.CharField(max_length=200, blank=True)
    a_deja_consulte    = models.BooleanField(null=True, blank=True)
    niveau_urgence     = models.IntegerField(choices=URGENCE_CHOICES, default=1)
    date_inscription   = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.username_anonyme} ({self.user.email})"

    class Meta:
        verbose_name        = "Profil utilisateur"
        verbose_name_plural = "Profils utilisateurs"
