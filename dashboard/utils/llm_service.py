import json
import logging
import re
import time
from groq import Groq
from django.conf import settings

logger = logging.getLogger(__name__)


def get_groq_client():
    if not settings.GROQ_API_KEY:
        return None
    try:
        return Groq(api_key=settings.GROQ_API_KEY)
    except Exception as e:
        logger.error(f"❌ Failed to initialize Groq client: {str(e)}")
        return None


def extract_json(text):
    if not text:
        return None
    cleaned = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
    if '</think>' in cleaned.lower():
        cleaned = re.sub(r'^.*?</think>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'^\s*```(?:json)?\s*|\s*```\s*$', '', cleaned, flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    for opener, closer in (('{', '}'), ('[', ']')):
        start = cleaned.find(opener)
        while start != -1:
            depth, in_str, esc = 0, False, False
            for i in range(start, len(cleaned)):
                ch = cleaned[i]
                if esc:
                    esc = False
                    continue
                if ch == '\\':
                    esc = True
                    continue
                if ch == '"':
                    in_str = not in_str
                    continue
                if in_str:
                    continue
                if ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(cleaned[start:i + 1])
                        except Exception:
                            break
            start = cleaned.find(opener, start + 1)
    return None


def _strip_reasoning(text):
    if not text:
        return text
    cleaned = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
    if '</think>' in cleaned.lower():
        cleaned = re.sub(r'^.*?</think>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    return cleaned.strip()


def _create_with_retry(client, params):
    last = None
    for attempt in range(getattr(settings, 'GROQ_MAX_RETRIES', 4)):
        try:
            return client.chat.completions.create(**params)
        except Exception as e:
            last = e
            msg = str(e)
            transient = ('429' in msg or 'RateLimit' in type(e).__name__
                         or 'rate_limit' in msg or '500' in msg or '502' in msg
                         or '503' in msg or 'Timeout' in type(e).__name__)
            if not transient or attempt == getattr(settings, 'GROQ_MAX_RETRIES', 4) - 1:
                raise
            wait = 1.8 ** attempt
            found = re.search(r'try again in ([\d.]+)s', msg, re.IGNORECASE)
            if found:
                wait = max(wait, float(found.group(1)))
            logger.warning(f"Groq {type(e).__name__}, retrying in {wait:.1f}s")
            time.sleep(wait)
    raise last


def _log_llm_error_to_db(event_type, message, error_type=None, traceback_str=None, metadata=None):
    """Helper to log LLM errors to the existing ApplicationLog model."""
    try:
        from dashboard.models import ApplicationLog 
        
        ApplicationLog.objects.create(
            level='ERROR',
            logger_name='dashboard.utils.llm_service',
            event_type=event_type,       # e.g., 'llm_auth_error', 'llm_rate_limit'
            status='failed',
            message=message,
            actor='system',
            source='groq_api',
            object_type='LLM',
            metadata=metadata or {},
            traceback=traceback_str or ''
        )
    except Exception as db_err:
        logger.error(f"Failed to save LLM error to DB: {db_err}")

def safe_llm_call(prompt, max_tokens=2048, model=None, return_error=False, want_json=False):
    """Call Groq, optionally returning the current error alongside the response.

    Existing callers receive the original response/None contract. Callers that
    need to show the current failure can opt into ``(response, error)``.
    """
    model_name = model or getattr(settings, 'GROQ_MODEL', 'qwen/qwen3.6-27b')

    def result(response, error=None):
        return (response, error) if return_error else response

    client = get_groq_client()
    if not client:
        error = {
            'type': 'ClientUnavailable',
            'message': 'Groq client could not be initialized. Check GROQ_API_KEY.'
        }
        _log_llm_error_to_db(
            'llm_client_missing',
            error['message'],
            metadata={'model': model_name}
        )
        return result(None, error)
    
    try:
        params = {
            'model': model_name,
            'messages': [{"role": "user", "content": prompt}],
            'temperature': 0.2,
            'max_completion_tokens': max(max_tokens, getattr(settings, 'GROQ_MIN_COMPLETION_TOKENS', 900)),
        }
        if model_name.startswith('qwen/'):
            params['reasoning_effort'] = 'none'
        elif model_name.startswith('openai/gpt-oss'):
            params['reasoning_format'] = 'hidden'
        if want_json:
            params['response_format'] = {'type': 'json_object'}

        response = _create_with_retry(client, params)
        
        if response and response.choices and response.choices[0].message.content:
            return result(_strip_reasoning(response.choices[0].message.content.strip()))

        error = {
            'type': 'EmptyResponse',
            'message': 'Groq API returned an empty response.'
        }
        _log_llm_error_to_db(
            'llm_empty_response',
            error['message'],
            metadata={'model': model_name}
        )
        return result(None, error)
            
    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)
        
        # Categorize the event type for easier filtering in Admin
        if 'Authentication' in error_type:
            event_cat = 'llm_auth_error'
        elif 'RateLimit' in error_type:
            event_cat = 'llm_rate_limit'
        elif 'Timeout' in error_type:
            event_cat = 'llm_timeout'
        else:
            event_cat = 'llm_api_error'

        _log_llm_error_to_db(
            event_cat, 
            f"Groq API call failed: {error_msg}",
            error_type=error_type,
            traceback_str=error_msg,
            metadata={
                'model': model_name,
                'prompt_length': len(prompt),
                'groq_error_type': error_type
            }
        )
        
        logger.error(f"❌ LLM call failed ({error_type}): {error_msg}")
        return result(None, {
            'type': error_type,
            'message': f'Groq API call failed: {error_msg}'
        })

def safe_llm_json(prompt, max_tokens=1400, model=None, default=None, return_error=False, as_array=False):
    response, error = safe_llm_call(prompt, max_tokens=max_tokens, model=model,
                                    return_error=True, want_json=not as_array)
    if not response:
        return (default, error) if return_error else default
    parsed = extract_json(response)
    if parsed is None:
        _log_llm_error_to_db(
            'llm_json_parse_error',
            'LLM response could not be parsed as JSON',
            metadata={'model': model or getattr(settings, 'GROQ_MODEL', 'unknown'),
                      'preview': response[:500]}
        )
        error = {'type': 'JSONParseError', 'message': 'LLM response was not valid JSON'}
        return (default, error) if return_error else default
    return (parsed, None) if return_error else parsed


def load_ttp_detection_model():
    """Load the fine-tuned Gemma model for TTP detection"""
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name="unsloth/gemma-4-E4B-it", 
        max_seq_length=4096,
        dtype=None,
        load_in_4bit=True,  # Use 4-bit quantization to save memory
    )
    
    # Load your fine-tuned adapter
    model = FastLanguageModel.get_peft_model(
        model,
        r=16,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                       "gate_proj", "up_proj", "down_proj"],
        lora_alpha=16,
        lora_dropout=0,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=3407,
        use_rslora=False,
        loftq_config=None,
    )
    
    # Load adapter weights
    model.load_adapter("/Users/hannateshager/Ethiopia_2026election_monitoring_tool/model_cache/gemma-disarm-phase3-ttp")
    
    return model, tokenizer

