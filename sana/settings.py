from pathlib import Path
import logging
import os
import dj_database_url
from dotenv import load_dotenv
from django.core.exceptions import ImproperlyConfigured

# Settings.py itself logs a few boot-time diagnostics (below) before Django's
# own LOGGING setting (defined further down) takes effect during django.setup()
# — this basicConfig covers that brief window only; the LOGGING dict governs
# everything once the app is actually running.
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========================
# BASE
# ========================
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / '.env'

if ENV_FILE.exists():
    load_dotenv(ENV_FILE, override=False)
    if not (os.getenv('GEMINI_API_KEY') or '').strip():
        load_dotenv(ENV_FILE, override=True)
else:
    logger.warning('Environment file not found at %s', ENV_FILE)

# ========================
# SECURITY
# ========================
_INSECURE_SECRET_KEY_DEFAULT = 'django-insecure-change-me-in-production'
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', _INSECURE_SECRET_KEY_DEFAULT)

DEBUG = os.getenv('DJANGO_DEBUG', 'False').strip().lower() in {
    '1', 'true', 'yes', 'on'
}

# Refuse to boot with the insecure fallback key once DEBUG is off — running a
# real deployment with a guessable SECRET_KEY makes sessions and signed
# cookies (including CSRF tokens) forgeable.
if not DEBUG and SECRET_KEY == _INSECURE_SECRET_KEY_DEFAULT:
    logger.critical('DJANGO_SECRET_KEY is not set — refusing to start with an insecure default key in production.')
    raise ImproperlyConfigured(
        'DJANGO_SECRET_KEY must be set via environment variable when DEBUG=False.'
    )

# Comma-separated env override; defaults to the known Render hostnames plus
# localhost for local testing with DEBUG=False.
ALLOWED_HOSTS = [
    h.strip() for h in os.getenv(
        'DJANGO_ALLOWED_HOSTS',
        'sana-w4ru.onrender.com,sana-2-2.onrender.com,sana-2-1.onrender.com,localhost,127.0.0.1',
    ).split(',') if h.strip()
]

# Derived from ALLOWED_HOSTS (minus localhost, which browsers never send an
# HTTPS origin for) instead of a separately hardcoded list — the Render
# hostname has changed across service recreations before, and duplicating it
# in two places meant fixing DisallowedHost errors without also fixing CSRF.
CSRF_TRUSTED_ORIGINS = [
    f'https://{h}' for h in ALLOWED_HOSTS if h not in {'localhost', '127.0.0.1'}
]

# ========================
# APPLICATIONS
# ========================
INSTALLED_APPS = [
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'sanasource',
    'channels',
    'django_ratelimit',
]

# ========================
# MIDDLEWARE
# ========================
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',

    # Static files
    'whitenoise.middleware.WhiteNoiseMiddleware',

    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# ========================
# URLS
# ========================
ROOT_URLCONF = 'sana.urls'

# ========================
# TEMPLATES
# ========================
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'sanasource' / 'html'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

# ========================
# ASGI / CHANNELS
# ========================
ASGI_APPLICATION = 'sana.asgi.application'

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    },
}


# ========================
# DATABASE (PostgreSQL only — no SQLite fallback)
# ========================
DATABASE_URL = os.getenv('DATABASE_URL')

if not DATABASE_URL:
    logger.critical('DATABASE_URL is not set — refusing to start without a configured PostgreSQL database.')
    raise ImproperlyConfigured(
        'DATABASE_URL must be set, e.g. '
        'postgresql://postgres:PASSWORD@localhost:5432/sana_bd (development) '
        'or the DATABASE_URL provided automatically by Render/Supabase (production).'
    )

DATABASES = {
    'default': dj_database_url.parse(
        DATABASE_URL,
        conn_max_age=600,  # persistent connections instead of one per request
        conn_health_checks=True,
        # The URL itself already carries sslmode=require when the provider
        # (e.g. Supabase) needs it; this only forces SSL as a floor whenever
        # DEBUG is off, in case a production URL omits it.
        ssl_require=not DEBUG,
    )
}

_db_config = DATABASES['default']
logger.info(
    'Database configured: engine=%s host=%s name=%s debug=%s',
    _db_config.get('ENGINE'),
    _db_config.get('HOST') or 'local-socket',
    _db_config.get('NAME'),
    DEBUG,
)

