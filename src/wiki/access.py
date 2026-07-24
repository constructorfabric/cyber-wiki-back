from django.db import models
from django.http import Http404

from .models import Space


def accessible_spaces_for_user(user):
    """Return the same space-visibility scope used by the space API."""
    return Space.objects.filter(
        models.Q(owner=user)
        | models.Q(visibility='public')
        | models.Q(visibility='team')
        | models.Q(permissions__user=user)
    ).distinct()


def get_accessible_space_or_404(user, slug):
    """Resolve a visible space or raise 404 for private/unknown spaces."""
    try:
        return accessible_spaces_for_user(user).get(slug=slug)
    except Space.DoesNotExist as exc:
        raise Http404('Space not found') from exc
