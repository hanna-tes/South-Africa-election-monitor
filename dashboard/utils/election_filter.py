# dashboard/utils/election_filter.py

from datetime import datetime
from django.utils import timezone
from dashboard.models import ProcessedPost  

def is_election_related(text):
    """STUB: Check if text is election-related."""
    election_keywords = ['election', 'vote', 'campaign', 'candidate', 'ballot', 'ምርጫ', 'ምፅ', 'ቅስቀሳ','እጩ', 'የምጽ ማበርበሪያ ካር']
    if not text: return False
    text_lower = text.lower()
    return any(kw in text_lower for kw in election_keywords)

def get_election_posts_queryset(request):
    # 1. Set defaults to October 1, 2025 
    start_date_str = request.GET.get('start_date') or '2025-10-01'
    end_date_str = request.GET.get('end_date') or timezone.now().strftime('%Y-%m-%d')
    
    # 2. Convert to timezone-aware datetime objects (fixes the naive datetime warnings)
    start_date = timezone.make_aware(datetime.strptime(start_date_str, '%Y-%m-%d'))
    end_date = timezone.make_aware(datetime.strptime(end_date_str, '%Y-%m-%d'))
    
    # 3. Filter the posts
    queryset = ProcessedPost.objects.filter(
        timestamp_share__range=(start_date, end_date),
        is_election_related=True  # Ensure this boolean field exists in your ProcessedPost model
    ).order_by('-timestamp_share')
    
    return queryset, start_date, end_date
