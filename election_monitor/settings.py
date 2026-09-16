"""
Django settings for Ethiopia Election Monitor project.
"""
import os
from pathlib import Path
import environ

# Build paths
BASE_DIR = Path(__file__).resolve().parent.parent

#LOAD .ENV FILE HERE
from dotenv import load_dotenv
load_dotenv(BASE_DIR / '.env')


# Initialize environment variables
env = environ.Env(
    DEBUG=(bool, False),
    SECRET_KEY=(str, 'django-insecure-change-this-in-production'),
)

def env_list(name, default=''):
    value = env(name, default=default)
    return [item.strip() for item in value.split(',') if item.strip()]


# CSRF Configuration for reverse proxies / load balancers
CSRF_TRUSTED_ORIGINS = env_list(
    'CSRF_TRUSTED_ORIGINS',
    'https://ethio-monitor.investigate.africa,http://ethio-monitor.investigate.africa,http://localhost,http://127.0.0.1',
)

# Trust the X-Forwarded-Proto header from Nginx
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Read .env file if it exists (optional)
environ.Env.read_env(os.path.join(BASE_DIR, '.env'))

# SECURITY
SECRET_KEY = env('SECRET_KEY')
DEBUG = env('DEBUG')
ALLOWED_HOSTS = env_list('ALLOWED_HOSTS', '*')

# CSRF cookie settings
CSRF_COOKIE_SECURE = env.bool('CSRF_COOKIE_SECURE', default=not DEBUG)
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = 'Lax'

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'huey.contrib.djhuey',
    'django_q',
    
    # Third-party
    'rest_framework',
    'corsheaders',
    'storages',
    
    # Local
    'dashboard',
]

MIDDLEWARE = [
    'election_monitor.health.HealthCheckMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'election_monitor.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],  # or your template dirs
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
            
            'builtins': ['dashboard.templatetags.custom_filters'],
        },
    },
]

WSGI_APPLICATION = 'election_monitor.wsgi.application'

# Database
DATABASE_URL = env('DATABASE_URL', default='')
if DATABASE_URL:
    DATABASES = {
        'default': {
            **env.db_url('DATABASE_URL'),
            'CONN_MAX_AGE': 600,
        },
    }
elif env('DB_HOST', default=''):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': env('DB_NAME', default='ethiopia_election_db'),
            'USER': env('DB_USER', default='ethiopia_user'),
            'PASSWORD': env('DB_PASSWORD', default=''),
            'HOST': env('DB_HOST'),
            'PORT': env('DB_PORT', default='5432'),
            'OPTIONS': {
                'sslmode': env('DB_SSLMODE', default='require'),
            },
            'CONN_MAX_AGE': 600,
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': env('SQLITE_PATH', default=str(BASE_DIR / 'db.sqlite3')),
        }
    }

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Addis_Ababa'
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [
    BASE_DIR / 'dashboard' / 'static',  
]
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}

# Media files
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

AWS_STORAGE_BUCKET_NAME = env('AWS_STORAGE_BUCKET_NAME', default='')
if AWS_STORAGE_BUCKET_NAME:
    AWS_ACCESS_KEY_ID = env('AWS_ACCESS_KEY_ID', default='')
    AWS_SECRET_ACCESS_KEY = env('AWS_SECRET_ACCESS_KEY', default='')
    AWS_S3_REGION_NAME = env('AWS_S3_REGION_NAME', default='eu-west-1')
    AWS_S3_ENDPOINT_URL = env('AWS_S3_ENDPOINT_URL', default=None)
    AWS_S3_ADDRESSING_STYLE = env('AWS_S3_ADDRESSING_STYLE', default='path')
    AWS_S3_SIGNATURE_VERSION = env('AWS_S3_SIGNATURE_VERSION', default='s3v4')
    AWS_QUERYSTRING_AUTH = env.bool('AWS_QUERYSTRING_AUTH', default=False)
    AWS_DEFAULT_ACL = None

    aws_s3_custom_domain = env('AWS_S3_CUSTOM_DOMAIN', default='')
    if aws_s3_custom_domain:
        AWS_S3_CUSTOM_DOMAIN = aws_s3_custom_domain
        AWS_S3_URL_PROTOCOL = env('AWS_S3_URL_PROTOCOL', default='http:')
        MEDIA_URL = f"{AWS_S3_URL_PROTOCOL}//{AWS_S3_CUSTOM_DOMAIN}/"

    STORAGES['default'] = {
        'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage',
    }

