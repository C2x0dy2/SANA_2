"""Tests for recent features/fixes: the never_cache CSRF fix, the onboarding
tutorial shown after registration, the Méditation section, and the group
"bubble" deep link. Kept in a separate file (pytest auto-discovers test_*.py)
so the existing tests.py doesn't need touching beyond its own fixture fix.
"""
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch

import json

from .models import SanaGroup, SolidarityMessage


class NeverCacheHeadersTests(TestCase):
    """Regression guard for the CSRF "Forbidden" bug: a cached copy of
    /login/ or /register/ embeds a stale CSRF token that no longer matches
    the cookie once Django rotates it after a successful login."""

    def setUp(self):
        cache.clear()

    def test_login_page_is_not_cacheable(self):
        response = self.client.get(reverse('sanasource:login'))
        self.assertIn('no-store', response.headers['Cache-Control'])

    def test_register_page_is_not_cacheable(self):
        response = self.client.get(reverse('sanasource:register'))
        self.assertIn('no-store', response.headers['Cache-Control'])


class OnboardingTutorialTests(TestCase):
    def setUp(self):
        cache.clear()

    @patch('sanasource.views.send_verification_email')
    def test_successful_registration_shows_tutorial_before_email_wait_page(self, mock_send):
        response = self.client.post(reverse('sanasource:register'), {
            'first_name': 'Robin', 'email': 'robin@test.com',
            'password1': 'Zr8!qLm2#Wp9x', 'password2': 'Zr8!qLm2#Wp9x',
            'username_anonyme': 'nuage_calme3',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'page/onboarding_tutorial.html')
        self.assertContains(response, 'tuto-slide')
        self.assertIn(reverse('sanasource:verify_email_sent'), response.context['continue_url'])
        self.assertIn('robin%40test.com', response.context['continue_url'])

    def test_failed_verification_email_skips_tutorial(self):
        with patch('sanasource.views.send_verification_email', side_effect=Exception('smtp down')):
            response = self.client.post(reverse('sanasource:register'), {
                'first_name': 'Robin', 'email': 'robin2@test.com',
                'password1': 'Zr8!qLm2#Wp9x', 'password2': 'Zr8!qLm2#Wp9x',
                'username_anonyme': 'nuage_calme4',
            })
        self.assertTemplateUsed(response, 'page/verify_email_sent.html')
        self.assertTrue(response.context['send_failed'])

    def test_verify_email_sent_view_reads_email_from_query_param(self):
        response = self.client.get(reverse('sanasource:verify_email_sent') + '?email=robin@test.com')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'robin@test.com')
        self.assertContains(response, 'Spams')

    def test_verify_email_sent_view_works_with_no_email_param(self):
        response = self.client.get(reverse('sanasource:verify_email_sent'))
        self.assertEqual(response.status_code, 200)


class MeditationSectionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='med@test.com', email='med@test.com', password='pass12345')
        self.client.force_login(self.user)

    def test_dashboard_has_dedicated_meditation_section(self):
        response = self.client.get(reverse('sanasource:dashboard'))
        self.assertContains(response, 'id="sec-meditation"')
        self.assertContains(response, "Commencer l'exercice")
        self.assertContains(response, 'onclick="openBreatheGame()"')

    def test_quick_card_points_at_meditation_not_ressources(self):
        response = self.client.get(reverse('sanasource:dashboard'))
        self.assertContains(response, "showSection('meditation',null)")

    def test_old_misleading_caption_is_gone(self):
        response = self.client.get(reverse('sanasource:dashboard'))
        self.assertNotContains(response, 'Exercice guidé de 5 minutes')


class GroupDeepLinkTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='grp@test.com', email='grp@test.com', password='pass12345')
        self.client.force_login(self.user)
        self.group = SanaGroup.objects.create(name='Groupe Test', description='desc', icon='🌸', created_by=self.user)

    def test_dashboard_group_card_links_to_group_page_with_open_param(self):
        response = self.client.get(reverse('sanasource:dashboard'))
        expected = f"{reverse('sanasource:group_page')}?open={self.group.id}"
        self.assertContains(response, expected)

    def test_group_join_button_stops_propagation_so_it_does_not_navigate(self):
        response = self.client.get(reverse('sanasource:dashboard'))
        self.assertContains(response, 'event.stopPropagation(); dashToggleGroup(')

    def test_group_page_renders_item_and_deep_link_script(self):
        response = self.client.get(reverse('sanasource:group_page') + f'?open={self.group.id}')
        self.assertContains(response, f'id="gi-{self.group.id}"')


