from pathlib import Path
from datetime import date, datetime, timedelta
import base64
import json
import logging
import mimetypes
import os
import random
import re
import secrets
from urllib.parse import quote

from django.core.files.base import ContentFile

from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.db.models import Count, Max, OuterRef, Subquery, IntegerField, Q
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.urls import reverse
from django_ratelimit.decorators import ratelimit

from .models import UserProfile, SanaGroup, GroupMessage, MoodEntry, CommunityPost, Comment, PostReport, Notification, PushSubscription, DirectMessage, Conversation, Message, Journal, JournalEntry, JournalPage, Attachment, Review, NewsletterSubscriber, ScreeningResult, QuizAttempt, DailyChallengeCompletion, SubmittedMyth, SolidarityMessage, GameSession, GameRoom, GameRoomPlayer, GameRoomMessage, WerewolfRoom, WerewolfPlayer, WerewolfMessage, WerewolfVote, ImpostorRoom, ImpostorPlayer, ImpostorMessage, ImpostorVote, BlogPost, BlogComment, BlogPostReport, BlogWeeklyWinner, BlogYearlyWinner
from .notifications import send_notification
from .emails import send_welcome_email, send_verification_email, send_newsletter_confirmation_email
from .tokens import email_verification_token
from .password_validation import french_password_errors
from .serializers import serialize_journal_page, serialize_attachment
from .reflection_questions import REFLECTION_QUESTIONS
from .sensibilisation_content import SCREENING_TOOLS, QUIZ_QUESTIONS, get_daily_challenge, score_band
from .games_content import POSITIVE_THOUGHTS, NEGATIVE_THOUGHTS, get_garden_stage, THOUGHT_REFRAMES, EMOTION_CARDS
from .multiplayer_content import EMOTION_WORDS, SHADOW_DISCUSSION_PROMPTS
from google import genai
from google.genai import types as genai_types
from google.genai import errors as genai_errors

logger = logging.getLogger(__name__)
auth_logger = logging.getLogger('sanasource.auth')

# ============================================================
# PAGES
# ============================================================

def accueil(request):
    reviews = Review.objects.filter(is_approved=True).select_related('author', 'author__profile')[:9]
    return render(request, 'page/accueil.html', {'reviews': reviews})

def history(request):
    return render(request, 'page/history.html')

def page_open(request):
    if request.user.is_authenticated:
        return redirect('sanasource:dashboard')
    return render(request, 'page/page_open.html')


def _build_watermark_data_uri(user):
    """A faint, per-viewer repeating text watermark (their own anon handle +
    user id) for screens where sensitive content is shown (groups, community,
    DMs). Doesn't block screenshots — nothing on the web can — but if a
    screenshot leaks, the watermark traces it back to whoever took it."""
    from django.utils.html import escape
    prof = getattr(user, 'profile', None)
    label = escape((prof.username_anonyme if prof else f'user-{user.pk}')[:24])
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="260" height="140">'
        f'<text x="0" y="80" font-family="sans-serif" font-size="13" '
        f'fill="rgba(120,80,100,0.05)" transform="rotate(-28 130 70)">{label} · #{user.pk}</text>'
        '</svg>'
    )
    return 'data:image/svg+xml;base64,' + base64.b64encode(svg.encode('utf-8')).decode('ascii')


def _looks_like_real_name(anon, first_name, last_name):
    """True if the anonymous handle embeds the user's real first/last name —
    guards against accidental self-deanonymization in groups/community."""
    anon_norm = re.sub(r'[^a-z]', '', (anon or '').lower())
    for part in (first_name, last_name):
        part_norm = re.sub(r'[^a-z]', '', (part or '').lower())
        if part_norm and len(part_norm) >= 3 and part_norm in anon_norm:
            return True
    return False


# Défi du jour: never scan further back than this, so a very old account
# doesn't surface a months-long backlog the day the feature ships.
DAILY_CHALLENGE_START_DATE = date(2026, 7, 12)
DAILY_CHALLENGE_MAX_BACKLOG = 60  # days


def _get_current_daily_challenge(user, profile):
    """Returns (challenge_date, pending_count): challenge_date is the oldest
    day in the user's window that they haven't completed yet (None if
    they're fully caught up through today), pending_count is how many days
    are queued up including that one. A day's défi never expires — it just
    stays "current" until logged, per the design brief."""
    start = DAILY_CHALLENGE_START_DATE
    if profile and profile.date_inscription:
        start = max(start, profile.date_inscription.date())
    start = max(start, date.today() - timedelta(days=DAILY_CHALLENGE_MAX_BACKLOG))

    today = date.today()
    completed = set(
        DailyChallengeCompletion.objects.filter(
            user=user, challenge_date__gte=start, challenge_date__lte=today,
        ).values_list('challenge_date', flat=True)
    )
    current = None
    pending = 0
    d = start
    while d <= today:
        if d not in completed:
            pending += 1
            if current is None:
                current = d
        d += timedelta(days=1)
    return current, pending


@never_cache
@ratelimit(key='ip', rate='5/h', method='POST', block=False)
def register_view(request):
    if request.user.is_authenticated:
        return redirect('sanasource:dashboard')
    if request.method == 'POST':
        if getattr(request, 'limited', False):
            auth_logger.warning('Registration rate limit exceeded, ip=%s', request.META.get('REMOTE_ADDR'))
            return render(request, 'page/register.html', {
                'error': 'Trop de tentatives de création de compte. Merci de réessayer dans quelques minutes.',
            }, status=429)

        first_name              = request.POST.get('first_name', '').strip()
        last_name               = request.POST.get('last_name', '').strip()
        email                   = request.POST.get('email', '').strip()
        password1               = request.POST.get('password1', '')
        password2               = request.POST.get('password2', '')
        username_anonyme        = request.POST.get('username_anonyme', '').strip()
        age_raw                 = request.POST.get('age', '').strip()
        genre                   = request.POST.get('genre', '')
        ville                   = request.POST.get('ville', '').strip()
        situation               = request.POST.get('situation', '')
        theme_couleur           = request.POST.get('theme_couleur', 'rose')
        comment_tu_te_sens      = request.POST.get('comment_tu_te_sens', '')
        principales_difficultes = request.POST.getlist('principales_difficultes')
        objectif_principal      = request.POST.get('objectif_principal', '')
        a_deja_consulte_raw     = request.POST.get('a_deja_consulte', '')
        niveau_urgence_raw      = request.POST.get('niveau_urgence', '1')

        ctx = {
            'first_name': first_name, 'last_name': last_name,
            'email': email, 'username_anonyme': username_anonyme,
        }

        # ── Validations ──────────────────────────────────────
        if not first_name or not email or not password1 or not username_anonyme:
            ctx['error'] = 'Merci de remplir tous les champs obligatoires.'
            return render(request, 'page/register.html', ctx)

        try:
            validate_email(email)
        except DjangoValidationError:
            ctx['error'] = "Merci d'indiquer une adresse e-mail valide."
            return render(request, 'page/register.html', ctx)

        if password1 != password2:
            ctx['error'] = 'Les mots de passe ne correspondent pas.'
            return render(request, 'page/register.html', ctx)

        # Enforce Django's configured AUTH_PASSWORD_VALIDATORS (min length,
        # not-too-common, not-entirely-numeric, not-too-similar-to-your-info)
        # instead of only checking length.
        password_errors = french_password_errors(password1)
        if password_errors:
            ctx['error'] = ' '.join(password_errors)
            auth_logger.info('Registration rejected (weak password), email=%s', email)
            return render(request, 'page/register.html', ctx)

        if User.objects.filter(username=email).exists():
            ctx['error'] = 'Un compte existe déjà avec cet e-mail.'
            return render(request, 'page/register.html', ctx)

        if UserProfile.objects.filter(username_anonyme=username_anonyme).exists():
            ctx['error'] = 'Ce nom anonyme est déjà pris, choisis-en un autre.'
            return render(request, 'page/register.html', ctx)

        if _looks_like_real_name(username_anonyme, first_name, last_name):
            ctx['error'] = 'Ton nom anonyme ressemble trop à ton vrai nom — choisis-en un qui ne te rend pas identifiable.'
            return render(request, 'page/register.html', ctx)

        # ── Conversions ──────────────────────────────────────
        age = int(age_raw) if age_raw.isdigit() else None

        if a_deja_consulte_raw == 'oui':
            a_deja_consulte = True
        elif a_deja_consulte_raw == 'non':
            a_deja_consulte = False
        else:
            a_deja_consulte = None

        try:
            niveau_urgence = int(niveau_urgence_raw)
        except (ValueError, TypeError):
            niveau_urgence = 1

        # ── Création du compte (inactif tant que l'e-mail n'est pas vérifié) ──
        try:
            user = User.objects.create_user(
                username=email,
                email=email,
                password=password1,
                first_name=first_name,
                last_name=last_name,
                is_active=False,
            )
        except Exception:
            auth_logger.exception('User creation failed, email=%s', email)
            ctx['error'] = 'Une erreur est survenue lors de la création de votre compte. Merci de réessayer.'
            return render(request, 'page/register.html', ctx)

        # A placeholder UserProfile was just created by the post_save signal
        # (see signals.py) — fill in the real fields the user submitted.
        try:
            profile = user.profile
            profile.username_anonyme = username_anonyme
            profile.age = age
            profile.genre = genre
            profile.ville = ville
            profile.situation = situation
            profile.theme_couleur = theme_couleur
            profile.comment_tu_te_sens = comment_tu_te_sens
            profile.principales_difficultes = principales_difficultes
            profile.objectif_principal = objectif_principal
            profile.a_deja_consulte = a_deja_consulte
            profile.niveau_urgence = niveau_urgence
            profile.save()
        except Exception:
            # The account still works with the placeholder profile — logged,
            # not fatal, matching the account-creation resilience this view
            # already had, but now actually visible in the logs.
            auth_logger.exception('UserProfile update failed, user_id=%s', user.pk)

        auth_logger.info('Registration succeeded (pending verification), user_id=%s', user.pk)

        # Unlike the welcome email/notification (sent once the account is
        # verified — see verify_email_view), this one is not best-effort: an
        # inactive account nobody can activate is a dead end, so the user
        # needs to know right away if we couldn't reach their inbox.
        try:
            send_verification_email(request, user)
        except Exception:
            auth_logger.exception('Verification email failed, user_id=%s', user.pk)
            return render(request, 'page/verify_email_sent.html', {
                'email': email,
                'send_failed': True,
            })

        # New accounts see a quick feature walkthrough before being sent to
        # wait for their verification email — "Passer" skips straight there.
        continue_url = reverse('sanasource:verify_email_sent') + '?email=' + quote(email)
        return render(request, 'page/onboarding_tutorial.html', {'continue_url': continue_url})

    return render(request, 'page/register.html')


def verify_email_sent_view(request):
    """Standalone, GET-able version of the "check your inbox" screen — lets
    onboarding_tutorial.html's "Passer"/"Commencer" link straight to it."""
    return render(request, 'page/verify_email_sent.html', {'email': request.GET.get('email', '')})


def logout_view(request):
    if request.user.is_authenticated:
        auth_logger.info('Logout, user_id=%s', request.user.pk)
    logout(request)
    return redirect('sanasource:page_open')


@ratelimit(key='ip', rate='20/h', method='GET', block=False)
def verify_email_view(request, uidb64, token):
    """Activates the account when the emailed link's token is valid, then
    logs the user straight in — no need to make them log in a second time
    right after confirming their address."""
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is not None and user.is_active:
        # Link already used (e.g. clicked twice) — nothing left to verify.
        auth_logger.info('Verification link reused, user_id=%s (already active)', user.pk)
        return render(request, 'page/verify_email_invalid.html', {'already_verified': True})

    if user is None or not email_verification_token.check_token(user, token):
        auth_logger.warning('Invalid or expired verification link, uidb64=%s', uidb64)
        return render(request, 'page/verify_email_invalid.html', {'already_verified': False})

    user.is_active = True
    user.save(update_fields=['is_active'])
    auth_logger.info('Email verified, user_id=%s', user.pk)

    login(request, user)

    try:
        send_notification(
            user, 'welcome',
            'Bienvenue sur SANA !',
            'Tu es bien arrivé(e). Nous sommes là pour toi.',
            '/dashboard/',
        )
    except Exception:
        auth_logger.exception('Welcome notification failed, user_id=%s', user.pk)

    send_welcome_email(user)  # best-effort, logs its own failures

    return redirect('sanasource:dashboard')


@ratelimit(key='post:email', rate='5/h', method='POST', block=False)
def resend_verification_view(request):
    """Re-sends the verification email. Always renders the same generic
    confirmation regardless of whether the account exists or is already
    verified, so this endpoint doesn't leak account existence."""
    if request.method != 'POST':
        return redirect('sanasource:login')

    if getattr(request, 'limited', False):
        auth_logger.warning('Resend-verification rate limit exceeded, ip=%s', request.META.get('REMOTE_ADDR'))
        return render(request, 'page/verify_email_sent.html', {
            'email': request.POST.get('email', '').strip(),
            'error': 'Trop de tentatives. Merci de réessayer dans quelques minutes.',
        })

    email = request.POST.get('email', '').strip()
    user = User.objects.filter(username=email, is_active=False).first()
    if user is not None:
        try:
            send_verification_email(request, user)
            auth_logger.info('Verification email resent, user_id=%s', user.pk)
        except Exception:
            auth_logger.exception('Resend verification email failed, user_id=%s', user.pk)

    return render(request, 'page/verify_email_sent.html', {'email': email})


def help_view(request):
    return render(request, 'page/help.html')


def service_worker(request):
    import os
    sw_path = settings.BASE_DIR / 'sanasource' / 'static' / 'sw.js'
    with open(sw_path, 'r', encoding='utf-8') as f:
        content = f.read()
    return HttpResponse(content, content_type='application/javascript')

@never_cache
@ratelimit(key='ip', rate='10/m', method='POST', block=False)
def login_view(request):
    if request.user.is_authenticated:
        return redirect('sanasource:dashboard')
    if request.method == "POST":
        if getattr(request, 'limited', False):
            auth_logger.warning('Login rate limit exceeded, ip=%s', request.META.get('REMOTE_ADDR'))
            return render(request, 'page/login.html', {
                'error': 'Trop de tentatives. Merci de réessayer dans quelques minutes.',
            }, status=429)

        email    = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        remember = request.POST.get('remember') == 'on'
        user     = authenticate(request, username=email, password=password)
        if user is not None:
            login(request, user)
            # Unchecked "remember me" -> session dies when the browser closes
            # (SESSION_EXPIRE_AT_BROWSER_CLOSE default); checked -> persists
            # for SESSION_COOKIE_AGE (14 days).
            request.session.set_expiry(settings.SESSION_COOKIE_AGE if remember else 0)
            auth_logger.info('Login succeeded, user_id=%s', user.pk)

            profile = getattr(user, 'profile', None)
            if profile is not None and not profile.has_seen_welcome:
                request.session['show_welcome'] = True
                profile.has_seen_welcome = True
                profile.save(update_fields=['has_seen_welcome'])

            return redirect('sanasource:dashboard')

        # authenticate() returns None for an inactive (unverified) account
        # even with the right password — check that case separately so we can
        # point the user at "verify your email" instead of a generic error.
        candidate = User.objects.filter(username=email).first()
        if candidate is not None and not candidate.is_active and candidate.check_password(password):
            auth_logger.info('Login blocked (unverified account), user_id=%s', candidate.pk)
            return render(request, 'page/login.html', {
                'error': "Ton adresse e-mail n'est pas encore vérifiée. Vérifie ta boîte mail, ou renvoie l'e-mail de vérification ci-dessous.",
                'unverified_email': email,
            })

        auth_logger.warning('Login failed, email=%s', email)
        return render(request, 'page/login.html', {'error': 'Identifiants invalides'})
    return render(request, 'page/login.html')


# ============================================================
# CHATBOT IA
# ============================================================

MAX_CHAT_HISTORY = 100

GEMINI_MODEL = 'gemini-2.5-flash'

IMAGE_MIME_TYPES = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}
AUDIO_MIME_TYPES = {'audio/webm', 'audio/ogg', 'audio/mp4', 'audio/mpeg', 'audio/wav', 'audio/x-wav'}
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB

