from django.contrib import admin
from .models import UserProfile, SanaGroup, GroupMessage, MoodEntry, CommunityPost, Comment, PostReport, Conversation, Message, Journal, JournalEntry, JournalPage, Attachment, Review, NewsletterSubscriber, ScreeningResult, QuizAttempt, DailyChallengeCompletion, SubmittedMyth, GameSession, GameRoom, WerewolfRoom


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
    list_display  = ['author', 'tag', 'content_preview', 'requests_support', 'is_reported', 'like_count', 'created_at']
    list_filter   = ['tag', 'requests_support', 'is_reported']
    search_fields = ['author__username', 'content']
    actions       = ['clear_report', 'delete_reported']

    @admin.display(description='Post')
    def content_preview(self, obj):
        return obj.content[:60]

    @admin.display(description='Likes')
    def like_count(self, obj):
        return obj.likes.count()

    @admin.action(description='Lever le signalement (rendre visible)')
    def clear_report(self, request, queryset):
        queryset.update(is_reported=False)

    @admin.action(description='Supprimer les posts signalés sélectionnés')
    def delete_reported(self, request, queryset):
        queryset.filter(is_reported=True).delete()


@admin.register(PostReport)
class PostReportAdmin(admin.ModelAdmin):
    list_display  = ['post', 'reporter', 'reason', 'created_at']
    list_filter   = ['reason']
    search_fields = ['post__content', 'reporter__username', 'details']


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


@admin.register(ScreeningResult)
class ScreeningResultAdmin(admin.ModelAdmin):
    list_display  = ['user', 'tool', 'score', 'band', 'flagged', 'created_at']
    list_filter   = ['tool', 'band', 'flagged']
    search_fields = ['user__username']


@admin.register(QuizAttempt)
class QuizAttemptAdmin(admin.ModelAdmin):
    list_display  = ['user', 'score', 'total', 'created_at']
    search_fields = ['user__username']


@admin.register(DailyChallengeCompletion)
class DailyChallengeCompletionAdmin(admin.ModelAdmin):
    list_display  = ['user', 'challenge_date', 'reflection_preview', 'completed_at']
    list_filter   = ['challenge_date']
    search_fields = ['user__username', 'reflection_text']

    @admin.display(description='Avis')
    def reflection_preview(self, obj):
        return obj.reflection_text[:60]


@admin.register(SubmittedMyth)
class SubmittedMythAdmin(admin.ModelAdmin):
    list_display  = ['author', 'myth_preview', 'is_approved', 'created_at']
    list_filter   = ['is_approved']
    search_fields = ['author__username', 'myth_text']
    actions       = ['approve_myths']

    @admin.display(description='Mythe')
    def myth_preview(self, obj):
        return obj.myth_text[:60]

    @admin.action(description='Approuver les mythes sélectionnés')
    def approve_myths(self, request, queryset):
        queryset.update(is_approved=True)


@admin.register(GameSession)
class GameSessionAdmin(admin.ModelAdmin):
    list_display  = ['user', 'game', 'score', 'played_at']
    list_filter   = ['game']
    search_fields = ['user__username']


@admin.register(GameRoom)
class GameRoomAdmin(admin.ModelAdmin):
    list_display  = ['code', 'host', 'status', 'round_number', 'max_rounds', 'created_at']
    list_filter   = ['status']
    search_fields = ['code', 'host__username']


@admin.register(WerewolfRoom)
class WerewolfRoomAdmin(admin.ModelAdmin):
    list_display  = ['code', 'host', 'status', 'round_number', 'result', 'created_at']
    list_filter   = ['status', 'result']
    search_fields = ['code', 'host__username']


