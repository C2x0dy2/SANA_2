"""Transactional emails for the authentication system.

Uses Django's configured EMAIL_BACKEND (see sana/settings.py — console
backend locally / when no SMTP host is configured, real SMTP otherwise).

send_welcome_email() is best-effort (failures are logged, never raised) —
missing it is a cosmetic loss. send_verification_email() deliberately RAISES
on failure instead: without that email the account is unreachable (it's
created inactive), so register_view needs to know it failed to tell the user,
rather than silently leaving them with a dead-end account.
"""
import logging

from django.conf import settings
from django.core.mail import send_mail, EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .tokens import email_verification_token

logger = logging.getLogger('sanasource.auth')


def send_welcome_email(user):
    """Best-effort welcome email sent once the account is verified. Never raises."""
    if not user.email:
        return
    try:
        send_mail(
            subject='Bienvenue sur SANA',
            message=render_to_string('email/welcome_email.txt', {'user': user}),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
        logger.info('Welcome email sent, user_id=%s', user.pk)
    except Exception:
        logger.exception('Welcome email failed, user_id=%s', user.pk)


def build_verification_url(request, user):
    token = email_verification_token.make_token(user)
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    return request.build_absolute_uri(
        reverse('sanasource:verify_email', kwargs={'uidb64': uidb64, 'token': token})
    )


def send_verification_email(request, user):
    """Sends the account-activation email (HTML + plain-text). Raises on
    failure — see module docstring for why this one isn't best-effort."""
    context = {
        'first_name': user.first_name,
        'verification_url': build_verification_url(request, user),
    }
    text_body = render_to_string('email/verification_email.txt', context)
    html_body = render_to_string('email/verification_email.html', context)
    message = EmailMultiAlternatives(
        subject='Confirme ton adresse e-mail — SANA',
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
    )
    message.attach_alternative(html_body, 'text/html')
    message.send(fail_silently=False)
    logger.info('Verification email sent, user_id=%s', user.pk)