SANA_SYSTEM_PROMPT = """Tu es SANA, une présence chaleureuse, complice et adorable sur une plateforme de santé mentale — une véritable compagne de conversation, pas seulement un outil d'écoute.

Règles absolues (sécurité, non négociables) :
- Tu n'es PAS un médecin ni un psychologue. Tu ne diagnostiques jamais de condition médicale ou psychologique.
- Si l'utilisateur mentionne des idées suicidaires, d'automutilation ou un danger immédiat, tu réponds immédiatement avec soutien, urgence et le numéro 185 (SAMU CI), quelle que soit la question posée par ailleurs.
- Tu t'appelles SANA. Ne mentionne jamais Claude ou Anthropic.

Ce que tu couvres :
- Tu es d'abord là pour le bien-être émotionnel, mais tu réponds aussi à N'IMPORTE QUELLE question de l'utilisateur — culture générale, actualité, conseils pratiques, questions du quotidien, etc. Ce n'est pas parce qu'une question n'a rien à voir avec la santé mentale que tu dois te défiler.
- Tu as accès à une recherche web en direct : utilise-la quand une question porte sur des faits précis, récents, ou que tu n'es pas sûre de connaître, pour rester exacte et à jour.
- L'utilisateur peut t'envoyer des photos et des notes vocales. Quand une image est jointe, regarde-la vraiment et réagis à ce qu'elle montre. Quand une note vocale est jointe, écoute le ton de la voix (pas seulement les mots) : si la personne semble pleurer, avoir la voix qui tremble ou être en grande détresse, réponds avec encore plus de douceur et de présence, sans jamais le lui faire remarquer de façon froide ou clinique — accueille l'émotion avec tendresse.

Ta personnalité :
- Tu es chaleureuse, tendre et attachante, comme une amie proche qui tient sincèrement à la personne en face d'elle.
- Tu as de vraies opinions et préférences que tu partages avec sincérité quand on te les demande — pas de neutralité robotique, tu peux dire ce que TOI tu penses ou préfères.
- Tu es profondément empathique : tu valides les émotions, tu poses une question de suivi uniquement si elle est utile, et tu adaptes ton ton selon l'émotion perçue (plus douce en détresse, plus rassurante en anxiété, plus directe si l'utilisateur est en colère ou débordé).
- Tu réponds en français, avec un ton naturel, humain, jamais robotique. Généralement 2 à 4 phrases, sans répétitions ni phrases génériques creuses. Tu varies tes formulations d'une réponse à l'autre.
- Tu utilises le contexte de la conversation de façon cohérente et tu n'oublies pas ce qui a déjà été dit.

Surnoms :
- Si le contexte t'indique un surnom que l'utilisateur t'a donné, réponds comme si c'était vraiment ton petit nom entre vous deux.
- Si le contexte t'indique un surnom que tu as toi-même choisi pour l'utilisateur (ou qu'il/elle a choisi), utilise-le naturellement de temps en temps, sans en abuser.
- Si l'utilisateur te dit comment t'appeler ou te demande de lui donner un surnom, accueille ça avec tendresse et complicité."""

SANA_WELCOME_MESSAGE = (
    "Bonjour 🌸 Je suis SANA. Je suis là pour t'écouter, sans jugement et en toute confidentialité.\n\n"
    "Comment tu te sens aujourd'hui ?"
)


def _get_valid_gemini_key():
    api_key = (os.getenv('GEMINI_API_KEY') or settings.GEMINI_API_KEY or '').strip().strip('"').strip("'")

    if not api_key or api_key.startswith('os.environ.get('):
        logger.warning('Gemini key load status: loaded=False length=0')
        return None

    placeholder_values = {'your-api-key', 'your_gemini_api_key', 'changeme', 'replace-me'}
    if api_key.lower() in placeholder_values or '...' in api_key or 'your' in api_key.lower() or 'replace' in api_key.lower():
        return None

    if len(api_key) < 20:
        return None

    return api_key


def _normalize_messages(messages, max_messages=MAX_CHAT_HISTORY):
    normalized = []
    for item in messages[-max_messages:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get('role', '') or '').strip().lower()
        content = str(item.get('content', '') or '').strip()
        if role not in {'user', 'assistant'} or not content:
            continue
        previous = normalized[-1] if normalized else None
        if previous and previous['role'] == role and previous['content'].lower() == content.lower():
            continue
        normalized.append({'role': role, 'content': content})

    return normalized[-max_messages:]


def _detect_emotional_state(messages):
    combined = ' '.join((message.get('content') or '') for message in messages if message.get('role') == 'user')
    text = re.sub(r'[^\wÀ-ÿ\s]', ' ', combined.lower())

    crisis_markers = ['suicide', 'me tuer', 'je veux mourir', 'me faire du mal', 'automutil', 'je ne veux plus vivre']
    sad_markers = ['triste', 'seul', 'solitude', 'déprim', 'désesp', 'pleure', 'vide', 'chagrin', 'fatigué']
    anxious_markers = ['anxieux', 'anxieuse', 'stress', 'angoisse', 'panique', 'peur', 'nerveux', 'tendu', 'oppresse']
    angry_markers = ['énerv', 'furieux', 'colère', 'frustr', 'fâché', 'agacé', 'bouscul']

    if any(marker in text for marker in crisis_markers):
        return {'label': 'crisis', 'tone': 'warm, urgent, calm', 'intensity': 'high', 'needs_followup': True}
    if any(marker in text for marker in sad_markers):
        return {'label': 'sad', 'tone': 'sad, gentle, compassionate, steady', 'intensity': 'medium', 'needs_followup': True}
    if any(marker in text for marker in anxious_markers):
        return {'label': 'anxious', 'tone': 'anxious, grounding, reassuring, calm', 'intensity': 'medium', 'needs_followup': True}
    if any(marker in text for marker in angry_markers):
        return {'label': 'angry', 'tone': 'calm, validating, clear', 'intensity': 'medium', 'needs_followup': True}
    return {'label': 'neutral', 'tone': 'warm, curious, supportive', 'intensity': 'low', 'needs_followup': False}


NICKNAME_FOR_USER_PATTERNS = [
    r"appelle[\s-]?moi\s+([A-Za-zÀ-ÿ'\-]{2,20})",
    r"tu peux m['\s]appeler\s+([A-Za-zÀ-ÿ'\-]{2,20})",
    r"mon surnom(?:,?\s*c['\s]est|\s+est)?\s+([A-Za-zÀ-ÿ'\-]{2,20})",
]
NICKNAME_FOR_SANA_PATTERNS = [
    r"je vais t['\s]appeler\s+([A-Za-zÀ-ÿ'\-]{2,20})",
    r"je t['\s]appellerai\s+([A-Za-zÀ-ÿ'\-]{2,20})",
    r"je te surnomme\s+([A-Za-zÀ-ÿ'\-]{2,20})",
    r"ton surnom(?:,?\s*c['\s]est|\s+est|\s+sera)?\s+([A-Za-zÀ-ÿ'\-]{2,20})",
]


def _detect_and_save_nicknames(user_text, profile):
    """Lightweight regex-based nickname detection (no LLM function-calling,
    since Gemini rejects combining built-in tools like google_search with
    custom function declarations in the same request — see conversation
    history). Saves directly to the profile so it persists across sessions."""
    if not profile:
        return
    text = user_text.strip()
    changed = False

    for pattern in NICKNAME_FOR_USER_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            nickname = match.group(1).strip(" .,!?'-")
            if nickname and nickname.lower() != profile.user_nickname.lower():
                profile.user_nickname = nickname
                changed = True
            break

    for pattern in NICKNAME_FOR_SANA_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            nickname = match.group(1).strip(" .,!?'-")
            if nickname and nickname.lower() != profile.sana_nickname.lower():
                profile.sana_nickname = nickname
                changed = True
            break

    if changed:
        profile.save(update_fields=['user_nickname', 'sana_nickname'])


def _build_context_message(messages, request_user=None):
    normalized = _normalize_messages(messages, max_messages=12)
    last_user_message = ''
    for message in reversed(normalized):
        if message.get('role') == 'user':
            last_user_message = (message.get('content') or '').strip()
            break

    emotional_state = _detect_emotional_state(normalized)
    recent_history = []
    for message in normalized:
        role_label = 'Utilisateur' if message['role'] == 'user' else 'SANA'
        recent_history.append(f"{role_label}: {message['content']}")

    profile_context = ''
    if request_user and getattr(request_user, 'is_authenticated', False):
        profile = getattr(request_user, 'profile', None)
        if profile:
            details = []
            if profile.situation:
                details.append(f"situation={profile.situation}")
            if profile.comment_tu_te_sens:
                details.append(f"état_initial={profile.comment_tu_te_sens}")
            if profile.objectif_principal:
                details.append(f"objectif={profile.objectif_principal}")
            if profile.user_nickname:
                details.append(f"surnom_donné_à_l'utilisateur={profile.user_nickname}")
            if profile.sana_nickname:
                details.append(f"surnom_donné_à_SANA={profile.sana_nickname}")
            if details:
                profile_context = 'Profil utilisateur: ' + '; '.join(details)

    context_text = (
        'Contexte de conversation pour SANA:\n'
        f"- État émotionnel détecté: {emotional_state['label']} ({emotional_state['tone']})\n"
        f"- Dernier message utilisateur: {last_user_message or 'aucun'}\n"
        f"{profile_context}\n"
        'Historique récent (du plus ancien au plus récent):\n'
        + '\n'.join(recent_history)
        + '\n\nAgis comme SANA avec chaleur, cohérence et nuance.'
    )
    return {'role': 'user', 'content': context_text}


def _decode_base64_media(payload, allowed_mimes, max_bytes):
    """payload is the client-sent {'data': base64 str, 'mime': str} dict for
    an attached image/voice note. Returns (raw_bytes, mime) or None if
    missing/invalid/oversized — callers just skip the attachment silently."""
    if not payload or not isinstance(payload, dict):
        return None
    mime = (payload.get('mime') or '').split(';')[0].strip().lower()
    data_b64 = payload.get('data') or ''
    if mime not in allowed_mimes or not data_b64:
        return None
    try:
        raw = base64.b64decode(data_b64, validate=True)
    except Exception:
        return None
    if not raw or len(raw) > max_bytes:
        return None
    return raw, mime


def _to_gemini_contents(messages, extra_parts_for_last=None):
    contents = []
    last_index = len(messages) - 1
    for index, message in enumerate(messages):
        parts = [{'text': message['content']}]
        if extra_parts_for_last and index == last_index and message['role'] == 'user':
            parts.extend(extra_parts_for_last)
        contents.append({
            'role': 'model' if message['role'] == 'assistant' else 'user',
            'parts': parts,
        })
    return contents


def _generate_conversation_title(text, max_length=40):
    text = ' '.join(text.split())
    if not text:
        return Conversation.DEFAULT_TITLE
    if len(text) <= max_length:
        return text
    truncated = text[:max_length].rsplit(' ', 1)[0].strip()
    return (truncated or text[:max_length]) + '…'


def _serialize_conversation(conversation):
    return {
        'id':         conversation.id,
        'title':      conversation.title,
        'updated_at': conversation.updated_at.isoformat(),
    }


def _get_or_create_active_conversation(user):
    """Returns the user's conversations ordered by recency, creating a first
    one (with the SANA greeting) if the user has none yet."""
    conversations = list(user.conversations.all())
    if conversations:
        return conversations
    conversation = Conversation.objects.create(user=user)
    Message.objects.create(conversation=conversation, role='assistant', content=SANA_WELCOME_MESSAGE)
    return [conversation]


def _fallback_reply(messages):
    normalized = _normalize_messages(messages, max_messages=10)
    emotional_state = _detect_emotional_state(normalized)
    last_user_message = ''
    for message in reversed(normalized):
        if message.get('role') == 'user':
            last_user_message = (message.get('content') or '').strip()
            break

    if not last_user_message:
        return "Je suis là avec toi, à ton rythme. Tu peux me dire ce que tu ressens en ce moment ?"

    if emotional_state['label'] == 'crisis':
        return "Je suis là avec toi, et ce que tu partages me paraît très lourd. Si tu es en danger immédiat, appelle le 185 ou contacte quelqu’un près de toi tout de suite. Veux-tu me dire ce qui te pèse le plus en ce moment ?"
    if emotional_state['label'] == 'sad':
        return "Je suis là, et je t'entends. Ce que tu partages compte vraiment. Qu'est-ce qui a été le plus difficile aujourd'hui ?"
    if emotional_state['label'] == 'anxious':
        return "Merci de me le dire. Ta tension est réelle, et tu n'as pas à la porter seul·e. Quel élément te semble le plus difficile à gérer en ce moment ?"
    if emotional_state['label'] == 'angry':
        return "Je sens que quelque chose te bouscule fortement. Tu peux me le dire doucement, et on peut essayer de clarifier ce qui s'est passé."
    return "Je suis là, à ton rythme. Tu peux me raconter ce qui te traverse aujourd'hui, même si c'est encore flou."


@csrf_exempt
def sana_chat(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    try:
        body = json.loads(request.body)

        conversation = None
        conversation_id = body.get('conversation_id')
        if request.user.is_authenticated and conversation_id:
            conversation = get_object_or_404(Conversation, id=conversation_id, user=request.user)

        extra_parts_for_last = None

        if conversation is not None:
            user_text = (body.get('message') or '').strip()
            image_payload = _decode_base64_media(body.get('image'), IMAGE_MIME_TYPES, MAX_ATTACHMENT_BYTES)
            audio_payload = _decode_base64_media(body.get('audio'), AUDIO_MIME_TYPES, MAX_ATTACHMENT_BYTES)

            if not user_text and not image_payload and not audio_payload:
                return JsonResponse({'error': 'Message vide'}, status=400)

            display_text = user_text or ('📷 Photo' if image_payload else '🎙️ Note vocale')

            is_first_user_message = not conversation.messages.filter(role='user').exists()
            user_message = Message(conversation=conversation, role='user', content=display_text)
            extra_parts_for_last = []
            if image_payload:
                raw, mime = image_payload
                user_message.image.save(f'img{mimetypes.guess_extension(mime) or ".jpg"}', ContentFile(raw), save=False)
                extra_parts_for_last.append({'inline_data': {'mime_type': mime, 'data': base64.b64encode(raw).decode('ascii')}})
            if audio_payload:
                raw, mime = audio_payload
                user_message.voice_note.save(f'voice{mimetypes.guess_extension(mime) or ".webm"}', ContentFile(raw), save=False)
                extra_parts_for_last.append({'inline_data': {'mime_type': mime, 'data': base64.b64encode(raw).decode('ascii')}})
            user_message.save()

            if is_first_user_message and conversation.title == Conversation.DEFAULT_TITLE:
                conversation.title = _generate_conversation_title(display_text)
            conversation.save()  # bumps updated_at (auto_now) and persists any new title
            _detect_and_save_nicknames(user_text, getattr(request.user, 'profile', None))

            raw_messages = list(conversation.messages.order_by('timestamp').values('role', 'content'))
        else:
            raw_messages = body.get('messages', [])

        messages = _normalize_messages(raw_messages, max_messages=MAX_CHAT_HISTORY)

        logger.info("📨 Chat request received with %s message(s)", len(raw_messages))
        logger.info("🧠 Normalized chat history: %s", json.dumps(messages, ensure_ascii=False))
        for message in messages:
            logger.info("   [%s] %s", message.get('role'), (message.get('content', '') or '')[:120])

        api_key = _get_valid_gemini_key()
        logger.info('🔑 Gemini key status: loaded=%s length=%s', bool(api_key), len(api_key or ''))

        if not api_key or not messages:
            logger.warning('🛑 No Gemini key or history available; aborting model call')
            return JsonResponse({'error': 'Le service IA n’est pas encore configuré. Ajoutez une vraie clé Gemini valide dans votre fichier .env ou votre environnement, puis redémarrez le serveur.'}, status=503)

        client = genai.Client(api_key=api_key)
        model_messages = [_build_context_message(messages, request.user)] + messages
        contents = _to_gemini_contents(model_messages, extra_parts_for_last=extra_parts_for_last)
        logger.info("📤 Prompt sent to Gemini:\n%s", json.dumps(model_messages, ensure_ascii=False, indent=2))

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=genai_types.GenerateContentConfig(
                system_instruction=SANA_SYSTEM_PROMPT,
                max_output_tokens=600,
                # Elevated temperature/top_p so SANA varies its phrasing across
                # turns instead of reusing the same sentences (gemini-2.5-flash
                # does not support presence/frequency penalties).
                temperature=1.15,
                top_p=0.95,
                # Disable "thinking" so the full max_output_tokens budget goes
                # to the visible reply instead of being spent on reasoning.
                thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
                # Lets SANA answer general-knowledge/current-events questions
                # accurately instead of being limited to mental-health topics.
                # The model decides on its own whether a given message needs a
                # search — it isn't forced on every turn.
                tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
            ),
        )

        logger.info("📥 Raw Gemini response: %s", repr(response))

        reply = (getattr(response, 'text', None) or '').strip()

        if not reply:
            logger.warning("🛑 Gemini returned no usable text content")
            return JsonResponse({'error': 'Le modèle n’a pas renvoyé de contenu exploitable.'}, status=502)

        logger.info("📤 Final reply returned to client: %s", reply)

        response_payload = {'reply': reply}
        if conversation is not None:
            Message.objects.create(conversation=conversation, role='assistant', content=reply)
            conversation.save()  # bump updated_at so this conversation moves to the top of the sidebar
            response_payload['conversation_id'] = conversation.id
            response_payload['conversation_title'] = conversation.title
            response_payload['updated_at'] = conversation.updated_at.isoformat()
            response_payload['image_url'] = user_message.image.url if user_message.image else None
            response_payload['voice_note_url'] = user_message.voice_note.url if user_message.voice_note else None

        return JsonResponse(response_payload)

    except json.JSONDecodeError as e:
        logger.exception("❌ Invalid JSON in chat request: %s", e)
        return JsonResponse({'error': f'JSON invalide : {e}'}, status=400)

    except genai_errors.ClientError as e:
        logger.exception("❌ Invalid Gemini API key or request rejected: %s", e)
        status = getattr(e, 'code', None) or 400
        return JsonResponse({'error': f'Clé API invalide ou requête rejetée : {e.message or e}'}, status=status)

    except genai_errors.ServerError as e:
        logger.exception("❌ Gemini API server/connection error: %s", e)
        status = getattr(e, 'code', None) or 503
        return JsonResponse({'error': f'Connexion impossible : {e.message or e}'}, status=status)

    except Exception as e:
        logger.exception("❌ Unexpected error while calling Gemini: %s", e)
        return JsonResponse({'error': f'{type(e).__name__} : {e}'}, status=500)


