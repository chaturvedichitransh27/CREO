from django.contrib import admin
from .models import CreatorReview


@admin.register(CreatorReview)
class CreatorReviewAdmin(admin.ModelAdmin):
    list_display = ['id', 'engagement', 'overall', 'created_at']
    readonly_fields = [field.name for field in CreatorReview._meta.fields]
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
