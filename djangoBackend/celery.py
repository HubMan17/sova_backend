import os
from celery import Celery

from django.conf import settings

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "djangoBackend.settings")

app = Celery("djangoBackend")

app.conf.timezone = settings.TIME_ZONE      # 'Europe/Moscow'
app.conf.enable_utc = True 

app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()