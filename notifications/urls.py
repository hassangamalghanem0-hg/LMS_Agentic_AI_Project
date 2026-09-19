from django.urls import path
from . import views

urlpatterns = [
    path("<int:notification_id>/read/", views.mark_read, name="notification_mark_read"),
    path("read-all/", views.mark_all_read, name="notification_mark_all_read"),
]