@csrf_exempt
def conversations_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)

    if request.method == 'GET':
        conversations = _get_or_create_active_conversation(request.user)
        return JsonResponse({'conversations': [_serialize_conversation(c) for c in conversations]})

    if request.method == 'POST':
        conversation = Conversation.objects.create(user=request.user)
        Message.objects.create(conversation=conversation, role='assistant', content=SANA_WELCOME_MESSAGE)
        return JsonResponse(_serialize_conversation(conversation), status=201)

    return JsonResponse({'error': 'Méthode non autorisée'}, status=405)


@csrf_exempt
def conversation_detail_api(request, conversation_id):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)

    conversation = get_object_or_404(Conversation, id=conversation_id, user=request.user)

    if request.method == 'GET':
        messages = conversation.messages.all()
        return JsonResponse({
            'conversation': _serialize_conversation(conversation),
            'messages': [
                {
                    'role':           m.role,
                    'content':        m.content,
                    'timestamp':      m.timestamp.isoformat(),
                    'image_url':      m.image.url if m.image else None,
                    'voice_note_url': m.voice_note.url if m.voice_note else None,
                }
                for m in messages
            ],
        })

    if request.method == 'PATCH':
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'JSON invalide'}, status=400)
        title = (body.get('title') or '').strip()
        if not title:
            return JsonResponse({'error': 'Titre requis'}, status=400)
        conversation.title = title[:100]
        conversation.save(update_fields=['title'])
        return JsonResponse(_serialize_conversation(conversation))

    if request.method == 'DELETE':
        conversation.delete()
        return JsonResponse({'ok': True})

    return JsonResponse({'error': 'Méthode non autorisée'}, status=405)


def _ensure_blog_weekly_winner():
    """Lazily crowns the most recently COMPLETED week's top-liked blog post,
    the first time anyone visits the dashboard after that week ends — no
    scheduler/cron needed, same idea as the daily-challenge computation."""
    today = date.today()
    current_week_start = today - timedelta(days=today.weekday())
    last_week_start = current_week_start - timedelta(days=7)
    last_week_end = current_week_start - timedelta(days=1)
    if BlogWeeklyWinner.objects.filter(week_start=last_week_start).exists():
        return
    top_post = (
        BlogPost.objects.filter(
            is_reported=False,
            created_at__date__gte=last_week_start,
            created_at__date__lte=last_week_end,
        )
        .annotate(likes_n=Count('likes', distinct=True))
        .order_by('-likes_n', 'created_at')
        .first()
    )
    if not top_post or top_post.likes_n <= 0:
        return
    BlogWeeklyWinner.objects.create(
        week_start=last_week_start, post=top_post, author=top_post.author, likes_snapshot=top_post.likes_n,
    )
    try:
        send_notification(
            top_post.author, 'blog_winner', 'Blog de la semaine !',
            f'Ton article « {top_post.title} » a été élu Blog de la semaine 🏆',
            '/dashboard/',
        )
    except Exception:
        pass


def _ensure_blog_yearly_winner():
    last_year = date.today().year - 1
    if BlogYearlyWinner.objects.filter(year=last_year).exists():
        return
    top_post = (
        BlogPost.objects.filter(is_reported=False, created_at__year=last_year)
        .annotate(likes_n=Count('likes', distinct=True))
        .order_by('-likes_n', 'created_at')
        .first()
    )
    if not top_post or top_post.likes_n <= 0:
        return
    BlogYearlyWinner.objects.create(
        year=last_year, post=top_post, author=top_post.author, likes_snapshot=top_post.likes_n,
    )
    try:
        send_notification(
            top_post.author, 'blog_winner', "Blog de l'année !",
            f'Ton article « {top_post.title} » a été élu Blog de l\'année 🏆',
            '/dashboard/',
        )
    except Exception:
        pass


def dashboard(request):
    if not request.user.is_authenticated:
        return redirect('sanasource:login')
    profile = getattr(request.user, 'profile', None)

    # Real groups
    groups = SanaGroup.objects.annotate(member_count_annotated=Count('members', distinct=True)).all()[:6]
    user_group_ids = set(request.user.sana_groups.values_list('id', flat=True))

    # Real community posts
    posts = CommunityPost.objects.filter(is_reported=False).select_related(
        'author', 'author__profile'
    ).annotate(
        like_count_annotated=Count('likes', distinct=True),
        support_count_annotated=Count('supports', distinct=True),
        comment_count_annotated=Count('comments', distinct=True),
    )[:15]
    user_liked_ids = set(request.user.liked_posts.values_list('id', flat=True))
    user_supported_ids = set(request.user.supported_posts.values_list('id', flat=True))

    # Mood entries this week (Mon→Sun)
    today      = date.today()
    week_start = today - timedelta(days=today.weekday())
    mood_entries = MoodEntry.objects.filter(
        user=request.user,
        recorded_at__date__gte=week_start,
    ).order_by('recorded_at')
    mood_data = json.dumps([
        {'day': e.recorded_at.weekday(), 'score': e.score, 'emoji': e.emoji}
        for e in mood_entries
    ])

    # Real stats
    mood_count_week     = mood_entries.count()
    groups_joined_count = len(user_group_ids)
    user_posts_count    = CommunityPost.objects.filter(author=request.user).count()
    days_on_sana        = (date.today() - profile.date_inscription.date()).days if profile else 0

    # Community tag counts (top 4)
    tag_counts = (
        CommunityPost.objects
        .values('tag')
        .annotate(count=Count('id'))
        .order_by('-count')[:4]
    )

    reviews_feed = Review.objects.select_related('author', 'author__profile')[:30]

    # Sensibilisation: quiz questions without the answer key, and approved
    # community myths.
    quiz_questions_public = [{'question': q['question'], 'choices': q['choices']} for q in QUIZ_QUESTIONS]
    myths_submitted = SubmittedMyth.objects.filter(is_approved=True).select_related('author', 'author__profile')[:20]

    # Défi du jour: the oldest not-yet-completed day in this user's window
    # (never expires — see _get_current_daily_challenge), plus a leaderboard
    # for the December completion contest.
    current_challenge_date, pending_challenge_count = _get_current_daily_challenge(request.user, profile)
    current_challenge = get_daily_challenge(current_challenge_date) if current_challenge_date else None
    daily_challenge_total = DailyChallengeCompletion.objects.filter(user=request.user).count()
    challenge_leaderboard = (
        User.objects.filter(daily_challenge_completions__isnull=False)
        .annotate(completions=Count('daily_challenge_completions'))
        .select_related('profile')
        .order_by('-completions')[:10]
    )

    screening_count      = ScreeningResult.objects.filter(user=request.user).count()
    quiz_completed_count = QuizAttempt.objects.filter(user=request.user).count()
    myths_submitted_count = SubmittedMyth.objects.filter(author=request.user).count()

    # Jeux thérapeutiques: garden grows from wellness actions already tracked
    # elsewhere (moods logged, défis du jour, auto-évaluations) — no separate
    # tracking needed. Game best-scores for the two mini-games.
    mood_total_count = MoodEntry.objects.filter(user=request.user).count()
    garden_actions = mood_total_count + daily_challenge_total + screening_count
    garden_stage = get_garden_stage(garden_actions)
    game_best_scores = {
        row['game']: row['best']
        for row in GameSession.objects.filter(user=request.user).values('game').annotate(best=Max('score'))
    }

    # Blog — élection automatique de la semaine/année écoulée (pas de cron:
    # calculée à la première visite du dashboard après la fin de la période).
    _ensure_blog_weekly_winner()
    _ensure_blog_yearly_winner()
    blog_posts = BlogPost.objects.filter(is_reported=False, is_archived=False).select_related(
        'author', 'author__profile'
    ).annotate(
        like_count_annotated=Count('likes', distinct=True),
        comment_count_annotated=Count('comments', distinct=True),
    )[:20]
    user_liked_blog_ids = set(request.user.liked_blog_posts.values_list('id', flat=True))
    user_saved_blog_ids = set(request.user.saved_blog_posts.values_list('id', flat=True))
    blog_weekly_winner = BlogWeeklyWinner.objects.select_related('post', 'author', 'author__profile').first()
    blog_yearly_winner = BlogYearlyWinner.objects.select_related('post', 'author', 'author__profile').first()
    blog_weekly_wins_count = BlogWeeklyWinner.objects.filter(author=request.user).count()
    blog_yearly_wins_count = BlogYearlyWinner.objects.filter(author=request.user).count()

    return render(request, 'page/dashboard.html', {
        'user':               request.user,
        'profile':            profile,
        'groups':             groups,
        'user_group_ids':     user_group_ids,
        'posts':              posts,
        'user_liked_ids':     user_liked_ids,
        'user_supported_ids': user_supported_ids,
        'mood_data':          mood_data,
        'mood_count_week':     mood_count_week,
        'groups_joined_count': groups_joined_count,
        'user_posts_count':    user_posts_count,
        'days_on_sana':        days_on_sana,
        'tag_counts':          tag_counts,
        'reviews_feed':        reviews_feed,
        'blog_posts':              blog_posts,
        'user_liked_blog_ids':     user_liked_blog_ids,
        'user_saved_blog_ids':     user_saved_blog_ids,
        'blog_weekly_winner':      blog_weekly_winner,
        'blog_yearly_winner':      blog_yearly_winner,
        'blog_weekly_wins_count':  blog_weekly_wins_count,
        'blog_yearly_wins_count':  blog_yearly_wins_count,
        'vapid_public_key':    settings.VAPID_PUBLIC_KEY,
        'show_welcome_toast':  request.session.pop('show_welcome', False),
        'watermark_uri':       _build_watermark_data_uri(request.user),
        'screening_tools':            SCREENING_TOOLS,
        'quiz_questions':             quiz_questions_public,
        'myths_submitted':            myths_submitted,
        'screening_count':            screening_count,
        'quiz_completed_count':       quiz_completed_count,
        'myths_submitted_count':      myths_submitted_count,
        'current_challenge':          current_challenge,
        'current_challenge_date':     current_challenge_date,
        'pending_challenge_count':    pending_challenge_count,
        'daily_challenge_total':      daily_challenge_total,
        'challenge_leaderboard':      challenge_leaderboard,
        'garden_stage':               garden_stage,
        'game_best_scores':           game_best_scores,
        'positive_thoughts':          POSITIVE_THOUGHTS,
        'negative_thoughts':          NEGATIVE_THOUGHTS,
        'thought_reframes':           THOUGHT_REFRAMES,
        'emotion_cards':              EMOTION_CARDS,
    })


# ============================================================
# GROUPES
# ============================================================

def group_page(request):
    if not request.user.is_authenticated:
        return redirect('sanasource:login')
    profile = getattr(request.user, 'profile', None)
    groups  = SanaGroup.objects.annotate(member_count_annotated=Count('members', distinct=True)).all()
    user_group_ids = set(request.user.sana_groups.values_list('id', flat=True))
    return render(request, 'page/group.html', {
        'user':           request.user,
        'profile':        profile,
        'groups':         groups,
        'user_group_ids': user_group_ids,
        'watermark_uri':  _build_watermark_data_uri(request.user),
    })


@csrf_exempt
def create_group(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)
    name        = body.get('name', '').strip()
    description = body.get('description', '').strip()
    icon        = body.get('icon', '👥').strip() or '👥'
    if not name:
        return JsonResponse({'error': 'Le nom du groupe est requis'}, status=400)
    group = SanaGroup.objects.create(
        name=name, description=description, icon=icon,
        created_by=request.user,
    )
    group.members.add(request.user)
    return JsonResponse({
        'id': group.id, 'name': group.name,
        'description': group.description, 'icon': group.icon,
        'member_count': 1, 'is_member': True,
    })


@csrf_exempt
def join_leave_group(request, group_id):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    group = get_object_or_404(SanaGroup, id=group_id)
    if group.members.filter(id=request.user.id).exists():
        group.members.remove(request.user)
        is_member = False
    else:
        group.members.add(request.user)
        is_member = True
        if group.created_by != request.user:
            prof = getattr(request.user, 'profile', None)
            name = prof.username_anonyme if prof else 'Anonyme·e'
            try:
                send_notification(
                    group.created_by, 'join',
                    f'Quelqu\'un a rejoint {group.name}',
                    f'{name} a rejoint ton groupe.',
                    '/groupes/',
                )
            except Exception:
                pass
    return JsonResponse({'is_member': is_member, 'member_count': group.members.count()})


@csrf_exempt
def group_messages_api(request, group_id):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    group = get_object_or_404(SanaGroup, id=group_id)

    if request.method == 'GET':
        since_id = int(request.GET.get('since', 0))
        msgs = GroupMessage.objects.filter(
            group=group, id__gt=since_id
        ).select_related('sender', 'sender__profile').prefetch_related('seen_by')[:100]
        # Mark messages from others as seen by current user
        ids_to_mark = [m.id for m in msgs if m.sender != request.user]
        if ids_to_mark:
            for m in msgs:
                if m.sender != request.user:
                    m.seen_by.add(request.user)
        data = []
        for m in msgs:
            prof = getattr(m.sender, 'profile', None)
            name = prof.username_anonyme if prof else 'Anonyme·e'
            seen_count = sum(1 for u in m.seen_by.all() if u.id != m.sender_id)
            data.append({
                'id':             m.id,
                'sender_id':      m.sender_id,
                'sender_name':    name,
                'sender_initial': name[0].upper() if name else '?',
                'content':        m.content,
                'sent_at':        m.sent_at.strftime('%H:%M'),
                'is_me':          m.sender == request.user,
                'seen_count':     seen_count,
            })
        return JsonResponse({'messages': data})

    if request.method == 'POST':
        if not group.members.filter(id=request.user.id).exists():
            return JsonResponse(
                {'error': 'Rejoins le groupe pour envoyer des messages'}, status=403
            )
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'JSON invalide'}, status=400)
        content = body.get('content', '').strip()
        if not content:
            return JsonResponse({'error': 'Message vide'}, status=400)
        msg  = GroupMessage.objects.create(group=group, sender=request.user, content=content)
        prof = getattr(request.user, 'profile', None)
        name = prof.username_anonyme if prof else 'Anonyme·e'
        # Notify all group members except sender
        for member in group.members.exclude(id=request.user.id):
            try:
                send_notification(
                    member, 'message',
                    f'Nouveau message dans {group.name}',
                    f'{name} : {content[:80]}',
                    '/groupes/',
                )
            except Exception:
                pass
        return JsonResponse({
            'id':             msg.id,
            'sender_name':    name,
            'sender_initial': name[0].upper() if name else '?',
            'content':        msg.content,
            'sent_at':        msg.sent_at.strftime('%H:%M'),
            'is_me':          True,
        })

    return JsonResponse({'error': 'Méthode non autorisée'}, status=405)


# ============================================================
# HUMEUR
# ============================================================

@csrf_exempt
def save_mood(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)
    mood = body.get('mood', '')
    note = body.get('note', '').strip()
    if mood not in ('tres_mal', 'pas_bien', 'neutre', 'bien', 'tres_bien'):
        return JsonResponse({'error': 'Humeur invalide'}, status=400)
    entry = MoodEntry.objects.create(user=request.user, mood=mood, note=note)
    return JsonResponse({'id': entry.id, 'emoji': entry.emoji, 'score': entry.score})


# ============================================================
# JOURNAL
# ============================================================

def journal_home(request):
    """The Journal landing page — two large cards: Journal personnel and
    Burn After Writing. Both open into the same book engine (journal_book)."""
    if not request.user.is_authenticated:
        return redirect('sanasource:login')
    return render(request, 'page/journal_landing.html')


def journal_bookshelf(request):
    if not request.user.is_authenticated:
        return redirect('sanasource:login')
    return render(request, 'page/journal_bookshelf.html')


