from django.contrib import admin
from .models import UserProfile


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
