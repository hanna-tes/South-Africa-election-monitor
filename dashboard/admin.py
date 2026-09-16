from django.contrib import admin
from .models import ApplicationLog, DashboardAnalyticsSnapshot, MonitoringReport, PostLexiconMatch

@admin.register(MonitoringReport)
class MonitoringReportAdmin(admin.ModelAdmin):
    list_display = ('title', 'report_category', 'uploaded_at', 'risk_level')
    list_filter = ('report_category', 'risk_level', 'uploaded_at')
    search_fields = ('title', 'summary')
    readonly_fields = ('uploaded_at',)  # Makes timestamp view-only
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('title', 'report_category', 'subtitle', 'source_analyst')
        }),
        ('Content', {
            'fields': ('summary', 'report_includes', 'key_findings')
        }),
        ('Files & Links', {
            'fields': ('report_file', 'full_report_url', 'cover_image')
        }),
        ('Analysis', {
            'fields': ('risk_level', 'mentioned_entities', 'weaponised_narratives', 'actor_spotlight', 'ttp_infrastructure')
        }),
    
    )


@admin.register(ApplicationLog)
class ApplicationLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'level', 'status', 'event_type', 'source', 'object_type', 'object_id', 'short_message')
    list_filter = ('level', 'status', 'event_type', 'source', 'created_at')
    search_fields = ('message', 'logger_name', 'source', 'object_id', 'actor', 'request_path')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)

    # Group fields in the detail view for better readability
    fieldsets = (
        ('Overview', {
            'fields': ('created_at', 'level', 'status', 'event_type', 'message')
        }),
        ('Context', {
            'fields': ('actor', 'source', 'object_type', 'object_id', 'request_path', 'duration_ms')
        }),
        ('Technical Details', {
            'classes': ('collapse',),  # This makes the section collapsible
            'fields': ('logger_name', 'metadata', 'traceback')
        }),
    )

    readonly_fields = (
        'created_at', 'level', 'logger_name', 'event_type', 'status', 
        'message', 'actor', 'source', 'object_type', 'object_id', 
        'request_path', 'duration_ms', 'metadata', 'traceback',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def short_message(self, obj):
        return obj.message[:120]
    short_message.short_description = 'Message'


@admin.register(DashboardAnalyticsSnapshot)
class DashboardAnalyticsSnapshotAdmin(admin.ModelAdmin):
    list_display = ('kind', 'key', 'start_date', 'end_date', 'generated_at', 'source')
    list_filter = ('kind', 'source', 'generated_at')
    search_fields = ('key',)
    readonly_fields = ('key', 'kind', 'start_date', 'end_date', 'payload', 'generated_at', 'source')
    date_hierarchy = 'generated_at'
    ordering = ('-generated_at',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PostLexiconMatch)
class PostLexiconMatchAdmin(admin.ModelAdmin):
    list_display = ('detected_at', 'term', 'category', 'severity', 'post_id')
    list_filter = ('category', 'severity', 'detected_at')
    search_fields = ('term', 'post__account_id', 'post__original_text')
    readonly_fields = ('post', 'term', 'category', 'severity', 'target_entity', 'language', 'detected_at')
    date_hierarchy = 'detected_at'
    ordering = ('-detected_at',)

    def has_add_permission(self, request):
        return False
