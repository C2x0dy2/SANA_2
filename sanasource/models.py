from django.db import models
from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password, check_password as verify_password_hash


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

    # Surnoms mutuels dans le chat avec SANA
    sana_nickname      = models.CharField(max_length=50, blank=True)  # comment l'utilisateur appelle SANA
    user_nickname      = models.CharField(max_length=50, blank=True)  # comment SANA appelle l'utilisateur
    has_seen_welcome   = models.BooleanField(default=False)  # "bonne arrivée" affiché une seule fois

    # Préférences (interrupteurs de la section Mon Profil → Paramètres)
    notif_rappels_humeur      = models.BooleanField(default=True)
    notif_messages_communaute = models.BooleanField(default=True)
    notif_nouveaux_articles   = models.BooleanField(default=False)
    notif_rappels_rdv         = models.BooleanField(default=True)
    priv_mode_anonyme         = models.BooleanField(default=True)
    priv_partager_progres     = models.BooleanField(default=True)
    priv_donnees_analytiques  = models.BooleanField(default=False)

    SETTINGS_FIELDS = {
        'notif_rappels_humeur', 'notif_messages_communaute', 'notif_nouveaux_articles',
        'notif_rappels_rdv', 'priv_mode_anonyme', 'priv_partager_progres', 'priv_donnees_analytiques',
    }

    # Moyen de paiement personnel — pour le soutien financier communautaire.
    # SANA ne collecte ni ne reverse jamais d'argent : le bouton "Soutenir"
    # affiche simplement ces coordonnées, le don se fait directement entre
    # les deux utilisateurs via leur propre moyen de paiement.
    PAYMENT_METHOD_CHOICES = [
        ('orange_money', 'Orange Money'),
        ('mtn_money',     'MTN Mobile Money'),
        ('moov_money',    'Moov Money'),
        ('wave',          'Wave'),
        ('paypal',        'PayPal'),
        ('autre',         'Autre'),
    ]
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, blank=True)
    payment_info   = models.CharField(max_length=200, blank=True)  # numéro, lien, identifiant

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
        annotated = self.__dict__.get('member_count_annotated')
        if annotated is not None:
            return annotated
        return self.members.count()


class GroupMessage(models.Model):
    group   = models.ForeignKey(SanaGroup, on_delete=models.CASCADE, related_name='messages')
    sender  = models.ForeignKey(User, on_delete=models.CASCADE, related_name='group_messages')
    content = models.TextField()
    seen_by = models.ManyToManyField(User, related_name='seen_group_messages', blank=True)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ['sent_at']
        verbose_name        = 'Message de groupe'
        verbose_name_plural = 'Messages de groupe'

    def __str__(self):
        return f'[{self.group.name}] {self.sender}: {self.content[:40]}'


class DirectMessage(models.Model):
    sender   = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_dms')
    receiver = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_dms')
    content  = models.TextField()
    read     = models.BooleanField(default=False)
    sent_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ['sent_at']
        verbose_name        = 'Message privé'
        verbose_name_plural = 'Messages privés'

    def __str__(self):
        return f'{self.sender} → {self.receiver}: {self.content[:40]}'


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


# ── Journal (carnet intime) ────────────────────────────────────────────────────

class Journal(models.Model):
    ICON_DEFAULT = '📔'
    COLOR_CHOICES = [
        ('burgundy',   '#7d3049'),
        ('forest',     '#3f5d43'),
        ('navy',       '#2c3e63'),
        ('mustard',    '#b8862f'),
        ('terracotta', '#b5533b'),
        ('plum',       '#5c3a63'),
        ('teal',       '#2c6e6b'),
        ('charcoal',   '#3a3a3f'),
    ]
    _COLOR_HEX = dict(COLOR_CHOICES)

    COVER_STYLE_CHOICES = [
        ('classic', 'Classique'),
        ('leather', 'Cuir'),
        ('linen',   'Lin'),
        ('floral',  'Fleuri'),
        ('kraft',   'Kraft'),
        ('velvet',  'Velours'),
    ]

    KIND_CHOICES = [
        ('personal', 'Personnel'),
        ('burn',     'Burn After Writing'),
    ]

    # `user` is the journal's owner (kept as `user` to match the FK convention
    # used across the app and to avoid breaking the existing journal views).
    user         = models.ForeignKey(User, on_delete=models.CASCADE, related_name='journals')
    kind         = models.CharField(max_length=10, choices=KIND_CHOICES, default='personal')
    title        = models.CharField(max_length=100, default='Mon journal')
    cover_style  = models.CharField(max_length=20, choices=COVER_STYLE_CHOICES, default='classic')
    icon         = models.CharField(max_length=8, default=ICON_DEFAULT)
    color        = models.CharField(max_length=20, choices=[(k, k) for k, _ in COLOR_CHOICES], default='burgundy')
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)
    last_opened  = models.DateTimeField(null=True, blank=True)
    is_locked    = models.BooleanField(default=False)
    password     = models.CharField(max_length=128, blank=True)  # stores a hash, never plaintext

    class Meta:
        ordering            = ['-updated_at']
        verbose_name        = 'Journal'
        verbose_name_plural = 'Journaux'

    def __str__(self):
        return f'{self.user.username} — {self.title}'

    @property
    def color_hex(self):
        return self._COLOR_HEX.get(self.color, self._COLOR_HEX['burgundy'])

    @property
    def has_password(self):
        return bool(self.password)

    def set_password(self, raw_password):
        self.password = make_password(raw_password) if raw_password else ''

    def check_password(self, raw_password):
        if not self.password:
            return False
        return verify_password_hash(raw_password, self.password)


