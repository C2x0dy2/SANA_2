"""French translation for Django's password-validator error messages.

Django's AUTH_PASSWORD_VALIDATORS (see sana/settings.py) raise ValidationError
with a stable `.code` per failure (e.g. 'password_too_short'), independent of
message wording or the active locale. Mapping by code — rather than changing
the project-wide LANGUAGE_CODE — means these messages are always correct
French regardless of Django's own translation catalog for a given version.

Shared by register_view (sanasource/views.py) and FrenchSetPasswordForm
(sanasource/forms.py), so both the registration form and the password-reset
form reject weak passwords with the same French wording.
"""
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

PASSWORD_VALIDATION_MESSAGES = {
    'password_too_short': "Le mot de passe doit contenir au moins 8 caractères.",
    'password_too_common': "Ce mot de passe est trop courant, merci d'en choisir un autre.",
    'password_entirely_numeric': "Le mot de passe ne peut pas être entièrement numérique.",
    'password_too_similar': "Le mot de passe ressemble trop à vos informations personnelles.",
}
DEFAULT_PASSWORD_ERROR = "Ce mot de passe n'est pas assez sécurisé."


def translate_password_validation_error(exc):
    """Map each item of a password ValidationError to a French message."""
    items = getattr(exc, 'error_list', None) or [exc]
    return [
        PASSWORD_VALIDATION_MESSAGES.get(getattr(item, 'code', None), DEFAULT_PASSWORD_ERROR)
        for item in items
    ]


def french_password_errors(password, user=None):
    """Run Django's configured validators; return a list of French error
    messages (empty if the password is valid)."""
    try:
        validate_password(password, user=user)
    except ValidationError as exc:
        return translate_password_validation_error(exc)
    return []
