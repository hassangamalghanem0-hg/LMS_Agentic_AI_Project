from django.urls import path
from . import views

urlpatterns = [
    path("quiz/<int:quiz_id>/", views.quiz_detail, name="quiz_detail"),
    path("quiz/<int:quiz_id>/submit/", views.quiz_submit, name="quiz_submit"),
    path("<int:course_id>/analytics/", views.course_analytics_view, name="course_analytics"),
    path("<int:course_id>/students/<int:student_id>/performance/", views.student_performance_view, name="student_performance"),
    path("my-analytics/", views.student_analytics_view, name="student_analytics"),
]
