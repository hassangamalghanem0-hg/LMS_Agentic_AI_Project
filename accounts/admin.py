from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from .models import User, StudentProfile, InstructorProfile


class UserAdmin(DjangoUserAdmin):
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("Role", {"fields": ("role",)}),
    )
    list_display = ("username", "email", "role", "is_staff")
    list_filter = ("role", "is_staff")


admin.site.register(User, UserAdmin)
admin.site.register(StudentProfile)
admin.site.register(InstructorProfile)