def summarize_cluster_ethiopia(posts, cluster_id=None):
    """
    Generate LLM summary for an Ethiopia election narrative cluster.
    STUB: Returns a placeholder summary for now.
    
    Args:
        posts: QuerySet or list of ProcessedPost objects
        cluster_id: Optional cluster identifier
    
    Returns:
        str: Summary text
    """
    if not posts:
        return "No posts available for this narrative cluster."
    
    # Extract sample texts for context
    sample_texts = [p.original_text[:200] for p in posts[:5] if p.original_text]
    
    # STUB: Return a placeholder summary
    # TODO: Implement actual Groq/LLM call here
    return f"""🇪🇹 Ethiopia Election Narrative Summary (Cluster {cluster_id or 'N/A'})

📊 Analysis of {len(posts)} election-related posts:

🔍 Key Themes Detected:
• Election integrity and process discussions
• Political party positioning and messaging
• Regional dynamics and ethnic considerations

⚠️ Risk Assessment:
• Sentiment: Mixed (requires deeper analysis)
• Coordination signals: Not detected in sample
• Foreign interference indicators: None identified

💡 Recommended Actions:
• Monitor for escalation in rhetoric
• Track narrative spread across platforms
• Cross-reference with verified news sources

---
*This is a placeholder summary. Full LLM analysis requires Groq API configuration.*
"""
