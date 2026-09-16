from django.db import models
from django.utils import timezone


class DataSource(models.Model):
    """Metadata about uploaded data sources"""
    name = models.CharField(max_length=100, unique=True)  # e.g., "Meltwater_Ethiopia_Apr2026"
    description = models.TextField(blank=True)
    uploaded_by = models.CharField(max_length=255, blank=True)  # username or "system"
    uploaded_at = models.DateTimeField(auto_now_add=True)
    file_path = models.CharField(max_length=500, blank=True)  # if stored locally
    source_url = models.URLField(blank=True, null=True)  # if from GitHub
    record_count = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    last_updated = models.DateTimeField(auto_now=True)

    
    class Meta:
        ordering = ['-uploaded_at']
    
    def __str__(self):
        return f"{self.name} ({self.record_count} records)"


class ProcessedPost(models.Model):
    """
    STORES PROCESSED/MERGED DATA (output of combine_social_media_data + preprocessing)
    This is what your dashboard queries - NOT raw CSV data
    """
    # Core identifiers
    content_id = models.CharField(max_length=255, db_index=True)
    account_id = models.CharField(max_length=255, db_index=True)
    
    # Content
    object_id = models.TextField()  # Raw post text
    original_text = models.TextField()  # Cleaned text for analysis (after extract_original_text)
    
    # Metadata
    url = models.TextField(blank=True, null=True)
    timestamp_share = models.DateTimeField(db_index=True)
    platform = models.CharField(max_length=50, db_index=True)  # X, Telegram, TikTok, Media
    source_dataset = models.ForeignKey(DataSource, on_delete=models.SET_NULL, null=True, db_index=True)


    ingested_at = models.DateTimeField(auto_now_add=True, db_index=True, null=True)
    batch_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    
    
    # Preprocessing flags
    is_original_post = models.BooleanField(default=True)  # After is_original_post() filter
    sentiment = models.CharField(max_length=20, blank=True, null=True)  # Negative, Neutral
    cluster = models.IntegerField(default=-1, db_index=True)  # After DBSCAN clustering
    
    # Election-specific
    is_election_related = models.BooleanField(default=False)
    election_keywords_matched = models.JSONField(default=list, blank=True)
    hashtags = models.JSONField(default=list, blank=True)
    
    # TikTok-specific fields (if applicable)
    play_count = models.BigIntegerField(null=True, blank=True)
    digg_count = models.BigIntegerField(null=True, blank=True)
    comment_count = models.BigIntegerField(null=True, blank=True)
    share_count = models.BigIntegerField(null=True, blank=True)
    text_language = models.CharField(max_length=10, blank=True)
    
    # Lexicon analysis
    lexicon_matches = models.JSONField(default=list, blank=True)  # [{term, category, severity}, ...]
    risk_score = models.FloatField(default=0.0)
    risk_level = models.CharField(
        max_length=20,
        choices=[('low', 'Low'), ('medium', 'Medium'), ('high', 'High'), ('critical', 'Critical')],
        default='low'
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['timestamp_share']),
            models.Index(fields=['platform', 'timestamp_share']),
            models.Index(fields=['cluster', 'timestamp_share']),
            models.Index(fields=['is_election_related', 'timestamp_share']),
            models.Index(fields=['risk_level', 'timestamp_share']),
            models.Index(fields=['timestamp_share'], name='idx_timestamp'),
            models.Index(fields=['is_election_related'], name='idx_election_related'),
        ]
        ordering = ['-timestamp_share']
    
    def __str__(self):
        return f"{self.platform} - {self.account_id[:30]} - {self.timestamp_share.strftime('%Y-%m-%d')}"


class NarrativeCluster(models.Model):
    """Stores LLM-generated narrative summaries"""
    cluster_id = models.IntegerField(unique=True, db_index=True)
    theme = models.CharField(max_length=255)
    llm_summary = models.TextField()  # Output from summarize_cluster_ethiopia()
    total_reach = models.IntegerField(default=0)
    virality_tier = models.CharField(
        max_length=50,
        choices=[
            ('Tier 1: Limited', 'Tier 1: Limited'),
            ('Tier 2: Moderate', 'Tier 2: Moderate'),
            ('Tier 3: High Spread', 'Tier 3: High Spread'),
            ('Tier 4: Viral Emergency', 'Tier 4: Viral Emergency')
        ]
    )
    first_detected = models.DateTimeField()
    last_updated = models.DateTimeField(auto_now=True)
    is_election_related = models.BooleanField(default=True)
    
    class Meta:
        ordering = ['-total_reach']
    
    def __str__(self):
        return f"Cluster {self.cluster_id} - {self.theme}"
        
