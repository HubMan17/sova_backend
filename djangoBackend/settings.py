from datetime import timedelta
from pathlib import Path
import os
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

from celery.schedules import crontab

from dotenv import load_dotenv


# Build paths inside the project like this: BASE_DIR / 'subdir'.


from celery.schedules import crontab


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

# ALLOWED_HOSTS = ['*']
ALLOWED_HOSTS = ["sova-aero.ru", "www.sova-aero.ru", "127.0.0.1", "localhost"]

CSRF_TRUSTED_ORIGINS = [
    "https://sova-aero.ru",
    "https://www.sova-aero.ru",
]


CORS_ALLOW_ALL_ORIGINS = True


# Директория, где будут храниться медиафайлы (например, фото и видео)
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# URL для доступа к медиафайлам
MEDIA_URL = '/media/'

STATIC_ROOT = os.path.join(BASE_DIR, 'static')


PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000")  # прод: https://sova-aero.ru


# celery
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_THREAD_ID = os.getenv("TG_THREAD_ID", "")
ARM_REPORT_TOPIC_ID = int(os.getenv("ARM_REPORT_TOPIC_ID", "405"))

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND")
CELERY_TIMEZONE = "Europe/Moscow"



ONLINE_INACTIVE_MIN = int(os.getenv("ONLINE_INACTIVE_MIN", "1"))
PROLONGED_OFFLINE_MIN = int(os.getenv("PROLONGED_OFFLINE_MIN", "2"))


CELERY_BEAT_SCHEDULE = {
    "check-offline-every-minute": {
        "task": "api_v1.tasks.check_offline_boards",
        "schedule": crontab(),
        "args": (ONLINE_INACTIVE_MIN, PROLONGED_OFFLINE_MIN),
    },
}


# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'app',
    
    'corsheaders',
    
    # API
    'api_v1',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    
    
    'corsheaders.middleware.CorsMiddleware',  # Добавляем этот middleware
    'django.middleware.common.CommonMiddleware',
    'django.middleware.security.SecurityMiddleware',
]

ROOT_URLCONF = 'djangoBackend.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'djangoBackend.wsgi.application'


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv("DATABASE_NAME"),       # Название вашей базы данных
        'USER': os.getenv("DATABASE_USER"),       # Имя пользователя базы данных
        'PASSWORD': os.getenv("DATABASE_PASS"), # Пароль пользователя базы данных
        'HOST': os.getenv("DATABASE_HOST"),                # Хост базы данных (может быть IP-адрес)
        'PORT': '5432',                     # Порт (по умолчанию PostgreSQL работает на порту 5432)
    }
}


REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
}

AUTH_USER_MODEL = 'app.AuthUser'  # Замените на имя вашего приложения и модели

SIMPLE_JWT = {

    'ROTATE_REFRESH_TOKENS': True,

    'BLACKLIST_AFTER_ROTATION': True,

    'ACCESS_TOKEN_LIFETIME': timedelta(days=30),

    'REFRESH_TOKEN_LIFETIME': timedelta(days=60),

}


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = 'static/'

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.BCryptSHA256PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
]