class JournalEntry(models.Model):
    journal    = models.ForeignKey(Journal, on_delete=models.CASCADE, related_name='entries')
    entry_date = models.DateField()
    title      = models.CharField(max_length=150, blank=True)
    content    = models.TextField(blank=True)
    mood       = models.CharField(max_length=20, choices=MoodEntry.MOOD_CHOICES, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering            = ['entry_date']
        unique_together     = [['journal', 'entry_date']]
        verbose_name        = "Entrée de journal"
        verbose_name_plural = "Entrées de journal"

    def __str__(self):
        return f'[{self.journal.title}] {self.entry_date}'


# ── Journal (nouveau système paginé) ──────────────────────────────────────────

class JournalPage(models.Model):
    _DAY_NAMES_FR = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche']

    RITUAL_CHOICES = [
        ('fire', 'Feu'),
    ]

    journal     = models.ForeignKey(Journal, on_delete=models.CASCADE, related_name='pages')
    page_number = models.PositiveIntegerField()
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)
    content     = models.TextField(blank=True)
    mood        = models.CharField(max_length=20, choices=MoodEntry.MOOD_CHOICES, blank=True)
    date        = models.DateField()
    day_of_week = models.CharField(max_length=12, blank=True)

    # Only set for Burn After Writing pages — the guided reflection question
    # shown above the (still free-form) answer area. Blank for personal pages.
    prompt = models.TextField(blank=True, default='')

    # Burn After Writing disposition — when set, the page is auto-burned once
    # this passes (checked lazily on read, see views._maybe_burn_expired).
    expires_at = models.DateTimeField(null=True, blank=True)

    # Once burned/released, content/mood/attachments are wiped for good and
    # only this flag + a symbolic marker remain (a placeholder, not a gap).
    is_archived    = models.BooleanField(default=False)
    is_locked      = models.BooleanField(default=False)
    is_released    = models.BooleanField(default=False)
    released_at    = models.DateTimeField(null=True, blank=True)
    release_ritual = models.CharField(max_length=20, choices=RITUAL_CHOICES, blank=True)

    class Meta:
        ordering            = ['page_number']
        unique_together     = [['journal', 'page_number']]
        verbose_name        = 'Page de journal'
        verbose_name_plural = 'Pages de journal'

    def __str__(self):
        return f'[{self.journal.title}] page {self.page_number}'

    def save(self, *args, **kwargs):
        if self.date:
            self.day_of_week = self._DAY_NAMES_FR[self.date.weekday()]
        super().save(*args, **kwargs)


class Attachment(models.Model):
    TYPE_CHOICES = [
        ('image',      'Image'),
        ('sticker',    'Sticker'),
        ('emoji',      'Emoji'),
        ('drawing',    'Dessin'),
        ('voice_note', 'Note vocale'),
        ('weather',    'Météo'),
        ('location',   'Lieu'),
    ]

    page            = models.ForeignKey(JournalPage, on_delete=models.CASCADE, related_name='attachments')
    attachment_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    file            = models.FileField(upload_to='journal_attachments/%Y/%m/', blank=True, null=True)
    sticker_code    = models.CharField(max_length=50, blank=True)
    label           = models.CharField(max_length=200, blank=True)
    order           = models.PositiveIntegerField(default=0)
    created_at      = models.DateTimeField(auto_now_add=True)

    # Free placement on the page's scrapbook layer — percentages of the page's
    # own box, so a spot stays put regardless of viewport size.
    position_x = models.FloatField(default=50)
    position_y = models.FloatField(default=50)
    width_pct  = models.FloatField(default=25)
    rotation   = models.FloatField(default=0)

    class Meta:
        ordering            = ['order', 'created_at']
        verbose_name        = 'Pièce jointe'
        verbose_name_plural = 'Pièces jointes'

    def __str__(self):
        return f'[{self.page}] {self.attachment_type}'


