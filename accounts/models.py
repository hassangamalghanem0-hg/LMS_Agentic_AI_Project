from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Custom user with a role. Role drives which Agent (Student/Instructor)
    a user is allowed to talk to, and is the first line of RBAC enforcement."""

    class Role(models.TextChoices):
        STUDENT = "student", "Student"
        INSTRUCTOR = "instructor", "Instructor"
        ADMIN = "admin", "Admin"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STUDENT)

    @property
    def is_student(self):
        return self.role == self.Role.STUDENT

    @property
    def is_instructor(self):
        return self.role == self.Role.INSTRUCTOR

    def __str__(self):
        return f"{self.username} ({self.role})"


class StudentProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="student_profile")
    bio = models.TextField(blank=True)
    level = models.CharField(max_length=50, blank=True, default="beginner")

    def __str__(self):
        return f"StudentProfile<{self.user.username}>"


class InstructorProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="instructor_profile")
    bio = models.TextField(blank=True)
    department = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return f"InstructorProfile<{self.user.username}>"
