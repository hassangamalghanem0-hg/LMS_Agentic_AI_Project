from django import forms
from django.contrib.auth.forms import UserCreationForm
from courses.models import Course
from .models import User


class StudentRegisterForm(UserCreationForm):
    email = forms.EmailField(required=False)
    level = forms.ChoiceField(
        choices=[("beginner", "Beginner"), ("intermediate", "Intermediate"), ("advanced", "Advanced")],
        initial="beginner", required=False,
    )
    bio = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False, label="Short bio (optional)")
    course = forms.ModelChoiceField(
        queryset=Course.objects.select_related("instructor").order_by("title"),
        required=True, label="Choose your course",
        empty_label="— Select a course —",
        help_text="Pick the instructor's course you want to join.",
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["course"].label_from_instance = self._course_label

    @staticmethod
    def _course_label(course):
        return f"{course.title} — Instructor: {course.instructor.username}"


class InstructorRegisterForm(UserCreationForm):
    email = forms.EmailField(required=False)
    department = forms.CharField(required=False, label="Department")
    bio = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False, label="Short bio (optional)")
    course_title = forms.CharField(
        max_length=200, label="Course you will teach",
        widget=forms.TextInput(attrs={"placeholder": "e.g. Full-Stack Python Development"}),
    )
    course_description = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}), required=False, label="Course description (optional)",
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

