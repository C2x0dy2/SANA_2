from pathlib import Path
from django.shortcuts import render, redirect
from django.http import HttpResponse, JsonResponse
from django.contrib.auth import authenticate, login
from django.contrib.auth.models import User
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from .models import UserProfile
import json
import anthropic


# ============================================================
# PAGES
# ============================================================

def accueil(request):
    return render(request, 'page/accueil.html')

def history(request):
    return render(request, 'page/history.html')

def page_open(request):
    return render(request, 'page/page_open.html')

def register_view(request):
    if request.method == 'POST':
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

        if password1 != password2:
            ctx['error'] = 'Les mots de passe ne correspondent pas.'
            return render(request, 'page/register.html', ctx)

        if len(password1) < 8:
            ctx['error'] = 'Le mot de passe doit contenir au moins 8 caractères.'
            return render(request, 'page/register.html', ctx)

        if User.objects.filter(username=email).exists():
            ctx['error'] = 'Un compte existe déjà avec cet e-mail.'
            return render(request, 'page/register.html', ctx)

        if UserProfile.objects.filter(username_anonyme=username_anonyme).exists():
            ctx['error'] = 'Ce nom anonyme est déjà pris, choisis-en un autre.'
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

        # ── Création User + UserProfile ──────────────────────
        user = User.objects.create_user(
            username=email,
            email=email,
            password=password1,
            first_name=first_name,
            last_name=last_name,
        )

        UserProfile.objects.create(
            user=user,
            username_anonyme=username_anonyme,
            age=age,
            genre=genre,
            ville=ville,
            situation=situation,
            theme_couleur=theme_couleur,
            comment_tu_te_sens=comment_tu_te_sens,
            principales_difficultes=principales_difficultes,
            objectif_principal=objectif_principal,
            a_deja_consulte=a_deja_consulte,
            niveau_urgence=niveau_urgence,
        )

        login(request, user)
        return redirect('sanasource:dashboard')

    return render(request, 'page/register.html')

def help_view(request):
    return render(request, 'page/help.html')

def login_view(request):
    if request.method == "POST":
        email    = request.POST.get('email')
        password = request.POST.get('password')
        user     = authenticate(request, username=email, password=password)
        if user is not None:
            login(request, user)
            return redirect('sanasource:dashboard')
        return render(request, 'page/login.html', {'error': 'Identifiants invalides'})
    return render(request, 'page/login.html')


# ============================================================
# CHATBOT IA
# ============================================================

SANA_SYSTEM_PROMPT = """Tu es SANA, un assistant d'écoute bienveillant pour une plateforme
de santé mentale en Côte d'Ivoire.

Règles absolues :
- Tu n'es PAS un médecin ou psychologue. Tu ne diagnostiques jamais.
- Tu écoutes, tu poses des questions douces, tu valides les émotions.
- Tu parles toujours en français, avec douceur et sans jargon médical.
- Tes réponses font 2-4 phrases max, naturelles et humaines.
- Si l'utilisateur mentionne des idées suicidaires ou automutilation,
  donne immédiatement le numéro 185 (SAMU CI) et reste présent.
- Tu t'appelles SANA. Ne mentionne jamais Claude ou Anthropic."""


def _get_valid_anthropic_key():
    api_key = (settings.ANTHROPIC_API_KEY or '').strip()

    # Guard against accidentally storing Python expressions in .env.
    if api_key.startswith('os.environ.get('):
        return None

    # Anthropic keys usually start with sk-ant-.
    if not api_key.startswith('sk-ant-'):
        return None

    return api_key


def _fallback_reply(messages):
    last_user_message = ''
    for message in reversed(messages):
        if message.get('role') == 'user':
            last_user_message = (message.get('content') or '').strip()
            break

    if not last_user_message:
        return "Je suis la pour t'ecouter. Tu peux m'ecrire ce que tu ressens en ce moment, a ton rythme."

    return "Merci de me faire confiance. Je t'entends, et ce que tu ressens compte. Dis-moi ce qui te pese le plus en ce moment."


@csrf_exempt
def sana_chat(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    try:
        # ── 1. Lecture du body ───────────────────────────────
        body     = json.loads(request.body)
        messages = body.get('messages', [])[-50:]

        print(f"📨 Nombre de messages reçus : {len(messages)}")
        for m in messages:
            print(f"   [{m.get('role')}] {m.get('content', '')[:80]}")

        # ── 2. Vérification clé API ──────────────────────────
        api_key = _get_valid_anthropic_key()
        print(f"🔑 Clé API chargée : {bool(api_key)}")

        if not api_key:
            return JsonResponse({'reply': _fallback_reply(messages), 'fallback': True})

        # ── 3. Appel Anthropic ───────────────────────────────
        client   = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model      = "claude-sonnet-4-20250514",
            max_tokens = 400,
            system     = SANA_SYSTEM_PROMPT,
            messages   = messages
        )

        reply = response.content[0].text
        print(f"✅ Réponse SANA : {reply[:120]}")
        return JsonResponse({'reply': reply})

    except json.JSONDecodeError as e:
        print(f"❌ JSON invalide : {e}")
        return JsonResponse({'error': f'JSON invalide : {e}'}, status=400)

    except anthropic.AuthenticationError as e:
        print(f"❌ Clé API invalide : {e}")
        return JsonResponse({'error': f'Clé API invalide : {e}'}, status=401)

    except anthropic.APIConnectionError as e:
        print(f"❌ Connexion Anthropic impossible : {e}")
        return JsonResponse({'error': f'Connexion impossible : {e}'}, status=503)

    except Exception as e:
        print(f"❌ Erreur inattendue ({type(e).__name__}) : {e}")
        return JsonResponse({'error': f'{type(e).__name__} : {e}'}, status=500)


def dashboard(request):
    if not request.user.is_authenticated:
        return redirect('sanasource:login')
    profile = getattr(request.user, 'profile', None)
    return render(request, 'page/dashboard.html', {
        'user': request.user,
        'profile': profile,
    })