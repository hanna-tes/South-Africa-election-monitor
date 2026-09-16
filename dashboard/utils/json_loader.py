import json
import os
import logging
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

def get_disarm_ttp_reference():
    """Extracts the most common DISARM techniques from the JSONL file safely."""
    # 1. Check cache for the FINAL result first
    cache_key = 'disarm_ttp_reference_final'
    cached_result = cache.get(cache_key)
    if cached_result is not None:
        return cached_result

    try:
        file_path = os.path.join(settings.BASE_DIR, 'dashboard', 'data', 'disarm_phase3_train.jsonl')
        if not os.path.exists(file_path):
            logger.warning(f"⚠️ JSONL file not found: {file_path}")
            return []

        techniques_map = {}
        
        # 2. Only read the first 5000 lines to save memory and time
        with open(file_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                if i >= 5000:  # Limit to 5000 lines for performance
                    break
                try:
                    record = json.loads(line.strip())
                    technique_list = record.get('allowed_techniques') or record.get('techniques') or record.get('ttps') or []
                    
                    if isinstance(technique_list, list):
                        for tech_id in technique_list:
                            if isinstance(tech_id, str) and tech_id.startswith('T'):
                                if tech_id not in techniques_map:
                                    techniques_map[tech_id] = {'id': tech_id, 'count': 0}
                                techniques_map[tech_id]['count'] += 1
                except json.JSONDecodeError:
                    continue
                    
        unique_ttps = list(techniques_map.values())
        unique_ttps.sort(key=lambda x: x['count'], reverse=True)
        
        final_result = unique_ttps[:10] # Return top 10
        
        # 3. Cache the FINAL small result, not the 80k raw records!
        cache.set(cache_key, final_result, 86400)
        
        return final_result
        
    except Exception as e:
        logger.error(f"❌ Error loading DISARM reference: {e}")
        return []