class GroupDeletionTests(TestCase):
    def setUp(self):
        self.creator = User.objects.create_user(username='creator@test.com', email='creator@test.com', password='pass12345')
        self.other = User.objects.create_user(username='other@test.com', email='other@test.com', password='pass12345')
        self.group = SanaGroup.objects.create(name='Groupe Test', description='desc', icon='🌸', created_by=self.creator)

    def test_creator_can_delete_their_group(self):
        self.client.force_login(self.creator)
        response = self.client.post(reverse('sanasource:delete_group', args=[self.group.id]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SanaGroup.objects.filter(id=self.group.id).exists())

    def test_non_creator_cannot_delete_the_group(self):
        self.client.force_login(self.other)
        response = self.client.post(reverse('sanasource:delete_group', args=[self.group.id]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(SanaGroup.objects.filter(id=self.group.id).exists())

    def test_anonymous_user_cannot_delete_group(self):
        response = self.client.post(reverse('sanasource:delete_group', args=[self.group.id]))
        self.assertEqual(response.status_code, 401)
        self.assertTrue(SanaGroup.objects.filter(id=self.group.id).exists())

    def test_group_page_exposes_creator_id_for_delete_button(self):
        self.client.force_login(self.creator)
        response = self.client.get(reverse('sanasource:group_page'))
        self.assertContains(response, f'data-creator="{self.creator.id}"')


class SolidarityWallTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='sw1@test.com', email='sw1@test.com', password='pass12345')
        self.other = User.objects.create_user(username='sw2@test.com', email='sw2@test.com', password='pass12345')
        self.client.force_login(self.user)

    def test_dashboard_has_solidarity_wall_ui(self):
        response = self.client.get(reverse('sanasource:dashboard'))
        self.assertContains(response, 'id="solidarityWall"')
        self.assertContains(response, 'submitSolidarityMessage()')

    def test_anonymous_user_cannot_post_or_list(self):
        self.client.logout()
        response = self.client.get(reverse('sanasource:solidarity_wall_api'))
        self.assertEqual(response.status_code, 401)

    def test_post_message_then_list_shows_it(self):
        response = self.client.post(
            reverse('sanasource:solidarity_wall_api'),
            data=json.dumps({'content': 'Tu n\'es pas seul·e, ça va aller.'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data['heart_count'], 0)
        self.assertTrue(data['is_mine'])

        listing = self.client.get(reverse('sanasource:solidarity_wall_api'))
        self.assertEqual(len(listing.json()['messages']), 1)

    def test_too_short_message_rejected(self):
        response = self.client.post(
            reverse('sanasource:solidarity_wall_api'),
            data=json.dumps({'content': 'hi'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(SolidarityMessage.objects.count(), 0)

    def test_heart_toggle_adds_and_removes(self):
        msg = SolidarityMessage.objects.create(author=self.other, content='Courage à toi 🌸')
        url = reverse('sanasource:solidarity_heart_toggle', args=[msg.id])

        first = self.client.post(url).json()
        self.assertTrue(first['is_hearted'])
        self.assertEqual(first['heart_count'], 1)

        second = self.client.post(url).json()
        self.assertFalse(second['is_hearted'])
        self.assertEqual(second['heart_count'], 0)

    def test_report_hides_message_from_wall(self):
        msg = SolidarityMessage.objects.create(author=self.other, content='Message à signaler')
        report_url = reverse('sanasource:solidarity_report', args=[msg.id])
        response = self.client.post(report_url)
        self.assertEqual(response.status_code, 200)

        msg.refresh_from_db()
        self.assertTrue(msg.is_reported)
        listing = self.client.get(reverse('sanasource:solidarity_wall_api'))
        self.assertEqual(len(listing.json()['messages']), 0)
