from django.contrib import admin

from .models import Document, DocumentMembership, DocumentRevision, DocumentState


class MembershipInline(admin.TabularInline):
    model = DocumentMembership
    extra = 0


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "id", "created_by", "created_at")
    inlines = [MembershipInline]


@admin.register(DocumentState)
class DocumentStateAdmin(admin.ModelAdmin):
    list_display = ("document", "version")


@admin.register(DocumentRevision)
class DocumentRevisionAdmin(admin.ModelAdmin):
    list_display = ("document", "version", "author", "created_at")
    list_filter = ("document",)
