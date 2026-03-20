from pathlib import Path
from datetime import date, timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.db.models import Count
from .models import UserProfile, SanaGroup, GroupMessage, MoodEntry, CommunityPost, Notification, PushSubscription, DirectMessage
from .notifications import send_notification
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
    if request.user.is_authenticated:
        return redirect('sanasource:dashboard')
    return render(request, 'page/page_open.html')

def register_view(request):
    if request.user.is_authenticated:
        return redirect('sanasource:dashboard')
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
        try:
            user = User.objects.create_user(
                username=email,
                email=email,
                password=password1,
                first_name=first_name,
                last_name=last_name,
            )
        except Exception as e:
            ctx['error'] = f'Erreur lors de la création du compte : {e}'
            return render(request, 'page/register.html', ctx)

        try:
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
        except Exception as e:
            # Profile creation failed (ex: migrations not run), still log user in
            print(f'⚠️ UserProfile creation failed: {e}')

        login(request, user)
        try:
            send_notification(
                user, 'welcome',
                'Bienvenue sur SANA !',
                'Tu es bien arrivé(e). Nous sommes là pour toi.',
                '/dashboard/',
            )
        except Exception:
            pass
        return redirect('sanasource:dashboard')

    return render(request, 'page/register.html')

def logout_view(request):
    logout(request)
    return redirect('sanasource:page_open')

def help_view(request):
    return render(request, 'page/help.html')


def service_worker(request):
    import os
    sw_path = settings.BASE_DIR / 'sanasource' / 'static' / 'sw.js'
    with open(sw_path, 'r', encoding='utf-8') as f:
        content = f.read()
    return HttpResponse(content, content_type='application/javascript')

def login_view(request):
    if request.user.is_authenticated:
        return redirect('sanasource:dashboard')
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

    # Real groups
    groups = SanaGroup.objects.all()[:6]
    user_group_ids = set(request.user.sana_groups.values_list('id', flat=True))

    # Real community posts
    posts = CommunityPost.objects.select_related(
        'author', 'author__profile'
    ).prefetch_related('likes')[:15]
    user_liked_ids = set(request.user.liked_posts.values_list('id', flat=True))

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

    return render(request, 'page/dashboard.html', {
        'user':               request.user,
        'profile':            profile,
        'groups':             groups,
        'user_group_ids':     user_group_ids,
        'posts':              posts,
        'user_liked_ids':     user_liked_ids,
        'mood_data':          mood_data,
        'mood_count_week':     mood_count_week,
        'groups_joined_count': groups_joined_count,
        'user_posts_count':    user_posts_count,
        'days_on_sana':        days_on_sana,
        'tag_counts':          tag_counts,
        'vapid_public_key':    settings.VAPID_PUBLIC_KEY,
    })


# ============================================================
# GROUPES
# ============================================================

def group_page(request):
    if not request.user.is_authenticated:
        return redirect('sanasource:login')
    profile = getattr(request.user, 'profile', None)
    groups  = SanaGroup.objects.all()
    user_group_ids = set(request.user.sana_groups.values_list('id', flat=True))
    return render(request, 'page/group.html', {
        'user':           request.user,
        'profile':        profile,
        'groups':         groups,
        'user_group_ids': user_group_ids,
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
    if request.user in group.members.all():
        group.members.remove(request.user)
        is_member = False
    else:
        group.members.add(request.user)
        is_member = True
        if group.created_by != request.user:
            prof = getattr(request.user, 'profile', None)
            name = prof.username_anonyme if prof else (request.user.first_name or 'Quelqu\'un')
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
            name = prof.username_anonyme if prof else (m.sender.first_name or m.sender.username)
            seen_count = m.seen_by.exclude(id=m.sender_id).count()
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
        if request.user not in group.members.all():
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
        name = prof.username_anonyme if prof else (request.user.first_name or request.user.username)
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
    if not content:
        return JsonResponse({'error': 'Contenu vide'}, status=400)
    post = CommunityPost.objects.create(author=request.user, content=content, tag=tag)
    prof = getattr(request.user, 'profile', None)
    anon = prof.username_anonyme if prof else 'Anonyme·e'
    return JsonResponse({
        'id':         post.id,
        'anon':       anon,
        'initial':    anon[0].upper() if anon else 'A',
        'content':    post.content,
        'tag_label':  post.get_tag_display(),
        'like_count': 0,
        'is_liked':   False,
    })


@csrf_exempt
def toggle_like(request, post_id):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
    post = get_object_or_404(CommunityPost, id=post_id)
    if request.user in post.likes.all():
        post.likes.remove(request.user)
        is_liked = False
    else:
        post.likes.add(request.user)
        is_liked = True
        if post.author != request.user:
            prof = getattr(request.user, 'profile', None)
            liker_name = prof.username_anonyme if prof else (request.user.first_name or 'Quelqu\'un')
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
        msgs = DirectMessage.objects.filter(
            sender__in=[request.user, other],
            receiver__in=[request.user, other],
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
        return JsonResponse({
            'messages':      data,
            'other_name':    other_prof.username_anonyme if other_prof else (other.first_name or 'Anonyme'),
            'other_initial': (other_prof.username_anonyme[0].upper() if other_prof else (other.first_name or 'A')[0].upper()),
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


def dm_conversations(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Non authentifié'}, status=401)
    from django.db.models import Q, Max
    # Get latest message per conversation partner
    partners = User.objects.filter(
        Q(sent_dms__receiver=request.user) | Q(received_dms__sender=request.user)
    ).distinct()
    convs = []
    for p in partners:
        last = DirectMessage.objects.filter(
            Q(sender=request.user, receiver=p) | Q(sender=p, receiver=request.user)
        ).order_by('-sent_at').first()
        unread = DirectMessage.objects.filter(sender=p, receiver=request.user, read=False).count()
        prof = getattr(p, 'profile', None)
        convs.append({
            'user_id':   p.id,
            'name':      prof.username_anonyme if prof else (p.first_name or 'Anonyme'),
            'initial':   (prof.username_anonyme[0].upper() if prof else (p.first_name or 'A')[0].upper()),
            'last_msg':  last.content[:60] if last else '',
            'sent_at':   last.sent_at.strftime('%H:%M') if last else '',
            'unread':    unread,
            'is_me':     last.sender == request.user if last else False,
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