# paths to your PEP Excel files (local or absolute)
PEP_FILES = {
    'hopr': os.getenv('HOPR_FILE_PATH', str(BASE_DIR / 'media' / 'peps' / 'HoPR_Candidates.xlsx')),
    'regional': os.getenv('REGIONAL_FILE_PATH', str(BASE_DIR / 'media' / 'peps' / 'Regional_Candidates.xlsx')),
    'executive': os.getenv('EXECUTIVE_FILE_PATH', str(BASE_DIR / 'media' / 'peps' / 'Executive_Members.xlsx')),
}

# REST Framework
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [],
    'DEFAULT_PERMISSION_CLASSES': [],
}

# CORS
CORS_ALLOW_ALL_ORIGINS = env.bool('CORS_ALLOW_ALL_ORIGINS', default=DEBUG)
CORS_ALLOWED_ORIGINS = env_list('CORS_ALLOWED_ORIGINS')

# Django-Q2 Configuration using Redis Broker
Q_CLUSTER = {
    'name': 'ethiopia_monitor_cluster',
    'workers': 2,                # Number of parallel worker processes
    'recycle': 500,              # Restart workers after 500 tasks to prevent memory leaks
    'timeout': 600,              # Automatically time out a task if it takes more than 10 mins
    'retry': 700,                # Retry time slightly higher than timeout
    'django_redis': 'default',   # Tells Django-Q to use your existing Redis cache config
}

# ── REDIS CACHE ──────────────────────────────────────────────
CFA_VALKEY_URL = env('CFA_VALKEY_URL', default='')
REDIS_URL = env('REDIS_URL', default=CFA_VALKEY_URL or 'redis://127.0.0.1:6379/1')
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "SOCKET_CONNECT_TIMEOUT": 5,
            "SOCKET_TIMEOUT": 5,
        }
    }
}

# Groq API
GROQ_API_KEY = env('GROQ_API_KEY', default='')
GROQ_MODEL = env('GROQ_MODEL', default='qwen/qwen3.6-27b')

# Optional shared TEI service for the AFRO-XLMR classifier. When the URL is
# absent, the existing local detector remains the development fallback.
AFRO_XLMR_API_URL = env('AFRO_XLMR_API_URL', default='')
AFRO_XLMR_API_KEY = env('AFRO_XLMR_API_KEY', default='')
AFRO_XLMR_API_TIMEOUT_SECONDS = env.float('AFRO_XLMR_API_TIMEOUT_SECONDS', default=30.0)

# Data source URLs
MELTWATER_URL = env('MELTWATER_URL', default='')
CIVICSIGNALS_URL = env('CIVICSIGNALS_URL', default='')
TIKTOK_URL = env('TIKTOK_URL', default='')
OPENMEASURES_URL = env('OPENMEASURES_URL', default='')
ORIGINAL_POSTS_URL = env('ORIGINAL_POSTS_URL', default='')
PEPS_CSV_URL = env('PEPS_CSV_URL', default='')

# Allow uploads up to 100MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 534773760  # 510 MB (510 * 1024 * 1024)

FILE_UPLOAD_MAX_MEMORY_SIZE = 26214400  # 25 MB

# Logging
DATABASE_LOG_LEVEL = env('DATABASE_LOG_LEVEL', default='INFO')
DATABASE_LOG_HANDLER_ENABLED = env.bool('DATABASE_LOG_HANDLER_ENABLED', default=False)
APPLICATION_LOG_RETENTION_DAYS = env.int('APPLICATION_LOG_RETENTION_DAYS', default=90)
root_log_handlers = ['console']
if DATABASE_LOG_HANDLER_ENABLED:
    root_log_handlers.append('database')
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'simple': {
            'format': '[{levelname}] {name}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
        'database': {
            'class': 'dashboard.utils.app_logging.DatabaseLogHandler',
            'level': DATABASE_LOG_LEVEL,
            'formatter': 'simple',
        },
    },
    'root': {
        'handlers': root_log_handlers,
        'level': env('LOG_LEVEL', default='INFO'),
    },
    'loggers': {
        'django.db.backends': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
    },
}

# Gama model
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
GEMMA_BASE_MODEL_PATH = 'unsloth/gemma-4-e4b-it-unsloth-bnb-4bit'
GEMMA_LORA_ADAPTER_PATH = './model_cache/gemma-lora-hate-speech'
GEMMA_TTP_MODEL_PATH = './model_cache/gemma-merged-4bit'
GEMMA_LOKA_MODEL_PATH = os.path.join(BASE_DIR, 'dashboard', 'model_cache', 'gemma_4_multiclass_hate_lexicon_lora')
