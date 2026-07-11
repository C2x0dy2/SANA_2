from django.contrib import admin
from .models import UserProfile, SanaGroup, GroupMessage, MoodEntry, CommunityPost, Comment, Conversation, Message, Journal, JournalEntry, JournalPage, Attachment, Review, NewsletterSubscriber


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


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ['role', 'content', 'timestamp']
    can_delete = False


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display  = ['title', 'user', 'created_at', 'updated_at']
    search_fields = ['title', 'user__username']
    inlines       = [MessageInline]


class JournalEntryInline(admin.TabularInline):
    model = JournalEntry
    extra = 0
    readonly_fields = ['entry_date', 'title', 'content', 'mood', 'created_at', 'updated_at']
    can_delete = False


class AttachmentInline(admin.TabularInline):
    model = Attachment
    extra = 0
    readonly_fields = ['attachment_type', 'file', 'sticker_code', 'order', 'created_at']
    can_delete = False


class JournalPageInline(admin.TabularInline):
    model = JournalPage
    extra = 0
    readonly_fields = ['page_number', 'date', 'day_of_week', 'mood', 'created_at', 'updated_at']
    can_delete = False


@admin.register(Journal)
class JournalAdmin(admin.ModelAdmin):
    list_display  = ['title', 'user', 'icon', 'color', 'cover_style', 'is_locked', 'created_at', 'updated_at', 'last_opened']
    list_filter   = ['color', 'cover_style', 'is_locked']
    search_fields = ['title', 'user__username']
    inlines       = [JournalEntryInline, JournalPageInline]


@admin.register(JournalPage)
class JournalPageAdmin(admin.ModelAdmin):
    list_display  = ['journal', 'page_number', 'date', 'day_of_week', 'mood', 'updated_at']
    list_filter   = ['mood']
    search_fields = ['journal__title', 'content']
    inlines       = [AttachmentInline]


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


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display  = ['author', 'post', 'content_preview', 'created_at']
    search_fields = ['author__username', 'content']

    @admin.display(description='Commentaire')
    def content_preview(self, obj):
        return obj.content[:60]


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display   = ['author', 'rating', 'content_preview', 'is_approved', 'created_at']
    list_filter    = ['is_approved', 'rating']
    list_editable  = ['is_approved']
    search_fields  = ['author__username', 'content']
    actions        = ['approve_reviews', 'unapprove_reviews']

    @admin.display(description='Avis')
    def content_preview(self, obj):
        return obj.content[:60]

    @admin.action(description='Approuver les avis sélectionnés')
    def approve_reviews(self, request, queryset):
        queryset.update(is_approved=True)

    @admin.action(description='Retirer les avis sélectionnés')
    def unapprove_reviews(self, request, queryset):
        queryset.update(is_approved=False)


@admin.register(NewsletterSubscriber)
class NewsletterSubscriberAdmin(admin.ModelAdmin):
    list_display  = ['email', 'is_confirmed', 'subscribed_at']
    list_filter   = ['is_confirmed']
    search_fields = ['email']


