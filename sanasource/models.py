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


# ── Groupes de soutien ────────────────────────────────────────────────────────

class SanaGroup(models.Model):
    name        = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    icon        = models.CharField(max_length=20, default='👥')
    created_by  = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_groups')
    members     = models.ManyToManyField(User, related_name='sana_groups', blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    MOOD_SCORE = {'tres_mal': 10, 'pas_bien': 30, 'neutre': 50, 'bien': 70, 'tres_bien': 90}

    class Meta:
        ordering            = ['created_at']
        verbose_name        = 'Groupe'
        verbose_name_plural = 'Groupes'

    def __str__(self):
        return self.name

    @property
    def member_count(self):
        return self.members.count()


class GroupMessage(models.Model):
    group   = models.ForeignKey(SanaGroup, on_delete=models.CASCADE, related_name='messages')
    sender  = models.ForeignKey(User, on_delete=models.CASCADE, related_name='group_messages')
    content = models.TextField()
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ['sent_at']
        verbose_name        = 'Message de groupe'
        verbose_name_plural = 'Messages de groupe'

    def __str__(self):
        return f'[{self.group.name}] {self.sender}: {self.content[:40]}'


# ── Suivi de l'humeur ─────────────────────────────────────────────────────────

class MoodEntry(models.Model):
    MOOD_CHOICES = [
        ('tres_mal',  '😔 Vraiment mal'),
        ('pas_bien',  '😟 Pas très bien'),
        ('neutre',    '😐 Neutre'),
        ('bien',      '🙂 Bien'),
        ('tres_bien', '😊 Très bien'),
    ]
    _EMOJI = {'tres_mal': '😔', 'pas_bien': '😟', 'neutre': '😐', 'bien': '🙂', 'tres_bien': '😊'}
    _SCORE = {'tres_mal': 10, 'pas_bien': 30, 'neutre': 50, 'bien': 70, 'tres_bien': 90}

    user        = models.ForeignKey(User, on_delete=models.CASCADE, related_name='mood_entries')
    mood        = models.CharField(max_length=20, choices=MOOD_CHOICES)
    note        = models.TextField(blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ['-recorded_at']
        verbose_name        = "Entrée d'humeur"
        verbose_name_plural = "Entrées d'humeur"

    def __str__(self):
        return f'{self.user.username} — {self.mood} — {self.recorded_at.date()}'

    @property
    def emoji(self):
        return self._EMOJI.get(self.mood, '😐')

    @property
    def score(self):
        return self._SCORE.get(self.mood, 50)


# ── Posts communautaires ──────────────────────────────────────────────────────

class CommunityPost(models.Model):
    TAG_CHOICES = [
        ('anxiete',    'Anxiété'),
        ('depression', 'Dépression'),
        ('burnout',    'Burn-out'),
        ('deuil',      'Deuil'),
        ('examens',    'Étudiants'),
        ('famille',    'Famille'),
        ('travail',    'Travail'),
        ('guerison',   'Guérison'),
        ('autre',      'Autre'),
    ]
    author     = models.ForeignKey(User, on_delete=models.CASCADE, related_name='community_posts')
    content    = models.TextField()
    tag        = models.CharField(max_length=20, choices=TAG_CHOICES, default='autre')
    likes      = models.ManyToManyField(User, related_name='liked_posts', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ['-created_at']
        verbose_name        = 'Post communautaire'
        verbose_name_plural = 'Posts communautaires'

    def __str__(self):
        return f'{self.author.username}: {self.content[:60]}'
