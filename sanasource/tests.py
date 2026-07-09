import json
import os
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings

from datetime import date, timedelta

from .models import Conversation, Journal, JournalEntry
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
