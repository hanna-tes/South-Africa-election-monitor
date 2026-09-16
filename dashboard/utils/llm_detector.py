import logging
import json
import re
import os
from .llm_service import safe_llm_call, extract_json

logger = logging.getLogger(__name__)

def detect_hate_speech_llm(text):
    """
    Use LLM to detect hate speech. Falls back gracefully if LLM unavailable.
    Now shows actual Groq error messages instead of generic "LLM unavailable".
    """
    if not text or len(text.strip()) < 10:
        return {
            'is_hate_speech': False,
            'confidence': 0.0,
            'categories': [],
            'explanation': 'Text too short for analysis',
            'llm_detected': False
        }
    
    # Check if API key exists
    if not os.getenv('GROQ_API_KEY'):
        # LLM not available - return gracefully without error
        return {
            'is_hate_speech': False,
            'confidence': 0.0,
            'categories': [],
            'explanation': 'LLM service not configured (lexicon detection only)',
            'llm_detected': False
        }
    
    prompt = f"""You are an expert content moderator analyzing text for hate speech.

Analyze: "{text}"

Respond in JSON:
{{
    "is_hate_speech": true/false,
    "confidence": 0.0-1.0,
    "categories": ["ethnic_hate", "religious_hate", "derogatory"],
    "targeted_groups": ["groups"],
    "severity": "low/medium/high/critical",
    "explanation": "Brief explanation"
}}"""

    try:
        response, llm_error = safe_llm_call(prompt, return_error=True)
        
        if not response:  # Handle None response
            error_details = ''
            if llm_error:
                error_details = f"Reason: {llm_error['type']} - {llm_error['message']}"
            return {
                'is_hate_speech': False,
                'confidence': 0.0,
                'categories': [],
                'explanation': f'LLM returned empty response. {error_details}',
                'llm_detected': False
            }
        
        result = extract_json(response)
        if result is not None:
            return {
                'is_hate_speech': result.get('is_hate_speech', False),
                'confidence': result.get('confidence', 0.0),
                'categories': result.get('categories', []),
                'targeted_groups': result.get('targeted_groups', []),
                'severity': result.get('severity', 'low'),
                'explanation': result.get('explanation', ''),
                'llm_detected': True
            }
        return {
            'is_hate_speech': False,
            'confidence': 0.0,
            'categories': [],
            'explanation': 'Failed to parse LLM response',
            'llm_detected': False
        }
    except Exception as e:
        logger.exception("LLM response processing failed")
        return {
            'is_hate_speech': False,
            'confidence': 0.0,
            'categories': [],
            'explanation': f'LLM response processing failed: {type(e).__name__} - {e}',
            'llm_detected': False
        }