class DataUpload(models.Model):
    """Tracks user CSV uploads for processing"""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    
    uploaded_file = models.FileField(upload_to='uploads/%Y/%m/%d/')
    original_filename = models.CharField(max_length=255)
    uploaded_by = models.CharField(max_length=255, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    
    # Processing metadata
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    processing_log = models.TextField(blank=True)  # Errors, warnings, progress
    records_processed = models.IntegerField(default=0)
    records_failed = models.IntegerField(default=0)
    
    # Data type
    data_type = models.CharField(
        max_length=50,
        choices=[
            ('meltwater', 'Meltwater/X'),
            ('civicsignal', 'Civicsignal/Media'),
            ('tiktok', 'TikTok'),
            ('openmeasure', 'OpenMeasure/Telegram'),
            ('custom', 'Custom Format'),
        ],
        default='custom'
    )
    
    class Meta:
        ordering = ['-uploaded_at']
    
    def __str__(self):
        return f"{self.original_filename} ({self.status})"


class ApplicationLog(models.Model):
    """Structured operational log entries for imports, uploads, syncs, and app events."""
    LEVEL_CHOICES = [
        ('DEBUG', 'Debug'),
        ('INFO', 'Info'),
        ('WARNING', 'Warning'),
        ('ERROR', 'Error'),
        ('CRITICAL', 'Critical'),
    ]
    STATUS_CHOICES = [
        ('started', 'Started'),
        ('success', 'Success'),
        ('skipped', 'Skipped'),
        ('warning', 'Warning'),
        ('failed', 'Failed'),
        ('info', 'Info'),
    ]

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default='INFO', db_index=True)
    logger_name = models.CharField(max_length=255, blank=True, db_index=True)
    event_type = models.CharField(max_length=100, blank=True, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='info', db_index=True)
    message = models.TextField()

    actor = models.CharField(max_length=255, blank=True)
    source = models.CharField(max_length=255, blank=True, db_index=True)
    object_type = models.CharField(max_length=100, blank=True)
    object_id = models.CharField(max_length=255, blank=True)
    request_path = models.CharField(max_length=500, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    traceback = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_at', 'level'], name='dashboard_a_created_154f5e_idx'),
            models.Index(fields=['event_type', 'status'], name='dashboard_a_event_t_f3b3b9_idx'),
            models.Index(fields=['source', 'created_at'], name='dashboard_a_source_519066_idx'),
        ]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M:%S} [{self.level}] {self.message[:80]}"


