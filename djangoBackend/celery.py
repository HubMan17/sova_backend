import os
from celery import Celery

# тут — ровно имя пакета, где settings.py
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "djangoBackend.settings")

app = Celery("djangoBackend")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