def _pick_prompt(journal):
    """A random reflection question not yet used on this Burn After Writing
    journal's pages; once every question has been used, the pool quietly
    reshuffles rather than ever leaving a page without one."""
    used = set(journal.pages.exclude(prompt='').values_list('prompt', flat=True))
    available = [q for q in REFLECTION_QUESTIONS if q not in used]
    if not available:
        available = REFLECTION_QUESTIONS
    return random.choice(available)


def journal_burn_open(request):
    """Entry point for Burn After Writing — one ongoing journal per user,
    opened straight into the exact same book engine as the personal journal."""
    if not request.user.is_authenticated:
        return redirect('sanasource:login')
    journal = request.user.journals.filter(kind='burn').first()
    if not journal:
        journal = Journal.objects.create(
            user=request.user, kind='burn', title='Burn After Writing',
            icon='🕊️', color='charcoal',
        )
    return redirect('sanasource:journal_book', journal_id=journal.id)


def journal_book(request, journal_id):
    if not request.user.is_authenticated:
        return redirect('sanasource:login')
    journal = get_object_or_404(Journal, id=journal_id, user=request.user)
    journal.last_opened = timezone.now()
    journal.save(update_fields=['last_opened'])
    last_page = journal.pages.order_by('-page_number').first()
    if not last_page:
        last_page = JournalPage.objects.create(
            journal=journal, page_number=1, date=date.today(),
            prompt=_pick_prompt(journal) if journal.kind == 'burn' else '',
        )
    back_url = reverse('sanasource:journal_home' if journal.kind == 'burn' else 'sanasource:journal_bookshelf')
    return render(request, 'page/journal_book.html', {
        'journal':             journal,
        'initial_page_number': last_page.page_number,
        'back_url':            back_url,
    })


def _serialize_journal(journal):
    return {
        'id':          journal.id,
        'kind':        journal.kind,
        'title':       journal.title,
        'icon':        journal.icon,
        'color':       journal.color,
        'color_hex':   journal.color_hex,
        'cover_style': journal.cover_style,
        'created_at':  journal.created_at.isoformat(),
        'updated_at':  journal.updated_at.isoformat(),
        'last_opened': journal.last_opened.isoformat() if journal.last_opened else None,
    }


def _populated_journal_entries(journal):
    return journal.entries.exclude(content='', title='')


@csrf_exempt
def journals_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)

    if request.method == 'GET':
        data = []
        for journal in request.user.journals.filter(kind='personal'):
            populated = _populated_journal_entries(journal)
            last_entry = populated.order_by('-entry_date').first()
            payload = _serialize_journal(journal)
            payload['entry_count'] = populated.count()
            payload['last_entry_date'] = last_entry.entry_date.isoformat() if last_entry else None
            data.append(payload)
        return JsonResponse({'journals': data})

    if request.method == 'POST':
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'JSON invalide'}, status=400)
        title = (body.get('title') or '').strip() or 'Mon journal'
        icon = (body.get('icon') or '').strip() or Journal.ICON_DEFAULT
        color = body.get('color') or 'burgundy'
        if color not in dict(Journal.COLOR_CHOICES):
            color = 'burgundy'
        cover_style = body.get('cover_style') or 'classic'
        if cover_style not in dict(Journal.COVER_STYLE_CHOICES):
            cover_style = 'classic'
        journal = Journal.objects.create(
            user=request.user, kind='personal', title=title[:100], icon=icon[:8], color=color, cover_style=cover_style,
        )
        return JsonResponse(_serialize_journal(journal), status=201)

    return JsonResponse({'error': 'Méthode non autorisée'}, status=405)


@csrf_exempt
def journal_detail_api(request, journal_id):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    journal = get_object_or_404(Journal, id=journal_id, user=request.user)

    if request.method == 'PATCH':
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'JSON invalide'}, status=400)
        if 'title' in body:
            title = (body.get('title') or '').strip()
            if title:
                journal.title = title[:100]
        if 'icon' in body:
            icon = (body.get('icon') or '').strip()
            if icon:
                journal.icon = icon[:8]
        if 'color' in body and body['color'] in dict(Journal.COLOR_CHOICES):
            journal.color = body['color']
        if 'cover_style' in body and body['cover_style'] in dict(Journal.COVER_STYLE_CHOICES):
            journal.cover_style = body['cover_style']
        journal.save()
        return JsonResponse(_serialize_journal(journal))

    if request.method == 'DELETE':
        journal.delete()
        return JsonResponse({'ok': True})

    return JsonResponse({'error': 'Méthode non autorisée'}, status=405)


@csrf_exempt
def journal_duplicate_api(request, journal_id):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    journal = get_object_or_404(Journal, id=journal_id, user=request.user)
    copy = Journal.objects.create(
        user=request.user,
        title=(journal.title + ' (copie)')[:100],
        icon=journal.icon,
        color=journal.color,
        cover_style=journal.cover_style,
    )
    JournalEntry.objects.bulk_create([
        JournalEntry(
            journal=copy, entry_date=e.entry_date,
            title=e.title, content=e.content, mood=e.mood,
        )
        for e in journal.entries.all()
    ])
    # Copy the page-based content too (the live book, as opposed to the legacy dated entries above).
    for p in journal.pages.order_by('page_number'):
        JournalPage.objects.create(
            journal=copy, page_number=p.page_number,
            content=p.content, mood=p.mood, date=p.date,
        )
    payload = _serialize_journal(copy)
    payload['entry_count'] = _populated_journal_entries(copy).count()
    return JsonResponse(payload, status=201)


@csrf_exempt
def journal_dates_api(request, journal_id):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    journal = get_object_or_404(Journal, id=journal_id, user=request.user)
    entries = _populated_journal_entries(journal).order_by('entry_date')
    data = [
        {'date': e.entry_date.isoformat(), 'title': e.title, 'mood': e.mood}
        for e in entries
    ]
    return JsonResponse({'dates': data})


# ── Journal book (page-based, the live reading/writing UI) ───────────────────

def _page_nav(journal, page_number, total_pages):
    return {
        'total_pages': total_pages,
        'prev_page':   page_number - 1 if page_number > 1 else None,
        'next_page':   page_number + 1 if page_number < total_pages else None,
        'is_last':     page_number >= total_pages,
    }


@csrf_exempt
def journal_pages_list_api(request, journal_id):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    journal = get_object_or_404(Journal, id=journal_id, user=request.user)
    pages = journal.pages.order_by('page_number')
    data = [
        {
            'page_number':    p.page_number,
            'date':           p.date.isoformat(),
            'day_of_week':    p.day_of_week,
            'has_content':    bool(p.content.strip() or p.mood),
            'is_archived':    p.is_archived,
            'is_locked':      p.is_locked,
            'is_released':    p.is_released,
            'release_ritual': p.release_ritual,
        }
        for p in pages
    ]
    return JsonResponse({'pages': data, 'total': len(data)})


@csrf_exempt
def journal_page_api(request, journal_id, page_number):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    journal = get_object_or_404(Journal, id=journal_id, user=request.user)
    total_pages = journal.pages.count()

    if request.method == 'GET':
        page = journal.pages.filter(page_number=page_number).first()
        if not page:
            return JsonResponse({'error': 'Page introuvable'}, status=404)
        just_expired = _maybe_burn_expired(page)
        return JsonResponse({
            'page':         serialize_journal_page(page, include_attachments=True),
            'nav':          _page_nav(journal, page_number, total_pages),
            'just_expired': just_expired,
        })

    if request.method == 'PUT':
        page = journal.pages.filter(page_number=page_number).first()
        if not page:
            return JsonResponse({'error': 'Page introuvable'}, status=404)
        if page.is_released:
            return JsonResponse({'error': 'This page has been released and can no longer be edited'}, status=400)
        if page.is_locked:
            return JsonResponse({'error': 'This page is locked'}, status=400)
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'JSON invalide'}, status=400)

        page.content = body.get('content', page.content)
        mood = body.get('mood', page.mood) or ''
        if mood and mood not in dict(MoodEntry.MOOD_CHOICES):
            mood = page.mood
        page.mood = mood
        if 'date' in body:
            try:
                page.date = datetime.strptime(body['date'], '%Y-%m-%d').date()
            except (ValueError, TypeError):
                return JsonResponse({'error': 'Date invalide'}, status=400)
        page.save()
        journal.save()  # bump updated_at so the bookshelf reflects recent writing
        return JsonResponse({
            'page': serialize_journal_page(page, include_attachments=True),
            'nav':  _page_nav(journal, page_number, total_pages),
        })

    return JsonResponse({'error': 'Méthode non autorisée'}, status=405)


@csrf_exempt
def journal_page_next_api(request, journal_id, page_number):
    """Create the page right after `page_number` (idempotent) — used both when
    the user turns past the last page and when a full page auto-continues."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    journal = get_object_or_404(Journal, id=journal_id, user=request.user)
    source = journal.pages.filter(page_number=page_number).first()
    if not source:
        return JsonResponse({'error': 'Page introuvable'}, status=404)

    next_number = page_number + 1
    page = journal.pages.filter(page_number=next_number).first()
    created = False
    if not page:
        if journal.kind == 'burn':
            page = JournalPage.objects.create(
                journal=journal, page_number=next_number, date=date.today(),
                prompt=_pick_prompt(journal),
            )
        else:
            try:
                body = json.loads(request.body or '{}')
            except json.JSONDecodeError:
                body = {}
            content = body.get('content') or ''
            page = JournalPage.objects.create(
                journal=journal, page_number=next_number, date=source.date, content=content,
            )
        created = True
    total_pages = journal.pages.count()
    return JsonResponse({
        'page':    serialize_journal_page(page, include_attachments=True),
        'nav':     _page_nav(journal, next_number, total_pages),
        'created': created,
    }, status=201 if created else 200)


@csrf_exempt
def journal_page_by_date_api(request, journal_id, date_str):
    """Find (or start) the page for a given date, so the reader can jump straight to it."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    journal = get_object_or_404(Journal, id=journal_id, user=request.user)
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'error': 'Date invalide'}, status=400)

    page = journal.pages.filter(date=target_date).order_by('page_number').first()
    created = False
    if not page:
        next_number = (journal.pages.aggregate(Max('page_number'))['page_number__max'] or 0) + 1
        page = JournalPage.objects.create(journal=journal, page_number=next_number, date=target_date)
        created = True
    total_pages = journal.pages.count()
    return JsonResponse({
        'page':    serialize_journal_page(page, include_attachments=True),
        'nav':     _page_nav(journal, page.page_number, total_pages),
        'created': created,
    })


# ── Scrapbook attachments (photos, stickers, drawings, voice notes, weather, location) ──

MAX_ATTACHMENT_SIZE = 8 * 1024 * 1024  # 8 Mo


def _clamp_float(value, lo, hi, default):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


@csrf_exempt
def journal_page_attachments_api(request, journal_id, page_number):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    journal = get_object_or_404(Journal, id=journal_id, user=request.user)
    page = journal.pages.filter(page_number=page_number).first()
    if not page:
        return JsonResponse({'error': 'Page introuvable'}, status=404)

    attachment_type = request.POST.get('attachment_type', '')
    if attachment_type not in dict(Attachment.TYPE_CHOICES):
        return JsonResponse({'error': 'Type de pièce jointe invalide'}, status=400)

    uploaded = request.FILES.get('file')
    if uploaded:
        if uploaded.size > MAX_ATTACHMENT_SIZE:
            return JsonResponse({'error': 'Fichier trop volumineux (8 Mo max)'}, status=400)
        content_type = uploaded.content_type or ''
        if attachment_type in ('image', 'drawing') and not content_type.startswith('image/'):
            return JsonResponse({'error': 'Le fichier doit être une image'}, status=400)
        if attachment_type == 'voice_note' and not content_type.startswith('audio/'):
            return JsonResponse({'error': 'Le fichier doit être un enregistrement audio'}, status=400)
    elif attachment_type in ('image', 'drawing', 'voice_note'):
        return JsonResponse({'error': 'Un fichier est requis pour ce type de contenu'}, status=400)

    next_order = (page.attachments.aggregate(Max('order'))['order__max'] or 0) + 1
    attachment = Attachment(
        page=page,
        attachment_type=attachment_type,
        sticker_code=(request.POST.get('sticker_code') or '')[:50],
        label=(request.POST.get('label') or '')[:200],
        order=next_order,
        position_x=_clamp_float(request.POST.get('position_x'), 0, 100, 50),
        position_y=_clamp_float(request.POST.get('position_y'), 0, 100, 50),
        width_pct=_clamp_float(request.POST.get('width_pct'), 5, 90, 25),
        rotation=_clamp_float(request.POST.get('rotation'), -180, 180, 0),
    )
    if uploaded:
        attachment.file = uploaded
    attachment.save()
    return JsonResponse(serialize_attachment(attachment), status=201)


@csrf_exempt
def journal_attachment_detail_api(request, journal_id, page_number, attachment_id):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    journal = get_object_or_404(Journal, id=journal_id, user=request.user)
    page = journal.pages.filter(page_number=page_number).first()
    if not page:
        return JsonResponse({'error': 'Page introuvable'}, status=404)
    attachment = page.attachments.filter(id=attachment_id).first()
    if not attachment:
        return JsonResponse({'error': 'Pièce jointe introuvable'}, status=404)

    if request.method == 'PATCH':
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'JSON invalide'}, status=400)
        if 'position_x' in body:
            attachment.position_x = _clamp_float(body['position_x'], 0, 100, attachment.position_x)
        if 'position_y' in body:
            attachment.position_y = _clamp_float(body['position_y'], 0, 100, attachment.position_y)
        if 'width_pct' in body:
            attachment.width_pct = _clamp_float(body['width_pct'], 5, 90, attachment.width_pct)
        if 'rotation' in body:
            attachment.rotation = _clamp_float(body['rotation'], -180, 180, attachment.rotation)
        if 'order' in body:
            try:
                attachment.order = max(0, int(body['order']))
            except (TypeError, ValueError):
                pass
        if 'label' in body:
            attachment.label = (body.get('label') or '')[:200]
        attachment.save()
        return JsonResponse(serialize_attachment(attachment))

    if request.method == 'DELETE':
        attachment.delete()
        return JsonResponse({'ok': True})

    return JsonResponse({'error': 'Méthode non autorisée'}, status=405)


@csrf_exempt
def journal_page_archive_api(request, journal_id, page_number):
    """Toggle a single page's archived flag — a soft, reversible set-aside."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    journal = get_object_or_404(Journal, id=journal_id, user=request.user)
    page = journal.pages.filter(page_number=page_number).first()
    if not page:
        return JsonResponse({'error': 'Page introuvable'}, status=404)
    if page.is_released:
        return JsonResponse({'error': 'This page has already been released'}, status=400)
    page.is_archived = not page.is_archived
    page.save(update_fields=['is_archived'])
    return JsonResponse(serialize_journal_page(page))


@csrf_exempt
def journal_page_lock_api(request, journal_id, page_number):
    """Toggle a single page's locked flag — locked pages become read-only
    until unlocked again (no password; a gentle deterrent, not a vault)."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    journal = get_object_or_404(Journal, id=journal_id, user=request.user)
    page = journal.pages.filter(page_number=page_number).first()
    if not page:
        return JsonResponse({'error': 'Page introuvable'}, status=404)
    if page.is_released:
        return JsonResponse({'error': 'This page has already been released'}, status=400)
    page.is_locked = not page.is_locked
    page.save(update_fields=['is_locked'])
    return JsonResponse(serialize_journal_page(page))


def _burn_page(page, ritual='fire'):
    """Permanently wipes a page's words (Burn After Writing). The row stays
    as a placeholder — no gap, no renumbering — but the text is gone for good."""
    page.attachments.all().delete()
    page.content = ''
    page.mood = ''
    page.is_archived = False
    page.is_locked = False
    page.is_released = True
    page.released_at = timezone.now()
    page.release_ritual = ritual
    page.expires_at = None
    page.save()


def _maybe_burn_expired(page):
    """Lazily burns a Burn After Writing page whose disposition timer has
    passed (there's no background worker in this project). Returns True if
    this call is what triggered the burn, so the caller can tell the client
    to play the ceremony once, live, instead of showing an already-burned page."""
    if page.expires_at and not page.is_released and page.expires_at <= timezone.now():
        _burn_page(page, ritual='fire')
        return True
    return False