class DashboardAnalyticsSnapshot(models.Model):
    """Precomputed dashboard payloads used to keep page loads lightweight."""
    key = models.CharField(max_length=255, unique=True)
    kind = models.CharField(max_length=100, db_index=True)
    start_date = models.DateField(null=True, blank=True, db_index=True)
    end_date = models.DateField(null=True, blank=True, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    generated_at = models.DateTimeField(default=timezone.now, db_index=True)
    source = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ['-generated_at']
        indexes = [
            models.Index(fields=['kind', 'generated_at'], name='dashboard_a_kind_57cda1_idx'),
            models.Index(fields=['kind', 'start_date', 'end_date'], name='dashboard_a_kind_6b6408_idx'),
        ]

    def __str__(self):
        return f"{self.kind} snapshot ({self.generated_at:%Y-%m-%d %H:%M})"
        

class LexiconTerm(models.Model):
    """Your Ethiopia hate speech lexicon (for reference + scanning)"""
    term = models.CharField(max_length=255, unique=True)
    category = models.CharField(max_length=100, db_index=True)
    severity = models.CharField(
        max_length=20,
        choices=[('low', 'Low'), ('medium', 'Medium'), ('high', 'High'), ('critical', 'Critical')],
        default='medium'
    )
    target_entity = models.CharField(max_length=255, blank=True)
    language = models.CharField(
        max_length=20,
        choices=[('amharic', 'Amharic'), ('english', 'English'), ('oromo', 'Oromo'), ('tigrinya', 'Tigrinya')]
    )
    is_election_related = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    justification = models.TextField(blank=True, null=True, help_text="Why is this a trigger term?")
    
    def __str__(self):
        return f"{self.term} ({self.severity})"


class PostLexiconMatch(models.Model):
    """Materialized lexicon detections for fast dashboard browsing."""
    post = models.ForeignKey(ProcessedPost, on_delete=models.CASCADE, related_name='materialized_lexicon_matches')
    term = models.CharField(max_length=255, db_index=True)
    category = models.CharField(max_length=100, db_index=True)
    severity = models.CharField(max_length=20, db_index=True)
    target_entity = models.CharField(max_length=255, blank=True)
    language = models.CharField(max_length=20, blank=True)
    detected_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-detected_at']
        constraints = [
            models.UniqueConstraint(fields=['post', 'term', 'category'], name='uniq_post_lexicon_match'),
        ]
        indexes = [
            models.Index(fields=['category', 'detected_at'], name='postlex_cat_detect_idx'),
            models.Index(fields=['severity', 'detected_at'], name='postlex_sev_detect_idx'),
            models.Index(fields=['term', 'detected_at'], name='dashboard_p_term_6f767a_idx'),
        ]

    def __str__(self):
        return f"{self.term} on post {self.post_id}"

class PEP(models.Model):
    """Political Exposed Person - election-focused"""
    name = models.CharField(max_length=255, unique=True)
    title = models.CharField(max_length=255, blank=True)
    affiliation = models.CharField(max_length=255, blank=True)
    ethnic_group = models.CharField(max_length=100, blank=True)
    last_updated = models.DateTimeField(auto_now=True, null=True, blank=True)
    
    # Social media
    x_link = models.URLField(blank=True, null=True)
    x_verified = models.BooleanField(default=False)
    facebook_link = models.URLField(blank=True, null=True)
    facebook_verified = models.BooleanField(default=False)
    confidence_level = models.CharField(
        max_length=20,
        choices=[('high', 'High'), ('medium', 'Medium'), ('low', 'Low'), ('uncertain', 'Uncertain')],
        blank=True,
        default='medium'
    )
    
    is_active = models.BooleanField(default=True)
    
    class Meta:
        ordering = ['name']
    
    def __str__(self):
        return f"{self.name} - {self.title}"

class SyncSource(models.Model):
    """Central registry for backend data sync sources"""
    FILE_TYPE_CHOICES = [
        ('csv_posts', 'Election Posts CSV'),
        ('csv_peps', 'PEPs/Candidates CSV'),
        ('pdf_report', 'Monthly Insight Report (PDF)'),
        ('json_narratives', 'Narratives/Clusters JSON'),
    ]
    
    FREQUENCY_CHOICES = [
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
    ]
    
    name = models.CharField(max_length=100, unique=True, help_text="e.g., 'Brandwatch Weekly Export'")
    file_type = models.CharField(max_length=50, choices=FILE_TYPE_CHOICES)
    url = models.URLField(help_text="Raw/public URL to the file")
    is_active = models.BooleanField(default=True)
    sync_frequency = models.CharField(max_length=20, choices=FREQUENCY_CHOICES, default='weekly')
    last_synced = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        
    def __str__(self):
        return f"{self.name} ({self.get_file_type_display()})"

class ElectionOfficeholder(models.Model):
    source_file = models.CharField(max_length=255)
    source_sheet = models.CharField(max_length=100, default='')
    row_index = models.IntegerField(null=True, blank=True)
    column_order = models.JSONField(default=list, blank=True)  
    raw_data = models.JSONField(default=dict)

    class Meta:
        unique_together = ('source_file', 'source_sheet', 'row_index')
        ordering = ['source_file', 'source_sheet', 'row_index']

    def __str__(self):
        return f"{self.source_file} - {self.source_sheet} (Row {self.row_index})"

class PostAIDetection(models.Model):
    """Stores permanent AI analysis results for posts."""
    post = models.ForeignKey(ProcessedPost, on_delete=models.CASCADE, related_name='ai_detections')
    category = models.CharField(max_length=100)
    confidence = models.FloatField(default=0.0)
    severity = models.CharField(max_length=20, default='medium')
    model_name = models.CharField(max_length=100, default='AFRO-XLMR')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('post', 'model_name') # Ensures only one AI result per post per model
        indexes = [
            models.Index(fields=['post', 'model_name']),
        ]

class MonitoringReport(models.Model):
    CATEGORY_CHOICES = [
        ('baseline', 'Baseline Report'),
        ('situational', 'Situational Report'),
        ('tiktok', 'TikTok Analysis Report'),
    ]
    
    title = models.CharField(max_length=255)
    subtitle = models.CharField(max_length=300, blank=True, help_text="Short tagline under main title")
    report_category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='baseline')
    
    source_analyst = models.CharField(max_length=255, blank=True, default="Internal Analyst")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    
    # Files & Links
    report_file = models.FileField(upload_to='reports/', blank=True, null=True, help_text="PDF/DOCX for direct download")
    full_report_url = models.URLField(blank=True, null=True, help_text="Fallback external link")
    cover_image = models.ImageField(upload_to='report_covers/', blank=True, null=True, help_text="Thumbnail for report cards")
    
    # Content & Analysis
    summary = models.TextField(blank=True, help_text="Executive summary")
    report_includes = models.JSONField(default=list, blank=True, help_text="Up to 5 bullet points")
    key_findings = models.JSONField(default=list, blank=True)
    
    mentioned_entities = models.JSONField(default=list, blank=True)
    risk_level = models.CharField(max_length=20, default='medium', choices=[
        ('low', 'Low'), ('medium', 'Medium'), ('high', 'High'), ('critical', 'Critical')
    ])
    
    # Section content
    weaponised_narratives = models.TextField(blank=True)
    weaponised_narratives_full = models.TextField(blank=True)
    actor_spotlight = models.TextField(blank=True)
    actor_spotlight_full = models.TextField(blank=True)
    ttp_infrastructure = models.TextField(blank=True)
    ttp_infrastructure_full = models.TextField(blank=True)
    key_findings_full = models.TextField(blank=True)
    
    is_processed = models.BooleanField(default=True)
    
    class Meta:
        ordering = ['-uploaded_at']
        verbose_name_plural = "Monitoring Reports"
        
    def __str__(self):
        return f"{self.title} ({self.get_report_category_display()})"
