"""Token generators for links sent by email.

Django ships PasswordResetTokenGenerator but no equivalent for email
verification, so this subclasses it with a distinct key_salt (a different
salt means a password-reset token and a verification token are never
interchangeable, even though the underlying mechanism is identical).

The inherited hash already includes the user's password and last_login, so a
verification link is naturally single-use: once verify_email_view logs the
user in (which updates last_login), the same token stops validating.
"""
from django.contrib.auth.tokens import PasswordResetTokenGenerator


class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    key_salt = 'sanasource.tokens.EmailVerificationTokenGenerator'


email_verification_token = EmailVerificationTokenGenerator()