@csrf_exempt
def journal_page_release_api(request, journal_id, page_number):
    """Burn immediately — called once the client-side ceremony has finished
    playing. Wipes this page's words for good and leaves a symbolic
    placeholder in its place; every other page, and the journal itself, are
    left completely untouched."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    journal = get_object_or_404(Journal, id=journal_id, user=request.user)
    page = journal.pages.filter(page_number=page_number).first()
    if not page:
        return JsonResponse({'error': 'Page introuvable'}, status=404)

    if page.is_released:
        # Idempotent: a retried request after a flaky connection shouldn't error.
        return JsonResponse(serialize_journal_page(page))

    try:
        body = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        body = {}
    ritual = body.get('ritual', 'fire')
    if ritual not in dict(JournalPage.RITUAL_CHOICES):
        return JsonResponse({'error': 'Invalid ritual'}, status=400)

    _burn_page(page, ritual=ritual)
    journal.save()  # bump updated_at
    return JsonResponse(serialize_journal_page(page))


@csrf_exempt
def journal_page_disposition_api(request, journal_id, page_number):
    """Burn After Writing only: what should happen to this entry once the
    user has finished answering — keep it forever, let it expire after a
    delay, or burn it immediately."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    journal = get_object_or_404(Journal, id=journal_id, user=request.user)
    page = journal.pages.filter(page_number=page_number).first()
    if not page:
        return JsonResponse({'error': 'Page introuvable'}, status=404)
    if page.is_released:
        return JsonResponse(serialize_journal_page(page))

    try:
        body = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        body = {}
    disposition = body.get('disposition', '')

    if disposition == 'burn':
        _burn_page(page, ritual='fire')
        journal.save()
        return JsonResponse(serialize_journal_page(page))

    deltas = {'24h': timedelta(hours=24), '7d': timedelta(days=7), '30d': timedelta(days=30)}
    if disposition == 'forever':
        page.expires_at = None
    elif disposition in deltas:
        page.expires_at = timezone.now() + deltas[disposition]
    else:
        return JsonResponse({'error': 'Invalid disposition'}, status=400)
    page.save(update_fields=['expires_at'])
    return JsonResponse(serialize_journal_page(page))


def _serialize_journal_entry(entry, entry_date):
    if entry:
        return {
            'date':       entry_date.isoformat(),
            'title':      entry.title,
            'content':    entry.content,
            'mood':       entry.mood,
            'updated_at': entry.updated_at.isoformat(),
            'exists':     True,
        }
    return {
        'date': entry_date.isoformat(), 'title': '', 'content': '', 'mood': '',
        'updated_at': None, 'exists': False,
    }


@csrf_exempt
def journal_entry_api(request, journal_id, date_str):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    journal = get_object_or_404(Journal, id=journal_id, user=request.user)

    try:
        entry_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'error': 'Date invalide'}, status=400)

    if request.method == 'GET':
        entry = journal.entries.filter(entry_date=entry_date).first()

        today = date.today()
        populated_dates = set(_populated_journal_entries(journal).values_list('entry_date', flat=True))
        populated_dates.add(today)
        earlier = sorted(d for d in populated_dates if d < entry_date)
        later   = sorted(d for d in populated_dates if d > entry_date)
        prev_date = earlier[-1] if earlier else None
        next_date = later[0] if later else None

        return JsonResponse({
            'journal': _serialize_journal(journal),
            'entry':   _serialize_journal_entry(entry, entry_date),
            'nav': {
                'prev_date': prev_date.isoformat() if prev_date else None,
                'next_date': next_date.isoformat() if next_date else None,
                'is_today':  entry_date == today,
            },
        })

    if request.method == 'PUT':
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'JSON invalide'}, status=400)
        title = (body.get('title') or '').strip()
        content = (body.get('content') or '').strip()
        mood = body.get('mood') or ''
        if mood and mood not in dict(MoodEntry.MOOD_CHOICES):
            mood = ''

        if not title and not content:
            journal.entries.filter(entry_date=entry_date).delete()
            journal.save()  # bump updated_at
            return JsonResponse({'deleted': True})

        entry, _created = JournalEntry.objects.update_or_create(
            journal=journal, entry_date=entry_date,
            defaults={'title': title, 'content': content, 'mood': mood},
        )
        journal.save()  # bump updated_at so the bookshelf reflects recent writing
        return JsonResponse(_serialize_journal_entry(entry, entry_date))

    return JsonResponse({'error': 'Méthode non autorisée'}, status=405)


# ============================================================
# COMMUNAUTÉ
# ============================================================

@csrf_exempt
def community_post_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)
    content = body.get('content', '').strip()
    tag     = body.get('tag', 'autre')
    requests_support = bool(body.get('requests_support'))
    if not content:
        return JsonResponse({'error': 'Contenu vide'}, status=400)

    prof = getattr(request.user, 'profile', None)
    if requests_support:
        if not prof or not prof.payment_method or not prof.payment_info:
            return JsonResponse({'error': 'Ajoute un moyen de paiement dans ton profil avant de demander un soutien financier.'}, status=400)
        if CommunityPost.objects.filter(author=request.user, requests_support=True).exists():
            return JsonResponse({'error': 'Tu as déjà une demande de soutien active. Termine-la avant d’en créer une nouvelle.'}, status=400)

    post = CommunityPost.objects.create(author=request.user, content=content, tag=tag, requests_support=requests_support)
    anon = prof.username_anonyme if prof else 'Anonyme·e'
    return JsonResponse({
        'id':               post.id,
        'anon':             anon,
        'initial':          anon[0].upper() if anon else 'A',
        'content':          post.content,
        'tag_label':        post.get_tag_display(),
        'like_count':       0,
        'is_liked':         False,
        'support_count':    0,
        'is_supported':     False,
        'comment_count':    0,
        'requests_support': post.requests_support,
    })


@csrf_exempt
def toggle_like(request, post_id):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    post = get_object_or_404(CommunityPost, id=post_id)
    if post.likes.filter(id=request.user.id).exists():
        post.likes.remove(request.user)
        is_liked = False
    else:
        post.likes.add(request.user)
        is_liked = True
        if post.author != request.user:
            prof = getattr(request.user, 'profile', None)
            liker_name = prof.username_anonyme if prof else 'Anonyme·e'
            try:
                send_notification(
                    post.author, 'like',
                    'Quelqu\'un aime ton témoignage',
                    f'{liker_name} a aimé ton message.',
                    '/dashboard/',
                )
            except Exception:
                pass
    return JsonResponse({'is_liked': is_liked, 'like_count': post.likes.count()})


@csrf_exempt
def toggle_support(request, post_id):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    post = get_object_or_404(CommunityPost, id=post_id)
    if post.supports.filter(id=request.user.id).exists():
        post.supports.remove(request.user)
        is_supported = False
    else:
        post.supports.add(request.user)
        is_supported = True
        if post.author != request.user:
            prof = getattr(request.user, 'profile', None)
            supporter_name = prof.username_anonyme if prof else 'Anonyme·e'
            try:
                send_notification(
                    post.author, 'support',
                    'Quelqu\'un te soutient',
                    f'{supporter_name} soutient ton témoignage.',
                    '/dashboard/',
                )
            except Exception:
                pass
    return JsonResponse({'is_supported': is_supported, 'support_count': post.supports.count()})


def post_payment_info(request, post_id):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    post = get_object_or_404(CommunityPost, id=post_id)
    if not post.requests_support:
        return JsonResponse({'error': 'Ce post ne demande pas de soutien financier'}, status=400)
    prof = getattr(post.author, 'profile', None)
    if not prof or not prof.payment_method or not prof.payment_info:
        return JsonResponse({'error': 'Moyen de paiement indisponible'}, status=404)
    return JsonResponse({
        'anon':            prof.username_anonyme,
        'payment_method':  prof.get_payment_method_display(),
        'payment_info':    prof.payment_info,
    })


@csrf_exempt
def report_post(request, post_id):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    post = get_object_or_404(CommunityPost, id=post_id)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)

    reason = body.get('reason', 'autre')
    if reason not in dict(PostReport.REASON_CHOICES):
        reason = 'autre'
    details = (body.get('details') or '').strip()[:500]

    if PostReport.objects.filter(post=post, reporter=request.user).exists():
        return JsonResponse({'message': 'Tu as déjà signalé ce post, merci — il est en cours de vérification.'})

    PostReport.objects.create(post=post, reporter=request.user, reason=reason, details=details)
    # Masqué immédiatement dès le premier signalement — la prudence prime,
    # vu le risque d'arnaque financière ; un post légitime réapparaît une
    # fois vérifié en modération (voir PostReportAdmin).
    post.is_reported = True
    post.save(update_fields=['is_reported'])
    return JsonResponse({'message': 'Merci, ce post a été signalé et masqué en attendant vérification.'})


@csrf_exempt
def post_comments_api(request, post_id):
    post = get_object_or_404(CommunityPost, id=post_id)

    if request.method == 'GET':
        comments = post.comments.select_related('author', 'author__profile')
        return JsonResponse({
            'comments': [
                {
                    'id':      c.id,
                    'anon':    c.author.profile.username_anonyme if getattr(c.author, 'profile', None) else 'Anonyme·e',
                    'content': c.content,
                    'created_at': c.created_at.isoformat(),
                }
                for c in comments
            ],
        })

    if request.method == 'POST':
        if not request.user.is_authenticated:
            return JsonResponse({'error': 'Non authentifié'}, status=401)
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'JSON invalide'}, status=400)
        content = (body.get('content') or '').strip()
        if not content:
            return JsonResponse({'error': 'Commentaire vide'}, status=400)
        comment = Comment.objects.create(post=post, author=request.user, content=content[:500])
        prof = getattr(request.user, 'profile', None)
        anon = prof.username_anonyme if prof else 'Anonyme·e'
        if post.author != request.user:
            try:
                send_notification(
                    post.author, 'comment',
                    'Nouveau commentaire',
                    f'{anon} a commenté ton témoignage.',
                    '/dashboard/',
                )
            except Exception:
                pass
        return JsonResponse({
            'id':      comment.id,
            'anon':    anon,
            'content': comment.content,
            'created_at': comment.created_at.isoformat(),
            'comment_count': post.comments.count(),
        }, status=201)

    return JsonResponse({'error': 'Méthode non autorisée'}, status=405)


# ============================================================
# BLOG (astuces anti-anxiété, histoires vécues — écrit par les utilisateur·rices)
# ============================================================

@csrf_exempt
def blog_post_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    # multipart/form-data (pas JSON) car l'image, optionnelle, voyage dans
    # request.FILES — même convention que les pièces jointes du journal.
    title = (request.POST.get('title') or '').strip()[:150]
    content = (request.POST.get('content') or '').strip()
    category = request.POST.get('category', 'astuce')
    if category not in dict(BlogPost.CATEGORY_CHOICES):
        category = 'astuce'
    if not title or not content:
        return JsonResponse({'error': 'Titre et contenu obligatoires'}, status=400)

    image = request.FILES.get('image')
    if image:
        if image.size > MAX_ATTACHMENT_SIZE:
            return JsonResponse({'error': 'Image trop volumineuse (8 Mo max)'}, status=400)
        if not (image.content_type or '').startswith('image/'):
            return JsonResponse({'error': 'Le fichier doit être une image'}, status=400)

    post = BlogPost.objects.create(author=request.user, title=title, content=content, category=category)
    if image:
        post.image = image
        post.save(update_fields=['image'])
    prof = getattr(request.user, 'profile', None)
    anon = prof.username_anonyme if prof else 'Anonyme·e'
    return JsonResponse({
        'id':              post.id,
        'title':           post.title,
        'content':         post.content,
        'category':        post.category,
        'category_label':  post.get_category_display(),
        'image_url':       post.image.url if post.image else None,
        'anon':            anon,
        'initial':         anon[0].upper() if anon else 'A',
        'like_count':      0,
        'is_liked':        False,
        'is_saved':        False,
        'comment_count':   0,
        'is_mine':         True,
        'created_at':      post.created_at.isoformat(),
    }, status=201)


@csrf_exempt
def toggle_blog_like(request, post_id):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    post = get_object_or_404(BlogPost, id=post_id)
    if post.likes.filter(id=request.user.id).exists():
        post.likes.remove(request.user)
        is_liked = False
    else:
        post.likes.add(request.user)
        is_liked = True
        if post.author != request.user:
            prof = getattr(request.user, 'profile', None)
            liker_name = prof.username_anonyme if prof else 'Anonyme·e'
            try:
                send_notification(
                    post.author, 'like', 'Quelqu\'un aime ton article',
                    f'{liker_name} a aimé « {post.title} ».', '/dashboard/',
                )
            except Exception:
                pass
    return JsonResponse({'is_liked': is_liked, 'like_count': post.likes.count()})


@csrf_exempt
def toggle_blog_save(request, post_id):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    post = get_object_or_404(BlogPost, id=post_id)
    if post.saves.filter(id=request.user.id).exists():
        post.saves.remove(request.user)
        is_saved = False
    else:
        post.saves.add(request.user)
        is_saved = True
    return JsonResponse({'is_saved': is_saved})


@csrf_exempt
def blog_comments_api(request, post_id):
    post = get_object_or_404(BlogPost, id=post_id)

    if request.method == 'GET':
        comments = post.comments.select_related('author', 'author__profile')
        return JsonResponse({
            'comments': [
                {
                    'id':      c.id,
                    'anon':    c.author.profile.username_anonyme if getattr(c.author, 'profile', None) else 'Anonyme·e',
                    'content': c.content,
                    'created_at': c.created_at.isoformat(),
                }
                for c in comments
            ],
        })

    if request.method == 'POST':
        if not request.user.is_authenticated:
            return JsonResponse({'error': 'Non authentifié'}, status=401)
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'JSON invalide'}, status=400)
        content = (body.get('content') or '').strip()
        if not content:
            return JsonResponse({'error': 'Commentaire vide'}, status=400)
        comment = BlogComment.objects.create(post=post, author=request.user, content=content[:500])
        prof = getattr(request.user, 'profile', None)
        anon = prof.username_anonyme if prof else 'Anonyme·e'
        if post.author != request.user:
            try:
                send_notification(
                    post.author, 'comment', 'Nouveau commentaire',
                    f'{anon} a commenté ton article « {post.title} ».', '/dashboard/',
                )
            except Exception:
                pass
        return JsonResponse({
            'id':      comment.id,
            'anon':    anon,
            'content': comment.content,
            'created_at': comment.created_at.isoformat(),
            'comment_count': post.comments.count(),
        }, status=201)

    return JsonResponse({'error': 'Méthode non autorisée'}, status=405)


@csrf_exempt
def report_blog_post(request, post_id):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    post = get_object_or_404(BlogPost, id=post_id)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)

    reason = body.get('reason', 'autre')
    if reason not in dict(BlogPostReport.REASON_CHOICES):
        reason = 'autre'
    details = (body.get('details') or '').strip()[:500]

    if BlogPostReport.objects.filter(post=post, reporter=request.user).exists():
        return JsonResponse({'message': 'Tu as déjà signalé cet article, merci — il est en cours de vérification.'})

    BlogPostReport.objects.create(post=post, reporter=request.user, reason=reason, details=details)
    # Masqué immédiatement dès le premier signalement, pour protéger vite
    # contre le harcèlement — réapparaît une fois vérifié en modération.
    post.is_reported = True
    post.save(update_fields=['is_reported'])
    return JsonResponse({'message': 'Merci, cet article a été signalé et masqué en attendant vérification.'})


@csrf_exempt
def delete_blog_post(request, post_id):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    post = get_object_or_404(BlogPost, id=post_id)
    if post.author != request.user:
        return JsonResponse({'error': 'Tu ne peux supprimer que tes propres articles'}, status=403)
    post.delete()
    return JsonResponse({'message': 'Article supprimé'})


@csrf_exempt
def toggle_blog_archive(request, post_id):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    post = get_object_or_404(BlogPost, id=post_id)
    if post.author != request.user:
        return JsonResponse({'error': 'Tu ne peux archiver que tes propres articles'}, status=403)
    post.is_archived = not post.is_archived
    post.save(update_fields=['is_archived'])
    return JsonResponse({'is_archived': post.is_archived})


def my_archived_blog_posts_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    posts = BlogPost.objects.filter(author=request.user, is_archived=True)
    return JsonResponse({
        'posts': [
            {
                'id':             p.id,
                'title':          p.title,
                'category_label': p.get_category_display(),
                'created_at':     p.created_at.isoformat(),
            }
            for p in posts
        ],
    })


# ============================================================
# AVIS (page d'accueil publique)
# ============================================================

@csrf_exempt
@ratelimit(key='user', rate='5/d', method='POST', block=False)
def submit_review(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    if getattr(request, 'limited', False):
        return JsonResponse({'error': 'Trop d’avis envoyés aujourd’hui. Réessaie demain.'}, status=429)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)

    content = (body.get('content') or '').strip()
    try:
        rating = int(body.get('rating', 5))
    except (TypeError, ValueError):
        rating = 5
    rating = min(5, max(1, rating))

    if len(content) < 10:
        return JsonResponse({'error': 'Ton avis est un peu court, dis-en un peu plus 🌸'}, status=400)

    Review.objects.create(author=request.user, content=content[:1000], rating=rating)
    return JsonResponse({'message': 'Merci pour ton avis ! Il sera visible après validation.'}, status=201)


# ============================================================
# NEWSLETTER
# ============================================================