# ── Chat SANA (conversations) ─────────────────────────────────────────────────

class Conversation(models.Model):
    DEFAULT_TITLE = 'Nouvelle conversation'

    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='conversations')
    title      = models.CharField(max_length=100, default=DEFAULT_TITLE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering            = ['-updated_at']
        verbose_name        = 'Conversation'
        verbose_name_plural = 'Conversations'

    def __str__(self):
        return f'{self.user.username} — {self.title}'


class Message(models.Model):
    ROLE_CHOICES = [
        ('user',      'Utilisateur'),
        ('assistant', 'Assistant'),
        ('system',    'Système'),
    ]
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    role         = models.CharField(max_length=20, choices=ROLE_CHOICES)
    content      = models.TextField()
    image        = models.FileField(upload_to='chat_attachments/%Y/%m/', blank=True, null=True)
    voice_note   = models.FileField(upload_to='chat_attachments/%Y/%m/', blank=True, null=True)
    timestamp    = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ['timestamp']
        verbose_name        = 'Message de chat'
        verbose_name_plural = 'Messages de chat'

    def __str__(self):
        return f'[{self.conversation_id}] {self.role}: {self.content[:40]}'


# ── Posts communautaires ──────────────────────────────────────────────────────

class Notification(models.Model):
    TYPE_CHOICES = [
        ('like',    'Like'),
        ('comment', 'Commentaire'),
        ('support', 'Soutien'),
        ('message', 'Message'),
        ('join',    'Rejoindre'),
        ('welcome', 'Bienvenue'),
    ]
    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    type       = models.CharField(max_length=20, choices=TYPE_CHOICES)
    title      = models.CharField(max_length=200)
    body       = models.TextField()
    url        = models.CharField(max_length=500, default='/')
    read       = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ['-created_at']
        verbose_name        = 'Notification'
        verbose_name_plural = 'Notifications'

    def __str__(self):
        return f'{self.user.username} — {self.type}: {self.title}'


class PushSubscription(models.Model):
    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='push_subscriptions')
    endpoint   = models.TextField(unique=True)
    p256dh     = models.TextField()
    auth       = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = 'Abonnement Push'
        verbose_name_plural = 'Abonnements Push'

    def __str__(self):
        return f'{self.user.username} — {self.endpoint[:60]}'


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
    supports   = models.ManyToManyField(User, related_name='supported_posts', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Demande de soutien financier — volontaire, distincte du post lui-même
    # (jamais activée par défaut) pour éviter qu'un simple message de
    # détresse déclenche automatiquement une sollicitation d'argent.
    requests_support = models.BooleanField(default=False)
    is_reported       = models.BooleanField(default=False)  # masqué en attendant modération

    class Meta:
        ordering            = ['-created_at']
        verbose_name        = 'Post communautaire'
        verbose_name_plural = 'Posts communautaires'

    def __str__(self):
        return f'{self.author.username}: {self.content[:60]}'

    @property
    def like_count(self):
        annotated = self.__dict__.get('like_count_annotated')
        if annotated is not None:
            return annotated
        return self.likes.count()

    @property
    def support_count(self):
        annotated = self.__dict__.get('support_count_annotated')
        if annotated is not None:
            return annotated
        return self.supports.count()

    @property
    def comment_count(self):
        annotated = self.__dict__.get('comment_count_annotated')
        if annotated is not None:
            return annotated
        return self.comments.count()


class Comment(models.Model):
    post       = models.ForeignKey(CommunityPost, on_delete=models.CASCADE, related_name='comments')
    author     = models.ForeignKey(User, on_delete=models.CASCADE, related_name='comments')
    content    = models.TextField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ['created_at']
        verbose_name        = 'Commentaire'
        verbose_name_plural = 'Commentaires'

    def __str__(self):
        return f'{self.author.username} on post {self.post_id}: {self.content[:40]}'


class PostReport(models.Model):
    REASON_CHOICES = [
        ('scam',        'Arnaque / demande d\'argent suspecte'),
        ('harassment',  'Harcèlement / propos violents'),
        ('spam',        'Spam'),
        ('inapproprie', 'Contenu inapproprié'),
        ('autre',       'Autre'),
    ]
    post       = models.ForeignKey(CommunityPost, on_delete=models.CASCADE, related_name='reports')
    reporter   = models.ForeignKey(User, on_delete=models.CASCADE, related_name='post_reports')
    reason     = models.CharField(max_length=20, choices=REASON_CHOICES, default='autre')
    details    = models.TextField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ['-created_at']
        verbose_name        = 'Signalement'
        verbose_name_plural = 'Signalements'
        constraints = [
            models.UniqueConstraint(fields=['post', 'reporter'], name='unique_report_per_user_per_post'),
        ]

    def __str__(self):
        return f'{self.reporter.username} → post {self.post_id} ({self.reason})'


# ── Avis publics (page d'accueil) ──────────────────────────────────────────────

class Review(models.Model):
    RATING_CHOICES = [(i, str(i)) for i in range(1, 6)]

    author      = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reviews')
    content     = models.TextField(max_length=1000)
    rating      = models.PositiveSmallIntegerField(choices=RATING_CHOICES, default=5)
    is_approved = models.BooleanField(default=False)  # modéré manuellement avant publication publique
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ['-created_at']
        verbose_name        = 'Avis'
        verbose_name_plural = 'Avis'

    def __str__(self):
        return f'{self.author.username} ({self.rating}★): {self.content[:40]}'


# ── Newsletter ──────────────────────────────────────────────────────────────

class NewsletterSubscriber(models.Model):
    email         = models.EmailField(unique=True)
    is_confirmed  = models.BooleanField(default=False)
    token         = models.CharField(max_length=64, unique=True)
    subscribed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ['-subscribed_at']
        verbose_name        = 'Abonné newsletter'
        verbose_name_plural = 'Abonnés newsletter'

    def __str__(self):
        return self.email


# ── Sensibilisation (auto-évaluations, quiz, défis, mythes) ─────────────────

class ScreeningResult(models.Model):
    TOOL_CHOICES = [
        ('phq9', 'PHQ-9 (dépression)'),
        ('gad7', 'GAD-7 (anxiété)'),
    ]
    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='screening_results')
    tool       = models.CharField(max_length=10, choices=TOOL_CHOICES)
    score      = models.PositiveSmallIntegerField()
    band       = models.CharField(max_length=50)  # ex: "Modéré"
    flagged    = models.BooleanField(default=False)  # item de risque suicidaire positif
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ['-created_at']
        verbose_name        = 'Résultat auto-évaluation'
        verbose_name_plural = 'Résultats auto-évaluation'

    def __str__(self):
        return f'{self.user.username} — {self.tool} ({self.score})'


