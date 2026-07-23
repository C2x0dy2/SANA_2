import json
import os
import re
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from datetime import date, timedelta

from .models import Conversation, Journal, JournalEntry, UserProfile
from .views import _detect_emotional_state, _fallback_reply, _normalize_messages, _get_valid_gemini_key


class ChatbotHelperTests(SimpleTestCase):
    def test_normalize_messages_keeps_recent_turns_only(self):
        messages = [
            {'role': 'user', 'content': 'bonjour'},
            {'role': 'assistant', 'content': 'bonjour, comment puis-je t’aider ?'},
            {'role': 'user', 'content': 'j’ai une grosse journée'},
            {'role': 'assistant', 'content': 'tu peux me dire ce qui s’est passé'},
            {'role': 'user', 'content': 'je me sens très seul'},
        ]

        normalized = _normalize_messages(messages, max_messages=4)

        self.assertEqual(len(normalized), 4)
        self.assertEqual(normalized[-1]['content'], 'je me sens très seul')

    def test_detect_emotional_state_identifies_distress(self):
        emotion = _detect_emotional_state([
            {'role': 'user', 'content': 'je me sens très seul et triste aujourd’hui'}
        ])

        self.assertEqual(emotion['label'], 'sad')
        self.assertIn('sad', emotion['tone'])

    @override_settings(GEMINI_API_KEY='')
    @patch.dict(os.environ, {'GEMINI_API_KEY': 'AQ.Ab1234567890abcdefghijklmnopqrstuv'}, clear=False)
    def test_get_valid_gemini_key_uses_environment_variable(self):
        self.assertEqual(_get_valid_gemini_key(), 'AQ.Ab1234567890abcdefghijklmnopqrstuv')

    @override_settings(GEMINI_API_KEY='')
    @patch.dict(os.environ, {'GEMINI_API_KEY': 'your-api-key...'}, clear=False)
    def test_get_valid_gemini_key_rejects_placeholder_values(self):
        self.assertIsNone(_get_valid_gemini_key())

    def test_fallback_reply_is_contextual_and_supportive(self):
        reply = _fallback_reply([
            {'role': 'user', 'content': 'je me sens très seul et j’ai envie de pleurer'}
        ])

        self.assertIn('Je suis là', reply)
        self.assertIn('?', reply)


class ConversationApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='u1@test.com', email='u1@test.com', password='pass12345')
        self.client.force_login(self.user)

    def test_conversations_list_auto_creates_first_conversation_with_welcome_message(self):
        response = self.client.get('/api/conversations/')

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data['conversations']), 1)

        conversation = Conversation.objects.get()
        self.assertEqual(conversation.user, self.user)
        self.assertEqual(conversation.messages.count(), 1)
        self.assertEqual(conversation.messages.first().role, 'assistant')

    def test_create_conversation_returns_new_entry(self):
        response = self.client.post('/api/conversations/', content_type='application/json')

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Conversation.objects.filter(user=self.user).count(), 1)

    def test_conversation_detail_is_scoped_to_owner(self):
        other = User.objects.create_user(username='u2@test.com', email='u2@test.com', password='pass12345')
        conversation = Conversation.objects.create(user=other)

        response = self.client.get(f'/api/conversations/{conversation.id}/')

        self.assertEqual(response.status_code, 404)

    def test_rename_and_delete_conversation(self):
        conversation = Conversation.objects.create(user=self.user)

        response = self.client.patch(
            f'/api/conversations/{conversation.id}/',
            data=json.dumps({'title': 'Mon titre'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        conversation.refresh_from_db()
        self.assertEqual(conversation.title, 'Mon titre')

        response = self.client.delete(f'/api/conversations/{conversation.id}/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Conversation.objects.filter(id=conversation.id).exists())

    @patch('sanasource.views._get_valid_gemini_key', return_value='fake-key')
    @patch('sanasource.views.genai.Client')
    def test_sana_chat_persists_messages_scoped_to_conversation_and_titles_it(self, mock_client_cls, mock_key):
        mock_response = MagicMock()
        mock_response.text = 'Réponse de test'
        mock_client_cls.return_value.models.generate_content.return_value = mock_response

        conversation = Conversation.objects.create(user=self.user)
        other_conversation = Conversation.objects.create(user=self.user)

        response = self.client.post(
            '/api/chat/',
            data=json.dumps({'conversation_id': conversation.id, 'message': "Je me sens triste aujourd'hui"}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['reply'], 'Réponse de test')
        self.assertEqual(data['conversation_id'], conversation.id)

        conversation.refresh_from_db()
        self.assertEqual(conversation.messages.count(), 2)
        self.assertNotEqual(conversation.title, Conversation.DEFAULT_TITLE)
        self.assertEqual(other_conversation.messages.count(), 0)


class JournalApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='j1@test.com', email='j1@test.com', password='pass12345')
        self.client.force_login(self.user)

    def test_create_and_list_journals(self):
        response = self.client.post(
            '/api/journal/',
            data=json.dumps({'title': 'Journal des rêves', 'icon': '🌙', 'color': 'navy'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201)
        created = response.json()
        self.assertEqual(created['title'], 'Journal des rêves')
        self.assertEqual(created['color_hex'], dict(Journal.COLOR_CHOICES)['navy'])

        response = self.client.get('/api/journal/')
        self.assertEqual(response.status_code, 200)
        journals = response.json()['journals']
        self.assertEqual(len(journals), 1)
        self.assertEqual(journals[0]['entry_count'], 0)

    def test_journal_detail_is_scoped_to_owner(self):
        other = User.objects.create_user(username='j2@test.com', email='j2@test.com', password='pass12345')
        journal = Journal.objects.create(user=other)

        response = self.client.get(f'/api/journal/{journal.id}/dates/')

        self.assertEqual(response.status_code, 404)

    def test_rename_and_delete_journal(self):
        journal = Journal.objects.create(user=self.user)

        response = self.client.patch(
            f'/api/journal/{journal.id}/',
            data=json.dumps({'title': 'Nouveau titre', 'color': 'forest'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        journal.refresh_from_db()
        self.assertEqual(journal.title, 'Nouveau titre')
        self.assertEqual(journal.color, 'forest')

        response = self.client.delete(f'/api/journal/{journal.id}/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Journal.objects.filter(id=journal.id).exists())

    def test_entry_autosave_upsert_and_blank_delete(self):
        journal = Journal.objects.create(user=self.user)
        today_str = date.today().isoformat()

        response = self.client.put(
            f'/api/journal/{journal.id}/entry/{today_str}/',
            data=json.dumps({'title': 'Un titre', 'content': 'Cher journal...', 'mood': 'bien'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(JournalEntry.objects.filter(journal=journal, entry_date=date.today()).count(), 1)

        # Re-saving the same day updates the row rather than creating a second one (unique_together).
        response = self.client.put(
            f'/api/journal/{journal.id}/entry/{today_str}/',
            data=json.dumps({'title': 'Un titre modifié', 'content': 'Suite...', 'mood': 'bien'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(JournalEntry.objects.filter(journal=journal).count(), 1)
        entry = JournalEntry.objects.get(journal=journal)
        self.assertEqual(entry.title, 'Un titre modifié')

        # Saving blank title+content deletes the row instead of leaving an empty entry.
        response = self.client.put(
            f'/api/journal/{journal.id}/entry/{today_str}/',
            data=json.dumps({'title': '', 'content': '', 'mood': ''}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])
        self.assertFalse(JournalEntry.objects.filter(journal=journal).exists())

    def test_entry_nav_skips_empty_days_and_always_includes_today(self):
        journal = Journal.objects.create(user=self.user)
        today = date.today()
        five_days_ago = today - timedelta(days=5)
        JournalEntry.objects.create(journal=journal, entry_date=five_days_ago, content='Il y a 5 jours')

        response = self.client.get(f'/api/journal/{journal.id}/entry/{today.isoformat()}/')

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['nav']['prev_date'], five_days_ago.isoformat())
        self.assertIsNone(data['nav']['next_date'])
        self.assertTrue(data['nav']['is_today'])
        self.assertFalse(data['entry']['exists'])

    def test_journal_dates_only_lists_populated_entries(self):
        journal = Journal.objects.create(user=self.user)
        JournalEntry.objects.create(journal=journal, entry_date=date.today(), content='Contenu réel')
        JournalEntry.objects.create(journal=journal, entry_date=date.today() - timedelta(days=1), content='', title='')

        response = self.client.get(f'/api/journal/{journal.id}/dates/')

        self.assertEqual(response.status_code, 200)
        dates = response.json()['dates']
        self.assertEqual(len(dates), 1)
        self.assertEqual(dates[0]['date'], date.today().isoformat())


# ============================================================
# AUTHENTICATION
# ============================================================

class SignalTests(TestCase):
    """UserProfile auto-creation via the post_save signal (signals.py)."""

    def test_profile_auto_created_for_user_made_outside_register_view(self):
        user = User.objects.create(username='outside@test.com', email='outside@test.com')
        profile = UserProfile.objects.get(user=user)
        self.assertEqual(profile.username_anonyme, f'user_{user.pk}')

    def test_two_profile_less_users_dont_collide_on_username_anonyme(self):
        user1 = User.objects.create(username='a@test.com', email='a@test.com')
        user2 = User.objects.create(username='b@test.com', email='b@test.com')
        self.assertNotEqual(
            UserProfile.objects.get(user=user1).username_anonyme,
            UserProfile.objects.get(user=user2).username_anonyme,
        )


class RegistrationTests(TestCase):
    def setUp(self):
        cache.clear()

    def _register(self, **overrides):
        data = {
            'first_name': 'Alex', 'email': 'alex@test.com',
            'password1': 'Zr8!qLm2#Wp9x', 'password2': 'Zr8!qLm2#Wp9x',
            'username_anonyme': 'nebula_quiet7',
        }
        data.update(overrides)
        return self.client.post(reverse('sanasource:register'), data)

    def test_weak_password_rejected_in_french_and_no_user_created(self):
        response = self._register(password1='password', password2='password')
        self.assertEqual(response.status_code, 200)
        self.assertIn('courant', response.context['error'])
        self.assertFalse(User.objects.filter(username='alex@test.com').exists())

    def test_mismatched_passwords_rejected(self):
        response = self._register(password2='Different1!')
        self.assertEqual(response.context['error'], 'Les mots de passe ne correspondent pas.')
        self.assertFalse(User.objects.filter(username='alex@test.com').exists())

    def test_invalid_email_format_rejected(self):
        response = self._register(email='not-an-email')
        self.assertIn('valide', response.context['error'])
        self.assertFalse(User.objects.filter(username='not-an-email').exists())

    @patch('sanasource.views.send_verification_email')
    def test_successful_registration_creates_inactive_user_and_sends_verification_email(self, mock_send_verification):
        response = self._register()
        self.assertEqual(response.status_code, 200)  # "check your email" page, not a redirect
        user = User.objects.get(username='alex@test.com')
        self.assertFalse(user.is_active)
        self.assertEqual(user.profile.username_anonyme, 'nebula_quiet7')
        mock_send_verification.assert_called_once()
        self.assertEqual(mock_send_verification.call_args[0][1], user)
        # Not logged in — an inactive account isn't authenticated yet.
        self.assertNotIn('_auth_user_id', self.client.session)
        dash = self.client.get(reverse('sanasource:dashboard'))
        self.assertRedirects(dash, reverse('sanasource:login'))

    def test_verification_email_send_failure_still_shows_confirmation_page(self):
        with patch('sanasource.views.send_verification_email', side_effect=Exception('smtp down')):
            response = self._register()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(User.objects.filter(username='alex@test.com', is_active=False).exists())
        self.assertContains(response, 'compte a bien été créé')

    def test_duplicate_email_rejected(self):
        User.objects.create_user(username='alex@test.com', email='alex@test.com', password='Xk9#mQ2vLp!7Rz')
        response = self._register()
        self.assertIn('existe déjà', response.context['error'])

    def test_duplicate_username_anonyme_rejected(self):
        other = User.objects.create_user(username='other@test.com', email='other@test.com', password='Xk9#mQ2vLp!7Rz')
        other.profile.username_anonyme = 'nebula_quiet7'
        other.profile.save()
        response = self._register()
        self.assertIn('déjà pris', response.context['error'])

    def test_registration_rate_limited_after_five_per_hour(self):
        for i in range(5):
            self._register(email=f'user{i}@test.com', username_anonyme=f'user{i}_anon')
            self.client.logout()  # each successful registration logs the client in
        response = self._register(email='oneMore@test.com', username_anonyme='oneMore_anon')
        self.assertEqual(response.status_code, 429)
        self.assertFalse(User.objects.filter(username='oneMore@test.com').exists())


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class LoginTests(TestCase):
    # A fast, non-cryptographic hasher here is the standard Django testing
    # practice for auth-heavy suites — PBKDF2's deliberate slowness (~1s per
    # check_password() call) made the rate-limit loop below vulnerable to
    # occasionally straddling django-ratelimit's fixed one-minute window
    # under load, which is a test-timing flake, not a bug in the rate limiter.
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='login@test.com', email='login@test.com', password='Xk9#mQ2vLp!7Rz')

    def test_correct_credentials_log_in(self):
        response = self.client.post(reverse('sanasource:login'), {'email': 'login@test.com', 'password': 'Xk9#mQ2vLp!7Rz'})
        self.assertRedirects(response, reverse('sanasource:dashboard'))

    def test_incorrect_password_shows_generic_error(self):
        response = self.client.post(reverse('sanasource:login'), {'email': 'login@test.com', 'password': 'wrong'})
        self.assertEqual(response.context['error'], 'Identifiants invalides')

    def test_unknown_email_shows_same_generic_error(self):
        response = self.client.post(reverse('sanasource:login'), {'email': 'nobody@test.com', 'password': 'whatever'})
        self.assertEqual(response.context['error'], 'Identifiants invalides')

    def test_remember_me_unchecked_expires_at_browser_close(self):
        self.client.post(reverse('sanasource:login'), {'email': 'login@test.com', 'password': 'Xk9#mQ2vLp!7Rz'})
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    def test_remember_me_checked_persists_for_session_cookie_age(self):
        self.client.post(reverse('sanasource:login'), {
            'email': 'login@test.com', 'password': 'Xk9#mQ2vLp!7Rz', 'remember': 'on',
        })
        self.assertFalse(self.client.session.get_expire_at_browser_close())

    def test_login_rate_limited_after_ten_per_minute(self):
        for _ in range(10):
            self.client.post(reverse('sanasource:login'), {'email': 'login@test.com', 'password': 'wrong'})
        response = self.client.post(reverse('sanasource:login'), {'email': 'login@test.com', 'password': 'wrong'})
        self.assertEqual(response.status_code, 429)

    def test_unverified_account_with_correct_password_shown_specific_message(self):
        User.objects.create_user(username='unverified@test.com', email='unverified@test.com', password='Xk9#mQ2vLp!7Rz', is_active=False)
        response = self.client.post(reverse('sanasource:login'), {'email': 'unverified@test.com', 'password': 'Xk9#mQ2vLp!7Rz'})
        self.assertIn('pas encore vérifiée', response.context['error'])
        self.assertEqual(response.context['unverified_email'], 'unverified@test.com')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_unverified_account_with_wrong_password_shows_generic_error(self):
        User.objects.create_user(username='unverified2@test.com', email='unverified2@test.com', password='Xk9#mQ2vLp!7Rz', is_active=False)
        response = self.client.post(reverse('sanasource:login'), {'email': 'unverified2@test.com', 'password': 'wrong'})
        self.assertEqual(response.context['error'], 'Identifiants invalides')
        self.assertNotIn('unverified_email', response.context)


class LogoutTests(TestCase):
    def test_logout_clears_session_and_redirects(self):
        user = User.objects.create_user(username='out@test.com', email='out@test.com', password='Xk9#mQ2vLp!7Rz')
        self.client.force_login(user)
        response = self.client.get(reverse('sanasource:logout'))
        self.assertRedirects(response, reverse('sanasource:page_open'))
        self.assertNotIn('_auth_user_id', self.client.session)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class EmailVerificationTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox = []
        self.user = User.objects.create_user(
            username='verifyme@test.com', email='verifyme@test.com',
            password='Xk9#mQ2vLp!7Rz', first_name='Sam', is_active=False,
        )

    def _link_for(self, user):
        from sanasource.tokens import email_verification_token
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode
        uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
        token = email_verification_token.make_token(user)
        return reverse('sanasource:verify_email', kwargs={'uidb64': uidb64, 'token': token})

    @patch('sanasource.views.send_welcome_email')
    def test_valid_link_activates_and_logs_in(self, mock_welcome_email):
        response = self.client.get(self._link_for(self.user))
        self.assertRedirects(response, reverse('sanasource:dashboard'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertIn('_auth_user_id', self.client.session)
        mock_welcome_email.assert_called_once_with(self.user)
        # can now log in normally too
        self.client.logout()
        self.assertTrue(self.client.login(username='verifyme@test.com', password='Xk9#mQ2vLp!7Rz'))

    def test_reusing_link_after_verification_shows_already_verified(self):
        link = self._link_for(self.user)
        self.client.get(link)
        self.client.logout()
        response = self.client.get(link)
        self.assertContains(response, 'Déjà')

    def test_tampered_token_rejected(self):
        response = self.client.get(self._link_for(self.user)[:-5] + 'xxxx/')
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertNotContains(response, 'Déjà')

    def test_unknown_uid_rejected_without_error(self):
        response = self.client.get('/verify-email/bogus/bogus-token/')
        self.assertEqual(response.status_code, 200)

    def test_registration_email_link_actually_verifies(self):
        # End-to-end: register -> real email -> click link -> active + logged in.
        self.client.post(reverse('sanasource:register'), {
            'first_name': 'Jo', 'email': 'jo@test.com',
            'password1': 'Zr8!qLm2#Wp9x', 'password2': 'Zr8!qLm2#Wp9x',
            'username_anonyme': 'jo_anon',
        })
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        m = re.search(r'(/verify-email/[^\s]+/)', body)
        self.assertIsNotNone(m, f'no verification link found in: {body}')
        response = self.client.get(m.group(1))
        self.assertRedirects(response, reverse('sanasource:dashboard'))
        self.assertTrue(User.objects.get(username='jo@test.com').is_active)

    def test_resend_verification_sends_new_email_for_unverified_account(self):
        response = self.client.post(reverse('sanasource:resend_verification'), {'email': 'verifyme@test.com'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)

    def test_resend_verification_is_silent_for_unknown_or_active_accounts(self):
        active_user = User.objects.create_user(username='active@test.com', email='active@test.com', password='Xk9#mQ2vLp!7Rz')
        for email in ['nobody@test.com', 'active@test.com']:
            mail.outbox = []
            response = self.client.post(reverse('sanasource:resend_verification'), {'email': email})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(mail.outbox), 0)  # same page shown either way, no leak


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class PasswordResetFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox = []
        self.user = User.objects.create_user(username='reset@test.com', email='reset@test.com', password='OldPassw0rd!23')

    def _extract_confirm_url(self, body):
        m = re.search(r'/password-reset/confirm/([^/]+)/([^/\s]+)/', body)
        self.assertIsNotNone(m, f'no reset link found in email body: {body}')
        return f'/password-reset/confirm/{m.group(1)}/{m.group(2)}/'

    def test_full_round_trip(self):
        response = self.client.post(reverse('sanasource:password_reset'), {'email': 'reset@test.com'})
        self.assertRedirects(response, reverse('sanasource:password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)

        confirm_url = self._extract_confirm_url(mail.outbox[0].body)
        redirect = self.client.get(confirm_url)
        set_password_url = redirect['Location']

        weak = self.client.post(set_password_url, {'new_password1': 'password', 'new_password2': 'password'})
        self.assertEqual(weak.status_code, 200)
        self.assertFalse(self.client.login(username='reset@test.com', password='password'))

        response = self.client.post(set_password_url, {
            'new_password1': 'Zr8!qLm2#Wp9x', 'new_password2': 'Zr8!qLm2#Wp9x',
        })
        self.assertRedirects(response, reverse('sanasource:password_reset_complete'))

        self.assertFalse(self.client.login(username='reset@test.com', password='OldPassw0rd!23'))
        self.assertTrue(self.client.login(username='reset@test.com', password='Zr8!qLm2#Wp9x'))

    def test_invalid_token_shows_error_not_form(self):
        response = self.client.get('/password-reset/confirm/bogus/bogus-token/')
        response = self.client.get(response['Location']) if response.status_code == 302 else response
        self.assertNotIn(b'name="new_password1"', response.content)

    def test_unknown_email_does_not_reveal_account_existence(self):
        response = self.client.post(reverse('sanasource:password_reset'), {'email': 'nobody@test.com'})
        self.assertRedirects(response, reverse('sanasource:password_reset_done'))
        self.assertEqual(len(mail.outbox), 0)


class AuthRegressionSmokeTests(TestCase):
    """A logged-in user should still be able to reach the app's main pages
    after the session/middleware/ALLOWED_HOSTS changes in this pass."""

    def setUp(self):
        user = User.objects.create_user(username='smoke@test.com', email='smoke@test.com', password='Xk9#mQ2vLp!7Rz')
        self.client.force_login(user)

    def test_dashboard_loads(self):
        self.assertEqual(self.client.get(reverse('sanasource:dashboard')).status_code, 200)

    def test_journal_landing_loads(self):
        self.assertEqual(self.client.get(reverse('sanasource:journal_home')).status_code, 200)

    def test_group_page_loads(self):
        self.assertEqual(self.client.get(reverse('sanasource:group_page')).status_code, 200)