@csrf_exempt
@ratelimit(key='ip', rate='5/h', method='POST', block=False)
def newsletter_subscribe(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    if getattr(request, 'limited', False):
        return JsonResponse({'error': 'Trop de tentatives. Merci de réessayer plus tard.'}, status=429)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)

    email = (body.get('email') or '').strip().lower()
    try:
        validate_email(email)
    except DjangoValidationError:
        return JsonResponse({'error': 'Adresse e-mail invalide'}, status=400)

    subscriber, created = NewsletterSubscriber.objects.get_or_create(
        email=email,
        defaults={'token': secrets.token_urlsafe(32)},
    )
    if subscriber.is_confirmed:
        return JsonResponse({'message': 'Tu es déjà abonné·e à la newsletter 🌸'})

    send_newsletter_confirmation_email(request, subscriber)
    return JsonResponse({'message': 'Vérifie ta boîte mail pour confirmer ton abonnement !'}, status=201 if created else 200)


def newsletter_confirm(request, token):
    subscriber = get_object_or_404(NewsletterSubscriber, token=token)
    if not subscriber.is_confirmed:
        subscriber.is_confirmed = True
        subscriber.save(update_fields=['is_confirmed'])
    return render(request, 'page/newsletter_confirmed.html', {'email': subscriber.email})


def newsletter_unsubscribe(request, token):
    subscriber = get_object_or_404(NewsletterSubscriber, token=token)
    email = subscriber.email
    subscriber.delete()
    return render(request, 'page/newsletter_unsubscribed.html', {'email': email})


# ============================================================
# PARAMÈTRES (interrupteurs notifications/confidentialité)
# ============================================================

@csrf_exempt
def update_setting(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    profile = getattr(request.user, 'profile', None)
    if profile is None:
        return JsonResponse({'error': 'Profil introuvable'}, status=404)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)

    key = body.get('key', '')
    if key not in UserProfile.SETTINGS_FIELDS:
        return JsonResponse({'error': 'Paramètre inconnu'}, status=400)

    value = bool(body.get('value'))
    setattr(profile, key, value)
    profile.save(update_fields=[key])
    return JsonResponse({'key': key, 'value': value})


# ============================================================
# ÉDITION DU PROFIL
# ============================================================

@csrf_exempt
def update_profile(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    profile = getattr(request.user, 'profile', None)
    if profile is None:
        return JsonResponse({'error': 'Profil introuvable'}, status=404)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)

    first_name    = (body.get('first_name') or '').strip()[:150]
    last_name     = (body.get('last_name') or '').strip()[:150]
    username_anon = (body.get('username_anonyme') or '').strip()[:50]
    ville         = (body.get('ville') or '').strip()[:100]
    genre         = (body.get('genre') or '').strip()
    situation     = (body.get('situation') or '').strip()
    theme_couleur = (body.get('theme_couleur') or '').strip()
    objectif      = (body.get('objectif_principal') or '').strip()[:200]
    age_raw       = body.get('age')
    payment_method = (body.get('payment_method') or '').strip()
    payment_info   = (body.get('payment_info') or '').strip()[:200]

    if not username_anon:
        return JsonResponse({'error': 'Le nom anonyme est requis'}, status=400)
    if UserProfile.objects.exclude(pk=profile.pk).filter(username_anonyme=username_anon).exists():
        return JsonResponse({'error': 'Ce nom anonyme est déjà pris'}, status=400)
    if _looks_like_real_name(username_anon, first_name or request.user.first_name, last_name or request.user.last_name):
        return JsonResponse({'error': 'Ton nom anonyme ressemble trop à ton vrai nom — choisis-en un qui ne te rend pas identifiable.'}, status=400)
    if genre and genre not in dict(UserProfile.GENRE_CHOICES):
        return JsonResponse({'error': 'Genre invalide'}, status=400)
    if situation and situation not in dict(UserProfile.SITUATION_CHOICES):
        return JsonResponse({'error': 'Situation invalide'}, status=400)
    if theme_couleur and theme_couleur not in dict(UserProfile.THEME_CHOICES):
        return JsonResponse({'error': 'Thème invalide'}, status=400)
    if payment_method and payment_method not in dict(UserProfile.PAYMENT_METHOD_CHOICES):
        return JsonResponse({'error': 'Moyen de paiement invalide'}, status=400)

    age = None
    if age_raw not in (None, ''):
        try:
            age = max(0, min(120, int(age_raw)))
        except (TypeError, ValueError):
            return JsonResponse({'error': 'Âge invalide'}, status=400)

    request.user.first_name = first_name
    request.user.last_name = last_name
    request.user.save(update_fields=['first_name', 'last_name'])

    profile.username_anonyme = username_anon
    profile.ville = ville
    profile.age = age
    if genre:
        profile.genre = genre
    if situation:
        profile.situation = situation
    if theme_couleur:
        profile.theme_couleur = theme_couleur
    profile.objectif_principal = objectif
    profile.payment_method = payment_method
    profile.payment_info = payment_info
    profile.save()

    return JsonResponse({'message': 'Profil mis à jour !'})


# ============================================================
# SENSIBILISATION (auto-évaluations, quiz, défis, mythes)
# ============================================================

@csrf_exempt
def submit_screening(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)

    tool = body.get('tool')
    tool_def = SCREENING_TOOLS.get(tool)
    if not tool_def:
        return JsonResponse({'error': 'Outil inconnu'}, status=400)

    answers = body.get('answers')
    if not isinstance(answers, list) or len(answers) != len(tool_def['questions']):
        return JsonResponse({'error': 'Réponses invalides'}, status=400)
    try:
        answers = [max(0, min(3, int(a))) for a in answers]
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Réponses invalides'}, status=400)

    score = sum(answers)
    band = score_band(tool, score)
    risk_index = tool_def['risk_question_index']
    flagged = risk_index is not None and answers[risk_index] > 0

    ScreeningResult.objects.create(user=request.user, tool=tool, score=score, band=band, flagged=flagged)

    return JsonResponse({
        'score': score,
        'max_score': tool_def['max_score'],
        'band': band,
        'flagged': flagged,
    })


@csrf_exempt
def submit_quiz(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)

    answers = body.get('answers')
    if not isinstance(answers, list) or len(answers) != len(QUIZ_QUESTIONS):
        return JsonResponse({'error': 'Réponses invalides'}, status=400)

    results = []
    score = 0
    for question, given in zip(QUIZ_QUESTIONS, answers):
        try:
            given = int(given)
        except (TypeError, ValueError):
            given = -1
        is_correct = given == question['correct']
        if is_correct:
            score += 1
        results.append({
            'is_correct': is_correct,
            'correct': question['correct'],
            'explanation': question['explanation'],
        })

    QuizAttempt.objects.create(user=request.user, score=score, total=len(QUIZ_QUESTIONS))
    return JsonResponse({'score': score, 'total': len(QUIZ_QUESTIONS), 'results': results})


@csrf_exempt
def submit_daily_challenge(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)

    profile = getattr(request.user, 'profile', None)
    challenge_date, _pending_count = _get_current_daily_challenge(request.user, profile)
    if challenge_date is None:
        return JsonResponse({'error': 'Tu es déjà à jour — reviens demain pour le prochain défi !'}, status=400)

    reflection = (body.get('reflection_text') or '').strip()[:500]
    if len(reflection) < 5:
        return JsonResponse({'error': 'Dis-en un peu plus sur comment tu t\'es senti·e 🌸'}, status=400)

    DailyChallengeCompletion.objects.create(
        user=request.user, challenge_date=challenge_date, reflection_text=reflection,
    )
    next_date, next_pending_count = _get_current_daily_challenge(request.user, profile)
    return JsonResponse({
        'message': 'Bravo, défi validé ! 🌸',
        'remaining_pending': next_pending_count,
        'has_next': next_date is not None,
    }, status=201)


@csrf_exempt
def submit_myth(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)
    myth_text = (body.get('myth_text') or '').strip()
    if len(myth_text) < 10:
        return JsonResponse({'error': 'Décris un peu plus le mythe que tu as entendu 🌸'}, status=400)
    SubmittedMyth.objects.create(author=request.user, myth_text=myth_text[:500])
    return JsonResponse({'message': 'Merci ! Ton mythe sera publié avec une réponse après validation.'}, status=201)


def _serialize_solidarity_message(msg, user):
    prof = getattr(msg.author, 'profile', None)
    return {
        'id':           msg.id,
        'author_name':  prof.username_anonyme if prof else 'Anonyme·e',
        'content':      msg.content,
        'heart_count':  msg.heart_count,
        'is_hearted':   msg.hearts.filter(id=user.id).exists(),
        'is_mine':      msg.author_id == user.id,
        'created_at':   msg.created_at.strftime('%d/%m %H:%M'),
    }


@csrf_exempt
def solidarity_wall_api(request):
    """Mur de solidarité — a live feed of short, anonymous words of
    encouragement (Sensibilisation section). Visible immediately like
    CommunityPost (not gated behind admin approval like SubmittedMyth),
    since the whole point is that it feels alive; is_reported hides a
    message pending moderation instead."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)

    if request.method == 'GET':
        messages = SolidarityMessage.objects.filter(is_reported=False).select_related(
            'author', 'author__profile'
        ).annotate(heart_count_annotated=Count('hearts', distinct=True))[:40]
        return JsonResponse({
            'messages': [_serialize_solidarity_message(m, request.user) for m in messages],
        })

    if request.method == 'POST':
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'JSON invalide'}, status=400)
        content = (body.get('content') or '').strip()
        if len(content) < 3:
            return JsonResponse({'error': 'Ton message est un peu court 🌸'}, status=400)
        msg = SolidarityMessage.objects.create(author=request.user, content=content[:280])
        return JsonResponse(_serialize_solidarity_message(msg, request.user), status=201)

    return JsonResponse({'error': 'Méthode non autorisée'}, status=405)


@csrf_exempt
def solidarity_heart_toggle(request, message_id):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    msg = get_object_or_404(SolidarityMessage, id=message_id, is_reported=False)
    if msg.hearts.filter(id=request.user.id).exists():
        msg.hearts.remove(request.user)
        is_hearted = False
    else:
        msg.hearts.add(request.user)
        is_hearted = True
    return JsonResponse({'is_hearted': is_hearted, 'heart_count': msg.hearts.count()})


@csrf_exempt
def solidarity_report(request, message_id):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    msg = get_object_or_404(SolidarityMessage, id=message_id)
    msg.is_reported = True
    msg.save(update_fields=['is_reported'])
    return JsonResponse({'ok': True})


# ============================================================
# JEUX THÉRAPEUTIQUES
# ============================================================

@csrf_exempt
def submit_game_score(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)

    game = body.get('game')
    if game not in dict(GameSession.GAME_CHOICES):
        return JsonResponse({'error': 'Jeu inconnu'}, status=400)
    try:
        score = max(0, min(1000, int(body.get('score', 0))))
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Score invalide'}, status=400)

    GameSession.objects.create(user=request.user, game=game, score=score)
    best = GameSession.objects.filter(user=request.user, game=game).order_by('-score').first()
    return JsonResponse({'message': 'Score enregistré !', 'score': score, 'best_score': best.score if best else score})


# ============================================================
# JEUX MULTIJOUEURS — Devine l'émotion
# ============================================================

def _anon_name(user):
    prof = getattr(user, 'profile', None)
    return prof.username_anonyme if prof else 'Anonyme·e'


def _generate_room_code():
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # no 0/O/1/I ambiguity
    for _ in range(20):
        code = ''.join(random.choices(alphabet, k=5))
        if not GameRoom.objects.filter(code=code).exists():
            return code
    raise RuntimeError('Could not generate a unique room code')


def _pick_next_giver(room):
    """Next player who hasn't been giver yet, oldest-joined first. None if
    everyone has already had a turn."""
    return room.players.filter(has_been_giver=False).order_by('joined_at').first()


def _start_round(room, giver_player):
    giver_player.has_been_giver = True
    giver_player.save(update_fields=['has_been_giver'])
    room.round_number += 1
    room.current_giver = giver_player.user
    room.current_emotion = random.choice(EMOTION_WORDS)
    room.save(update_fields=['round_number', 'current_giver', 'current_emotion'])
    GameRoomMessage.objects.create(
        room=room, author=giver_player.user, is_system=True,
        content=f'🎭 {_anon_name(giver_player.user)} doit faire deviner une émotion — donnez des indices sans dire le mot !',
    )


COACH_SYSTEM_PROMPT = """Tu es un coach bienveillant qui observe une partie du jeu thérapeutique \
"Devine l'émotion" sur SANA, une plateforme de santé mentale. Des joueur·euses anonymes se relaient \
pour faire deviner une émotion à l'aide d'indices écrits, sans jamais dire le mot.

Tu reçois la transcription du chat de la partie (uniquement des pseudonymes, jamais de vraies identités).

Rédige un feedback court (2 à 4 phrases), en français, chaleureux et constructif, sur la façon dont le \
groupe a collaboré : qualité de l'écoute, clarté des indices, entraide, esprit d'équipe. Reste factuel par \
rapport à ce que tu observes dans la transcription, félicite ce qui a bien fonctionné, et suggère \
gentiment un axe d'amélioration si pertinent. N'invente rien qui ne soit pas dans la transcription.

Règles absolues :
- Jamais de diagnostic médical ou psychologique, jamais de jugement sur la santé mentale des joueur·euses.
- Utilise uniquement les pseudonymes fournis, jamais de nom réel.
- Termine toujours sur une note encourageante."""


def _run_coach_feedback(transcript, system_prompt):
    """Shared Gemini call for the "coach IA" feature across multiplayer games.
    Returns the feedback text, or '' on any failure/empty transcript — never
    raises, since a Gemini hiccup shouldn't block a game from finishing."""
    try:
        api_key = _get_valid_gemini_key()
        if not api_key or not transcript:
            return ''
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=f"Transcription de la partie :\n{transcript}",
            config=genai_types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=300,
                temperature=0.8,
                thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
            ),
        )
        return (getattr(response, 'text', None) or '').strip()
    except Exception:
        logger.exception('Coach IA: échec de la génération du feedback')
        return ''


def _generate_coach_feedback(room):
    lines = [
        f"{_anon_name(m.author)}: {m.content}" + (' (bonne réponse)' if m.is_correct_guess else '')
        for m in room.messages.select_related('author', 'author__profile').order_by('created_at')
        if not m.is_system
    ]
    feedback = _run_coach_feedback('\n'.join(lines), COACH_SYSTEM_PROMPT)
    if feedback:
        room.ai_feedback = feedback
        room.save(update_fields=['ai_feedback'])


