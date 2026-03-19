from django.contrib import admin
from .models import UserProfile, SanaGroup, GroupMessage, MoodEntry, CommunityPost


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display  = [
        'username_anonyme', 'get_email', 'age', 'genre', 'ville',
        'situation', 'theme_couleur', 'comment_tu_te_sens',
        'niveau_urgence', 'a_deja_consulte', 'date_inscription',
    ]
    list_filter   = [
        'genre', 'situation', 'theme_couleur',
        'comment_tu_te_sens', 'niveau_urgence', 'a_deja_consulte',
    ]
    search_fields = ['username_anonyme', 'user__email', 'ville']
    readonly_fields = ['date_inscription']
    fieldsets = (
        ('Compte', {
            'fields': ('user', 'username_anonyme', 'date_inscription'),
        }),
        ('Profil', {
            'fields': ('age', 'genre', 'ville', 'situation', 'theme_couleur'),
        }),
        ('Questionnaire', {
            'fields': (
                'comment_tu_te_sens', 'principales_difficultes',
                'objectif_principal', 'a_deja_consulte', 'niveau_urgence',
            ),
        }),
    )

    @admin.display(description='Email')
    def get_email(self, obj):
        return obj.user.email


@admin.register(SanaGroup)
class SanaGroupAdmin(admin.ModelAdmin):
    list_display  = ['name', 'icon', 'created_by', 'member_count', 'created_at']
    search_fields = ['name', 'created_by__username']
    filter_horizontal = ['members']


@admin.register(GroupMessage)
class GroupMessageAdmin(admin.ModelAdmin):
    list_display  = ['group', 'sender', 'content_preview', 'sent_at']
    list_filter   = ['group']
    search_fields = ['sender__username', 'content']

    @admin.display(description='Message')
    def content_preview(self, obj):
        return obj.content[:60]


@admin.register(MoodEntry)
class MoodEntryAdmin(admin.ModelAdmin):
    list_display  = ['user', 'mood', 'note_preview', 'recorded_at']
    list_filter   = ['mood']
    search_fields = ['user__username']

    @admin.display(description='Note')
    def note_preview(self, obj):
        return obj.note[:60]


@admin.register(CommunityPost)
class CommunityPostAdmin(admin.ModelAdmin):
    list_display  = ['author', 'tag', 'content_preview', 'like_count', 'created_at']
    list_filter   = ['tag']
    search_fields = ['author__username', 'content']

    @admin.display(description='Post')
    def content_preview(self, obj):
        return obj.content[:60]

    @admin.display(description='Likes')
    def like_count(self, obj):
        return obj.likes.count()
