from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("register/student/", views.register_student, name="register_student"),
    path("register/instructor/", views.register_instructor, name="register_instructor"),
    path("student/", views.student_dashboard, name="student_dashboard"),
    path("instructor/", views.instructor_dashboard, name="instructor_dashboard"),
]