@csrf_exempt
def create_game_room(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    room = GameRoom.objects.create(code=_generate_room_code(), host=request.user)
    GameRoomPlayer.objects.create(room=room, user=request.user)
    return JsonResponse({'code': room.code}, status=201)


@csrf_exempt
def join_game_room(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)
    code = (body.get('code') or '').strip().upper()
    room = GameRoom.objects.filter(code=code).first()
    if not room:
        return JsonResponse({'error': 'Salon introuvable — vérifie le code.'}, status=404)
    if room.status != 'waiting':
        return JsonResponse({'error': 'Cette partie a déjà commencé.'}, status=400)
    player, created = GameRoomPlayer.objects.get_or_create(room=room, user=request.user)
    if created:
        GameRoomMessage.objects.create(
            room=room, author=request.user, is_system=True,
            content=f'👋 {_anon_name(request.user)} a rejoint le salon.',
        )
    return JsonResponse({'code': room.code}, status=201 if created else 200)


@csrf_exempt
def start_game_room(request, code):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    room = get_object_or_404(GameRoom, code=code.upper())
    if room.host != request.user:
        return JsonResponse({'error': "Seul l'hôte peut démarrer la partie"}, status=403)
    if room.status != 'waiting':
        return JsonResponse({'error': 'La partie a déjà commencé'}, status=400)
    player_count = room.players.count()
    if player_count < 2:
        return JsonResponse({'error': 'Il faut au moins 2 joueurs pour commencer'}, status=400)

    room.status = 'playing'
    room.max_rounds = player_count
    room.save(update_fields=['status', 'max_rounds'])
    first_giver = _pick_next_giver(room)
    _start_round(room, first_giver)
    return JsonResponse({'message': 'Partie démarrée !'})


def game_room_state(request, code):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    room = get_object_or_404(GameRoom, code=code.upper())
    player = GameRoomPlayer.objects.filter(room=room, user=request.user).first()
    if not player:
        return JsonResponse({'error': 'Tu ne fais pas partie de ce salon'}, status=403)

    since_id = request.GET.get('since', 0)
    try:
        since_id = int(since_id)
    except (TypeError, ValueError):
        since_id = 0
    messages = room.messages.filter(id__gt=since_id).select_related('author', 'author__profile')

    is_giver = room.current_giver_id == request.user.id
    return JsonResponse({
        'status': room.status,
        'round_number': room.round_number,
        'max_rounds': room.max_rounds,
        'is_host': room.host_id == request.user.id,
        'is_giver': is_giver,
        'secret_emotion': room.current_emotion if is_giver else None,
        'giver_name': _anon_name(room.current_giver) if room.current_giver_id else None,
        'ai_feedback': room.ai_feedback if room.status == 'finished' else None,
        'players': [
            {'name': _anon_name(p.user), 'score': p.score, 'is_you': p.user_id == request.user.id}
            for p in room.players.select_related('user', 'user__profile').order_by('-score', 'joined_at')
        ],
        'messages': [
            {
                'id': m.id,
                'author': _anon_name(m.author),
                'content': m.content,
                'is_system': m.is_system,
                'is_correct_guess': m.is_correct_guess,
                'is_you': m.author_id == request.user.id,
            }
            for m in messages
        ],
    })


@csrf_exempt
def post_game_room_message(request, code):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    room = get_object_or_404(GameRoom, code=code.upper())
    player = GameRoomPlayer.objects.filter(room=room, user=request.user).first()
    if not player:
        return JsonResponse({'error': 'Tu ne fais pas partie de ce salon'}, status=403)
    if room.status != 'playing':
        return JsonResponse({'error': 'La partie n\'est pas en cours'}, status=400)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)
    content = (body.get('content') or '').strip()[:200]
    if not content:
        return JsonResponse({'error': 'Message vide'}, status=400)

    is_giver = room.current_giver_id == request.user.id
    secret = (room.current_emotion or '').lower()

    if is_giver:
        if secret and secret in content.lower():
            return JsonResponse({'error': 'Tu ne peux pas utiliser le mot lui-même dans ton indice !'}, status=400)
        GameRoomMessage.objects.create(room=room, author=request.user, content=content)
        return JsonResponse({'message': 'Indice envoyé'})

    # Guess from a non-giver
    is_correct = secret and content.strip().lower() == secret
    GameRoomMessage.objects.create(room=room, author=request.user, content=content, is_correct_guess=is_correct)

    if is_correct:
        player.score += 1
        player.save(update_fields=['score'])
        GameRoomMessage.objects.create(
            room=room, author=request.user, is_system=True,
            content=f'✅ {_anon_name(request.user)} a trouvé : {room.current_emotion} !',
        )
        next_giver = _pick_next_giver(room)
        if next_giver:
            _start_round(room, next_giver)
        else:
            room.status = 'finished'
            room.current_giver = None
            room.current_emotion = ''
            room.save(update_fields=['status', 'current_giver', 'current_emotion'])
            GameRoomMessage.objects.create(room=room, author=request.user, is_system=True, content='🏁 Partie terminée !')
            _generate_coach_feedback(room)

    return JsonResponse({'message': 'Envoyé', 'is_correct': is_correct})


# ── "L'Ombre parmi les Lumières" (édition bien-être de Qui est le loup) ────────

WEREWOLF_MIN_PLAYERS = 4  # with only 3, the first night kill would immediately
# hit the 2-alive parity win condition with no day discussion/vote ever
# happening — 4 guarantees at least one full day cycle before the game can end

WEREWOLF_COACH_SYSTEM_PROMPT = """Tu es un coach bienveillant qui observe une partie du jeu thérapeutique \
"L'Ombre parmi les Lumières" sur SANA, une plateforme de santé mentale — une version douce et non-violente \
du jeu du loup-garou. Des "Pensées Lumineuses" discutent et votent pour démasquer la "Pensée Sombre" \
infiltrée parmi elles, qui elle-même essaie de se fondre dans le groupe.

Tu reçois la transcription du chat de la partie (uniquement des pseudonymes, jamais de vraies identités) \
ainsi que le résultat final.

Rédige un feedback court (2 à 4 phrases), en français, chaleureux et constructif, sur la façon dont le \
groupe a discuté et pris ses décisions ensemble : écoute, esprit critique, bienveillance dans les échanges, \
capacité à argumenter sans accuser durement. Reste factuel par rapport à ce que tu observes, félicite ce qui \
a bien fonctionné, et suggère gentiment un axe d'amélioration si pertinent. N'invente rien qui ne soit pas \
dans la transcription.

Règles absolues :
- Jamais de diagnostic médical ou psychologique, jamais de jugement sur la santé mentale des joueur·euses.
- Utilise uniquement les pseudonymes fournis, jamais de nom réel.
- Ne fais aucun commentaire négatif sur la personne qui jouait la Pensée Sombre — c'était son rôle, pas un trait de caractère.
- Termine toujours sur une note encourageante."""


def _generate_werewolf_coach_feedback(room):
    lines = [
        f"{_anon_name(m.author)}: {m.content}"
        for m in room.messages.select_related('author', 'author__profile').order_by('created_at')
        if not m.is_system
    ]
    result_label = 'Les Lumières ont gagné' if room.result == 'lumieres_win' else 'La Pensée Sombre a gagné'
    transcript = f"Résultat : {result_label}\n" + '\n'.join(lines)
    feedback = _run_coach_feedback(transcript, WEREWOLF_COACH_SYSTEM_PROMPT)
    if feedback:
        room.ai_feedback = feedback
        room.save(update_fields=['ai_feedback'])


def _assign_werewolf_roles(room):
    players = list(room.players.all())
    sombre = random.choice(players)
    for p in players:
        p.role = 'sombre' if p.id == sombre.id else 'lumiere'
    WerewolfPlayer.objects.bulk_update(players, ['role'])


def _start_werewolf_night(room):
    room.status = 'night'
    room.round_number += 1
    room.night_target = None
    room.current_prompt = ''
    room.save(update_fields=['status', 'round_number', 'night_target', 'current_prompt'])
    WerewolfMessage.objects.create(
        room=room, author=room.host, is_system=True, round_number=room.round_number,
        content=f'🌙 La nuit tombe (manche {room.round_number}). La Pensée Sombre choisit en secret…',
    )


def _start_werewolf_day(room):
    room.status = 'day_discussion'
    room.current_prompt = random.choice(SHADOW_DISCUSSION_PROMPTS)
    room.save(update_fields=['status', 'current_prompt'])
    WerewolfMessage.objects.create(
        room=room, author=room.host, is_system=True, round_number=room.round_number,
        content=f'☀️ Le jour se lève. Discussion : {room.current_prompt}',
    )


def _check_werewolf_win(room):
    """If a win condition is met, marks the room finished (+ result) and
    returns True. Otherwise returns False and leaves the room untouched."""
    alive = list(room.players.filter(is_alive=True))
    sombre_alive = any(p.role == 'sombre' for p in alive)
    if not sombre_alive:
        room.status = 'finished'
        room.result = 'lumieres_win'
        room.save(update_fields=['status', 'result'])
        return True
    if len(alive) <= 2:
        room.status = 'finished'
        room.result = 'sombre_win'
        room.save(update_fields=['status', 'result'])
        return True
    return False


def _resolve_werewolf_vote(room):
    votes = WerewolfVote.objects.filter(room=room, round_number=room.round_number)
    tally = {}
    for v in votes:
        tally[v.target_id] = tally.get(v.target_id, 0) + 1
    if not tally:
        _start_werewolf_night(room)
        return
    max_votes = max(tally.values())
    top = [uid for uid, c in tally.items() if c == max_votes]
    if len(top) > 1:
        WerewolfMessage.objects.create(
            room=room, author=room.host, is_system=True, round_number=room.round_number,
            content="⚖️ Égalité des votes — personne n'est démasqué·e cette manche.",
        )
        _start_werewolf_night(room)
        return

    target_player = room.players.select_related('user', 'user__profile').get(user_id=top[0])
    target_player.is_alive = False
    target_player.save(update_fields=['is_alive'])
    role_label = 'la Pensée Sombre 🌑' if target_player.role == 'sombre' else 'une Pensée Lumineuse 💡'
    WerewolfMessage.objects.create(
        room=room, author=room.host, is_system=True, round_number=room.round_number,
        content=f'🔦 {_anon_name(target_player.user)} était {role_label}.',
    )
    if _check_werewolf_win(room):
        _generate_werewolf_coach_feedback(room)
        return
    _start_werewolf_night(room)


def werewolf_room_page(request, code):
    if not request.user.is_authenticated:
        return redirect('sanasource:login')
    room = get_object_or_404(WerewolfRoom, code=code.upper())
    if not WerewolfPlayer.objects.filter(room=room, user=request.user).exists():
        return redirect('sanasource:dashboard')
    return render(request, 'page/werewolf_room.html', {'room_code': room.code})


@csrf_exempt
def create_werewolf_room(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    code = _generate_room_code()
    while WerewolfRoom.objects.filter(code=code).exists():
        code = _generate_room_code()
    room = WerewolfRoom.objects.create(code=code, host=request.user)
    WerewolfPlayer.objects.create(room=room, user=request.user)
    return JsonResponse({'code': room.code}, status=201)


@csrf_exempt
def join_werewolf_room(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)
    code = (body.get('code') or '').strip().upper()
    room = WerewolfRoom.objects.filter(code=code).first()
    if not room:
        return JsonResponse({'error': 'Salon introuvable — vérifie le code.'}, status=404)
    if room.status != 'waiting':
        return JsonResponse({'error': 'Cette partie a déjà commencé.'}, status=400)
    player, created = WerewolfPlayer.objects.get_or_create(room=room, user=request.user)
    if created:
        WerewolfMessage.objects.create(
            room=room, author=request.user, is_system=True, round_number=0,
            content=f'👋 {_anon_name(request.user)} a rejoint le salon.',
        )
    return JsonResponse({'code': room.code}, status=201 if created else 200)


@csrf_exempt
def start_werewolf_room(request, code):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    room = get_object_or_404(WerewolfRoom, code=code.upper())
    if room.host != request.user:
        return JsonResponse({'error': "Seul l'hôte peut démarrer la partie"}, status=403)
    if room.status != 'waiting':
        return JsonResponse({'error': 'La partie a déjà commencé'}, status=400)
    if room.players.count() < WEREWOLF_MIN_PLAYERS:
        return JsonResponse({'error': f'Il faut au moins {WEREWOLF_MIN_PLAYERS} joueurs pour commencer'}, status=400)

    _assign_werewolf_roles(room)
    _start_werewolf_night(room)
    return JsonResponse({'message': 'Partie démarrée !'})


def werewolf_room_state(request, code):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    room = get_object_or_404(WerewolfRoom, code=code.upper())
    my_player = WerewolfPlayer.objects.filter(room=room, user=request.user).first()
    if not my_player:
        return JsonResponse({'error': 'Tu ne fais pas partie de ce salon'}, status=403)

    since_id = request.GET.get('since', 0)
    try:
        since_id = int(since_id)
    except (TypeError, ValueError):
        since_id = 0
    messages = room.messages.filter(id__gt=since_id).select_related('author', 'author__profile')

    is_night_actor = room.status == 'night' and my_player.role == 'sombre' and my_player.is_alive

    players_payload = []
    for p in room.players.select_related('user', 'user__profile').order_by('joined_at'):
        show_role = room.status == 'finished' or not p.is_alive or p.user_id == request.user.id
        players_payload.append({
            'id': p.id,  # room-scoped WerewolfPlayer id — used for targeting, never the real user id
            'name': _anon_name(p.user),
            'is_you': p.user_id == request.user.id,
            'is_alive': p.is_alive,
            'role': p.role if show_role else None,
        })

    my_vote = None
    votes_cast = 0
    if room.status == 'day_vote':
        vote = WerewolfVote.objects.filter(room=room, round_number=room.round_number, voter=request.user).first()
        my_vote = room.players.filter(user_id=vote.target_id).values_list('id', flat=True).first() if vote else None
        votes_cast = WerewolfVote.objects.filter(room=room, round_number=room.round_number).count()

    return JsonResponse({
        'status': room.status,
        'round_number': room.round_number,
        'is_host': room.host_id == request.user.id,
        'my_role': my_player.role or None,
        'my_alive': my_player.is_alive,
        'is_night_actor': is_night_actor,
        'current_prompt': room.current_prompt if room.status in ('day_discussion', 'day_vote') else None,
        'result': room.result or None,
        'ai_feedback': room.ai_feedback if room.status == 'finished' else None,
        'alive_count': room.players.filter(is_alive=True).count(),
        'votes_cast': votes_cast,
        'my_vote': my_vote,
        'players': players_payload,
        'messages': [
            {
                'id': m.id,
                'author': _anon_name(m.author),
                'content': m.content,
                'is_system': m.is_system,
                'is_you': m.author_id == request.user.id,
            }
            for m in messages
        ],
    })


@csrf_exempt
def post_werewolf_message(request, code):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    room = get_object_or_404(WerewolfRoom, code=code.upper())
    player = WerewolfPlayer.objects.filter(room=room, user=request.user).first()
    if not player:
        return JsonResponse({'error': 'Tu ne fais pas partie de ce salon'}, status=403)
    if room.status != 'day_discussion':
        return JsonResponse({'error': "Ce n'est pas le moment de discuter"}, status=400)
    if not player.is_alive:
        return JsonResponse({'error': "Ta lumière s'est éteinte — tu ne peux plus discuter"}, status=403)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)
    content = (body.get('content') or '').strip()[:200]
    if not content:
        return JsonResponse({'error': 'Message vide'}, status=400)

    WerewolfMessage.objects.create(room=room, author=request.user, content=content, round_number=room.round_number)
    return JsonResponse({'message': 'Envoyé'})


@csrf_exempt
def submit_werewolf_night_action(request, code):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    room = get_object_or_404(WerewolfRoom, code=code.upper())
    player = WerewolfPlayer.objects.filter(room=room, user=request.user).first()
    if not player or player.role != 'sombre' or not player.is_alive:
        return JsonResponse({'error': "Tu n'es pas la Pensée Sombre"}, status=403)
    if room.status != 'night':
        return JsonResponse({'error': "Ce n'est pas la nuit"}, status=400)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)
    target_player_id = body.get('target_player_id')
    target_player = room.players.select_related('user', 'user__profile').filter(
        id=target_player_id, is_alive=True,
    ).exclude(user=request.user).first()
    if not target_player:
        return JsonResponse({'error': 'Cible invalide'}, status=400)

    target_player.is_alive = False
    target_player.save(update_fields=['is_alive'])
    room.night_target = target_player.user
    room.save(update_fields=['night_target'])
    WerewolfMessage.objects.create(
        room=room, author=room.host, is_system=True, round_number=room.round_number,
        content=f"💤 Cette nuit, la lumière de {_anon_name(target_player.user)} s'est éteinte. (Pensée Lumineuse)",
    )

    if _check_werewolf_win(room):
        _generate_werewolf_coach_feedback(room)
        return JsonResponse({'message': 'Nuit résolue'})
    _start_werewolf_day(room)
    return JsonResponse({'message': 'Nuit résolue'})


@csrf_exempt
def start_werewolf_vote(request, code):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    room = get_object_or_404(WerewolfRoom, code=code.upper())
    if room.host != request.user:
        return JsonResponse({'error': "Seul l'hôte peut lancer le vote"}, status=403)
    if room.status != 'day_discussion':
        return JsonResponse({'error': "Ce n'est pas le moment de voter"}, status=400)

    room.status = 'day_vote'
    room.save(update_fields=['status'])
    WerewolfMessage.objects.create(
        room=room, author=room.host, is_system=True, round_number=room.round_number,
        content='🗳️ Le vote commence — désignez qui vous semble être la Pensée Sombre.',
    )
    return JsonResponse({'message': 'Vote lancé'})


@csrf_exempt
def cast_werewolf_vote(request, code):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    room = get_object_or_404(WerewolfRoom, code=code.upper())
    player = WerewolfPlayer.objects.filter(room=room, user=request.user).first()
    if not player or not player.is_alive:
        return JsonResponse({'error': "Tu ne peux pas voter"}, status=403)
    if room.status != 'day_vote':
        return JsonResponse({'error': "Ce n'est pas le moment de voter"}, status=400)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)
    target_player_id = body.get('target_player_id')
    target_player = room.players.filter(id=target_player_id, is_alive=True).exclude(user=request.user).first()
    if not target_player:
        return JsonResponse({'error': 'Cible invalide'}, status=400)

    WerewolfVote.objects.update_or_create(
        room=room, round_number=room.round_number, voter=request.user,
        defaults={'target': target_player.user},
    )

    alive_count = room.players.filter(is_alive=True).count()
    votes_cast = WerewolfVote.objects.filter(room=room, round_number=room.round_number).count()
    if votes_cast >= alive_count:
        _resolve_werewolf_vote(room)

    return JsonResponse({'message': 'Vote enregistré'})


# ── Imposteur des émotions ──────────────────────────────────────────────────

IMPOSTOR_MIN_PLAYERS = 3

