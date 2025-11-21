# core/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path("metrics/", views.metrics_api, name="metrics_api"),
    path("health/", views.health, name="health"),
]