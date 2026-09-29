from django.conf import settings
from django.db import models


class AgentActionLog(models.Model):
    """Every tool call by every agent (Student or Instructor) is recorded
    here, regardless of whether it succeeded, was denied by RBAC, or is a
    destructive action awaiting confirmation. This is the audit trail
    required by the non-functional 'security' requirement in the SRS."""

    class Agent(models.TextChoices):
        STUDENT = "student_agent", "Student Agent"
        INSTRUCTOR = "instructor_agent", "Instructor Agent"

    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        DENIED = "denied", "Denied (RBAC)"
        ERROR = "error", "Error"
        PENDING_CONFIRM = "pending_confirm", "Awaiting confirmation"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="agent_actions")
    agent = models.CharField(max_length=30, choices=Agent.choices)
    tool_name = models.CharField(max_length=100)
    params = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.user.username} -> {self.tool_name} [{self.status}]"
