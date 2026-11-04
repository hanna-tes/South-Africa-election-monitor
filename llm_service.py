import os
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

def safe_llm_call(prompt: str, max_tokens: int = 1024, temperature: float = 0.1) -> str:
    """
    Safely calls the LLM API via OpenRouter with robust error handling.
    Returns the generated text or an empty string on failure.
    """
    # Get API key from Django settings or environment variables
    api_key = getattr(settings, 'OPENROUTER_API_KEY', os.environ.get('OPENROUTER_API_KEY', ''))
    
    if not api_key:
        logger.warning("OPENROUTER_API_KEY is not set. LLM call skipped.")
        return ""
        
    try:
        from openai import OpenAI
        
        # Initialize OpenAI client pointing to OpenRouter
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
        )
        
        # Default model (You can change this in settings.py or here)
        # Examples: 'meta-llama/llama-3.3-70b-instruct', 'google/gemini-2.0-flash-exp:free', 'mistralai/mixtral-8x7b-instruct'
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


def summarize_cluster_sa(texts: list, urls: list = None, min_ts: str = "", max_ts: str = "") -> str:
    """
    Generates a structured summary of a cluster of texts using the LLM.
    """
    if not texts:
        return "No content available for summarization."
        
    # Take up to 30 representative texts to avoid token limits
    joined_texts = "\n".join([f"[{i+1}] {t}" for i, t in enumerate(texts[:30]) if t and len(t.strip()) > 10])
    url_context = "\nRelevant post links:\n" + "\n".join(urls[:5]) if urls else ""
    
    prompt = f"""
Generate a structured intelligence report on online narratives related to the South Africa 2026 Municipal Elections based ONLY on the provided posts.

Strict Instructions:
- Only report claims explicitly present in the provided posts.
- Do NOT invent, extrapolate, or fact-check claims.
- Summarize clearly and concisely.

Output Format (Plain text, NO markdown):
Narrative Title: [Short, descriptive title]
Core Claim(s):
[Claim 1]
[Claim 2]
Originator(s): [Account IDs or "Unknown"]
Amplification: [{len(texts)} posts]
First Detected: {min_ts}
Last Updated: {max_ts}
Language/Tone Observed: [e.g., accusatory, urgent, informational]
Sample Quotes (max 3):
'[exact quote 1]'
'[exact quote 2]'

Documents for analysis:
{joined_texts}
{url_context}
"""
    
    return safe_llm_call(prompt, max_tokens=1024, temperature=0.1)
