from django.contrib import admin
from .models import AgentActionLog


@admin.register(AgentActionLog)
class AgentActionLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "user", "agent", "tool_name", "status")
    list_filter = ("agent", "status", "tool_name")
    readonly_fields = [f.name for f in AgentActionLog._meta.fields]
