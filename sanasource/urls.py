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
    path('logout/', views.logout_view, name='logout'),
    path('api/chat/', views.sana_chat, name='sana_chat'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('groupes/', views.group_page, name='group_page'),
    path('api/groupes/creer/', views.create_group, name='create_group'),
    path('api/groupes/<int:group_id>/membres/', views.join_leave_group, name='join_leave_group'),
    path('api/groupes/<int:group_id>/messages/', views.group_messages_api, name='group_messages_api'),
    path('api/humeur/', views.save_mood, name='save_mood'),
    path('api/communaute/', views.community_post_api, name='community_post_api'),
    path('api/communaute/<int:post_id>/like/', views.toggle_like, name='toggle_like'),
    path('api/notifications/', views.notifications_api, name='notifications_api'),
    path('api/notifications/<int:notif_id>/read/', views.notification_read, name='notification_read'),
    path('api/push/subscribe/', views.push_subscribe, name='push_subscribe'),
    path('api/push/unsubscribe/', views.push_unsubscribe, name='push_unsubscribe'),
]