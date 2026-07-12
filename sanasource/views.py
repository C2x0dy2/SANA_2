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

from django.core.files.base import ContentFile

from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.db.models import Count, Max, OuterRef, Subquery, IntegerField, Q
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.urls import reverse
from django_ratelimit.decorators import ratelimit

from .models import UserProfile, SanaGroup, GroupMessage, MoodEntry, CommunityPost, Comment, PostReport, Notification, PushSubscription, DirectMessage, Conversation, Message, Journal, JournalEntry, JournalPage, Attachment, Review, NewsletterSubscriber, ScreeningResult, QuizAttempt, UserChallengeProgress, SubmittedMyth
from .notifications import send_notification
from .emails import send_welcome_email, send_verification_email, send_newsletter_confirmation_email
from .tokens import email_verification_token
from .password_validation import french_password_errors
from .serializers import serialize_journal_page, serialize_attachment
from .reflection_questions import REFLECTION_QUESTIONS
from .sensibilisation_content import SCREENING_TOOLS, QUIZ_QUESTIONS, CHALLENGES, CHALLENGES_BY_ID, score_band
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

        return render(request, 'page/verify_email_sent.html', {'email': email})

    return render(request, 'page/register.html')

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

    # Sensibilisation: quiz questions without the answer key, challenges with
    # this user's own progress folded in, and approved community myths.
    quiz_questions_public = [{'question': q['question'], 'choices': q['choices']} for q in QUIZ_QUESTIONS]
    progress_by_challenge = {
        p.challenge_id: p
        for p in UserChallengeProgress.objects.filter(user=request.user, challenge_id__in=CHALLENGES_BY_ID.keys())
    }
    challenges_with_progress = []
    for c in CHALLENGES:
        progress = progress_by_challenge.get(c['id'])
        challenges_with_progress.append({
            **c,
            'started': progress is not None,
            'days_done': len(progress.checkin_dates) if progress else 0,
            'completed': bool(progress and progress.completed_at),
            'checked_in_today': bool(progress and timezone.localdate().isoformat() in progress.checkin_dates),
        })
    myths_submitted = SubmittedMyth.objects.filter(is_approved=True).select_related('author', 'author__profile')[:20]

    screening_count           = ScreeningResult.objects.filter(user=request.user).count()
    quiz_completed_count      = QuizAttempt.objects.filter(user=request.user).count()
    challenges_completed_count = sum(1 for c in challenges_with_progress if c['completed'])
    myths_submitted_count     = SubmittedMyth.objects.filter(author=request.user).count()

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
        'vapid_public_key':    settings.VAPID_PUBLIC_KEY,
        'show_welcome_toast':  request.session.pop('show_welcome', False),
        'watermark_uri':       _build_watermark_data_uri(request.user),
        'screening_tools':            SCREENING_TOOLS,
        'quiz_questions':             quiz_questions_public,
        'challenges':                 challenges_with_progress,
        'myths_submitted':            myths_submitted,
        'screening_count':            screening_count,
        'quiz_completed_count':       quiz_completed_count,
        'challenges_completed_count': challenges_completed_count,
        'myths_submitted_count':      myths_submitted_count,
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
def start_challenge(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)
    challenge_id = body.get('challenge_id')
    if challenge_id not in CHALLENGES_BY_ID:
        return JsonResponse({'error': 'Défi inconnu'}, status=400)

    progress, _ = UserChallengeProgress.objects.get_or_create(user=request.user, challenge_id=challenge_id)
    return JsonResponse({'challenge_id': challenge_id, 'days_done': len(progress.checkin_dates)})


@csrf_exempt
def checkin_challenge(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)
    challenge_id = body.get('challenge_id')
    challenge_def = CHALLENGES_BY_ID.get(challenge_id)
    if not challenge_def:
        return JsonResponse({'error': 'Défi inconnu'}, status=400)

    progress = get_object_or_404(UserChallengeProgress, user=request.user, challenge_id=challenge_id)
    today = timezone.localdate().isoformat()
    if today not in progress.checkin_dates:
        progress.checkin_dates.append(today)
        if len(progress.checkin_dates) >= challenge_def['duration_days'] and not progress.completed_at:
            progress.completed_at = timezone.now()
        progress.save()

    return JsonResponse({
        'days_done': len(progress.checkin_dates),
        'duration_days': challenge_def['duration_days'],
        'completed': progress.completed_at is not None,
    })


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