# ========================
# PASSWORDS
# ========================
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ========================
# LANGUAGE / TIME
# ========================
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# ========================
# STATIC FILES
# ========================
STATIC_URL = '/static/'

STATICFILES_DIRS = [
    BASE_DIR / 'sanasource' / 'static',
]

STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}

# ========================
# SECURITY BONUS
# ========================
SECURE_CONTENT_TYPE_NOSNIFF = True

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# ========================
# AUTHENTICATION
# ========================
LOGIN_URL = 'sanasource:login'
LOGIN_REDIRECT_URL = 'sanasource:dashboard'
LOGOUT_REDIRECT_URL = 'sanasource:page_open'

# Session cookie: default expires when the browser closes; login_view
# extends it to SESSION_COOKIE_AGE only when "remember me" is checked
# (see login_view's request.session.set_expiry() call).
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14  # 14 days, used for the "remember me" case
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'

# ========================
# DEFAULT ID
# ========================
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ========================
# CACHE (also used by django-ratelimit for login/registration throttling)
# ========================
# In-process cache — fine as long as this app runs a single web worker (see
# Procfile: `gunicorn sana.wsgi:application`, no --workers flag, so 1 by
# default). If this is ever scaled to multiple workers/dynos, rate-limit
# counters would no longer be shared across them and should move to a
# shared backend (e.g. django-redis) instead.
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'sana-cache',
    }
}

# django-ratelimit's system check (E003) only officially recognizes
# Memcached/Redis as "shared" caches and errors on LocMemCache. LocMemCache's
# operations are internally lock-protected and correct within a single
# process, which matches this app's actual deployment (Procfile runs
# `gunicorn sana.wsgi:application` with no --workers flag, i.e. 1 worker) —
# so this is a deliberate, documented exception, not an oversight. Revisit
# (switch to django-redis) if this app is ever scaled to multiple workers.
SILENCED_SYSTEM_CHECKS = ['django_ratelimit.E003']

# ========================
# EMAIL (password reset, welcome email)
# ========================
_EMAIL_HOST = os.getenv('EMAIL_HOST', '').strip()
if not _EMAIL_HOST:
    # No SMTP configured at all: print emails to the console instead of
    # trying to send them, so nothing hangs or errors.
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
else:
    EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST = _EMAIL_HOST
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '').strip()
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '').strip()
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'True').strip().lower() in {'1', 'true', 'yes', 'on'}
EMAIL_TIMEOUT = 5  # seconds — fail fast rather than hang the request if SMTP is unreachable
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'SANA <no-reply@sana.app>')

logger.info(
    'Email configured: backend=%s host=%s port=%s user_set=%s password_length=%s tls=%s',
    EMAIL_BACKEND, EMAIL_HOST, EMAIL_PORT, bool(EMAIL_HOST_USER), len(EMAIL_HOST_PASSWORD), EMAIL_USE_TLS,
)

# ========================
# LOGGING
# ========================
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {
            'format': '{asctime} {levelname} {name} {message}',
            'style': '{',
        },
    },
    'handlers': {
        # Stdout only — Render captures stdout as logs, and its filesystem
        # is ephemeral so file-based handlers/rotation wouldn't persist.
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'standard',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        'sanasource': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        # Dedicated logger for auth events (login/registration/logout/password
        # reset) so they're easy to grep for separately from general app logs.
        'sanasource.auth': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        'django_ratelimit': {'handlers': ['console'], 'level': 'WARNING', 'propagate': False},
    },
}

# ========================
# API KEYS
# ========================
GEMINI_API_KEY = (os.getenv('GEMINI_API_KEY', '') or '').strip().strip('"').strip("'")
GEMINI_KEY_LOADED = bool(GEMINI_API_KEY)
GEMINI_KEY_LENGTH = len(GEMINI_API_KEY)
logger.info('Gemini key load status: loaded=%s length=%s', GEMINI_KEY_LOADED, GEMINI_KEY_LENGTH)

# ========================
# VAPID (Web Push)
# ========================
VAPID_PRIVATE_KEY = os.getenv('VAPID_PRIVATE_KEY', '').strip()
VAPID_PUBLIC_KEY  = os.getenv('VAPID_PUBLIC_KEY', '').strip()
VAPID_EMAIL       = os.getenv('VAPID_EMAIL', 'contact@sana.app').strip()