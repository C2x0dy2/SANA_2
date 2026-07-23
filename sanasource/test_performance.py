"""Non-regression test for the "many people register -> everyone else gets
stuck" bug: a single blocking request (registration's verification-email
send) must not freeze the whole app for concurrent users, which is what a
single-worker synchronous server (the old Procfile) did. Runs against a
real ASGI server (channels' test live server, same application as
production's Daphne) instead of Django's plain test client, since the bug
was about server concurrency, not application logic.

KNOWN FAILING as of this commit: the concurrent login still measured ~3.6s
(target was <1s). Manual testing earlier (a real `daphne` process vs a
single-threaded `wsgiref` server) showed the fix working outside of Django's
test harness, so this is likely specific to how Django's ASGIHandler
dispatches sync views here (adapt_method_mode wraps them with
sync_to_async(..., thread_sensitive=True), which can funnel unrelated
requests through one shared worker thread depending on asgiref's context
handling) rather than the production fix itself being wrong. Left failing
and documented rather than deleted or loosened, so this doesn't get
quietly forgotten - needs a closer look at asgiref's thread-sensitive
executor behavior under ChannelsLiveServerTestCase specifically.
"""
import threading
import time
from unittest.mock import patch

import requests
from channels.testing import ChannelsLiveServerTestCase
from django.core.cache import cache


class RegistrationDoesNotBlockOtherUsersTests(ChannelsLiveServerTestCase):
    def setUp(self):
        cache.clear()

    def test_concurrent_login_is_not_blocked_by_a_slow_registration(self):
        results = {}

        def slow_send(request, user):
            time.sleep(2)

        def do_register():
            session = requests.Session()
            session.get(self.live_server_url + '/register/', timeout=30)
            token = session.cookies.get('csrftoken')
            t0 = time.time()
            with patch('sanasource.views.send_verification_email', side_effect=slow_send):
                session.post(
                    self.live_server_url + '/register/',
                    data={
                        'csrfmiddlewaretoken': token,
                        'first_name': 'Perf', 'email': 'perf@test.com',
                        'password1': 'Zr8!qLm2#Wp9x', 'password2': 'Zr8!qLm2#Wp9x',
                        'username_anonyme': 'lac_paisible8',
                    },
                    headers={'Referer': self.live_server_url + '/register/'},
                    timeout=30,
                )
            results['register_time'] = time.time() - t0

        def do_login():
            time.sleep(0.4)  # let the registration request start first
            t0 = time.time()
            requests.get(self.live_server_url + '/login/', timeout=30)
            results['login_time'] = time.time() - t0

        register_thread = threading.Thread(target=do_register)
        login_thread = threading.Thread(target=do_login)
        register_thread.start()
        login_thread.start()
        register_thread.join(timeout=15)
        login_thread.join(timeout=15)

        self.assertIn('register_time', results, 'registration request never completed')
        self.assertIn('login_time', results, 'login request never completed')
        # Sanity check the slow email-send actually happened as designed.
        self.assertGreater(results['register_time'], 1.5)
        # The real assertion: a concurrent login must not be stuck waiting
        # behind the slow registration - this is what broke under the old
        # single-worker gunicorn Procfile.
        self.assertLess(results['login_time'], 1.0)
