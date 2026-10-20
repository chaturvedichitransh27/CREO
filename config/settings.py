import os
from pathlib import Path
import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env', override=False)
DEBUG = os.getenv('DJANGO_DEBUG', 'True').lower() == 'true'
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', '')
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError('Set DJANGO_SECRET_KEY when DJANGO_DEBUG=False.')
    SECRET_KEY = 'local-development-only-creo-change-before-deployment'
ALLOWED_HOSTS = [h.strip() for h in os.getenv('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1,[::1],testserver').split(',') if h.strip()]
if os.getenv('REPLIT_DEV_DOMAIN'):
    ALLOWED_HOSTS.append(os.environ['REPLIT_DEV_DOMAIN'])
if os.getenv('RENDER_EXTERNAL_HOSTNAME'):
    ALLOWED_HOSTS.append(os.environ['RENDER_EXTERNAL_HOSTNAME'])
CSRF_TRUSTED_ORIGINS = [s.strip() for s in os.getenv('CSRF_TRUSTED_ORIGINS', '').split(',') if s.strip()]
if os.getenv('REPLIT_DEV_DOMAIN'):
    CSRF_TRUSTED_ORIGINS.append('https://' + os.environ['REPLIT_DEV_DOMAIN'])
if os.getenv('RENDER_EXTERNAL_HOSTNAME'):
    CSRF_TRUSTED_ORIGINS.append('https://' + os.environ['RENDER_EXTERNAL_HOSTNAME'])
INSTALLED_APPS = ['django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes',
                  'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles',
                  'rest_framework', 'accounts', 'creators', 'portfolio', 'briefs', 'trust', 'engagements', 'studio']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'whitenoise.middleware.WhiteNoiseMiddleware',
              'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.common.CommonMiddleware',
              'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware',
              'django.contrib.messages.middleware.MessageMiddleware', 'django.middleware.clickjacking.XFrameOptionsMiddleware']
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [BASE_DIR / 'templates'],
              'APP_DIRS': True, 'OPTIONS': {'context_processors': ['django.template.context_processors.request',
              'django.contrib.auth.context_processors.auth', 'django.contrib.messages.context_processors.messages']}}]
WSGI_APPLICATION = 'config.wsgi.application'
DATABASES = {'default': dj_database_url.config(default=f'sqlite:///{BASE_DIR / "db.sqlite3"}', conn_max_age=60)}
AUTH_USER_MODEL = 'accounts.User'
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
MEDIA_URL = '/media/'
MEDIA_ROOT = Path(os.getenv('MEDIA_ROOT', str(BASE_DIR / 'media')))
PRIVATE_MEDIA_ROOT = Path(os.getenv('PRIVATE_MEDIA_ROOT', str(BASE_DIR / 'private_media')))
DATABASE_MEDIA = os.getenv('DATABASE_MEDIA', 'False').lower() == 'true'
STORAGES = {'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
            'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage' if DEBUG else 'whitenoise.storage.CompressedManifestStaticFilesStorage'}}
if DATABASE_MEDIA:
    STORAGES['default'] = {'BACKEND': 'portfolio.storage.DatabaseMediaStorage'}
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'home'
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = os.getenv('SECURE_SSL_REDIRECT', 'True').lower() == 'true'
    SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', '3600'))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = os.getenv('SECURE_HSTS_INCLUDE_SUBDOMAINS', 'False').lower() == 'true'
    SECURE_HSTS_PRELOAD = False
if os.getenv('TRUST_PROXY_HTTPS', 'False').lower() == 'true':
    # Enable only behind a proxy that removes client-supplied forwarding headers.
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
REST_FRAMEWORK = {'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'],
                  'DEFAULT_RENDERER_CLASSES': ['rest_framework.renderers.JSONRenderer']}

# Enable only with an API key from a project kept on Gemini's free tier.
# The API cannot prove billing status; this is an operator acknowledgement.
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
GEMINI_FREE_TIER_CONFIRMED = os.getenv('GEMINI_FREE_TIER_CONFIRMED', 'False').lower() == 'true'
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-3.5-flash-lite')
GEMINI_DAILY_LIMIT = 50
GEMINI_COOLDOWN_SECONDS = 5
