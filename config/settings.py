"""
Django settings for election_monitor project.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# 1. Load environment variables from the .env file
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# 2. Security Settings (Pulled from .env)
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'django-insecure-fallback-key-for-local-dev-only')
DEBUG = os.environ.get('DJANGO_DEBUG', 'True').lower() in ['true', '1', 'yes']
ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', '*').split(',')

# ... [Keep your INSTALLED_APPS, MIDDLEWARE, ROOT_URLCONF, etc. exactly as they were] ...

# 3. Database Settings (Pulled from .env)
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME', 'election_monitor'),
        'USER': os.environ.get('DB_USER', 'postgres'),
        'PASSWORD': os.environ.get('DB_PASSWORD', 'postgres'),
        'HOST': os.environ.get('DB_HOST', 'localhost'),
        'PORT': os.environ.get('DB_PORT', '5432'),
        'OPTIONS': {
            'sslmode': 'disable',  
        },
    }
}

# ... [Keep the rest of your settings] ...

# 4. Custom Project Settings (Pulled from .env)
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '')
OPENROUTER_MODEL_ID = os.environ.get('OPENROUTER_MODEL_ID', 'meta-llama/llama-4-scout')
