from .celery import app as celery_app

# алиас, чтобы -A djangoBackend находил атрибут "celery"
celery = celery_app

__all__ = ("celery_app", "celery")