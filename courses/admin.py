from django.contrib import admin
from .models import (
    Course, Module, Lesson, Topic, Quiz, Question, Choice,
    QuizAttempt, QuestionResponse, TopicMastery, Material,
)

admin.site.register(Course)
admin.site.register(Module)
admin.site.register(Lesson)
admin.site.register(Topic)
admin.site.register(Quiz)
admin.site.register(Question)
admin.site.register(Choice)
admin.site.register(QuizAttempt)
admin.site.register(QuestionResponse)
admin.site.register(TopicMastery)
admin.site.register(Material)
