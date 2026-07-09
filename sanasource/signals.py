"""Model signal handlers for the authentication system.

Kept in a dedicated module (rather than inline in models.py or views.py) so
signal wiring stays visible in one place — see SanasourceConfig.ready() in
apps.py for where this gets connected.
"""
import logging

from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import UserProfile

logger = logging.getLogger('sanasource.auth')


@receiver(post_save, sender=User)
def ensure_user_profile(sender, instance, created, **kwargs):
    """Guarantee every User has a UserProfile, even ones created outside
    register_view (Django admin, `createsuperuser`, data migrations, ...).

    register_view still creates the profile's real fields itself right after
    this signal fires — this only seeds a minimal, guaranteed-unique
    placeholder so the OneToOne relation is never missing. The placeholder
    username uses the user's own primary key, which cannot collide, unlike a
    bare `get_or_create(user=instance)` default of '' would on a second
    profile-less user (username_anonyme is unique=True).
    """
    if not created or hasattr(instance, 'profile'):
        return
    UserProfile.objects.get_or_create(
        user=instance,
        defaults={'username_anonyme': f'user_{instance.pk}'},
    )
    logger.info('Placeholder profile created for user_id=%s', instance.pk)