class QuizAttempt(models.Model):
    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='quiz_attempts')
    score      = models.PositiveSmallIntegerField()
    total      = models.PositiveSmallIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ['-created_at']
        verbose_name        = 'Tentative de quiz'
        verbose_name_plural = 'Tentatives de quiz'

    def __str__(self):
        return f'{self.user.username} — {self.score}/{self.total}'


class DailyChallengeCompletion(models.Model):
    """One row per user per calendar day whose défi they've completed.
    Days are never skipped/expired — see views._get_current_daily_challenge:
    if a user falls behind, the oldest incomplete day stays as their
    "current" challenge until they log it, so gaps accumulate rather than
    vanish. challenge_date is the day the défi was FOR, not necessarily the
    day it was actually completed (completed_at is that)."""
    user            = models.ForeignKey(User, on_delete=models.CASCADE, related_name='daily_challenge_completions')
    challenge_date  = models.DateField()
    reflection_text = models.TextField(max_length=500, blank=True)
    completed_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ['-challenge_date']
        verbose_name        = 'Défi du jour complété'
        verbose_name_plural = 'Défis du jour complétés'
        constraints = [
            models.UniqueConstraint(fields=['user', 'challenge_date'], name='unique_daily_challenge_per_user_per_date'),
        ]

    def __str__(self):
        return f'{self.user.username} — {self.challenge_date}'


class SubmittedMyth(models.Model):
    author        = models.ForeignKey(User, on_delete=models.CASCADE, related_name='submitted_myths')
    myth_text     = models.TextField(max_length=500)
    response_text = models.TextField(max_length=1000, blank=True)
    is_approved   = models.BooleanField(default=False)
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ['-created_at']
        verbose_name        = 'Mythe soumis'
        verbose_name_plural = 'Mythes soumis'

    def __str__(self):
        return f'{self.author.username}: {self.myth_text[:40]}'


# ── Jeux thérapeutiques ───────────────────────────────────────────────────────

class GameSession(models.Model):
    GAME_CHOICES = [
        ('attrape_pensees', 'Attrape les pensées positives'),
        ('respire_avec_moi', 'Respire avec moi'),
        ('chasse_pensees', 'Chasse aux pensées'),
        ('memory_emotions', 'Memory des émotions'),
    ]
    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='game_sessions')
    game       = models.CharField(max_length=30, choices=GAME_CHOICES)
    score      = models.PositiveSmallIntegerField()
    played_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ['-played_at']
        verbose_name        = 'Partie jouée'
        verbose_name_plural = 'Parties jouées'

    def __str__(self):
        return f'{self.user.username} — {self.game} ({self.score})'
