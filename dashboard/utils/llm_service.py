import json
import logging
import re
import time
from openai import OpenAI
from django.conf import settings

logger = logging.getLogger(__name__)


def get_openrouter_client():
    """Initialize OpenRouter client."""
    api_key = getattr(settings, 'OPENROUTER_API_KEY', None)
    if not api_key:
        return None
    try:
        return OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
        )
    except Exception as e:
        logger.error(f"❌ Failed to initialize OpenRouter client: {str(e)}")
        return None


def extract_json(text):
    """Extract JSON from text, handling markdown code blocks and reasoning tags."""
    if not text:
        return None
    
    # Strip reasoning tags
    cleaned = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
    if '</think>' in cleaned.lower():
        cleaned = re.sub(r'^.*?</think>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    
    # Strip markdown code blocks
    cleaned = re.sub(r'^\s*```(?:json)?\s*|\s*```\s*$', '', cleaned, flags=re.MULTILINE).strip()
    
    # Try direct parse
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    
    # Try to find JSON object or array
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
    """Strip reasoning tags from response."""
    if not text:
        return text
    cleaned = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
    if '</think>' in cleaned.lower():
        cleaned = re.sub(r'^.*?</think>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    return cleaned.strip()


def _create_with_retry(client, params):
    """Make API call with retry logic for transient errors."""
    last = None
    max_retries = getattr(settings, 'OPENROUTER_MAX_RETRIES', 4)
    
    for attempt in range(max_retries):
        try:
            return client.chat.completions.create(**params)
        except Exception as e:
            last = e
            msg = str(e)
            transient = ('429' in msg or 'RateLimit' in type(e).__name__
                         or 'rate_limit' in msg or '500' in msg or '502' in msg
                         or '503' in msg or 'Timeout' in type(e).__name__)
            
            if not transient or attempt == max_retries - 1:
                raise
            
            wait = 1.8 ** attempt
            found = re.search(r'try again in ([\d.]+)s', msg, re.IGNORECASE)
            if found:
                wait = max(wait, float(found.group(1)))
            
            logger.warning(f"OpenRouter {type(e).__name__}, retrying in {wait:.1f}s")
            time.sleep(wait)
    
    raise last


def _log_llm_error_to_db(event_type, message, error_type=None, traceback_str=None, metadata=None):
    """Helper to log LLM errors to the existing ApplicationLog model."""
    try:
        from dashboard.models import ApplicationLog
        
        ApplicationLog.objects.create(
            level='ERROR',
            logger_name='dashboard.utils.llm_service',
            event_type=event_type,
            status='failed',
            message=message,
            actor='system',
            source='openrouter_api',
            object_type='LLM',
            metadata=metadata or {},
            traceback=traceback_str or ''
        )
    except Exception as db_err:
        logger.error(f"Failed to save LLM error to DB: {db_err}")


def safe_llm_call(prompt, max_tokens=2048, model=None, return_error=False, want_json=False):
    """
    Call OpenRouter LLM, optionally returning the current error alongside the response.
    
    Existing callers receive the original response/None contract. Callers that
    need to show the current failure can opt into ``(response, error)``.
    """
    model_name = model or getattr(settings, 'OPENROUTER_MODEL_ID', 'meta-llama/llama-3.3-70b-instruct')

    def result(response, error=None):
        return (response, error) if return_error else response

    client = get_openrouter_client()
    if not client:
        error = {
            'type': 'ClientUnavailable',
            'message': 'OpenRouter client could not be initialized. Check OPENROUTER_API_KEY.'
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
            'messages': [
                {
                    "role": "system",
                    "content": "You are an expert AI assistant specialized in South African political discourse, multilingual sociolinguistics, and information operations analysis. Be precise, objective, and strictly follow the requested output format."
                },
                {"role": "user", "content": prompt}
            ],
            'temperature': 0.2,
            'max_tokens': max_tokens,
        }
        
        if want_json:
            params['response_format'] = {'type': 'json_object'}

        response = _create_with_retry(client, params)
        
        if response and response.choices and response.choices[0].message.content:
            return result(_strip_reasoning(response.choices[0].message.content.strip()))

        error = {
            'type': 'EmptyResponse',
            'message': 'OpenRouter API returned an empty response.'
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
            f"OpenRouter API call failed: {error_msg}",
            error_type=error_type,
            traceback_str=error_msg,
            metadata={
                'model': model_name,
                'prompt_length': len(prompt),
                'api_error_type': error_type
            }
        )
        
        logger.error(f"❌ LLM call failed ({error_type}): {error_msg}")
        return result(None, {
            'type': error_type,
            'message': f'OpenRouter API call failed: {error_msg}'
        })


def safe_llm_json(prompt, max_tokens=1400, model=None, default=None, return_error=False, as_array=False):
    """Call LLM and parse JSON response."""
    response, error = safe_llm_call(prompt, max_tokens=max_tokens, model=model,
                                    return_error=True, want_json=not as_array)
    if not response:
        return (default, error) if return_error else default
    
    parsed = extract_json(response)
    if parsed is None:
        _log_llm_error_to_db(
            'llm_json_parse_error',
            'LLM response could not be parsed as JSON',
            metadata={'model': model or getattr(settings, 'OPENROUTER_MODEL_ID', 'unknown'),
                      'preview': response[:500]}
        )
        error = {'type': 'JSONParseError', 'message': 'LLM response was not valid JSON'}
        return (default, error) if return_error else default
    
    return (parsed, None) if return_error else parsed


def summarize_cluster_sa(texts, urls, cluster_data, min_ts, max_ts):
    """
    Generate structured IMI-style intelligence report for SA election narratives.
    Uses ONLY explicit claims from provided posts.
    """
    if not texts:
        return "No content available for summarization."
    
    # Join first 50 texts for context (avoids token limits)
    joined = "\n".join([f"[{i+1}] {t}" for i, t in enumerate(texts[:50]) if t and len(t.strip()) > 10])
    
    # Add real URLs for reference
    url_context = "\nRelevant post links:\n" + "\n".join(urls[:5]) if urls else ""
    
    # SA-specific prompt
    prompt = f"""
Generate a structured IMI intelligence report on online narratives related to the South Africa 2026 Municipal Elections.
Focus on pre and post-election tensions and emerging narratives, including:
- Service delivery failures (water, electricity, roads)
- Corruption allegations and tender fraud
- Load shedding / electricity crisis narratives
- Xenophobia and anti-immigrant sentiment (Operation Dudula, kwerekwere)
- Ethnic tensions (Zulu, Xhosa, Sotho, Afrikaans communities)
- Political party conflicts (ANC, DA, EFF, MK Party, Action SA)
- Gender-based violence and femicide
- Farm attacks and rural security
- IEC integrity and electoral fraud claims
- Foreign interference narratives
- Economic inequality and unemployment
- Land reform and expropriation debates
- Municipal governance failures

Strict Instructions:
Only report claims explicitly present in the provided posts below.
Identify originators: accounts that first posted the core claim.
Note amplification: how widely it spread.
Do NOT invent, cut out, assume, extrapolate, or fact-check claims.
Summarize clearly and concisely using simple language.

Output Format (Use plain text, NO markdown headers or bold):
Narrative Title: [Short, descriptive title]
Core Claim(s):
[Claim 1, directly quoted or closely paraphrased from posts]
[Claim 2]
[Claim 3]
Originator(s): [Account IDs or "Unknown" if not determinable]
Amplification: [Total posts] posts across [X] unique accounts
First Detected: {min_ts}
Last Updated: {max_ts}
Language/Tone Observed: [e.g., accusatory, urgent, informational, mixed]
Sample Quotes (exact phrases from posts, max 3):
'[exact quote 1]'
'[exact quote 2]'
'[exact quote 3]'

Documents for analysis:
{joined}{url_context}
"""
    
    try:
        response = safe_llm_call(prompt, max_tokens=2048)
        raw_summary = response.strip() if response else ""
    except Exception as e:
        logger.warning(f"LLM summary failed: {e}")
        # Fallback: extract key phrases directly from texts
        return _fallback_summary_sa(texts, urls, min_ts, max_ts)
    
    # Clean LLM output: remove markdown, code blocks, instruction echoes
    cleaned = re.sub(r'\*\*.*?(Instructions|strict|Output Format).*?\*\*', '', raw_summary, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r'`.*?`', '', cleaned, flags=re.DOTALL)
    cleaned = re.sub(r'###|##|#|\*\*|\*', '', cleaned)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    
    return cleaned.strip()


def _fallback_summary_sa(texts, urls, min_ts, max_ts):
    """Fallback when LLM fails: extract key phrases directly from posts."""
    if not texts:
        return "No content available for summarization."
    
    # Extract top keywords (simple frequency)
    from collections import Counter
    words = []
    for t in texts[:20]:
        words.extend(re.findall(r'\b[a-zA-Z]{4,}\b', t.lower()))
    top_words = [w for w, _ in Counter(words).most_common(10) if w not in {'the', 'and', 'for', 'that', 'with', 'this', 'from', 'have', 'were', 'will'}]
    
    # Get first 3 substantive sentences
    sentences = []
    for t in texts:
        sents = [s.strip() for s in t.split('.') if len(s.strip()) > 30]
        sentences.extend(sents[:2])
        if len(sentences) >= 3:
            break
    
    return f"""Narrative Title: Cluster discussing {', '.join(top_words[:3]) if top_words else 'SA election-related topics'}
Core Claim(s):
Posts reference electoral processes and political developments in South Africa
Content includes mentions of specific incidents, actors, or regional dynamics
Originator(s): Unknown (automated extraction)
Amplification: {len(texts)} posts
First Detected: {min_ts}
Last Updated: {max_ts}
Language/Tone Observed: Mixed tones with informational and analytical content
Sample Quotes:
'{sentences[0][:150]}...'
'{sentences[1][:150]}...'
'{sentences[2][:150]}...'"""
