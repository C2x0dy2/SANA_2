"""Custom auth forms — thin subclasses of Django's built-ins that translate
error messages to French, used by the password-reset flow
(sanasource/urls.py wires these into django.contrib.auth.views).
"""
from django import forms
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .password_validation import translate_password_validation_error


class FrenchPasswordResetForm(PasswordResetForm):
    email = forms.EmailField(
        label="Adresse e-mail",
        max_length=254,
        error_messages={'invalid': "Merci d'indiquer une adresse e-mail valide."},
    )


class FrenchSetPasswordForm(SetPasswordForm):
    error_messages = {
        **SetPasswordForm.error_messages,
        'password_mismatch': "Les mots de passe ne correspondent pas.",
    }

    def validate_password_for_user(self, user, password_field_name='new_password2'):
        # Mirrors SetPasswordMixin.validate_password_for_user, but translates
        # Django's validator errors to French via their stable .code (see
        # password_validation.py) instead of using the (English) default.
        password = self.cleaned_data.get(password_field_name)
        if not password:
            return
        try:
            validate_password(password, user)
        except ValidationError as exc:
            self.add_error(password_field_name, ValidationError(translate_password_validation_error(exc)))
