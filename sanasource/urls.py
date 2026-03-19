from django.urls import path
from . import views

app_name = 'sanasource'
urlpatterns = [
    path('', views.page_open, name='page_open'),
    path('accueil/', views.accueil, name='accueil'),
    path('history/', views.history, name='history'),
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('help/', views.help_view, name='help'),
    path('api/chat/', views.sana_chat, name='sana_chat'),
    path('dashboard/', views.dashboard, name='dashboard'),
  
    ]