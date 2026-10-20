"""Adding staff to a store (existing account, or a new one created by the owner)."""
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction

from ..models import StoreMembership, User
from .errors import BusinessRuleError


@transaction.atomic
def add_staff(store, *, username, role, full_name='', email='', phone_number='', password=''):
    """
    Give someone access to `store` with `role`.
      - If an account with that username exists, it is simply added to the store.
      - Otherwise a new account is created — `password` is then required, and
        the owner hands it to the staff member (who can change it afterwards).
    Returns (membership, account_created).
    """
    username = (username or '').strip()
    if not username:
        raise BusinessRuleError("Username is required.")
    if role == StoreMembership.Role.OWNER:
        raise BusinessRuleError("Ownership cannot be assigned to a team member.")

    user = User.objects.filter(username__iexact=username).first()
    created = False
    if user is None:
        if not password:
            raise BusinessRuleError(
                "No account exists with that username. Enter a password to create one for this person."
            )
        if email and User.objects.filter(email__iexact=email).exists():
            raise BusinessRuleError("An account with that email already exists.")
        if phone_number and User.objects.filter(phone_number=phone_number).exists():
            raise BusinessRuleError("An account with that phone number already exists.")
        user = User(
            username=username, email=email or '', full_name=full_name or '',
            phone_number=phone_number or None,
        )
        try:
            validate_password(password, user=user)
        except DjangoValidationError as exc:
            raise BusinessRuleError(' '.join(exc.messages))
        user.set_password(password)
        user.save()
        created = True

    membership = StoreMembership.objects.filter(store=store, user=user).first()
    if membership:
        if membership.role == StoreMembership.Role.OWNER:
            raise BusinessRuleError("That person is the store owner.")
        if membership.is_active:
            raise BusinessRuleError("That person is already on your team.")
        membership.is_active = True
        membership.role = role
        membership.save(update_fields=['is_active', 'role'])
    else:
        membership = StoreMembership.objects.create(user=user, store=store, role=role)
    return membership, created
