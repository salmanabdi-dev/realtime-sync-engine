from django.urls import path

from . import views

urlpatterns = [
    path("", views.documents),
    path("<uuid:document_id>/", views.document_detail),
    path("<uuid:document_id>/members/", views.document_members),
]