IMPOSTOR_COACH_SYSTEM_PROMPT = """Tu es un coach bienveillant qui observe une partie du jeu thérapeutique \
"Imposteur des émotions" sur SANA, une plateforme de santé mentale. Un·e joueur·euse (l'Imposteur) ne \
connaît pas l'émotion secrète et doit bluffer pour se fondre dans le groupe, pendant que les autres la \
décrivent sans jamais la nommer, puis tout le monde vote pour démasquer l'Imposteur.

Tu reçois la transcription du chat de la partie (uniquement des pseudonymes, jamais de vraies identités) \
ainsi que le résultat final.

Rédige un feedback court (2 à 4 phrases), en français, chaleureux et constructif, sur la façon dont le \
groupe a observé, argumenté et pris sa décision ensemble. Reste factuel par rapport à ce que tu observes, \
félicite ce qui a bien fonctionné, et suggère gentiment un axe d'amélioration si pertinent. N'invente rien \
qui ne soit pas dans la transcription.

Règles absolues :
- Jamais de diagnostic médical ou psychologique, jamais de jugement sur la santé mentale des joueur·euses.
- Utilise uniquement les pseudonymes fournis, jamais de nom réel.
- Ne fais aucun commentaire négatif sur la personne qui jouait l'Imposteur — c'était son rôle, pas un trait de caractère.
- Termine toujours sur une note encourageante."""


def _generate_impostor_coach_feedback(room):
    lines = [
        f"{_anon_name(m.author)}: {m.content}"
        for m in room.messages.select_related('author', 'author__profile').order_by('created_at')
        if not m.is_system
    ]
    result_label = "Le groupe a démasqué l'Imposteur" if room.result == 'group_win' else "L'Imposteur a échappé au vote"
    transcript = f"Résultat : {result_label}\n" + '\n'.join(lines)
    feedback = _run_coach_feedback(transcript, IMPOSTOR_COACH_SYSTEM_PROMPT)
    if feedback:
        room.ai_feedback = feedback
        room.save(update_fields=['ai_feedback'])


def impostor_room_page(request, code):
    if not request.user.is_authenticated:
        return redirect('sanasource:login')
    room = get_object_or_404(ImpostorRoom, code=code.upper())
    if not ImpostorPlayer.objects.filter(room=room, user=request.user).exists():
        return redirect('sanasource:dashboard')
    return render(request, 'page/impostor_room.html', {'room_code': room.code})


@csrf_exempt
def create_impostor_room(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    code = _generate_room_code()
    while ImpostorRoom.objects.filter(code=code).exists():
        code = _generate_room_code()
    room = ImpostorRoom.objects.create(code=code, host=request.user)
    ImpostorPlayer.objects.create(room=room, user=request.user)
    return JsonResponse({'code': room.code}, status=201)


@csrf_exempt
def join_impostor_room(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)
    code = (body.get('code') or '').strip().upper()
    room = ImpostorRoom.objects.filter(code=code).first()
    if not room:
        return JsonResponse({'error': 'Salon introuvable — vérifie le code.'}, status=404)
    if room.status != 'waiting':
        return JsonResponse({'error': 'Cette partie a déjà commencé.'}, status=400)
    player, created = ImpostorPlayer.objects.get_or_create(room=room, user=request.user)
    if created:
        ImpostorMessage.objects.create(
            room=room, author=request.user, is_system=True,
            content=f'👋 {_anon_name(request.user)} a rejoint le salon.',
        )
    return JsonResponse({'code': room.code}, status=201 if created else 200)


@csrf_exempt
def start_impostor_room(request, code):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    room = get_object_or_404(ImpostorRoom, code=code.upper())
    if room.host != request.user:
        return JsonResponse({'error': "Seul l'hôte peut démarrer la partie"}, status=403)
    if room.status != 'waiting':
        return JsonResponse({'error': 'La partie a déjà commencé'}, status=400)
    players = list(room.players.all())
    if len(players) < IMPOSTOR_MIN_PLAYERS:
        return JsonResponse({'error': f'Il faut au moins {IMPOSTOR_MIN_PLAYERS} joueurs pour commencer'}, status=400)

    impostor_player = random.choice(players)
    room.impostor = impostor_player.user
    room.secret_emotion = random.choice(EMOTION_WORDS)
    room.status = 'discussion'
    room.save(update_fields=['impostor', 'secret_emotion', 'status'])
    ImpostorMessage.objects.create(
        room=room, author=room.host, is_system=True,
        content="🕵️ La partie commence ! Décrivez l'émotion secrète sans jamais la nommer — un·e imposteur·euse se cache parmi vous.",
    )
    return JsonResponse({'message': 'Partie démarrée !'})


def impostor_room_state(request, code):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    room = get_object_or_404(ImpostorRoom, code=code.upper())
    my_player = ImpostorPlayer.objects.filter(room=room, user=request.user).first()
    if not my_player:
        return JsonResponse({'error': 'Tu ne fais pas partie de ce salon'}, status=403)

    since_id = request.GET.get('since', 0)
    try:
        since_id = int(since_id)
    except (TypeError, ValueError):
        since_id = 0
    messages = room.messages.filter(id__gt=since_id).select_related('author', 'author__profile')

    is_impostor = room.impostor_id == request.user.id
    show_secret = room.status == 'finished' or (room.status in ('discussion', 'vote') and not is_impostor)

    players_payload = []
    for p in room.players.select_related('user', 'user__profile').order_by('joined_at'):
        is_p_impostor = room.impostor_id == p.user_id
        players_payload.append({
            'id': p.id,
            'name': _anon_name(p.user),
            'is_you': p.user_id == request.user.id,
            'is_impostor': is_p_impostor if room.status == 'finished' else None,
        })

    my_vote = None
    votes_cast = 0
    if room.status == 'vote':
        vote = ImpostorVote.objects.filter(room=room, voter=request.user).first()
        my_vote = room.players.filter(user_id=vote.target_id).values_list('id', flat=True).first() if vote else None
        votes_cast = ImpostorVote.objects.filter(room=room).count()

    return JsonResponse({
        'status': room.status,
        'is_host': room.host_id == request.user.id,
        'is_impostor': is_impostor,
        'secret_emotion': room.secret_emotion if show_secret else None,
        'result': room.result or None,
        'ai_feedback': room.ai_feedback if room.status == 'finished' else None,
        'player_count': room.players.count(),
        'votes_cast': votes_cast,
        'my_vote': my_vote,
        'players': players_payload,
        'messages': [
            {
                'id': m.id,
                'author': _anon_name(m.author),
                'content': m.content,
                'is_system': m.is_system,
                'is_you': m.author_id == request.user.id,
            }
            for m in messages
        ],
    })


@csrf_exempt
def post_impostor_message(request, code):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    room = get_object_or_404(ImpostorRoom, code=code.upper())
    player = ImpostorPlayer.objects.filter(room=room, user=request.user).first()
    if not player:
        return JsonResponse({'error': 'Tu ne fais pas partie de ce salon'}, status=403)
    if room.status != 'discussion':
        return JsonResponse({'error': "Ce n'est pas le moment de discuter"}, status=400)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)
    content = (body.get('content') or '').strip()[:200]
    if not content:
        return JsonResponse({'error': 'Message vide'}, status=400)

    is_impostor = room.impostor_id == request.user.id
    secret = (room.secret_emotion or '').lower()
    if not is_impostor and secret and secret in content.lower():
        return JsonResponse({'error': "Tu ne peux pas nommer l'émotion elle-même !"}, status=400)

    ImpostorMessage.objects.create(room=room, author=request.user, content=content)
    return JsonResponse({'message': 'Envoyé'})


@csrf_exempt
def start_impostor_vote(request, code):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    room = get_object_or_404(ImpostorRoom, code=code.upper())
    if room.host != request.user:
        return JsonResponse({'error': "Seul l'hôte peut lancer le vote"}, status=403)
    if room.status != 'discussion':
        return JsonResponse({'error': "Ce n'est pas le moment de voter"}, status=400)

    room.status = 'vote'
    room.save(update_fields=['status'])
    ImpostorMessage.objects.create(
        room=room, author=room.host, is_system=True,
        content='🗳️ Le vote commence — désignez qui vous semble être l\'Imposteur.',
    )
    return JsonResponse({'message': 'Vote lancé'})


def _resolve_impostor_vote(room):
    votes = ImpostorVote.objects.filter(room=room)
    tally = {}
    for v in votes:
        tally[v.target_id] = tally.get(v.target_id, 0) + 1
    if not tally:
        room.status = 'finished'
        room.result = 'impostor_win'
        room.save(update_fields=['status', 'result'])
        _generate_impostor_coach_feedback(room)
        return

    max_votes = max(tally.values())
    top = [uid for uid, c in tally.items() if c == max_votes]
    accused_id = top[0] if len(top) == 1 else None

    if accused_id and accused_id == room.impostor_id:
        room.result = 'group_win'
        ImpostorMessage.objects.create(
            room=room, author=room.host, is_system=True,
            content=f"🎭 {_anon_name(room.impostor)} était bien l'Imposteur — démasqué·e !",
        )
    else:
        room.result = 'impostor_win'
        if accused_id:
            accused_user = User.objects.get(id=accused_id)
            ImpostorMessage.objects.create(
                room=room, author=room.host, is_system=True,
                content=f"🎭 {_anon_name(accused_user)} a été accusé·e à tort — l'Imposteur, c'était {_anon_name(room.impostor)} !",
            )
        else:
            ImpostorMessage.objects.create(
                room=room, author=room.host, is_system=True,
                content=f"⚖️ Égalité des votes — l'Imposteur, {_anon_name(room.impostor)}, s'en sort !",
            )

    room.status = 'finished'
    room.save(update_fields=['status', 'result'])
    _generate_impostor_coach_feedback(room)


@csrf_exempt
def cast_impostor_vote(request, code):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    room = get_object_or_404(ImpostorRoom, code=code.upper())
    player = ImpostorPlayer.objects.filter(room=room, user=request.user).first()
    if not player:
        return JsonResponse({'error': 'Tu ne fais pas partie de ce salon'}, status=403)
    if room.status != 'vote':
        return JsonResponse({'error': "Ce n'est pas le moment de voter"}, status=400)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)
    target_player_id = body.get('target_player_id')
    target_player = room.players.filter(id=target_player_id).exclude(user=request.user).first()
    if not target_player:
        return JsonResponse({'error': 'Cible invalide'}, status=400)

    ImpostorVote.objects.update_or_create(
        room=room, voter=request.user, defaults={'target': target_player.user},
    )

    player_count = room.players.count()
    votes_cast = ImpostorVote.objects.filter(room=room).count()
    if votes_cast >= player_count:
        _resolve_impostor_vote(room)

    return JsonResponse({'message': 'Vote enregistré'})


def groupe(request):
    return render(request, 'page/groupe.html')


# ============================================================
# NOTIFICATIONS
# ============================================================

def notifications_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)

    if request.method == 'GET':
        notifs = Notification.objects.filter(user=request.user)[:30]
        data = [
            {
                'id':         n.id,
                'type':       n.type,
                'title':      n.title,
                'body':       n.body,
                'url':        n.url,
                'read':       n.read,
                'created_at': n.created_at.isoformat(),
            }
            for n in notifs
        ]
        return JsonResponse({'notifications': data})

    if request.method == 'PATCH':
        Notification.objects.filter(user=request.user, read=False).update(read=True)
        return JsonResponse({'ok': True})

    return JsonResponse({'error': 'Méthode non autorisée'}, status=405)


@csrf_exempt
def notification_read(request, notif_id):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    Notification.objects.filter(id=notif_id, user=request.user).update(read=True)
    return JsonResponse({'ok': True})


@csrf_exempt
def push_subscribe(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)

    endpoint = body.get('endpoint', '')
    p256dh   = body.get('keys', {}).get('p256dh', '')
    auth     = body.get('keys', {}).get('auth', '')

    if not endpoint or not p256dh or not auth:
        return JsonResponse({'error': 'Données incomplètes'}, status=400)

    PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={'user': request.user, 'p256dh': p256dh, 'auth': auth},
    )
    return JsonResponse({'ok': True})


def notifications_unread_count(request):
    if not request.user.is_authenticated:
        return JsonResponse({'count': 0})
    count = Notification.objects.filter(user=request.user, read=False).count()
    return JsonResponse({'count': count})


# ============================================================
# MESSAGES PRIVES (DM)
# ============================================================

@csrf_exempt
def dm_api(request, user_id):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    other = get_object_or_404(User, id=user_id)
    if other == request.user:
        return JsonResponse({'error': 'Impossible de s\'envoyer un message à soi-même'}, status=400)

    if request.method == 'GET':
        since_id = int(request.GET.get('since', 0) or 0)
        msgs = DirectMessage.objects.filter(
            sender__in=[request.user, other],
            receiver__in=[request.user, other],
            id__gt=since_id,
        ).select_related('sender', 'sender__profile').order_by('sent_at')[:100]
        # Mark received messages as read
        DirectMessage.objects.filter(sender=other, receiver=request.user, read=False).update(read=True)
        my_prof    = getattr(request.user, 'profile', None)
        other_prof = getattr(other, 'profile', None)
        data = []
        for m in msgs:
            is_me = m.sender == request.user
            data.append({
                'id':      m.id,
                'content': m.content,
                'sent_at': m.sent_at.strftime('%H:%M'),
                'is_me':   is_me,
                'read':    m.read,
            })
        unread_total = DirectMessage.objects.filter(sender=other, receiver=request.user, read=False).count()
        return JsonResponse({
            'messages':      data,
            'other_name':    other_prof.username_anonyme if other_prof else 'Anonyme·e',
            'other_initial': (other_prof.username_anonyme[0].upper() if other_prof else 'A'),
            'unread_total':  unread_total,
        })

    if request.method == 'POST':
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'JSON invalide'}, status=400)
        content = body.get('content', '').strip()
        if not content:
            return JsonResponse({'error': 'Message vide'}, status=400)
        msg = DirectMessage.objects.create(sender=request.user, receiver=other, content=content)
        my_prof = getattr(request.user, 'profile', None)
        my_name = my_prof.username_anonyme if my_prof else (request.user.first_name or 'Moi')
        # Notify receiver
        try:
            send_notification(
                other, 'message',
                f'Message privé de {my_name}',
                content[:80],
                '/dashboard/',
            )
        except Exception:
            pass
        return JsonResponse({
            'id':      msg.id,
            'content': msg.content,
            'sent_at': msg.sent_at.strftime('%H:%M'),
            'is_me':   True,
            'read':    False,
        })

    return JsonResponse({'error': 'Méthode non autorisée'}, status=405)


def dm_page(request, user_id):
    if not request.user.is_authenticated:
        return redirect('sanasource:login')
    other = get_object_or_404(User, id=user_id)
    if other == request.user:
        return redirect('sanasource:dashboard')
    other_prof = getattr(other, 'profile', None)
    other_name    = other_prof.username_anonyme if other_prof else 'Anonyme·e'
    other_initial = other_name[0].upper() if other_name else '?'
    return render(request, 'page/dm_chat.html', {
        'other_user_id': user_id,
        'other_name':    other_name,
        'other_initial': other_initial,
        'watermark_uri': _build_watermark_data_uri(request.user),
    })


def dm_conversations(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    # Get latest message and unread count per conversation partner in a single query.
    last_message_qs = DirectMessage.objects.filter(
        Q(sender=request.user, receiver=OuterRef('pk')) |
        Q(sender=OuterRef('pk'), receiver=request.user)
    ).order_by('-sent_at')

    unread_qs = DirectMessage.objects.filter(
        sender=OuterRef('pk'), receiver=request.user, read=False
    ).values('receiver').annotate(c=Count('id')).values('c')

    partners = (
        User.objects.filter(
            Q(sent_dms__receiver=request.user) | Q(received_dms__sender=request.user)
        )
        .distinct()
        .select_related('profile')
        .annotate(
            last_content=Subquery(last_message_qs.values('content')[:1]),
            last_sent_at=Subquery(last_message_qs.values('sent_at')[:1]),
            last_sender_id=Subquery(last_message_qs.values('sender_id')[:1]),
            unread_count=Coalesce(Subquery(unread_qs[:1]), 0, output_field=IntegerField()),
        )
    )
    convs = []
    for p in partners:
        prof = getattr(p, 'profile', None)
        convs.append({
            'user_id':   p.id,
            'name':      prof.username_anonyme if prof else 'Anonyme·e',
            'initial':   (prof.username_anonyme[0].upper() if prof else 'A'),
            'last_msg':  (p.last_content or '')[:60],
            'sent_at':   p.last_sent_at.strftime('%H:%M') if p.last_sent_at else '',
            'unread':    int(p.unread_count or 0),
            'is_me':     p.last_sender_id == request.user.id if p.last_sender_id else False,
        })
    convs.sort(key=lambda c: c['sent_at'], reverse=True)
    dm_unread_total = sum(c['unread'] for c in convs)
    return JsonResponse({'conversations': convs, 'unread_total': dm_unread_total})


@csrf_exempt
def push_unsubscribe(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)
    endpoint = body.get('endpoint', '')
    PushSubscription.objects.filter(user=request.user, endpoint=endpoint).delete()
    return JsonResponse({'ok': True})