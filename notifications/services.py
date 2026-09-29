"""Tiny helper around Notification creation. Kept as a function (not a
method on the model) so callers in other apps (agents.tools,
courses.services) don't need to import the Notification model directly --
one place to change if delivery ever grows beyond an in-app row (email,
push, etc).
"""
from .models import Notification


def notify(user, message):
    """Create a single unread notification for `user`. Never raises on a
    bad `user` (None) -- notifications are a nice-to-have, not something
    that should ever break the action that triggered them."""
    if not user:
        return None
    return Notification.objects.create(user=user, message=message[:300])


def notify_many(users, message):
    for u in users:
        notify(u, message)
