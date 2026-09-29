from django.urls import path
from . import views

urlpatterns = [
    path("plan/<int:plan_id>/", views.study_plan_detail, name="study_plan_detail"),
]
