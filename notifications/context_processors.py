from .models import Notification


def notifications(request):
    """Exposes nav_notifications / nav_unread_count on every template
    (via base.html's topbar bell) without every view having to fetch them."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    qs = Notification.objects.filter(user=user, is_read=False)
    return {
        "nav_notifications": qs[:8],
        "nav_unread_count": qs.count(),
    }
