import os
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

def safe_llm_call(prompt: str, max_tokens: int = 1024, temperature: float = 0.1) -> str:
    """
    Safely calls the LLM API via OpenRouter with robust error handling.
    Returns the generated text or an empty string on failure.
    """
    api_key = getattr(settings, 'OPENROUTER_API_KEY', os.environ.get('OPENROUTER_API_KEY', ''))
    
    if not api_key:
        logger.warning("OPENROUTER_API_KEY is not set. LLM call skipped.")
        return ""
        
    try:
        from openai import OpenAI
        
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
        )
        
        model_id = getattr(settings, 'OPENROUTER_MODEL_ID', 'meta-llama/llama-3.3-70b-instruct')
        
        response = client.chat.completions.create(
            model=model_id,
            messages=[
                {
                    "role": "system", 
                    "content": "You are an expert AI assistant specialized in South African political discourse, multilingual sociolinguistics, and information operations analysis. Be precise, objective, and strictly follow the requested output format."
                },
                {
                    "role": "user", 
                    "content": prompt
                }
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        
        return response.choices[0].message.content.strip()
        
    except ImportError:
        logger.error("openai library is not installed. Please run: pip install openai")
        return ""
    except Exception as e:
        logger.error(f"OpenRouter LLM API call failed: {e}")
        return ""


def summarize_cluster_sa(texts, urls, cluster_data, min_ts, max_ts):
    """
    Generate structured IMI-style intelligence report for SA election narratives.
    Uses ONLY explicit claims from provided posts.
    """
    joined = "\n".join([f"[{i+1}] {t}" for i, t in enumerate(texts[:50]) if t and len(t.strip()) > 10])
    url_context = "\nRelevant post links:\n" + "\n".join(urls[:5]) if urls else ""
    
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
        return _fallback_summary_sa(texts, urls, min_ts, max_ts)
    
    # Clean LLM output
    import re
    cleaned = re.sub(r'\*\*.*?(Instructions|strict|Output Format).*?\*\*', '', raw_summary, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r'`.*?`', '', cleaned, flags=re.DOTALL)
    cleaned = re.sub(r'###|##|#|\*\*|\*', '', cleaned)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()


def _fallback_summary_sa(texts, urls, min_ts, max_ts):
    """Fallback when LLM fails: extract key phrases directly from posts"""
    if not texts:
        return "No content available for summarization."
    
    from collections import Counter
    import re
    words = []
    for t in texts[:20]:
        words.extend(re.findall(r'\b[a-zA-Z]{4,}\b', t.lower()))
    top_words = [w for w, _ in Counter(words).most_common(10) if w not in {'the', 'and', 'for', 'that', 'with', 'this', 'from', 'have', 'were', 'will'}]
    
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
