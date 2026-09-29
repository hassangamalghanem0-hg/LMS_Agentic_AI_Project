from django.urls import path
from . import views

urlpatterns = [
    path("student/chat/", views.student_chat, name="student_chat"),
    path("student/action/<str:action>/", views.student_action, name="student_action"),
    path("instructor/chat/", views.instructor_chat, name="instructor_chat"),
    path("instructor/action/<str:action>/", views.instructor_action, name="instructor_action"),
    path("instructor/upload-material/", views.instructor_upload_material, name="instructor_upload_material"),
]
