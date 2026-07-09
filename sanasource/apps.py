from django.apps import AppConfig


class SanasourceConfig(AppConfig):
    name = 'sanasource'

    def ready(self):
        from . import signals  # noqa: F401 — connects the post_save receiver
