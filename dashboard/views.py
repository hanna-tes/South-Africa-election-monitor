"""
Django views for South Africa Municipal Elections Monitor
"""
import json
import logging
import os
import re
from django.db.models import Q
import requests
import threading
import random
import hashlib
from django.core.cache import cache
import csv
from django.utils import timezone
from io import StringIO
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from .utils.json_loader import get_disarm_ttp_reference
from django.shortcuts import render, redirect
from django.contrib import messages
from django.views.generic import TemplateView, View
from django.http import JsonResponse, HttpResponse
from django.db.models import Count, Q, F, Case, When, Value, CharField, Max, Avg, Min
from django.contrib.postgres.aggregates import ArrayAgg
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.db.models.functions import TruncDay
import networkx as nx
import plotly.express as px
import plotly.graph_objects as go
from django.utils import timezone
from .models import (
    DashboardAnalyticsSnapshot,
    DataUpload,
    LexiconTerm,
    NarrativeCluster,
    PEP,
    PostLexiconMatch,
    ProcessedPost,
)
from .utils.llm_service import safe_llm_call, summarize_cluster_sa
from .utils.data_loader import load_data_robustly, load_peps_from_github
from .utils.csv_processor import process_uploaded_csv, map_columns_by_type, preprocess_dataframe
from .utils.lexicon_engine import scan_text_for_lexicon_terms, calculate_risk_score, generate_lexicon_analytics
from .utils.election_filter import is_election_related
from .utils.wordcloud import generate_trigger_wordcloud, wordcloud_to_base64
from .utils.app_logging import log_event
from .utils.data_loader import parse_timestamp_robust
from .utils.csv_processor import combine_social_media_data
from .models import ProcessedPost, DataSource
from django.views.decorators.cache import never_cache
from .utils.llm_detector import detect_hate_speech_llm
from .models import ElectionOfficeholder
from django.shortcuts import render, get_object_or_404
from .models import MonitoringReport
from typing import List, Dict, Any, Optional
from django.contrib.auth.decorators import login_required
from .detectors import is_election_related
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger(__name__)

# Global model cache
_GEMMA_MODEL = None
_GEMMA_TOKENIZER = None

# Cache to merge CONFIG and Database lexicon terms
_COMBINED_LEXICON_CACHE = None
_CACHE_TIMESTAMP = 0


def load_gemma_lora_model():
    """Gemma LoRA model - DISABLED"""
    logger.warning("Gemma LoRA model is DISABLED")
    return None, None


def get_combined_lexicon():
    """
    Merges CONFIG lexicon with Database lexicon terms.
    Cached for 60 seconds to prevent database hammering during scans.
    """
    global _COMBINED_LEXICON_CACHE, _CACHE_TIMESTAMP
    import time
    if _COMBINED_LEXICON_CACHE is None or (time.time() - _CACHE_TIMESTAMP) > 60:
        try:
            combined = {k: v.copy() for k, v in CONFIG.get("lexicon", {}).items()}
            db_terms = LexiconTerm.objects.filter(is_election_related=True).values(
                'term', 'category', 'severity', 'target_entity', 'language'
            )
            for term_obj in db_terms:
                cat = term_obj['category']
                if cat not in combined:
                    combined[cat] = {}
                combined[cat][term_obj['term']] = {
                    'severity': term_obj['severity'],
                    'target_entity': term_obj['target_entity'],
                    'language': term_obj['language']
                }
            _COMBINED_LEXICON_CACHE = combined
            _CACHE_TIMESTAMP = time.time()
        except Exception as e:
            logger.warning(f"Failed to load DB lexicon terms, falling back to CONFIG: {e}")
            _COMBINED_LEXICON_CACHE = CONFIG.get("lexicon", {})
            _CACHE_TIMESTAMP = time.time()
    return _COMBINED_LEXICON_CACHE


WEAPONIZED_KEYWORDS = [
    # === ENGLISH: VIOLENCE & CONFLICT ===
    'genocide', 'kill', 'attack', 'war', 'slur', 'hate',
    'ethnic cleansing', 'massacre', 'slaughter', 'destroy',
    'eliminate', 'terrorist', 'extremist', 'traitor', 'criminal',
    'dictator', 'oppressor', 'failed state', 'invasion', 'exterminate',
    'wipe out', 'blood on hands', 'step down', 'death to', 'puppet of',
    # === SA POLITICAL SLURS & CONTEXT ===
    'kwerekwere', 'makwerekwere', 'operation dudula', 'amapara',
    'phara', 'skhotheni', 'tsotsi', 'gangster', 'cable theft',
    'load shedding', 'stage 6', 'stage 8', 'eskom', 'corrupt',
    'state capture', 'zondo', 'guptas', 'tenderpreneur',
    'farm attack', 'plaasmoord', 'white monopoly capital', 'wmc',
    'radical economic transformation', 'ret', 'radical',
    'land expropriation', 'expropriation without compensation', 'ewc',
    'xenophobia', 'afrophobia', 'anti-immigrant', 'foreigner',
    'gender-based violence', 'gbv', 'femicide', 'rape',
    'service delivery', 'municipal failure', 'council collapse',
    'anc', 'da', 'eff', 'mk party', 'action sa', 'patriotic alliance',
    'iff', 'zuma', 'ramaphosa', 'maffikeng', 'lekgowa', 'makgowa',
    'boer', 'boere', 'kaffer', 'kaffir', 'hotnot', 'coolie',
    'white genocide', 'white extinction', 'farm murders',
    'black danger', 'swart gevaar', 'black threat',
    'tribalism', 'ethnic violence', 'xenophobic attack',
    'burn', 'burn down', 'loot', 'looting', 'riot', 'rioting',
    'shooting', 'stabbing', 'hijacking', 'carjacking',
    'house invasion', 'home invasion', 'kidnap', 'kidnapping',
]


def should_process_post(text: str) -> bool:
    """
    Allows all posts through EXCEPT those that have no mention of South African
    affairs/geography or are clearly spam/walls of irrelevant hashtags.
    """
    if not text:
        return False
    text_lower = text.lower()
    
    # 1. Broadest possible anchors to confirm the post is about SA/local affairs
    sa_anchors = [
        "south africa", "sa election", "municipal election", "iec", "anc", "da", "eff",
        "iff", "action sa", "patriotic alliance", "mk party", "zuma", "ramaphosa",
        "maffikeng", "pretoria", "cape town", "durban", "joburg", "johannesburg",
        "tshwane", "ekurhuleni", "ethekwini", "buffalo city", "mangaung",
        "nelson mandela bay", "gauteng", "kwazulu-natal", "western cape",
        "eastern cape", "limpopo", "mpumalanga", "north west", "free state",
        "northern cape", "ward councillor", "proportional representation",
        "municipal council", "local government", "2026 election", "november 2026",
        "load shedding", "eskom", "service delivery", "xenophobia", "kwerekwere",
        "operation dudula", "farm attack", "gender-based violence", "gbv",
        "state capture", "tenderpreneur", "corruption", "municipal",
        "zulu", "xhosa", "sotho", "tswana", "venda", "tsonga", "ndebele",
        "swati", "pedi", "afrikaans", "afrikaner", "boer", "boere",
        "coloured", "indian south african", "black south african",
        "white south african", "rainbow nation", "ubuntu",
    ]
    has_local_context = any(anchor in text_lower for anchor in sa_anchors)
    
    # 2. Identify hashtag spam not talking about SA affairs
    hashtags = re.findall(r"#\w+", text_lower)
    if hashtags:
        spam_hashtags = {
            "#crypto", "#bitcoin", "#nft", "#forex", "#marketing", "#digitalmarketing",
            "#makeup", "#fashion", "#travel", "#football", "#cricket", "#ecommerce",
            "#jobs", "#hiring", "#win", "#giveaway", "#fitness", "#motivation",
            "#blessed", "#love", "#happy", "#instagood", "#photooftheday",
        }
        spam_count = sum(1 for tag in hashtags if tag in spam_hashtags)
        if len(hashtags) >= 3 and (spam_count / len(hashtags)) > 0.5:
            return False
    
    # 3. Final Decision
    return has_local_context


def detect_hate_speech_afro_xlmr(text: str) -> dict:
    """
    Detect hate speech using AFRO-XLMR model
    """
    try:
        from .utils.afro_xlmr_detector import get_detector
        detector = get_detector()
        return detector.detect(text)
    except Exception as e:
        logger.error(f"AFRO-XLMR detection failed: {e}")
        return {
            'is_hate_speech': False,
            'confidence': 0.0,
            'category': 'error',
            'severity': 'low',
            'error': str(e)
        }


def translate_sa_terms_llm(terms_list):
    """
    Use LLM to translate SA terms (Zulu, Xhosa, Afrikaans, Sotho, etc.) to English with context
    """
    if not terms_list:
        return []
    
    sa_language_terms = [t for t in terms_list if t.get('metadata', {}).get('language', '').lower() in 
                         ['zulu', 'xhosa', 'afrikaans', 'sotho', 'tswana', 'venda', 'tsonga', 'ndebele', 'swati', 'pedi']]
    if not sa_language_terms:
        return []
    
    try:
        terms_text = "\n".join([f"- {t['term']} (Severity: {t['metadata'].get('severity', 'unknown')}, Category: {t['metadata'].get('category', 'unknown')}, Language: {t['metadata'].get('language', 'unknown')})" for t in sa_language_terms])
        prompt = f"""Translate these South African hate speech terms (from Zulu, Xhosa, Afrikaans, Sotho, Tswana, Venda, Tsonga, Ndebele, Swati, Pedi) to English. Provide contextual meaning and explain the severity:
{terms_text}
Format each translation as:
Term: [Original term]
English: [Translation]
Context: [Explanation of usage and severity in SA context]
"""
        from .utils.llm_integration import call_llm_api
        response = call_llm_api(prompt, temperature=0.3)
        translations = parse_llm_translations(response)
        for term in sa_language_terms:
            term_term = term['term']
            if term_term in translations:
                term['translation'] = translations[term_term]
        return sa_language_terms
    except Exception as e:
        logger.error(f"LLM translation failed: {e}")
        return sa_language_terms


# ==========================================
# 1. DEFINE UTILITY FUNCTIONS
# ==========================================
def preprocess_and_clean_post(post_text, threshold=5):
    """
    Checks for hashtag stuffing. If the number of hashtags exceeds the threshold,
    it strips them from the text so the classifier isn't misled.
    """
    hashtag_pattern = r'#\S+'
    hashtags = re.findall(hashtag_pattern, post_text)
    if len(hashtags) > threshold:
        cleaned_text = re.sub(hashtag_pattern, '', post_text)
        cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip()
        confidence_flag = "low-confidence"
    else:
        cleaned_text = post_text
        confidence_flag = "normal"
    return cleaned_text, confidence_flag


# ==========================================
# 2. THE PROCESSING PIPELINE
# ==========================================
def process_incoming_post(raw_post, classifier, publish_function):
    cleaned_text, confidence = preprocess_and_clean_post(raw_post, threshold=5)
    category_prediction = classifier.predict(cleaned_text)
    if confidence == "low-confidence":
        print(f"Post dropped! Caught hashtag stuffing. (Predicted: {category_prediction})")
        return False
    publish_function(category_prediction, raw_post)
    return True


def parse_llm_translations(response_text):
    """Parse LLM response to extract translations"""
    translations = {}
    current_term = None
    current_translation = {}
    for line in response_text.strip().split('\n'):
        line = line.strip()
        if line.startswith('Term:'):
            if current_term and current_translation:
                translations[current_term] = current_translation
            current_term = line.replace('Term:', '').strip()
            current_translation = {}
        elif line.startswith('English:'):
            current_translation['english'] = line.replace('English:', '').strip()
        elif line.startswith('Context:'):
            current_translation['context'] = line.replace('Context:', '').strip()
    if current_term and current_translation:
        translations[current_term] = current_translation
    return translations


def export_merged_gephi_csv(request):
    """Generates and downloads a Gephi-compatible CSV edge-list representing the coordination network"""
    import re
    from collections import defaultdict
    
    min_connections = int(request.GET.get('min_connections', 2))
    cluster_filter = request.GET.get('cluster') or request.GET.get('group_id') or request.GET.get('coordination_group')
    query_kwargs = {'is_election_related': True}
    
    if cluster_filter and cluster_filter.strip():
        min_connections = 1
        if hasattr(ProcessedPost, 'coordination_group_id'):
            query_kwargs['coordination_group_id'] = cluster_filter
        elif hasattr(ProcessedPost, 'coordination_group'):
            query_kwargs['coordination_group'] = cluster_filter
        else:
            try:
                query_kwargs['cluster'] = int(cluster_filter)
            except ValueError:
                pass
    else:
        if hasattr(ProcessedPost, 'cluster'):
            query_kwargs['cluster__gte'] = 0
    
    posts_data = ProcessedPost.objects.filter(**query_kwargs).values('account_id', 'original_text')
    G = nx.Graph()
    rt_pattern = re.compile(r'RT\s+@([a-zA-Z0-9_]+)', re.IGNORECASE)
    text_to_accounts = defaultdict(set)
    
    for post in posts_data:
        acc = post['account_id']
        text = post['original_text'] or ''
        if not acc or acc == 'unknown':
            continue
        rt_match = rt_pattern.search(text)
        if rt_match:
            target_user = rt_match.group(1)
            if acc != target_user:
                if G.has_edge(acc, target_user):
                    G[acc][target_user]['weight'] += 1
                else:
                    G.add_edge(acc, target_user, weight=1)
        text_to_accounts[text].add(acc)
    
    for acc_set in text_to_accounts.values():
        if len(acc_set) >= 2:
            acc_list = list(acc_set)
            for i in range(len(acc_list)):
                for j in range(i + 1, len(acc_list)):
                    if acc_list[i] != acc_list[j]:
                        if G.has_edge(acc_list[i], acc_list[j]):
                            G[acc_list[i]][acc_list[j]]['weight'] += 1
                        else:
                            G.add_edge(acc_list[i], acc_list[j], weight=1)
    
    nodes_to_keep = [n for n, d in G.degree() if d >= min_connections]
    G = G.subgraph(nodes_to_keep)
    
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="coordination_network_edges_{timezone.now().strftime("%Y%m%d")}.csv"'
    writer = csv.writer(response)
    writer.writerow(['Source', 'Target', 'Type', 'Weight'])
    for u, v, data in G.edges(data=True):
        writer.writerow([u, v, 'Undirected', data.get('weight', 1)])
    return response


def export_network_csv(request):
    """Export coordination network data as Gephi-ready CSV files"""
    import csv
    from django.http import HttpResponse
    from django.db.models import Count
    
    min_connections = int(request.GET.get('min_connections', 2))
    posts = ProcessedPost.objects.filter(is_election_related=True)
    coordination_groups = get_coordination_groups(posts, min_accounts=min_connections, max_groups=15)
    
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="coordination_network_edges.csv"'
    writer = csv.writer(response)
    writer.writerow(['Source', 'Target', 'Weight', 'Type', 'Id'])
    edge_id = 1
    for group in coordination_groups:
        accounts = group.get('accounts', [])
        for i in range(len(accounts)):
            for j in range(i+1, len(accounts)):
                source = accounts[i]
                target = accounts[j]
                weight = group.get('post_count', 1)
                writer.writerow([source, target, weight, 'Undirected', f'edge_{edge_id}'])
                edge_id += 1
    return response


def export_network_nodes_csv(request):
    """Export nodes CSV for Gephi"""
    import csv
    from django.http import HttpResponse
    from django.db.models import Count
    
    min_connections = int(request.GET.get('min_connections', 2))
    posts = ProcessedPost.objects.filter(is_election_related=True)
    coordination_groups = get_coordination_groups(posts, min_accounts=min_connections, max_groups=15)
    
    all_accounts = {}
    for group in coordination_groups:
        for account in group.get('accounts', []):
            if account not in all_accounts:
                all_accounts[account] = {'post_count': 0, 'group_count': 0, 'platforms': set()}
            all_accounts[account]['post_count'] += group.get('post_count', 0)
            all_accounts[account]['group_count'] += 1
            if 'platforms' in group:
                all_accounts[account]['platforms'].update(group['platforms'])
    
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="coordination_network_nodes.csv"'
    writer = csv.writer(response)
    writer.writerow(['Id', 'Label', 'Post Count', 'Group Count', 'Platforms', 'Type'])
    for account, data in all_accounts.items():
        platforms = ', '.join(data['platforms']) if data['platforms'] else 'Unknown'
        writer.writerow([account, account, data['post_count'], data['group_count'], platforms, 'Account'])
    return response


# === GEPHI CSV EXPORT FUNCTIONS ===
def _get_coordination_edges():
    """Helper to extract coordination edges based on identical text."""
    coordinated_texts = ProcessedPost.objects.filter(is_election_related=True) \
        .values('original_text') \
        .annotate(account_count=Count('account_id', distinct=True)) \
        .filter(account_count__gte=2) \
        .filter(original_text__isnull=False) \
        .exclude(original_text='')
    
    edges = []
    nodes_dict = {}
    
    for text_group in coordinated_texts:
        text = text_group['original_text']
        if len(text.strip()) < 20:
            continue
        posts = list(ProcessedPost.objects.filter(original_text=text)
                     .order_by('timestamp_share')
                     .values('account_id', 'timestamp_share', 'url', 'platform'))
        if not posts:
            continue
        source_account = posts[0]['account_id']
        first_date = posts[0]['timestamp_share']
        url = posts[0]['url']
        platform = posts[0]['platform']
        
        if source_account not in nodes_dict:
            nodes_dict[source_account] = {'originated': 0, 'amplified': 0}
        nodes_dict[source_account]['originated'] += 1
        
        for post in posts[1:]:
            target_account = post['account_id']
            if target_account == source_account:
                continue
            if target_account not in nodes_dict:
                nodes_dict[target_account] = {'originated': 0, 'amplified': 0}
            nodes_dict[target_account]['amplified'] += 1
            edges.append({
                'source': source_account,
                'target': target_account,
                'tweet': text,
                'date': first_date.isoformat() if hasattr(first_date, 'isoformat') else (first_date.strftime('%Y-%m-%d %H:%M:%S') if first_date else ''),
                'platform': platform,
                'url': url or ''
            })
    return edges, nodes_dict


def export_gephi_nodes_csv(request):
    """Export Nodes CSV for Gephi with source/amplifier classification"""
    min_connections = int(request.GET.get('min_connections', 2))
    posts = ProcessedPost.objects.filter(is_election_related=True)
    coordination_groups = get_coordination_groups(posts, min_accounts=min_connections, max_groups=15)
    
    all_accounts = {}
    for group in coordination_groups:
        text = group.get('text_sample', '')
        if not text:
            continue
        account_posts = posts.filter(original_text=text).order_by('timestamp_share')
        for idx, post in enumerate(account_posts):
            username = clean_username(post.account_id)
            if username and len(username) > 2:
                if username not in all_accounts:
                    all_accounts[username] = {
                        'post_count': 0, 'group_count': 0, 'platforms': set(),
                        'is_source': False, 'is_amplifier': False,
                        'sub_narrative': group.get('sub_narrative', 'General Coordination')
                    }
                all_accounts[username]['post_count'] += 1
                all_accounts[username]['group_count'] += 1
                if post.platform:
                    all_accounts[username]['platforms'].add(post.platform)
                if idx == 0:
                    all_accounts[username]['is_source'] = True
                else:
                    all_accounts[username]['is_amplifier'] = True
    
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="gephi_nodes.csv"'
    writer = csv.writer(response)
    writer.writerow(['Id', 'Label', 'Post Count', 'Group Count', 'Platforms', 'Node Type', 'Sub Narrative', 'Is Source', 'Is Amplifier'])
    for account, data in all_accounts.items():
        platforms = ', '.join(data['platforms']) if data['platforms'] else 'Unknown'
        node_type = 'Source' if data['is_source'] and not data['is_amplifier'] else 'Amplifier' if data['is_amplifier'] else 'Both'
        writer.writerow([account, account, data['post_count'], data['group_count'], platforms, node_type, data['sub_narrative'], data['is_source'], data['is_amplifier']])
    return response


def export_gephi_edges_csv(request):
    """Export Edges CSV for Gephi with tweet content filtered explicitly for SA context"""
    min_connections = int(request.GET.get('min_connections', 2))
    posts = ProcessedPost.objects.filter(
        is_election_related=True
    ).filter(
        Q(original_text__icontains='South Africa') |
        Q(original_text__icontains='Mzansi') |
        Q(original_text__icontains='SA election')
    )
    coordination_groups = get_coordination_groups(posts, min_accounts=min_connections, max_groups=15)
    
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="gephi_edges.csv"'
    writer = csv.writer(response)
    writer.writerow(['Source', 'Target', 'Timestamp', 'Text', 'URL'])
    for group in coordination_groups:
        accounts = group.get('accounts', [])
        text_sample = group.get('text_sample', '')
        timestamp = ''
        url = ''
        if group.get('sample_posts_with_urls'):
            first_post = group['sample_posts_with_urls'][0]
            timestamp = first_post.get('timestamp', '')
            url = first_post.get('url', '')
        tweet_clean = text_sample.replace('\n', ' ').replace('\r', '').replace('"', '""').strip()
        for i in range(len(accounts)):
            for j in range(i+1, len(accounts)):
                writer.writerow([accounts[i], accounts[j], timestamp, tweet_clean, url])
    return response


def export_gephi_edges_with_roles_csv(request):
    """Export Edges CSV showing Source -> Amplifier relationships"""
    min_connections = int(request.GET.get('min_connections', 2))
    posts = ProcessedPost.objects.filter(is_election_related=True)
    coordination_groups = get_coordination_groups(posts, min_accounts=min_connections, max_groups=15)
    
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="gephi_source_amplifier_edges.csv"'
    writer = csv.writer(response)
    writer.writerow(['Source Account', 'Amplifier Account', 'Weight', 'Type', 'Tweet', 'Sub Narrative', 'First Post Time'])
    for group in coordination_groups:
        text = group.get('text_sample', '')
        if not text:
            continue
        account_posts = posts.filter(original_text=text).order_by('timestamp_share')
        source_account = None
        first_time = None
        for idx, post in enumerate(account_posts):
            username = clean_username(post.account_id)
            if not username or len(username) < 2:
                continue
            if idx == 0:
                source_account = username
                first_time = post.timestamp_share.isoformat() if post.timestamp_share else ''
            else:
                tweet_clean = text[:200].replace('\n', ' ').replace('\r', '').replace('"', '""')
                writer.writerow([source_account, username, 1, 'Directed', f'"{tweet_clean}"', group.get('sub_narrative', 'General Coordination'), first_time])
    return response


def export_gephi_edges_tweets_csv(request):
    """Export Edges + Tweets CSV for Gephi"""
    edges, _ = _get_coordination_edges()
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="gephi_edges_tweets.csv"'
    writer = csv.writer(response)
    writer.writerow(['Source', 'Target', 'Tweet', 'Date', 'URL'])
    for edge in edges:
        tweet_text = edge['tweet'][:200].replace('\n', ' ').replace('\r', '')
        writer.writerow([edge['source'], edge['target'], tweet_text, edge['date'], edge['url']])
    return response


def export_network_edges_with_tweets(request):
    """Export edges with tweet content for Gephi"""
    import csv
    from django.http import HttpResponse
    
    min_connections = int(request.GET.get('min_connections', 2))
    posts = ProcessedPost.objects.filter(is_election_related=True)
    coordination_groups = get_coordination_groups(posts, min_accounts=min_connections, max_groups=15)
    
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="coordination_edges_with_tweets.csv"'
    writer = csv.writer(response)
    writer.writerow(['Source', 'Target', 'Tweet', 'Post Count', 'Platforms', 'Timestamp'])
    for group in coordination_groups:
        accounts = group.get('accounts', [])
        text_sample = group.get('text_sample', '')
        post_count = group.get('post_count', 0)
        platforms = ', '.join(group.get('platforms', [])) if 'platforms' in group else 'Unknown'
        timestamp = ''
        if group.get('sample_posts_with_urls'):
            first_post = group['sample_posts_with_urls'][0]
            timestamp = first_post.get('timestamp', '')
        for i in range(len(accounts)):
            for j in range(i+1, len(accounts)):
                writer.writerow([accounts[i], accounts[j], text_sample[:200], post_count, platforms, timestamp])
    return response


def export_complete_network_csv(request):
    """Export COMPLETE network data - both nodes and edges with full metadata"""
    import csv
    from django.http import HttpResponse
    from django.utils import timezone
    
    min_connections = int(request.GET.get('min_connections', 2))
    posts = ProcessedPost.objects.filter(is_election_related=True)
    coordination_groups = get_coordination_groups(posts, min_accounts=min_connections, max_groups=50)
    
    all_nodes = {}
    all_edges = []
    
    for group_idx, group in enumerate(coordination_groups):
        accounts = group.get('accounts', [])
        text_sample = group.get('text_sample', '')
        post_count = group.get('post_count', 0)
        platforms = group.get('platforms', [])
        coordination_type = group.get('coordination_type', 'Unknown')
        sub_narrative = group.get('sub_narrative', 'General Coordination')
        bot_count = group.get('bot_count', 0)
        bot_percentage = group.get('bot_percentage', 0)
        
        timestamp = ''
        url = ''
        if group.get('sample_posts_with_urls'):
            first_post = group['sample_posts_with_urls'][0]
            timestamp = first_post.get('timestamp', '')
            url = first_post.get('url', '')
        
        for account in accounts:
            if account not in all_nodes:
                all_nodes[account] = {
                    'account_id': account, 'group_count': 0, 'total_posts': 0,
                    'platforms': set(), 'coordination_types': set(), 'sub_narratives': set(),
                    'is_bot': False, 'bot_percentage': 0, 'first_seen': timestamp, 'sample_url': url
                }
            all_nodes[account]['group_count'] += 1
            all_nodes[account]['total_posts'] += post_count
            all_nodes[account]['platforms'].update(platforms)
            all_nodes[account]['coordination_types'].add(coordination_type)
            all_nodes[account]['sub_narratives'].add(sub_narrative)
            if bot_percentage >= 50:
                all_nodes[account]['is_bot'] = True
                all_nodes[account]['bot_percentage'] = max(all_nodes[account]['bot_percentage'], bot_percentage)
        
        for i in range(len(accounts)):
            for j in range(i + 1, len(accounts)):
                all_edges.append({
                    'source': accounts[i], 'target': accounts[j], 'weight': post_count,
                    'group_id': group_idx + 1, 'text_sample': text_sample[:200],
                    'timestamp': timestamp, 'url': url,
                    'coordination_type': coordination_type, 'sub_narrative': sub_narrative
                })
    
    response = HttpResponse(content_type='text/csv')
    timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
    response['Content-Disposition'] = f'attachment; filename="complete_network_data_{timestamp}.csv"'
    response.write('\ufeff')
    writer = csv.writer(response)
    writer.writerow(['=== NODES (Accounts) ==='])
    writer.writerow(['Account ID', 'Group Count', 'Total Posts', 'Platforms', 'Coordination Types', 'Sub Narratives', 'Is Bot', 'Bot Percentage', 'First Seen', 'Sample URL'])
    for account, data in sorted(all_nodes.items()):
        writer.writerow([account, data['group_count'], data['total_posts'], ', '.join(data['platforms']), ', '.join(data['coordination_types']), ', '.join(data['sub_narratives']), 'Yes' if data['is_bot'] else 'No', f"{data['bot_percentage']:.1f}%", data['first_seen'], data['sample_url']])
    writer.writerow([])
    writer.writerow(['=== EDGES (Connections) ==='])
    writer.writerow(['Source', 'Target', 'Weight', 'Group ID', 'Text Sample', 'Timestamp', 'URL', 'Coordination Type', 'Sub Narrative'])
    for edge in all_edges:
        text_clean = edge['text_sample'].replace('\n', ' ').replace('\r', '').replace('"', '""')
        writer.writerow([edge['source'], edge['target'], edge['weight'], edge['group_id'], text_clean, edge['timestamp'], edge['url'], edge['coordination_type'], edge['sub_narrative']])
    writer.writerow([])
    writer.writerow(['=== NETWORK SUMMARY ==='])
    writer.writerow(['Total Nodes (Accounts):', len(all_nodes)])
    writer.writerow(['Total Edges (Connections):', len(all_edges)])
    writer.writerow(['Total Coordination Groups:', len(coordination_groups)])
    writer.writerow(['Export Date:', timezone.now().strftime('%Y-%m-%d %H:%M:%S UTC')])
    messages.success(request, f"Exported complete network: {len(all_nodes)} nodes, {len(all_edges)} edges")
    return response


def extract_sub_narrative(text_sample):
    """
    Extract the primary sub-narrative from a text sample using the CONFIG lexicon.
    Uses SA-specific terms across multiple languages.
    """
    if not text_sample or len(text_sample.strip()) < 10:
        return "General News"
    
    matches = scan_text_for_lexicon_terms(text_sample)
    if not matches:
        return "General News"
    
    category_counts = Counter([m['category'] for m in matches])
    top_category = category_counts.most_common(1)[0][0]
    
    narrative_map = {
        'dehumanizing': 'Dehumanizing Language',
        'derogatory': 'Derogatory Terms',
        'ethnic_slur': 'Ethnic Slurs',
        'ethnicity': 'Ethnic References',
        'slur': 'Slurs',
        'violence': 'Violence & Threats',
        'incitement': 'Incitement',
        'misogynistic': 'Gender-Based Attacks',
        'stereotype': 'Stereotypes',
        'xenophobia': 'Xenophobia & Anti-Immigrant',
        'homophobic': 'Homophobic Language',
        'religious': 'Religious Hate',
        'class_based': 'Class Discrimination',
        'ancestry': 'Ancestry-Based Hate',
        'extremism': 'Extremism',
        'inflammatory': 'Inflammatory Language',
        'call_to_action': 'Calls to Action',
        'gender_disinfo': 'Gender Disinformation',
        'structural': 'Structural Discrimination',
    }
    return narrative_map.get(top_category, 'General News')


def format_ttp_input(coordination_groups: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Format coordination groups into the input structure expected by the Gemma model
    Following the Phase 3 schema from the notebook
    """
    all_posts = []
    platforms = set()
    account_ids = set()
    for group in coordination_groups:
        platforms.update(group.get('platforms', []))
        account_ids.update(group.get('accounts', []))
        sample_posts = group.get('sample_posts_with_urls', [])
        for post in sample_posts:
            post_data = {
                "account": post.get('username', ''),
                "platform": post.get('platform', ''),
                "post": post.get('text_preview', '')[:200],
                "primary_url": post.get('url', ''),
                "publication_date": post.get('timestamp', ''),
                "hashtags": [],
                "domain": "",
            }
            all_posts.append(post_data)
    
    input_data = {
        "network_id": "sa_municipal_election_monitor",
        "case_title": "Coordination Detection",
        "source_organization": "ADDO - African Digital Democracy Observatory",
        "geography": ["South Africa"],
        "account_count": len(account_ids),
        "platforms": list(platforms),
        "signal_totals": {
            "coLink": 0, "coText": len(coordination_groups), "nearPosting": 0,
            "domainBurst": 0, "lexicalFlood": 0, "crossPost": 0,
            "personaCue": 0, "plagiarism": 0, "replyTarget": 0, "repostChain": 0
        },
        "manipulation_share": 0.0,
        "shared_manipulation_categories": [],
        "top_domains": [], "top_urls": [], "top_hashtags": [],
        "evidence_posts": all_posts[:3],
        "allowed_techniques": [
            "T0049", "T0049.002", "T0049.003", "T0049.005",
            "T0016", "T0060", "T0119", "T0119.001", "T0119.002",
            "T0097.102", "T0097.202", "T0143.002", "T0143.003",
            "T0149.003", "T0084.002"
        ]
    }
    return input_data


def detect_hate_speech_llm_enhanced(text: str) -> dict:
    """
    Enhanced LLM detection that returns category label and accurate context.
    Specialized for South African multilingual discourse.
    """
    if not text or len(text.strip()) < 20:
        return {
            'is_hate_speech': False, 'confidence': 0.0, 'category': 'neutral',
            'explanation': 'Text too short for analysis', 'severity': 'low'
        }
    
    category_options = """
    - Ethnic Slur
    - Religious Hate
    - Gender-Based Violence/Misogyny
    - Political Incitement
    - Violence/Threats
    - Dehumanization
    - Xenophobia
    - Homophobic/LGBTQ+ Hate
    - Disability Hate
    - Class/Caste Discrimination
    - Conspiracy Theory
    - Misinformation/Disinformation
    - Harassment/Bullying
    - Hate Speech (General)
    - Neutral/Not Hate Speech
    - Other (specify in explanation)
    """
    
    prompt = (
        "You are an elite expert content moderator specialized in South African political discourse, "
        "multilingual sociolinguistics (Zulu, Xhosa, Afrikaans, Sotho, Tswana, Venda, Tsonga, Ndebele, Swati, Pedi, English), "
        "ethnic conflict dynamics, xenophobia, and dangerous speech.\n\n"
        "SYSTEM ROLE & GOAL:\n"
        "Your task is to analyze social media text from South Africa and identify whether it contains hate speech, "
        "ethnic targeting, dehumanization, xenophobia, or dangerous atrocity claims.\n\n"
        f"TEXT TO ANALYZE:\n\"{text}\"\n\n"
        "ALLOWED CATEGORIES (Select the single best match):\n"
        f"{category_options}\n\n"
        "CRITICAL SA DISCOURSE & MODERATION RULES:\n"
        "1. XENOPHOBIA & ANTI-IMMIGRANT SENTIMENT:\n"
        "   - Posts targeting 'kwerekwere', 'makwerekwere', 'amapara', 'foreigners' must NEVER be classified as neutral.\n"
        "   - They are HIGH-RISK INCITEMENT (Severity: HIGH or CRITICAL) because they drive Operation Dudula-style violence.\n\n"
        "2. ETHNIC TARGETING:\n"
        "   - Framing entire ethnic groups (Zulu, Xhosa, Sotho, etc.) as criminals or threats is Ethnic Generalization (Severity: HIGH).\n"
        "   - Terms like 'lekgowa', 'makgowa', 'boer', 'kaffer' carry historical weight from apartheid.\n\n"
        "3. POLITICAL VIOLENCE:\n"
        "   - Posts calling for burning of foreign-owned shops ('shisa amakwerekwere') are CRITICAL incitement.\n"
        "   - Farm attack narratives ('plaasmoord') require careful context analysis.\n\n"
        "4. GENDER-BASED VIOLENCE:\n"
        "   - South Africa has one of the highest GBV rates globally. Posts normalizing violence against women are CRITICAL.\n\n"
        "5. FAIR CRITIQUE vs. HATE SPEECH:\n"
        "   - FAIR CRITIQUE: Criticizing ANC, DA, EFF policies or specific politicians on performance grounds.\n"
        "   - HATE SPEECH: Attributing institutional failure to entire racial/ethnic groups.\n\n"
        "RESPONSE FORMAT:\n"
        "You must return your output strictly in JSON format. Do not add markdown outside the JSON block.\n\n"
        "{\n"
        '    "category": "Chosen category from list",\n'
        '    "explanation": "Detailed step-by-step reasoning grounded in the specialized rules above",\n'
        '    "severity": "low|medium|high|critical",\n'
        '    "confidence": 0.0-1.0,\n'
        '    "is_hate_speech": true|false,\n'
        '    "target_group": "Name of targeted group or none",\n'
        '    "harmful_elements": ["list", "of", "specific", "harmful", "phrases/elements"]\n'
        "}"
    )
    
    try:
        response = safe_llm_call(prompt, max_tokens=600)
        if not response:
            return {
                'is_hate_speech': False, 'confidence': 0.0, 'category': 'error',
                'explanation': 'LLM failed to respond', 'severity': 'low'
            }
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group(0))
            return {
                'is_hate_speech': result.get('is_hate_speech', False),
                'confidence': float(result.get('confidence', 0.0)),
                'category': result.get('category', 'uncategorized'),
                'explanation': result.get('explanation', ''),
                'severity': result.get('severity', 'low'),
                'target_group': result.get('target_group', ''),
                'harmful_elements': result.get('harmful_elements', [])
            }
        else:
            return {
                'is_hate_speech': 'true' in response.lower() and 'is_hate_speech": true' in response.lower(),
                'confidence': 0.5, 'category': 'uncategorized',
                'explanation': response, 'severity': 'medium'
            }
    except Exception as e:
        logger.error(f"Enhanced LLM detection failed: {e}")
        return {
            'is_hate_speech': False, 'confidence': 0.0, 'category': 'error',
            'explanation': f'Analysis failed: {str(e)}', 'severity': 'low'
        }


def clean_username(raw_name):
    if not raw_name or pd.isna(raw_name):
        return "Unknown"
    name = str(raw_name).strip()
    name = re.sub(r'\s+(name|source|nan|none)$', '', name, flags=re.IGNORECASE).strip()
    name = re.sub(r'^[@\s]+|[\s@]+$', '', name)
    if not name or name.lower() in ['nan', 'none', '-', '', 'unknown']:
        return "Unknown"
    return name


def normalize_platform(platform_name):
    """Normalize platform names to consistent format"""
    if not platform_name:
        return "Unknown"
    p = str(platform_name).lower().strip()
    if p in ['x', 'twitter', 't.co', 'x.com', 'twitter source', 'twitter.com']:
        return 'X'
    elif p in ['facebook', 'fb.watch', 'facebook.com', 'fb']:
        return 'Facebook'
    elif p in ['telegram', 't.me', 'tg']:
        return 'Telegram'
    elif p in ['tiktok', 'tik tok', 'tik-tok']:
        return 'TikTok'
    elif p in ['media', 'news', 'news/media', 'civicsignal']:
        return 'Media'
    elif p in ['youtube', 'youtu.be', 'yt']:
        return 'YouTube'
    elif p in ['instagram', 'insta', 'ig']:
        return 'Instagram'
    return platform_name.title()


def get_queryset(self):
    qs = ProcessedPost.objects.all()
    start = self.request.GET.get('start_date')
    end = self.request.GET.get('end_date')
    if start and end:
        qs = qs.filter(timestamp_share__range=[start, end])
    else:
        from django.utils import timezone
        qs = qs.filter(timestamp_share__gte=timezone.now() - timezone.timedelta(days=30))
    return qs.order_by('-timestamp_share')


# Constants
MAX_UPLOAD_SIZE_BYTES = 500 * 1024 * 1024  # 500 MB


def dynamically_map_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Scans an arbitrary DataFrame's columns and maps custom/generic headers
    to our standardized schema names.
    """
    mapping = {}
    aliases = {
        'account_id': ['account_id', 'account', 'username', 'user', 'author', 'handle', 'sender', 'screen_name'],
        'original_text': ['original_text', 'text', 'content', 'body', 'tweet', 'post', 'message', 'caption', 'description', 'comment'],
        'url': ['url', 'link', 'uri', 'href', 'permalink', 'post_url'],
        'timestamp_share': ['timestamp_share', 'timestamp', 'date', 'created_at', 'published', 'time', 'datetime', 'published_date'],
        'platform': ['platform', 'source', 'channel', 'network', 'social_network']
    }
    for target_field, alias_list in aliases.items():
        for col_name in df.columns:
            clean_col = str(col_name).lower().strip()
            if clean_col in alias_list:
                mapping[col_name] = target_field
                break
    if mapping:
        df = df.rename(columns=mapping)
    if 'account_id' not in df.columns:
        df['account_id'] = 'Unknown'
    if 'original_text' not in df.columns:
        string_cols = df.select_dtypes(include=['object', 'string']).columns
        if len(string_cols) > 0:
            fallback_col = max(string_cols, key=lambda c: df[c].astype(str).str.len().mean())
            df = df.rename(columns={fallback_col: 'original_text'})
        else:
            df['original_text'] = ''
    if 'url' not in df.columns:
        df['url'] = ''
    if 'timestamp_share' not in df.columns:
        df['timestamp_share'] = timezone.now()
    if 'platform' not in df.columns:
        df['platform'] = 'Generic CSV'
    return df


@never_cache
def dashboard_view(request):
    """Main Dashboard View with Sidebar Upload and Stats Reporting"""
    if request.method == 'POST' and request.FILES.getlist('files'):
        platform_type = request.POST.get('platform', 'generic')
        uploaded_files = request.FILES.getlist('files')
        stats = {'files_count': 0, 'total_rows': 0, 'saved': 0, 'duplicates': 0}
        for f in uploaded_files:
            if f.size > MAX_UPLOAD_SIZE_BYTES:
                messages.error(request, f"The file '{f.name}' exceeds our 500MB limit.")
                return redirect(request.POST.get('next', 'home'))
            try:
                if platform_type == 'brandwatch':
                    df = pd.read_csv(f, sep=',', low_memory=False, skiprows=6, on_bad_lines='skip')
                else:
                    df = load_data_robustly(f)
                stats['total_rows'] += len(df)
                known_platforms = ['meltwater', 'tiktok', 'openmeasure', 'brandwatch', 'civicsignals']
                if platform_type in known_platforms:
                    if platform_type == 'meltwater':
                        processed_df = combine_social_media_data(meltwater_df=df)
                    elif platform_type == 'tiktok':
                        processed_df = combine_social_media_data(tiktok_df=df)
                    elif platform_type == 'openmeasure':
                        processed_df = combine_social_media_data(openmeasures_df=df)
                    elif platform_type == 'brandwatch':
                        processed_df = combine_social_media_data(brandwatch_df=df)
                    else:
                        processed_df = combine_social_media_data(civicsignals_df=df)
                else:
                    processed_df = dynamically_map_columns(df)
                for _, row in processed_df.iterrows():
                    cid = row.get('content_id')
                    if not cid:
                        text_hash = hashlib.md5(str(row.get('original_text', '')).encode('utf-8')).hexdigest()
                        cid = f"gen_{text_hash[:16]}"
                    if ProcessedPost.objects.filter(content_id=cid).exists():
                        stats['duplicates'] += 1
                        continue
                    source_name = row.get('source_dataset', platform_type)
                    source_obj, _ = DataSource.objects.get_or_create(name=source_name)
                    ProcessedPost.objects.create(
                        account_id=str(row.get('account_id', 'Unknown'))[:100],
                        content_id=cid,
                        original_text=str(row.get('original_text', '')),
                        url=row.get('url') or row.get('URL') or row.get('link') or row.get('Link') or '',
                        platform=row.get('platform', platform_type.title()),
                        timestamp_share=parse_timestamp_robust(row.get('timestamp_share')),
                        source_dataset=source_obj,
                        is_election_related=is_election_related(str(row.get('original_text', '')))
                    )
                    stats['saved'] += 1
                stats['files_count'] += 1
            except Exception as e:
                logger.error(f"Upload error processing '{f.name}': {e}")
                messages.error(request, f"Error processing '{f.name}'. Ensure it's a valid CSV format.")
        detail_msg = (
            f"<strong>Data Upload Details:</strong><br>"
            f"• Source: {platform_type.title()}<br>"
            f"• Files processed: {stats['files_count']}<br>"
            f"• Rows analyzed: {stats['total_rows']}<br>"
            f"• <strong>New unique posts: {stats['saved']}</strong><br>"
            f"• Duplicates ignored: {stats['duplicates']}"
        )
        messages.success(request, detail_msg)
        return redirect(request.POST.get('next', 'home'))
    
    if request.GET.get('refresh'):
        logger.info("Performing hard refresh of election metrics")
    
    all_posts = ProcessedPost.objects.all().order_by('-timestamp_share')
    summaries = get_sa_summaries(all_posts)
    coordination = get_coordination_groups(all_posts)
    context = {
        'tabs': [
            {'name': 'Overview', 'url_name': 'home', 'icon': '📊'},
            {'name': 'Narratives', 'url_name': 'narratives', 'icon': '🗣️'},
            {'name': 'Coordination', 'url_name': 'networks', 'icon': '🕸️'},
        ],
        'active_tab': 'home',
        'summaries': summaries,
        'coordination': coordination,
        'total_posts': all_posts.count(),
    }
    return render(request, 'dashboard.html', context)


# Compile patterns once, reuse them forever
_COMPILED_PATTERNS = {}


def get_cached_pattern(term, language):
    """Get or compile a regex pattern, caching it to prevent memory spikes."""
    cache_key = f"{term}_{language}"
    if cache_key not in _COMPILED_PATTERNS:
        try:
            if language in ["zulu", "xhosa", "sotho", "tswana", "venda", "tsonga", "ndebele", "swati", "pedi", "afrikaans"]:
                pattern = re.escape(term)
            else:
                pattern = r'\b' + re.escape(term) + r'\b'
            _COMPILED_PATTERNS[cache_key] = re.compile(pattern, re.IGNORECASE)
        except re.error:
            _COMPILED_PATTERNS[cache_key] = None
    return _COMPILED_PATTERNS[cache_key]


def scan_text_for_lexicon_terms(text, category_filter=None):
    """
    FAST PATH: Regex matching for lexicon terms across categories.
    Scans ALL posts (including retweets) for accurate aggregate volume counts.
    """
    if not isinstance(text, str) or not text.strip():
        return []
    text_lower = text.lower()
    is_retweet = bool(re.match(r'^\s*rt\s+@\w+', text_lower, re.IGNORECASE))
    matches = []
    lexicon = CONFIG.get("lexicon", {})
    categories_to_check = category_filter if category_filter else lexicon.keys()
    
    sa_extended_terms = [
        'foreign', 'international', 'kwerekwere', 'makwerekwere', 'amapara',
        'operation dudula', 'dudula', 'xenophobia', 'afrophobia',
        'load shedding', 'eskom', 'stage 6', 'stage 8',
        'service delivery', 'municipal', 'iec', 'ward', 'councillor',
        'anc', 'da', 'eff', 'mk party', 'action sa', 'patriotic alliance',
        'zuma', 'ramaphosa', 'maffikeng', 'gauteng', 'kwazulu', 'western cape',
        'farm attack', 'plaasmoord', 'gender-based violence', 'gbv', 'femicide',
    ]
    
    for category in categories_to_check:
        category_terms = lexicon.get(category, {}).copy()
        category_normalized = category.lower().replace(" ", "_").replace("-", "_")
        is_sa_category = any(kw in category_normalized for kw in ["xenophobia", "ethnic", "violence", "dehumanizing"])
        
        if is_sa_category or category not in lexicon:
            for ext_term in sa_extended_terms:
                if ext_term not in category_terms:
                    category_terms[ext_term] = {
                        'severity': 'medium', 'target_entity': 'geopolitical', 'language': 'english'
                    }
        if not category_terms:
            continue
        for term, metadata in category_terms.items():
            term_clean = term.strip()
            if len(term_clean) < 2:
                continue
            pattern = get_cached_pattern(term_clean, metadata.get("language", "english"))
            if pattern and pattern.search(text_lower):
                matches.append({
                    'term': term_clean, 'category': category,
                    'severity': metadata.get('severity', 'medium'),
                    'target_entity': metadata.get('target_entity', ''),
                    'language': metadata.get('language', 'english'),
                    'source': 'Lexicon', 'is_retweet': is_retweet
                })
    return matches


def auto_save_important_llm_terms(llm_terms):
    """Automatically save high-confidence LLM-discovered terms to database."""
    from dashboard.models import LexiconTerm
    saved_count = 0
    for term_data in llm_terms:
        if term_data.get('severity') in ['high', 'critical'] and term_data.get('confidence', 0) >= 0.6:
            detected_language = term_data.get('language', 'Unknown')
            if detected_language:
                detected_language = detected_language.strip().title()
                language_map = {
                    'Zulu': 'zulu', 'Xhosa': 'xhosa', 'Afrikaans': 'afrikaans',
                    'Sotho': 'sotho', 'Tswana': 'tswana', 'Venda': 'venda',
                    'Tsonga': 'tsonga', 'Ndebele': 'ndebele', 'Swati': 'swati',
                    'Pedi': 'pedi', 'English': 'english',
                }
                normalized_language = language_map.get(detected_language, detected_language.lower())
            else:
                normalized_language = 'unknown'
            obj, created = LexiconTerm.objects.get_or_create(
                term=term_data['term'],
                defaults={
                    'category': term_data['category'], 'severity': term_data['severity'],
                    'target_entity': term_data.get('target_entity', ''),
                    'language': normalized_language, 'is_election_related': True, 'is_active': True
                }
            )
            if created:
                saved_count += 1
                logger.info(f"✅ Auto-saved NEW term: '{term_data['term']}' (Category: {term_data['category']}, Lang: {normalized_language})")
            else:
                logger.info(f"ℹ️ Term already exists: '{term_data['term']}'")
    return saved_count


def calculate_risk_score(matches):
    """Calculate risk score based on matched terms"""
    if not matches:
        return {'score': 0, 'level': 'low', 'breakdown': {}, 'term_count': 0}
    scoring = CONFIG.get("risk_scoring", {})
    severity_weights = scoring.get("severity_weights", {'low': 1, 'medium': 2, 'high': 3, 'critical': 4})
    category_weights = scoring.get("category_weights", {})
    thresholds = scoring.get("risk_thresholds", {'low': 3, 'medium': 6, 'high': 10, 'critical': 15})
    total_score = 0
    breakdown = defaultdict(int)
    for match in matches:
        sev = match.get('severity', 'medium')
        cat = match.get('category', 'general')
        weight = severity_weights.get(sev, 2) * category_weights.get(cat, 1.0)
        total_score += weight
        breakdown[cat] += weight
    if total_score >= thresholds.get('critical', 15):
        level = 'critical'
    elif total_score >= thresholds.get('high', 10):
        level = 'high'
    elif total_score >= thresholds.get('medium', 6):
        level = 'medium'
    else:
        level = 'low'
    return {'score': round(total_score, 2), 'level': level, 'breakdown': dict(breakdown), 'term_count': len(matches)}


def assign_virality_tier(n):
    if n >= 500:
        return "Tier 4: Viral Emergency"
    elif n >= 100:
        return "Tier 3: High Spread"
    elif n >= 20:
        return "Tier 2: Moderate"
    else:
        return "Tier 1: Limited"


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
        return _fallback_summary(texts, urls, min_ts, max_ts)
    
    cleaned = re.sub(r'\*\*.*?(Instructions|strict|Output Format).*?\*\*', '', raw_summary, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r'`.*?`', '', cleaned, flags=re.DOTALL)
    cleaned = re.sub(r'###|##|#|\*\*|\*', '', cleaned)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()


def _fallback_summary(texts, urls, min_ts, max_ts):
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


def get_sa_summaries(posts_queryset, max_clusters=15):
    """
    Generates IMI-style structured summaries with SA-specific topic/tone mapping.
    Uses clustering + LLM with robust fallback when model fails.
    """
    from sklearn.cluster import DBSCAN
    from collections import defaultdict, Counter
    import re
    
    all_summaries = []
    post_data = list(posts_queryset.values(
        'id', 'original_text', 'url', 'account_id', 'platform', 'timestamp_share'
    ).filter(original_text__isnull=False).exclude(original_text='').order_by('-timestamp_share')[:2000])
    
    if len(post_data) < 20:
        return all_summaries
    
    texts = [p['original_text'] for p in post_data if p['original_text'] and len(p['original_text'].strip()) > 50]
    urls = [p['url'] for p in post_data if p.get('url') and str(p['url']).startswith('http')]
    
    if len(texts) < 20:
        return all_summaries
    
    try:
        vectorizer = TfidfVectorizer(max_features=3000, stop_words='english', ngram_range=(1, 2), min_df=2)
        X = vectorizer.fit_transform(texts)
        clustering = DBSCAN(eps=0.3, min_samples=2, metric='cosine').fit(X)
        labels = clustering.labels_
        
        cluster_posts = defaultdict(list)
        for idx, label in enumerate(labels):
            if label != -1:
                cluster_posts[label].append(post_data[idx])
        
        top_clusters = sorted(cluster_posts.items(), key=lambda x: len(x[1]), reverse=True)[:max_clusters]
        
        for cluster_id, cluster_data in top_clusters:
            if len(cluster_data) < 3:
                continue
            
            cluster_texts = [p['original_text'] for p in cluster_data if p['original_text']]
            cluster_urls = [p['url'] for p in cluster_data if p.get('url') and str(p['url']).startswith('http')]
            timestamps = [p['timestamp_share'] for p in cluster_data if p.get('timestamp_share')]
            if not timestamps:
                continue
            
            min_ts = min(timestamps)
            max_ts = max(timestamps)
            min_ts_str = min_ts.strftime('%Y-%m-%d') if min_ts else 'N/A'
            max_ts_str = max_ts.strftime('%Y-%m-%d') if max_ts else 'N/A'
            
            sorted_by_time = sorted(cluster_data, key=lambda x: x['timestamp_share'] or datetime.max)
            originators = list(dict.fromkeys([p['account_id'] for p in sorted_by_time[:10] if p.get('account_id')]))[:5]
            originators = [str(o)[:40] for o in originators] if originators else ["Unknown"]
            
            total_reach = len(cluster_data)
            unique_accounts = len(set(p['account_id'] for p in cluster_data if p.get('account_id')))
            platforms = [p['platform'] for p in cluster_data if p.get('platform')]
            platform_counts = Counter(platforms)
            top_platforms = ", ".join([f"{p} ({c})" for p, c in platform_counts.most_common(3)])
            
            # === SA-SPECIFIC TOPIC & TONE MAPPING ===
            all_text_lower = ' '.join(cluster_texts[:10]).lower()
            topic_map = {
                'election process': ['election', 'vote', 'ballot', 'iec', 'results', 'polling', 'tally', 'counting', 'ward', 'councillor', 'municipal'],
                'service delivery': ['service delivery', 'water', 'electricity', 'load shedding', 'roads', 'infrastructure', 'municipal', 'eskom'],
                'corruption': ['corruption', 'corrupt', 'bribe', 'embezzle', 'tender', 'fraud', 'scandal', 'state capture', 'guptas'],
                'xenophobia': ['xenophobia', 'foreign', 'immigrant', 'kwerekwere', 'operation dudula', 'amapara', 'makwerekwere'],
                'ethnic dynamics': ['zulu', 'xhosa', 'sotho', 'tswana', 'venda', 'tsonga', 'ndebele', 'swati', 'pedi', 'afrikaans', 'afrikaner', 'ethnic'],
                'political actors': ['anc', 'da', 'eff', 'mk party', 'action sa', 'patriotic alliance', 'iff', 'zuma', 'ramaphosa', 'maffikeng', 'government'],
                'security concerns': ['crime', 'violence', 'attack', 'murder', 'farm attack', 'plaasmoord', 'gender-based violence', 'gbv', 'femicide', 'security'],
                'economic issues': ['economy', 'unemployment', 'poverty', 'inequality', 'land reform', 'expropriation', 'bbeee', 'bbb'],
                'information environment': ['media', 'social media', 'disinformation', 'fake news', 'platform', 'censorship'],
            }
            detected_topics = [t for t, kws in topic_map.items() if any(kw in all_text_lower for kw in kws)]
            
            tone_map = {
                'accusatory and urgent': ['accuse', 'blame', 'demand', 'condemn', 'outrage', 'crisis', 'emergency', 'immediate'],
                'informational and analytical': ['report', 'analysis', 'data', 'found', 'according', 'study', 'research', 'evidence'],
                'emotional and concerned': ['worried', 'concerned', 'fear', 'alarming', 'devastating', 'tragic', 'heartbreaking'],
                'critical and skeptical': ['criticize', 'fail', 'question', 'doubt', 'skeptical', 'misleading', 'manipulated'],
                'hopeful and constructive': ['hope', 'solution', 'progress', 'unity', 'peace', 'dialogue', 'reform'],
            }
            detected_tone = 'Mixed tones with informational and analytical content'
            for tone, kws in tone_map.items():
                if any(kw in all_text_lower for kw in kws):
                    detected_tone = tone.title()
                    break
            
            joined = "\n".join([f"[{i+1}] {t}" for i, t in enumerate(cluster_texts[:50]) if t and len(t.strip()) > 10])
            url_context = "\nRelevant post links:\n" + "\n".join(cluster_urls[:5]) if cluster_urls else ""
            
            prompt = f"""
Generate a structured IMI intelligence report on online narratives related to the South Africa 2026 Municipal Elections.
Focus on pre and post-election tensions and emerging narratives including:
- Service delivery failures, corruption, load shedding
- Xenophobia, ethnic tensions, political party conflicts
- Gender-based violence, farm attacks, IEC integrity
- Economic inequality, land reform, municipal governance

Strict Instructions:
Only report claims explicitly present in the provided posts.
Identify originators and note amplification.
Do NOT invent, cut out, assume, extrapolate, or fact-check claims.

Output Format (plain text, NO markdown):
Narrative Title: [Short, descriptive title]
Core Claim(s):
[Claim 1]
[Claim 2]
[Claim 3]
Originator(s): [Account IDs or "Unknown"]
Amplification: [Total posts] posts across [X] unique accounts
First Detected: {min_ts_str}
Last Updated: {max_ts_str}
Language/Tone Observed: {detected_tone}
Primary Topics: {', '.join(detected_topics[:3]) if detected_topics else 'SA electoral discourse'}
Sample Quotes (max 3):
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
                logger.warning(f"LLM summary failed for cluster {cluster_id}: {e}")
                raw_summary = ""
            
            cleaned_summary = re.sub(r'\*\*.*?(Instructions|strict|Output Format).*?\*\*', '', raw_summary, flags=re.IGNORECASE | re.DOTALL)
            cleaned_summary = re.sub(r'```.*?```', '', cleaned_summary, flags=re.DOTALL)
            cleaned_summary = re.sub(r'###|##|#|\*\*|\*', '', cleaned_summary)
            cleaned_summary = re.sub(r'\n{3,}', '\n\n', cleaned_summary).strip()
            
            if not cleaned_summary or len(cleaned_summary) < 50:
                specific_claims = []
                seen_claims = set()
                claim_keywords = ['alleged', 'claims', 'reported', 'found', 'accused', 'stated', 'manipulated', 'rigged', 'violence', 'attack', 'killed', 'hate', 'disinformation', 'fake', 'biased', 'unfair', 'targeting', 'condemn', 'corrupt', 'load shedding', 'xenophobia', 'kwerekwere']
                for text in cluster_texts[:30]:
                    sentences = [s.strip() for s in text.split('.') if len(s.strip()) > 40]
                    for sentence in sentences:
                        if sentence.lower().startswith(('http', 'read more', 'via @', 'source:', 'follow', 'rt ')):
                            continue
                        if any(kw in sentence.lower() for kw in claim_keywords):
                            claim_key = sentence[:80].lower()
                            if claim_key not in seen_claims:
                                seen_claims.add(claim_key)
                                specific_claims.append(sentence[:250] + ('...' if len(sentence) > 250 else ''))
                                if len(specific_claims) >= 3:
                                    break
                    if len(specific_claims) >= 3:
                        break
                
                claims_text = '\n'.join([f"- {c}" for c in specific_claims[:3]]) if specific_claims else \
                    "- Posts discuss electoral processes and political developments in South Africa\n" \
                    "- Content includes references to specific incidents, actors, or regional dynamics\n" \
                    "- Themes include governance, service delivery, security, and information dynamics"
                
                sample_quotes = []
                for text in cluster_texts[:3]:
                    clean = text[:150].replace("'", "'").strip()
                    if clean and len(clean) > 20:
                        sample_quotes.append(f"'{clean}...'")
                
                cleaned_summary = f"""Narrative Title: Cluster discussing {', '.join(detected_topics[:2]) if detected_topics else 'SA election-related topics'}
Core Claim(s):
{claims_text}
Originator(s): {', '.join(originators) if originators != ["Unknown"] else "Unknown"}
Amplification: {total_reach} posts across {unique_accounts} unique accounts
First Detected: {min_ts_str}
Last Updated: {max_ts_str}
Language/Tone Observed: {detected_tone}
Primary Topics: {', '.join(detected_topics[:3]) if detected_topics else 'SA electoral discourse'}
Sample Quotes:
{chr(10).join(sample_quotes[:3]) if sample_quotes else "1. '[No substantial quotes available]'" }"""
            
            summary_lower = cleaned_summary.lower()
            noise_indicators = ["no relevant claims", "no explicit claims", "insufficient data", "not applicable", "unable to determine"]
            if any(ind in summary_lower for ind in noise_indicators):
                continue
            
            virality = assign_virality_tier(total_reach)
            title_match = re.search(r'Narrative Title:\s*(.*?)(?:\n|Core Claim)', cleaned_summary, re.IGNORECASE | re.DOTALL)
            if title_match:
                narrative_title = title_match.group(1).strip()
            else:
                first_line = cleaned_summary.split('\n')[0].strip()
                narrative_title = first_line if first_line and len(first_line) < 100 else f"Cluster #{int(cluster_id) + 1}"
            
            narrative_title = re.sub(r'\s*Core Claim.*$', '', narrative_title, flags=re.IGNORECASE).strip()
            all_summaries.append({
                'cluster_id': int(cluster_id) + 1,
                'Narrative_Title': narrative_title,
                'Context': cleaned_summary,
                'Originators': ", ".join(originators),
                'Amplifiers_Count': unique_accounts,
                'Total_Reach': total_reach,
                'Emerging_Virality': virality,
                'Top_Platforms': top_platforms,
                'Min_TS': min_ts_str,
                'Max_TS': max_ts_str,
                'Posts_Data': cluster_data,
                'Platform_Diversity': len(platform_counts),
                'Detected_Topics': detected_topics,
                'Detected_Tone': detected_tone,
            })
        
        all_summaries.sort(key=lambda x: x['Total_Reach'], reverse=True)
    except Exception as e:
        logger.error(f"Narrative clustering failed: {e}")
        import traceback
        traceback.print_exc()
    
    return all_summaries[:max_clusters]


def extract_narrative_description(summary_text, sample_posts):
    """Generate a specific, meaningful 1-sentence narrative description from actual cluster posts"""
    if not sample_posts:
        return "Analyzing narrative content..."
    
    all_text = ' '.join([p for p in sample_posts if p and isinstance(p, str)]).lower()
    
    topic_keywords = {
        'election fraud': ['rigged', 'fraud', 'stolen', 'manipulated', 'fake results', 'cheating', 'ballot', 'marked cards', 'vote cards', 'iec'],
        'voter intimidation': ['intimidation', 'threat', 'forced', 'coerced', 'violence', 'fear', 'suppress', 'arrest'],
        'ethnic tension': ['zulu', 'xhosa', 'sotho', 'tswana', 'venda', 'tsonga', 'ndebele', 'swati', 'pedi', 'afrikaans', 'ethnic', 'tribal'],
        'political violence': ['kill', 'attack', 'war', 'conflict', 'militia', 'armed', 'bloodshed', 'massacre', 'farm attack'],
        'international observation': ['observer', 'international', 'au', 'un', 'monitor', 'transparency', 'legitimate', 'credible'],
        'government criticism': ['government', 'authorities', 'regime', 'corrupt', 'failed', 'oppression', 'tyranny', 'anc', 'da', 'eff'],
        'opposition support': ['opposition', 'protest', 'resistance', 'freedom', 'democracy', 'rights', 'liberation', 'mk party', 'action sa'],
        'media manipulation': ['propaganda', 'fake news', 'disinformation', 'censorship', 'biased media', 'state media'],
        'humanitarian crisis': ['displaced', 'refugee', 'hunger', 'famine', 'aid', 'crisis', 'suffering', 'load shedding', 'water crisis'],
        'youth engagement': ['youth', 'young', 'students', 'next generation', 'future', 'university'],
        'xenophobia': ['kwerekwere', 'makwerekwere', 'amapara', 'operation dudula', 'foreign', 'immigrant', 'xenophobia'],
        'gender-based violence': ['gbv', 'gender-based violence', 'femicide', 'rape', 'domestic violence', 'women'],
        'service delivery': ['service delivery', 'water', 'electricity', 'load shedding', 'roads', 'municipal', 'eskom'],
        'corruption': ['corruption', 'corrupt', 'bribe', 'tender', 'fraud', 'state capture', 'guptas', 'tenderpreneur'],
    }
    
    topic_scores = {}
    for topic, keywords in topic_keywords.items():
        score = sum(1 for kw in keywords if kw in all_text)
        if score > 0:
            topic_scores[topic] = score
    
    if topic_scores:
        top_topics = sorted(topic_scores.items(), key=lambda x: x[1], reverse=True)[:3]
        topic_list = [t[0] for t in top_topics]
        if len(topic_list) == 1:
            return f"Posts discussing {topic_list[0]}."
        elif len(topic_list) == 2:
            return f"Posts discussing {topic_list[0]} and {topic_list[1]}."
        else:
            return f"Posts discussing {topic_list[0]}, {topic_list[1]}, and {topic_list[2]}."
    
    best_post = None
    for post in sample_posts:
        if post and isinstance(post, str) and len(post.strip()) > 50:
            best_post = post
            break
    if best_post:
        clean = re.sub(r'http\S+|@\w+|#\w+', '', best_post).strip()
        sentences = [s.strip() for s in clean.split('.') if len(s.strip()) > 30]
        if sentences:
            return sentences[0][:200] + ('...' if len(sentences[0]) > 200 else '')
        return clean[:200] + ('...' if len(clean) > 200 else '')
    return "Analyzing narrative content from posts..."


def detect_ttps_with_gemma(coordination_groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """DISABLED: Gemma TTP model removed. Using rule-based + LLM only."""
    logger.info("Gemma TTP detection skipped (model removed). Using rule-based + LLM only.")
    return []


def _convert_techniques_to_ttp_format(techniques: List[Dict]) -> List[Dict]:
    """Convert Gemma-format techniques to view-compatible format"""
    ttps = []
    for technique in techniques:
        ttp_data = {
            'name': technique.get('technique_id', ''),
            'description': technique.get('description', ''),
            'severity': _get_ttp_severity(technique.get('technique_id', '')),
            'evidence': f"Detected via Gemma model. {technique.get('evidence', '')}",
            'confidence': technique.get('confidence', 0.8),
            'model_source': 'gemma_finetuned'
        }
        ttps.append(ttp_data)
    return ttps


def _get_ttp_severity(technique_id: str) -> str:
    """Map technique IDs to severity levels"""
    high_severity = ['T0049', 'T0049.002', 'T0049.003', 'T0049.005']
    medium_severity = ['T0119', 'T0119.001', 'T0119.002', 'T0097.102', 'T0097.202']
    if technique_id in high_severity:
        return 'High'
    elif technique_id in medium_severity:
        return 'Medium'
    else:
        return 'Low'


def is_likely_normal_news(text):
    """Check if text appears to be normal news rather than coordinated manipulation"""
    if not text or len(text.strip()) < 20:
        return False
    text_lower = text.lower()
    news_indicators = [
        'ambassador', 'briefing', 'press', 'spokesperson', 'ministry',
        'official', 'statement', 'announced', 'reported', 'according to',
        'development', 'progress', 'achievement', 'inauguration', 'ceremony',
        'award', 'recognition', 'honor', 'appointed', 'champion',
        'expo', 'exhibition', 'airline', 'flight', 'service',
        'diplomatic', 'bilateral', 'cooperation', 'partnership', 'agreement',
        'current affairs', 'news', 'media', 'journalist', 'reporter',
        'south africa', 'mzansi', 'sa news',
    ]
    news_score = sum(1 for indicator in news_indicators if indicator in text_lower)
    news_domains = ['bbc.com', 'reuters.com', 'aljazeera.com', 'nytimes.com', 'washingtonpost.com', 'news24.com', 'iol.co.za', 'timeslive.co.za']
    has_news_url = any(domain in text_lower for domain in news_domains)
    return news_score >= 3 or has_news_url


def detect_llm_ttps(coordination_groups, posts):
    """Use LLM to detect additional TTPs - HIGHLY CONSERVATIVE VERSION"""
    if not coordination_groups or len(coordination_groups) < 3:
        return []
    
    normal_news_count = sum(1 for g in coordination_groups if is_likely_normal_news(g.get('text_sample', '')))
    if normal_news_count > len(coordination_groups) * 0.5:
        logger.info(f"Skipping TTP detection: {normal_news_count}/{len(coordination_groups)} groups appear to be normal news")
        return []
    
    data_summary = {
        "total_groups": len(coordination_groups),
        "total_accounts": sum(g.get('account_count', 0) for g in coordination_groups),
        "sample_texts": [g.get('text_sample', '')[:200] for g in coordination_groups[:5]],
        "platforms": list(set(p for g in coordination_groups for p in g.get('platforms', []))),
        "has_urls": any(len(g.get('unique_urls', [])) > 0 for g in coordination_groups),
        "has_hashtags": any('#' in g.get('text_sample', '') for g in coordination_groups),
        "avg_posts_per_group": sum(g.get('post_count', 0) for g in coordination_groups) / len(coordination_groups)
    }
    
    prompt = f"""You are an expert DISARM framework analyst detecting coordinated inauthentic behavior in South African social media.
Data Summary:
{json.dumps(data_summary, indent=2)}
CRITICAL INSTRUCTIONS:
ONLY detect TTPs if there is UNAMBIGUOUS evidence of coordinated manipulation.
DO NOT flag normal news sharing, legitimate political discourse, or campaign messaging.
ONLY flag if you see:
- Identical text posted by 5+ accounts within hours
- Bot-like accounts (generic names like "user123", "news_bot")
- Coordinated hashtag campaigns with no organic engagement
- Cross-platform amplification of the same manipulative content
- Temporal coordination (posts within minutes of each other)
KNOWN DISARM TECHNIQUES (only use these IDs):
T0049: Coordinated Inauthentic Behavior
T0049.002: Controlled Profiles
T0049.003: Bot Networks
T0060: Multi-Platform Manipulation
T0119: Rapid Response
T0143: Hashtag Hijacking
OUTPUT FORMAT:
Return a JSON array of detected TTPs. Each object must have:
"technique_id": One of the IDs listed above
"name": Human-readable name
"description": Brief explanation of how it applies
"severity": "High", "Medium", or "Low"
"confidence": 0.0 to 1.0
"evidence": Specific evidence from the data
If no clear manipulation is detected, return an empty array: []
EXAMPLE VALID RESPONSE:
[
{{
 "technique_id": "T0049.003",
 "name": "Bot Network Amplification",
 "description": "Multiple accounts with generic names posting identical content",
 "severity": "High",
 "confidence": 0.85,
 "evidence": "5 accounts with names like 'user123' posted same text within 2 hours"
}}
]
Return ONLY the JSON array, no other text."""
    
    try:
        response = safe_llm_call(prompt, max_tokens=1024)
        if not response:
            return []
        json_match = re.search(r'\[.*\]', response, re.DOTALL)
        if json_match:
            ttps = json.loads(json_match.group(0))
            valid_ttps = []
            valid_technique_ids = ['T0049', 'T0049.002', 'T0049.003', 'T0060', 'T0119', 'T0143']
            for ttp in ttps:
                if not isinstance(ttp, dict):
                    continue
                technique_id = ttp.get('technique_id', '')
                if technique_id not in valid_technique_ids:
                    logger.warning(f"Skipping unknown technique ID: {technique_id}")
                    continue
                confidence = ttp.get('confidence', 0)
                if confidence < 0.6:
                    logger.info(f"Skipping low-confidence TTP: {technique_id} (confidence: {confidence})")
                    continue
                evidence = ttp.get('evidence', '')
                if not evidence or len(evidence) < 10:
                    logger.warning(f"Skipping TTP without evidence: {technique_id}")
                    continue
                valid_ttps.append({
                    'name': ttp.get('name', technique_id),
                    'description': ttp.get('description', ''),
                    'severity': ttp.get('severity', 'Medium'),
                    'evidence': evidence,
                    'confidence': confidence,
                    'source': 'LLM_DISARM',
                    'technique_id': technique_id
                })
            if valid_ttps:
                logger.info(f"LLM detected {len(valid_ttps)} valid TTPs")
            else:
                logger.info("LLM found no valid TTPs (all filtered out)")
            return valid_ttps
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse LLM TTP response: {e}")
    except Exception as e:
        logger.warning(f"LLM TTP detection failed: {e}")
    return []


def _make_post_dict(post: dict, reason: str) -> dict:
    """Normalise a raw post dict into the shape the template expects."""
    return {
        "username": post.get("username", "Unknown"),
        "text_preview": post.get("text_preview", ""),
        "platform": post.get("platform", ""),
        "timestamp": post.get("timestamp", ""),
        "url": post.get("url"),
        "ttp_reason": reason,
    }


def _parse_ts(ts_str: str):
    """Parse a 'YYYY-MM-DD HH:MM' string; returns datetime.max on failure."""
    try:
        return datetime.strptime(ts_str, "%Y-%m-%d %H:%M")
    except Exception:
        return datetime.max


def _posts_within_window(posts: list, window_minutes: int = 60) -> list:
    """Return pairs of posts whose timestamps are within window_minutes of each other."""
    pairs = []
    timed = []
    for p in posts:
        ts = p.get("timestamp", "")
        if ts and ts != "N/A":
            timed.append((p, _parse_ts(ts)))
    timed.sort(key=lambda x: x[1])
    for i in range(len(timed) - 1):
        diff = (timed[i + 1][1] - timed[i][1]).total_seconds() / 60
        if 0 <= diff <= window_minutes:
            pairs.append([timed[i][0], timed[i + 1][0]])
    return pairs


def _post_has_weaponized(text):
    """Check full text against the module-level WEAPONIZED_KEYWORDS list."""
    text_lower = text.lower()
    for kw in WEAPONIZED_KEYWORDS:
        if re.search(r'\b' + re.escape(kw) + r'\b', text_lower):
            return True
    return False


def _make_evidence_post(post, reason):
    return {
        'username': post.get('username', 'Unknown'),
        'text_preview': post.get('text_preview', post.get('full_text', '')[:300]),
        'platform': post.get('platform', ''),
        'timestamp': post.get('timestamp', ''),
        'url': post.get('url'),
        'ttp_reason': reason,
    }


def _normalize_platform(raw):
    """Collapse platform-name variants into one canonical label."""
    if not raw:
        return ''
    p = str(raw).strip().lower()
    if p in ('x', 'x.com', 'twitter', 'twitter.com', 'twitter/x'):
        return 'X'
    if p in ('facebook', 'fb', 'facebook.com'):
        return 'Facebook'
    if p in ('instagram', 'ig', 'instagram.com'):
        return 'Instagram'
    if p in ('tiktok', 'tiktok.com'):
        return 'TikTok'
    return str(raw).strip().title()


def _prioritize_unused_groups(candidate_groups, used_ids):
    """Stable-sort candidate groups so ones whose evidence hasn't already been shown come first."""
    return sorted(candidate_groups, key=lambda g: g.get('id') in used_ids)


def analyze_ttps(coordination_groups, posts):
    """Analyze Tactics, Techniques, and Procedures. Returns 12 TTPs with specific 'example_posts'."""
    ttps = []
    if not coordination_groups:
        return ttps
    
    def make_evidence(post, reason):
        return {
            'username': post.get('username', 'Unknown'),
            'text_preview': post.get('text_preview', post.get('full_text', '')[:300]),
            'platform': post.get('platform', ''),
            'timestamp': post.get('timestamp', ''),
            'url': post.get('url'),
            'ttp_reason': reason,
        }
    
    # TTP 1: Coordinated Inauthentic Behavior (CIB)
    cib_groups = [g for g in coordination_groups if g.get('account_count', 0) >= 5]
    if cib_groups:
        evidence = []
        for g in cib_groups[:2]:
            for p in g.get('sample_posts_with_urls', [])[:2]:
                evidence.append(make_evidence(p, f"One of {g.get('account_count')} accounts sharing near-identical content."))
        ttps.append({
            'name': 'Coordinated Inauthentic Behavior (CIB)',
            'description': f'{len(cib_groups)} group(s) with 5+ distinct accounts posting near-identical content.',
            'severity': 'High',
            'evidence': f'{sum(g.get("post_count", 0) for g in cib_groups)} posts across {sum(g.get("account_count", 0) for g in cib_groups)} accounts.',
            'example_posts': evidence[:4]
        })
    
    # TTP 2: Cross-Platform Amplification
    cross_groups = [g for g in coordination_groups if len(g.get('platforms', [])) > 1]
    if cross_groups:
        all_plats = set()
        for g in cross_groups:
            all_plats.update(g.get('platforms', []))
        evidence = []
        for g in cross_groups:
            for p in g.get('sample_posts_with_urls', []):
                if p.get('platform') and len(evidence) < 4:
                    evidence.append(make_evidence(p, f"Posted on {p.get('platform')} as part of cross-platform campaign."))
        ttps.append({
            'name': 'Cross-Platform Amplification',
            'description': f'{len(cross_groups)} group(s) spreading identical content across {len(all_plats)} platforms.',
            'severity': 'Medium',
            'evidence': f"Platforms: {', '.join(sorted(all_plats))}",
            'example_posts': evidence
        })
    
    # TTP 3: Rapid Response / Burst Posting
    burst_groups = [g for g in coordination_groups if g.get('post_count', 0) > 10]
    if burst_groups:
        biggest = max(burst_groups, key=lambda x: x.get('post_count', 0))
        evidence = [make_evidence(p, f"Part of {biggest.get('post_count')}-post burst.") for p in biggest.get('sample_posts_with_urls', [])[:3]]
        ttps.append({
            'name': 'Rapid Response / Burst Posting',
            'description': f'{len(burst_groups)} group(s) with high-volume posting (max: {biggest.get("post_count")} posts).',
            'severity': 'Medium',
            'evidence': f"Content repeated across {sum(g.get('account_count',0) for g in burst_groups)} accounts.",
            'example_posts': evidence
        })
    
    # TTP 4: Hashtag Manipulation
    hashtag_groups = [g for g in coordination_groups if g.get('hashtags')]
    if hashtag_groups:
        all_tags = set()
        for g in hashtag_groups:
            all_tags.update(g.get('hashtags', []))
        evidence = []
        for g in hashtag_groups:
            for p in g.get('sample_posts_with_urls', []):
                if len(evidence) < 4:
                    evidence.append(make_evidence(p, f"Contains coordinated hashtags."))
        ttps.append({
            'name': 'Hashtag Manipulation',
            'description': f'Coordinated use of {len(all_tags)} hashtag(s) across {len(hashtag_groups)} groups.',
            'severity': 'Low',
            'evidence': f"Tags: {', '.join(list(all_tags)[:5])}",
            'example_posts': evidence
        })
    
    # TTP 5: URL Amplification
    url_groups = [g for g in coordination_groups if g.get('unique_urls')]
    if url_groups:
        evidence = []
        for g in url_groups:
            for p in g.get('sample_posts_with_urls', []):
                if p.get('url') and len(evidence) < 4:
                    evidence.append(make_evidence(p, f"Amplifying external URL."))
        ttps.append({
            'name': 'URL Amplification',
            'description': f'{len(url_groups)} group(s) amplifying external URLs.',
            'severity': 'Low',
            'evidence': 'Multiple accounts sharing same external links.',
            'example_posts': evidence
        })
    
    # TTP 6: Narrative Weaponization
    weaponized_keywords = ['genocide', 'kill', 'attack', 'war', 'slur', 'hate', 'ethnic cleansing', 'massacre', 'kwerekwere', 'makwerekwere', 'burn', 'loot', 'riot']
    weap_groups = [g for g in coordination_groups if any(kw in g.get('text_sample', '').lower() for kw in weaponized_keywords)]
    if weap_groups:
        evidence = []
        for g in weap_groups:
            for p in g.get('sample_posts_with_urls', []):
                text = (p.get('text_preview') or '').lower()
                if any(kw in text for kw in weaponized_keywords) and len(evidence) < 4:
                    evidence.append(make_evidence(p, f"Contains weaponized keyword."))
        ttps.append({
            'name': 'Narrative Weaponization',
            'description': f'{len(weap_groups)} group(s) using high-risk weaponized keywords.',
            'severity': 'Critical',
            'evidence': 'Coordinated amplification of inflammatory language.',
            'example_posts': evidence
        })
    
    # TTP 7: Temporal Coordination
    sync_groups = []
    for g in coordination_groups:
        timestamps = []
        for p in g.get('sample_posts_with_urls', []):
            if p.get('timestamp') and p['timestamp'] != 'N/A':
                try:
                    timestamps.append(datetime.strptime(p['timestamp'], '%Y-%m-%d %H:%M'))
                except:
                    pass
        if len(timestamps) >= 2:
            timestamps.sort()
            for i in range(len(timestamps) - 1):
                if (timestamps[i+1] - timestamps[i]).total_seconds() / 60 <= 60:
                    sync_groups.append(g)
                    break
    if sync_groups:
        evidence = [make_evidence(p, f"Posted within 60 mins of other accounts.") for p in sync_groups[0].get('sample_posts_with_urls', [])[:3]]
        ttps.append({
            'name': 'Temporal Coordination (Synchronized Posting)',
            'description': f'{len(sync_groups)} group(s) posted identical content within 1 hour.',
            'severity': 'High',
            'evidence': 'Real-time coordination or scheduling tools suspected.',
            'example_posts': evidence
        })
    
    # TTP 8: Multi-Platform Narrative Seeding
    seed_groups = [g for g in coordination_groups if len(g.get('platforms', [])) >= 2 and g.get('account_count', 0) >= 3]
    if seed_groups:
        evidence = [make_evidence(p, f"Seeding narrative across platforms.") for p in seed_groups[0].get('sample_posts_with_urls', [])[:3]]
        ttps.append({
            'name': 'Multi-Platform Narrative Seeding',
            'description': f'{len(seed_groups)} group(s) seeding narratives across 2+ platforms with 3+ accounts.',
            'severity': 'High',
            'evidence': 'Cross-platform push to maximise reach.',
            'example_posts': evidence
        })
    
    # TTP 9: Bot-like Account Behavior
    bot_groups = [g for g in coordination_groups if g.get('bot_count', 0) > 0 or any(x in acc.lower() for acc in g.get('accounts', []) for x in ['bot', 'auto', 'news_bot'])]
    if bot_groups:
        evidence = [make_evidence(p, f"Posted by bot-like account.") for p in bot_groups[0].get('sample_posts_with_urls', [])[:3]]
        ttps.append({
            'name': 'Bot-like Account Behavior',
            'description': f'{len(bot_groups)} group(s) contain bot-like accounts or automated patterns.',
            'severity': 'Medium',
            'evidence': 'Potential use of automated accounts.',
            'example_posts': evidence
        })
    
    # TTP 10: Account Clustering
    cluster_groups = []
    for g in coordination_groups:
        accounts = g.get('accounts', [])
        numeric = [re.findall(r'\d+', acc) for acc in accounts]
        numeric = [int(p[0]) for p in numeric if p]
        if len(numeric) >= 3:
            diffs = [numeric[i+1] - numeric[i] for i in range(len(numeric)-1)]
            if any(d == 1 for d in diffs[:3]):
                cluster_groups.append(g)
    
    # TTP 11: Content Recycling
    recycle_groups = []
    for g in coordination_groups:
        timestamps = []
        for p in g.get('sample_posts_with_urls', []):
            if p.get('timestamp') and p['timestamp'] != 'N/A':
                try:
                    timestamps.append(datetime.strptime(p['timestamp'], '%Y-%m-%d %H:%M'))
                except:
                    pass
        if len(timestamps) >= 2 and (max(timestamps) - min(timestamps)).days > 7:
            recycle_groups.append(g)
    if recycle_groups:
        evidence = [make_evidence(p, f"Content recycled over extended period.") for p in recycle_groups[0].get('sample_posts_with_urls', [])[:3]]
        ttps.append({
            'name': 'Content Recycling',
            'description': f'{len(recycle_groups)} group(s) reposting identical content over > 7 days.',
            'severity': 'Medium',
            'evidence': 'Long-running recycling keeps narrative alive.',
            'example_posts': evidence
        })
    
    # TTP 12: Amplification Networks
    amp_groups = [g for g in coordination_groups if len(g.get('sample_posts_with_urls', [])) >= 3]
    if amp_groups:
        evidence = [make_evidence(p, f"Part of source-to-amplifier network.") for p in amp_groups[0].get('sample_posts_with_urls', [])[:3]]
        ttps.append({
            'name': 'Amplification Networks',
            'description': f'{len(amp_groups)} group(s) showing clear source-to-amplifier patterns.',
            'severity': 'Medium',
            'evidence': 'Content originates from few sources, amplified by many.',
            'example_posts': evidence
        })
    
    return ttps


def get_top_pairs(coordination_groups):
    """Get top coordinated account pairs"""
    pairs = []
    for group in coordination_groups[:10]:
        accounts = group['accounts']
        if len(accounts) >= 2:
            pairs.append({
                'accounts': f'{accounts[0][:20]}... ↔ {accounts[1][:20]}...',
                'shared_posts': group['post_count'],
                'platforms': group['platforms']
            })
    return pairs[:10]


# === IMPROVED NETWORK & COORDINATION FUNCTIONS ===
def is_primarily_sa_related(text: str) -> bool:
    """Check if post is about South Africa elections - EXCLUDES other countries' elections"""
    if not text or len(text.strip()) < 20:
        return False
    text_lower = text.lower()
    
    # === EXCLUSION LIST: Other countries' election content ===
    other_election_signals = [
        'colorado', 'california', 'texas', 'florida', 'new york', 'georgia',
        'pennsylvania', 'michigan', 'wisconsin', 'arizona', 'nevada',
        'congressional district', 'us election', 'us vote', 'us primary',
        'american election', 'united states election', 'us congress',
        'us house', 'us senate', 'us midterm', 'democratic primary',
        'republican primary', 'us ballot', 'nigeria election', 'kenya election',
    ]
    if any(signal in text_lower for signal in other_election_signals):
        return False
    
    # === POSITIVE SA Signals ===
    native_signals = [
        'south africa', 'sa election', 'mzansi', 'azania', 'rainbow nation',
        'anc', 'da', 'eff', 'mk party', 'action sa', 'patriotic alliance', 'iff',
        'zuma', 'ramaphosa', 'maffikeng', 'tshwane', 'ekurhuleni', 'ethekwini',
        'buffalo city', 'mangaung', 'nelson mandela bay', 'cape town', 'durban',
        'johannesburg', 'pretoria', 'gauteng', 'kwazulu', 'western cape',
        'eastern cape', 'limpopo', 'mpumalanga', 'north west', 'free state',
        'northern cape', 'ward', 'councillor', 'municipal', 'iec',
        'zulu', 'xhosa', 'sotho', 'tswana', 'venda', 'tsonga', 'ndebele',
        'swati', 'pedi', 'afrikaans', 'afrikaner', 'boer', 'boere',
        'kwerekwere', 'makwerekwere', 'amapara', 'operation dudula',
        'load shedding', 'eskom', 'service delivery', 'xenophobia',
        'farm attack', 'plaasmoord', 'gender-based violence', 'gbv',
        'state capture', 'tenderpreneur', 'corruption',
    ]
    if any(signal in text_lower for signal in native_signals):
        return True
    
    # Secondary Geopolitical Context (Southern Africa)
    sa_neighbors = ['lesotho', 'swaziland', 'eswatini', 'mozambique', 'zimbabwe', 'botswana', 'namibia']
    geopolitical_context = ['sadc', 'african union', 'au', 'brics', 'g20']
    has_neighbor = any(neighbor in text_lower for neighbor in sa_neighbors)
    has_context = any(ctx in text_lower for ctx in geopolitical_context)
    if has_neighbor and has_context:
        return True
    
    return False


def get_coordination_groups(posts_queryset, min_accounts=3, max_groups=15, similarity_threshold=0.85, max_posts=5000):
    """COORDINATION GROUP BUILDER (DUAL-SOURCE ARCHITECTURE)"""
    coordination = []
    posts_data = list(
        posts_queryset.exclude(platform__iexact="TikTok")
        .exclude(platform__iexact="Media")
        .exclude(platform__iexact="News")
        .values("id", "account_id", "original_text", "platform", "url", "timestamp_share", "risk_level")
        .order_by("-timestamp_share")[:max_posts]
    )
    if len(posts_data) < min_accounts:
        return []
    
    valid_indices = []
    valid_texts = []
    for i, p in enumerate(posts_data):
        text = p.get("original_text", "")
        if text and len(str(text)) > 20:
            valid_indices.append(i)
            valid_texts.append(str(text))
    if len(valid_texts) < 2:
        return []
    
    vectorizer = TfidfVectorizer(max_features=2000, stop_words="english", ngram_range=(1, 2), min_df=2, max_df=0.9)
    tfidf_matrix = vectorizer.fit_transform(valid_texts)
    chunk_size = 500
    similarity_groups = []
    processed_indices = set()
    
    for chunk_start in range(0, len(valid_texts), chunk_size):
        chunk_end = min(chunk_start + chunk_size, len(valid_texts))
        chunk_matrix = tfidf_matrix[chunk_start:chunk_end]
        chunk_similarities = cosine_similarity(chunk_matrix, tfidf_matrix)
        for local_i, global_i in enumerate(range(chunk_start, chunk_end)):
            if global_i in processed_indices:
                continue
            similar_indices = [global_i]
            above_threshold = np.where(chunk_similarities[local_i] >= similarity_threshold)[0]
            for j in above_threshold:
                if j != global_i and j not in processed_indices:
                    similar_indices.append(int(j))
                    processed_indices.add(int(j))
            processed_indices.add(global_i)
            if len(similar_indices) >= min_accounts:
                group_accounts = set(
                    posts_data[valid_indices[idx]].get("account_id")
                    for idx in similar_indices
                    if posts_data[valid_indices[idx]].get("account_id")
                )
                if len(group_accounts) >= min_accounts:
                    similarity_groups.append(similar_indices)
    
    for group_indices in similarity_groups[:max_groups]:
        group_posts = [posts_data[valid_indices[idx]] for idx in group_indices]
        sorted_group_posts = sorted(group_posts, key=lambda x: x.get("timestamp_share") or datetime.max)
        sources_set = set()
        text_sample_raw = str(sorted_group_posts[0].get("original_text", ""))
        
        for post in sorted_group_posts:
            text_content = str(post.get("original_text", ""))
            rt_match = re.search(r"(?:RT|via)\s+@(\w+)", text_content, re.IGNORECASE)
            if rt_match:
                ext_source = clean_username(rt_match.group(1).strip())
                if ext_source and ext_source != "Unknown":
                    sources_set.add(ext_source)
        
        earliest_db_account = clean_username(sorted_group_posts[0].get("account_id"))
        if earliest_db_account and earliest_db_account != "Unknown":
            sources_set.add(earliest_db_account)
        sources_list = list(sources_set)
        primary_source = sources_list[0] if sources_list else "Unknown"
        
        amplifiers_set = set()
        sources_lower = {s.lower() for s in sources_set}
        for post in sorted_group_posts:
            acct = clean_username(post.get("account_id"))
            if acct and acct != "Unknown" and acct.lower() not in sources_lower:
                amplifiers_set.add(acct)
        amplifiers_list = list(amplifiers_set)
        ordered_accounts_set = list(dict.fromkeys(sources_list + amplifiers_list))
        if len(ordered_accounts_set) < min_accounts:
            continue
        
        real_posts_for_bot_check = [p for p in sorted_group_posts if clean_username(p.get("account_id")).lower() not in sources_lower]
        if not real_posts_for_bot_check:
            real_posts_for_bot_check = sorted_group_posts
        bot_data = identify_bot_accounts(real_posts_for_bot_check)
        bot_accounts = list(bot_data.keys())
        bot_count = len(bot_accounts)
        bot_percentage = (bot_count / len(amplifiers_list) * 100) if amplifiers_list else 0
        coordination_type = determine_coordination_type(real_posts_for_bot_check, bot_count)
        
        sample_posts_with_urls = []
        all_platforms = set()
        all_hashtags = []
        for post in sorted_group_posts[:15]:
            if post.get("platform"):
                all_platforms.add(post["platform"])
            text = str(post.get("original_text", "")).strip()
            found = re.findall(r"#(\w+)", text, re.IGNORECASE)
            all_hashtags.extend([h.lower() for h in found])
            ts = post.get("timestamp_share")
            raw_account = post.get("account_id")
            username_clean = clean_username(raw_account)
            bot_reasons = bot_data.get(raw_account, [])
            sample_posts_with_urls.append({
                "username": username_clean, "platform": post.get("platform", ""),
                "url": post.get("url") if post.get("url") and str(post["url"]).startswith("http") else None,
                "timestamp": ts.strftime("%Y-%m-%d %H:%M") if ts else "N/A",
                "text_preview": text[:150] + "..." if text else "",
                "is_bot": raw_account in bot_accounts,
                "bot_reasons": ", ".join(bot_reasons) if bot_reasons else "",
                "risk_level": post.get("risk_level", "unknown"),
                "is_source": username_clean.lower() in sources_lower,
            })
        
        graph_nodes = []
        graph_links = []
        existing_nodes = set()
        for src in sources_list:
            src_key = str(src).strip()
            if src_key.lower() not in existing_nodes:
                graph_nodes.append({"id": src_key, "label": src_key, "type": "source", "group": "source", "color": "#1e90ff", "size": 26, "shape": "dot", "physics": True})
                existing_nodes.add(src_key.lower())
        for amp in amplifiers_list:
            amp_key = str(amp).strip()
            if amp_key.lower() not in existing_nodes:
                graph_nodes.append({"id": amp_key, "label": amp_key, "type": "amplifier", "group": "amplifier", "color": "#e67e22", "size": 14, "shape": "dot", "physics": True})
                existing_nodes.add(amp_key.lower())
            for src in sources_list:
                s_id = str(src).strip()
                t_id = str(amp_key).strip()
                if s_id.lower() in existing_nodes and t_id.lower() in existing_nodes and s_id.lower() != t_id.lower():
                    graph_links.append({"from": s_id, "to": t_id, "source": s_id, "target": t_id, "length": 80, "arrows": "to", "width": 1.5})
        
        unique_urls = list(set(p["url"] for p in real_posts_for_bot_check if p.get("url") and str(p["url"]).startswith("http")))[:5]
        text_sample = str(text_sample_raw)[:200] if text_sample_raw else "[Similar content]"
        
        coordination.append({
            "id": len(coordination) + 1, "accounts": ordered_accounts_set[:15],
            "account_count": len(ordered_accounts_set), "post_count": len(sorted_group_posts),
            "bot_count": bot_count, "bot_percentage": round(bot_percentage, 1),
            "text_sample": text_sample, "sample_posts_with_urls": sample_posts_with_urls,
            "unique_urls": unique_urls, "platforms": sorted(list(all_platforms)),
            "coordination_type": coordination_type, "similarity_score": f"≥{int(similarity_threshold*100)}%",
            "sub_narrative": extract_sub_narrative(text_sample),
            "hashtags": list(set(all_hashtags))[:10],
            "primary_type": "amplification_network" if bot_percentage >= 50 else "coordination",
            "source_node": primary_source, "sources": sources_list, "amplifiers": amplifiers_list,
            "source_count": len(sources_list), "amplifier_count": len(amplifiers_list),
            "nodes": graph_nodes, "links": graph_links,
        })
    
    coordination.sort(key=lambda x: (-x["bot_percentage"], -x["account_count"]))
    return coordination[:max_groups]


def identify_bot_accounts(posts):
    """Enhanced bot detection with timestamp analysis and behavioral patterns."""
    bot_data = {}
    account_posts = defaultdict(list)
    for post in posts:
        if post.get('account_id'):
            account_posts[post['account_id']].append(post)
    
    for account_id, account_post_list in account_posts.items():
        bot_signals = 0
        signal_reasons = []
        
        if len(account_post_list) >= 5:
            timestamps = [p.get('timestamp_share') for p in account_post_list if p.get('timestamp_share')]
            if len(timestamps) >= 5:
                timestamps.sort()
                time_diffs = []
                for i in range(1, len(timestamps)):
                    diff = (timestamps[i] - timestamps[i-1]).total_seconds() / 60
                    time_diffs.append(diff)
                if time_diffs:
                    avg_interval = sum(time_diffs) / len(time_diffs)
                    variance = sum((x - avg_interval) ** 2 for x in time_diffs) / len(time_diffs)
                    std_dev = variance ** 0.5
                    coefficient_of_variation = std_dev / avg_interval if avg_interval > 0 else 1
                    if coefficient_of_variation < 0.3 and len(account_post_list) >= 10:
                        bot_signals += 3
                        signal_reasons.append(f"Regular posting interval (CV: {coefficient_of_variation:.2f})")
                    if avg_interval < 5:
                        bot_signals += 2
                        signal_reasons.append(f"Very high frequency ({avg_interval:.1f} min avg)")
        
        if len(account_post_list) >= 10:
            hours_active = set()
            for post in account_post_list:
                if post.get('timestamp_share'):
                    hours_active.add(post['timestamp_share'].hour)
            if len(hours_active) >= 20:
                bot_signals += 2
                signal_reasons.append("24/7 activity pattern")
        
        if len(account_post_list) >= 10:
            posts_per_hour = defaultdict(int)
            for post in account_post_list:
                if post.get('timestamp_share'):
                    hour_key = post['timestamp_share'].strftime('%Y-%m-%d %H')
                    posts_per_hour[hour_key] += 1
            max_posts_in_hour = max(posts_per_hour.values()) if posts_per_hour else 0
            if max_posts_in_hour >= 20:
                bot_signals += 2
                signal_reasons.append(f"Burst posting ({max_posts_in_hour} posts/hour)")
            elif max_posts_in_hour >= 10:
                bot_signals += 1
                signal_reasons.append(f"High volume burst ({max_posts_in_hour} posts/hour)")
        
        clean_name = clean_username(account_id).lower()
        strong_bot_patterns = ['bot', 'auto', 'auto_', 'auto.', 'daily_', 'daily.', 'news_bot', 'update_bot']
        if any(pattern in clean_name for pattern in strong_bot_patterns):
            bot_signals += 2
            signal_reasons.append("Bot-like name pattern")
        
        short_posts = sum(1 for p in account_post_list if p.get('original_text') and len(str(p['original_text']).strip()) < 30)
        if short_posts > len(account_post_list) * 0.7:
            bot_signals += 1
            signal_reasons.append(f"Mostly short content ({short_posts}/{len(account_post_list)})")
        
        if len(account_post_list) >= 5:
            content_hashes = [hash(str(p.get('original_text', ''))[:50]) for p in account_post_list if p.get('original_text')]
            unique_content = len(set(content_hashes))
            if unique_content < len(content_hashes) * 0.3:
                bot_signals += 2
                signal_reasons.append(f"High content repetition ({unique_content}/{len(content_hashes)} unique)")
        
        if re.search(r'(2023|2024|2025|2026)', clean_name):
            bot_signals += 0.5
        
        high_risk_posts = sum(1 for p in account_post_list if p.get('risk_level') in ['high', 'critical'])
        if high_risk_posts > 0:
            bot_signals += 0.5
        
        if bot_signals >= 3:
            logger.info(f"BOT DETECTED: {account_id} (score: {bot_signals}) - Reasons: {', '.join(signal_reasons)}")
            bot_data[account_id] = signal_reasons
    
    return bot_data


def determine_coordination_type(posts, bot_count):
    """Determine the type of coordination based on patterns"""
    total_accounts = len(set(p['account_id'] for p in posts if p.get('account_id')))
    bot_percentage = (bot_count / total_accounts * 100) if total_accounts > 0 else 0
    urls = [p['url'] for p in posts if p.get('url')]
    has_url_coordination = len(urls) > 0 and len(set(urls)) < len(urls)
    hashtags = []
    for post in posts:
        if post.get('original_text'):
            found = re.findall(r'#\w+', str(post['original_text']))
            hashtags.extend(found)
    has_hashtag_coordination = len(hashtags) > 0 and len(set(hashtags)) < len(hashtags)
    
    if bot_percentage >= 50:
        return "🤖 Bot Network"
    elif has_url_coordination and has_hashtag_coordination:
        return "🔗 URL + Hashtag Amplification"
    elif has_url_coordination:
        return "🔗 URL Amplification"
    elif has_hashtag_coordination:
        return "#️⃣ Hashtag Coordination"
    elif bot_percentage >= 25:
        return "🤖 Bot-Assisted"
    else:
        return "👥 Human Coordination"


def generate_network_graph_data(posts_queryset, min_connections=2, top_n=50, layout='spring'):
    """OPTIMIZED: Pre-fetches all account data in ONE query."""
    import networkx as nx
    from django.db.models import Count
    
    G = nx.Graph()
    account_roles = {}
    text_groups = posts_queryset.values('original_text').annotate(account_count=Count('account_id', distinct=True)).filter(account_count__gte=min_connections)
    relevant_texts = [g['original_text'] for g in text_groups if g['original_text']]
    if not relevant_texts:
        return {'nodes': [], 'edges': [], 'stats': {'nodes': 0, 'edges': 0}}
    
    all_posts = list(posts_queryset.filter(original_text__in=relevant_texts).order_by('timestamp_share').values('account_id', 'platform', 'url', 'timestamp_share', 'original_text'))
    posts_by_text = defaultdict(list)
    for post in all_posts:
        posts_by_text[post['original_text']].append(post)
    
    account_stats = defaultdict(lambda: {'post_count': 0, 'platforms': set(), 'sample_url': None, 'first_post_time': None})
    for post in all_posts:
        acc = clean_username(post['account_id'])
        if not acc or len(acc) < 2:
            continue
        account_stats[acc]['post_count'] += 1
        if post.get('platform'):
            account_stats[acc]['platforms'].add(post['platform'])
        if not account_stats[acc]['sample_url'] and post.get('url') and str(post['url']).startswith('http'):
            account_stats[acc]['sample_url'] = post['url']
        if not account_stats[acc]['first_post_time'] and post.get('timestamp_share'):
            account_stats[acc]['first_post_time'] = post['timestamp_share']
    
    for group in text_groups:
        text = group['original_text']
        if not is_primarily_sa_related(text):
            continue
        posts_for_text = posts_by_text.get(text, [])
        if not posts_for_text:
            continue
        accounts = []
        for idx, post_data in enumerate(posts_for_text):
            username = clean_username(post_data['account_id'])
            if not username or len(username) < 2 or username.lower() in ['twitter', 'facebook', 'tiktok', 'source']:
                continue
            if idx == 0:
                account_roles[username] = 'source'
            elif username not in account_roles:
                account_roles[username] = 'amplifier'
            accounts.append({'id': username, 'platform': post_data.get('platform', ''), 'sample_url': post_data.get('url') if post_data.get('url') and str(post_data['url']).startswith('http') else None})
        for i in range(len(accounts)):
            for j in range(i+1, len(accounts)):
                u_id, v_id = accounts[i]['id'], accounts[j]['id']
                if u_id == v_id:
                    continue
                if G.has_edge(u_id, v_id):
                    G[u_id][v_id]['weight'] += 1
                else:
                    G.add_edge(u_id, v_id, weight=1, platform1=accounts[i]['platform'], platform2=accounts[j]['platform'], sample_url1=accounts[i]['sample_url'], sample_url2=accounts[j]['sample_url'])
    
    if G.number_of_edges() == 0:
        return {'nodes': [], 'edges': [], 'stats': {'nodes': 0, 'edges': 0}}
    
    nodes_to_keep = [n for n, d in G.degree() if d >= min_connections]
    G = G.subgraph(nodes_to_keep).copy()
    if G.number_of_edges() == 0:
        return {'nodes': [], 'edges': [], 'stats': {'nodes': 0, 'edges': 0}}
    
    top_nodes = sorted(G.degree(), key=lambda x: x[1], reverse=True)[:top_n]
    top_node_names = [n for n, _ in top_nodes]
    G_top = G.subgraph(top_node_names).copy()
    
    if layout == 'circular':
        pos = nx.circular_layout(G_top)
    elif layout == 'kamada_kawai':
        pos = nx.kamada_kawai_layout(G_top)
    else:
        pos = nx.spring_layout(G_top, k=0.6, iterations=50, seed=42)
    
    nodes = []
    for node in G_top.nodes():
        degree = G_top.degree(node)
        stats = account_stats.get(node, {'post_count': 0, 'platforms': set(), 'sample_url': None})
        post_count = stats['post_count']
        platforms = list(stats['platforms'])
        platform = platforms[0] if platforms else 'Unknown'
        sample_url = stats['sample_url']
        node_type = account_roles.get(node, 'source')
        node_color = '#3b82f6' if node_type == 'source' else '#f59e0b'
        nodes.append({'id': node, 'label': node, 'degree': degree, 'post_count': post_count, 'platform': platform, 'url': sample_url, 'sample_url': sample_url, 'x': float(pos[node][0]), 'y': float(pos[node][1]), 'size': max(15, degree * 3), 'color': node_color, 'type': node_type})
    
    edges = []
    for u, v, data in G_top.edges(data=True):
        if u in pos and v in pos:
            edges.append({'source': u, 'target': v, 'weight': data.get('weight', 1), 'source_x': float(pos[u][0]), 'source_y': float(pos[u][1]), 'target_x': float(pos[v][0]), 'target_y': float(pos[v][1]), 'sample_url': data.get('sample_url1') or data.get('sample_url2')})
    
    return {'nodes': nodes, 'edges': edges, 'stats': {'nodes': len(nodes), 'edges': len(edges), 'density': G_top.number_of_edges() / (G_top.number_of_nodes() * (G_top.number_of_nodes() - 1) / 2) if G_top.number_of_nodes() > 1 else 0}}


def generate_network_graph_from_groups(coordination_groups, top_n=50, layout='spring'):
    """Build network graph from coordination groups."""
    G = nx.Graph()
    account_roles = {}
    account_post_counts = {}
    account_platforms = {}
    account_timestamps = {}
    hub_nodes = set()
    rt_pattern = re.compile(r'RT\s+@([A-Za-z0-9_]+)', re.IGNORECASE)
    
    for group_idx, group in enumerate(coordination_groups):
        group_accounts = set(group.get('accounts', []))
        sample_posts = group.get('sample_posts_with_urls', [])
        group_id = group.get('id', f'group_{group_idx}')
        if len(group_accounts) < 2:
            continue
        for post in sample_posts:
            username = post.get('username', '')
            if not username:
                continue
            account_post_counts[username] = account_post_counts.get(username, 0) + 1
            platform = post.get('platform', 'Unknown')
            if platform and platform != 'Unknown':
                account_platforms[username] = platform
            elif username not in account_platforms:
                account_platforms[username] = 'Unknown'
            timestamp_str = post.get('timestamp', '')
            if username and timestamp_str and timestamp_str != 'N/A':
                try:
                    ts = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M')
                    if username not in account_timestamps or ts < account_timestamps[username]:
                        account_timestamps[username] = ts
                except:
                    pass
            text = post.get('text_preview', '') or ''
            rt_match = rt_pattern.match(text.strip())
            if rt_match:
                rt_target = rt_match.group(1).strip()
                if rt_target and rt_target.lower() not in [acc.lower() for acc in group_accounts]:
                    group_accounts.add(rt_target)
                    if rt_target not in account_roles:
                        account_roles[rt_target] = 'source'
        
        group_timestamps = {acc: account_timestamps.get(acc) for acc in group_accounts if account_timestamps.get(acc)}
        if group_timestamps:
            sorted_accounts = sorted(group_timestamps.items(), key=lambda x: x[1])
            source_account = None
            for acc, ts in sorted_accounts:
                is_always_rt = False
                for post in sample_posts:
                    if post.get('username') == acc:
                        text = post.get('text_preview', '') or ''
                        if not rt_pattern.match(text.strip()):
                            source_account = acc
                            break
                if source_account:
                    break
            if not source_account and sorted_accounts:
                source_account = sorted_accounts[0][0]
            if source_account:
                account_roles[source_account] = 'source'
                for acc in group_accounts:
                    if acc != source_account and acc not in account_roles:
                        account_roles[acc] = 'amplifier'
    
    for group in coordination_groups:
        group_accounts = list(group.get('accounts', []))
        if len(group_accounts) < 2:
            continue
        weight = group.get('post_count', 1)
        for i in range(len(group_accounts)):
            for j in range(i + 1, len(group_accounts)):
                u, v = group_accounts[i], group_accounts[j]
                if G.has_edge(u, v):
                    G[u][v]['weight'] += weight
                else:
                    G.add_edge(u, v, weight=1, type='coordination')
    
    if G.number_of_nodes() == 0:
        return {'nodes': [], 'edges': [], 'stats': {'nodes': 0, 'edges': 0, 'density': 0}}
    
    G_final = G.copy()
    if layout == 'circular':
        pos = nx.circular_layout(G_final)
    elif layout == 'kamada_kawai':
        pos = nx.kamada_kawai_layout(G_final)
    else:
        pos = nx.spring_layout(G_final, k=0.7, iterations=100, seed=42, scale=2.0)
    
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    x_range = (x_max - x_min) or 1.0
    y_range = (y_max - y_min) or 1.0
    TARGET = 900.0
    
    def _rescale(x, y):
        nx_ = (x - x_min) / x_range * TARGET - TARGET / 2
        ny_ = (y - y_min) / y_range * TARGET - TARGET / 2
        return nx_, ny_
    pos = {node: _rescale(*p) for node, p in pos.items()}
    
    nodes = []
    for node in G_final.nodes():
        degree = G_final.degree(node)
        node_type = account_roles.get(node, 'amplifier')
        node_color = '#3b82f6' if node_type == 'source' else '#f59e0b'
        post_count = account_post_counts.get(node, 0)
        platform = account_platforms.get(node, 'Unknown')
        base_size = 28 if node_type == 'source' else 18
        size = max(base_size, min(55, base_size + degree * 2))
        nodes.append({'id': node, 'label': node[:20], 'degree': degree, 'post_count': post_count, 'platform': platform, 'x': float(pos[node][0]), 'y': float(pos[node][1]), 'size': size, 'color': node_color, 'type': node_type})
    
    edges = []
    for u, v, data in G_final.edges(data=True):
        if u in pos and v in pos:
            edges.append({'source': u, 'target': v, 'weight': data.get('weight', 1), 'type': data.get('type', 'coordination'), 'source_x': float(pos[u][0]), 'source_y': float(pos[u][1]), 'target_x': float(pos[v][0]), 'target_y': float(pos[v][1])})
    
    n_nodes = G_final.number_of_nodes()
    n_edges = G_final.number_of_edges()
    density = n_edges / (n_nodes * (n_nodes - 1) / 2) if n_nodes > 1 else 0
    source_count = sum(1 for role in account_roles.values() if role == 'source')
    amplifier_count = sum(1 for role in account_roles.values() if role == 'amplifier')
    
    return {'nodes': nodes, 'edges': edges, 'bounds': {'x_min': -TARGET/2, 'x_max': TARGET/2, 'y_min': -TARGET/2, 'y_max': TARGET/2}, 'stats': {'nodes': n_nodes, 'edges': n_edges, 'density': round(density, 4), 'sources': source_count, 'amplifiers': amplifier_count}}


def calculate_sa_relevance(text):
    if not text:
        return 0
    text = str(text).lower()
    sa_entities = ["south africa", "sa election", "municipal", "iec", "anc", "da", "eff", "mk party", "zuma", "ramaphosa", "gauteng", "kwazulu", "western cape", "tshwane", "ekurhuleni", "ethekwini"]
    matches = sum(text.count(entity) for entity in sa_entities)
    unique_matches = sum(1 for entity in sa_entities if entity in text)
    return (matches * 0.4 + unique_matches * 0.6)


def _get_platform_color(platform):
    """Get color hex code for platform"""
    colors = {
        'X': '#1DA1F2', 'Twitter': '#1DA1F2', 'Facebook': '#1877F2',
        'TikTok': '#000000', 'Telegram': '#0088cc', 'Media': '#6B7280',
        'News': '#6B7280', 'Unknown': '#9CA3AF'
    }
    return colors.get(platform, '#9CA3AF')


# === CONFIG: South Africa Lexicon ===
CONFIG = {
    "model_id": "meta-llama/llama-4-scout-17b-16e-instruct",
    "bertrend": {"min_cluster_size": 3},
    "analysis": {"time_window": "48H"},
    "coordination_detection": {"threshold": 0.85, "max_features": 5000},
    "lexicon": {
        "dehumanizing": {
            "diere": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "isilwanyana": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "liphoofolo": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "phologolo": {"severity": "high", "target_entity": "", "language": "Tswana"},
            "bobbejaan": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "imfene": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "tshoene": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "tšoene": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "barbaar": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "barbaarse": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "barbare": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kakkerlak": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "beeste": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "inkomo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "izinkomo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kgomo": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "khomo": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "koei": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "hond": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "honde": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "inja": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "lenja": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "lintja": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "nja": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "njandini": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ntja": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "ntsa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "wenja": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "zinja": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "donkie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "imbongolo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "tonki": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "dintsa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "enja": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "aphela": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "hiëna": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "jakkals": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "phokojwe": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "bloedsuier": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "intwala": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "luis": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "nta": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "esinezimpethu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "impethu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "maaiers": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "seboko": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "imigodoyi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "lomgodoyi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "mgodoyi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "nemgodoyi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umgodoyi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ungumgodoyi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "womgodoyi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ape": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "inkawu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kgabo": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "nkawana": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "nkawu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "aap": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "bosluis": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "imbuka": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "verraaier": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "verraaiers": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kwaadsteker": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "leferefere": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "maferefere": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "demonies": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "demoniese": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "idemon": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "idimoni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ledemona": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "letimona": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "matimona": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "bosatane": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "duiwel": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "duiwels": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "lisatane": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "masatane": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "osatane": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "satane": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "satanies": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "sataniese": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "satans": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "sathane": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "usathane": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isigebengu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "misdadiger": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "senokoane": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "senokwane": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "asozoninyisa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kak": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "nyela": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "nyele": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "onya": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "seliyanya": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ukunya": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "uyanya": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "uyonyela": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isishosha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "segole": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "sehole": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "umuntu okhubazekile": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "gestremde": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "umntu okhubazekileyo": {"severity": "high", "target_entity": "", "language": "Xhosa"},
            "seqhwala": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "segole": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "enyanyekayo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "afskuwelik": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "amasikizi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "hond": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "inja": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ntja": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "ntšwa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "imbongolo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "donkie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "iesile": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "tonki": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "isidakwa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "dronklap": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "inxila": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "letahwa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "letagwa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "omubi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "vervloek": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "boos": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "nyonyeho": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "ikaka": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "masepa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "dikgat": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "isidudla": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "les'dudla": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "lesdudla": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "motenya": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "sdudla": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "injakazi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "teef": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "bomatla": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "booatla": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "dimatla": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "domkop": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ibharu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "idioot": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "isduphunga": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isibhanxa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isidenge": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isilima": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "iziphukuphuku": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "lithoto": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "litlaela": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "mampara": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "sedidi": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "seeleele": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "sematla": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "sethoto": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "setlaela": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "silima": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "sisibhanxa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "slima": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "sphukuphuku": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "wisdomu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "dihema": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "sehema": {"severity": "high", "target_entity": "", "language": "Sotho"},
        },
        "derogatory": {
            "endunu": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "gat": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "hol": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "ididi": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "indunu": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "kwemdidi": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "mdidi": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "mnqundu": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "umdidi": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "umnqundu": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "umqundu": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "windgat": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "dibono": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "lomsunu": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "msoon": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "msunu": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "msunywana": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "ndimsoone": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "ngumsunu": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "poephol": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "poepholle": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "sebono": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "sebonong": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "umsunu": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "wumsunu": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "babe": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "bosula": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "kubi": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "lelik": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "lelike": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "maswe": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "mobe": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "mubi": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "sleg": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "slegstes": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "slegte": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "ububi": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "gatkruiper": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "gatkruiperige": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "gatkruipers": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "brand in die hel": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "ezimpundu": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "impundu": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "izinqa": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "kont": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "letono": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "litono": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "marago": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "sbono": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "uzimpundu": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "amagwala": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "igwala": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "lafaard": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "legatlapa": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "lekoala": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "magatlapa": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "makoala": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "gek": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "hlanya": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "hlanyaa": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "hlanye": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "lehlanya": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "mahlanya": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "uhlanya": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "uphambene": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "dinokwane": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "amalebe": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "malebe": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "maleve": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "onamalebe": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "samalebe": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "ditshwakga": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "ivila": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "luiaard": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "osebodu": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "ixoki": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "leuenaar": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "mohata": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "bokhopo": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "booswigte": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "khopo": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "amasimba": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "bolokoe": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "ikaka": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "kaka": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "masimba": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "masimbakho": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "stront": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "yikaka": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "isidudla": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "les'dudla": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "lesdudla": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "motenya": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "sdudla": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "mampara": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "sedidi": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "seeleele": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "sematla": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "sethoto": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "setlaela": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "silima": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "sisibhanxa": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "slima": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "sphukuphuku": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "wisdomu": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "dihema": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "sehema": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "amakwerekwere": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "ikwerekwere": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "kwerekwere": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "lekoere": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "lekoerekoere": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "makoekoere": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "makoere": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "makoerekoere": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "makwerekwere": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "wekwerekwere": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "abambhebha": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "bhebha": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "bhejwa": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "fokken": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "libheba": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "naai": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "ngibhebhe": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "nobhebha": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "ubhebhana": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "uyangbhebha": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "fokkof": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "fokof": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "fuseg": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "fusegi": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "futsek": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "tsek": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "voestek": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "voetsek": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "inkonkoni": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "isitabane": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "istabane": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "moffie": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "ubutabane": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "isifombo": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "isifumbu": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "sisifombo": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "baikaketsi": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "huigelaars": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "bolaea": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "bolaee": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "bolauwa": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "bolawa": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "bolaya": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "bulala": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "doodmaak": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "amalebe": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "malebe": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "maleve": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "onamalebe": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "samalebe": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "ditshwakga": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "ivila": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "luiaard": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "osebodu": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "bloedsuier": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "ixoki": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "leuenaar": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "umqambimanga": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "mohata": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "intwala": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "luis": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "nta": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "esinezimpethu": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "impethu": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "maaiers": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "seboko": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "baster": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "basters": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "imigodoyi": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "lomgodoyi": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "mgodoyi": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "nemgodoyi": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "umgodoyi": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "ungumgodoyi": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "womgodoyi": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "ape": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "inkawu": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "kgabo": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "nkawana": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "nkawu": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "polao": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "vermoor": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "mmolai": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "moordenaars": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "umbulali": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "isimungulu": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "semumu": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "negroïde": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "parasiet": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "paria": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "botona": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "incanca": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "ipipi": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "lerete": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "piel": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "pipi": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "dikolobe": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "ihagu": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "kolobe": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "vark": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "isidlwengu": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "umdlwenguli": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "verkragter": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "verkragters": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "dipeba": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "igundane": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "igundwane": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "peba": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "tweba": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "gemors": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "letlakala": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "matlakala": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "udoti": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "vuilgoed": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "vullis": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "bafeba": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "boaka": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "boakabo": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "bofebe": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "bofebeng": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "botekatsi": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "feba": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "hoerery": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "lofeba": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "matekatseng": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "sikufebele": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "ubufebe": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "ukufeba": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "ukufebela": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "bobelete": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "diaka": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "difebe": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "febelina": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "hoer": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "hoere": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "ihule": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "isifebe": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "izifebe": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "lebelete": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "letekatsana": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "letekatse": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "lifebe": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "mabelete": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "oomarhosha": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "owumahosha": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "seaka": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "sefebe": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "sfebe": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "siyisifebe": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "umarhosha": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "unondindwa": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "dubula": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "skiet": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "thunya": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "thunyang": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "neger": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "inyoka": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "nyoka": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "slang": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "hlaba": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "steek": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "tlhaba": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "dom": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "simpel": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "bububhanxa": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "ububhanxa": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "ubuphukuphuku": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "yobudenge": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "amamenemene": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "imenemene": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "lemenemene": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "limenemene": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "moqhekelli": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "skelm": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "umkhohlisi": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "amasende": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "ballas": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "balle": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "mae": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "marete": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "masende": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "ngamasende": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "ngesende": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "ungamasende": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "yamasende": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "bosholu": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "dief": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "isela": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "legodu": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "lesholu": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "magodu": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "masholu": {"severity": "medium", "target_entity": "", "language": "Sotho"},
            "rampokker": {"severity": "medium", "target_entity": "", "language": "Afrikaans"},
            "tsotsi": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "umdlwembe": {"severity": "medium", "target_entity": "", "language": "Zulu"},
            "utsotsi": {"severity": "medium", "target_entity": "", "language": "Zulu"},
        },
        "ethnic_slur": {
            "amakula": {"severity": "high", "target_entity": "Indian South Africans", "language": "Zulu"},
            "ikula": {"severity": "high", "target_entity": "Indian South Africans", "language": "Zulu"},
            "lekula": {"severity": "high", "target_entity": "Indian South Africans", "language": "Sotho"},
            "makula": {"severity": "high", "target_entity": "Indian South Africans", "language": "Sotho"},
            "amabhunu": {"severity": "high", "target_entity": "Afrikaners", "language": "Zulu"},
            "boer": {"severity": "high", "target_entity": "Afrikaners", "language": "Afrikaans"},
            "boere": {"severity": "high", "target_entity": "Afrikaners", "language": "Afrikaans"},
            "ibhunu": {"severity": "high", "target_entity": "Afrikaners", "language": "Zulu"},
            "ibunu": {"severity": "high", "target_entity": "Afrikaners", "language": "Zulu"},
            "leburu": {"severity": "high", "target_entity": "Afrikaners", "language": "Sotho"},
            "maburu": {"severity": "high", "target_entity": "Afrikaners", "language": "Sotho"},
            "abelungu": {"severity": "high", "target_entity": "White South Africans", "language": "Zulu"},
            "lekgowa": {"severity": "high", "target_entity": "White South Africans", "language": "Sotho"},
            "makgowa": {"severity": "high", "target_entity": "White South Africans", "language": "Sotho"},
            "ngamla": {"severity": "high", "target_entity": "White South Africans", "language": "Zulu"},
            "umlungu": {"severity": "high", "target_entity": "White South Africans", "language": "Zulu"},
            "wit mense": {"severity": "high", "target_entity": "White South Africans", "language": "Afrikaans"},
            "abathakathi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "baloi": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "igqwirha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "igqwirhakazi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ligqwirha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "moloi": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "moloingwana": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "ngamagqwirha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umthakathi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "anobugqwirha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "boloi": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "elathakatha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "eyothakatha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "loile": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "loilwe": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "loiwa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "othakatha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ubuthakathi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ukuthakatha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "uyathakatha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "zobugqwirha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "wurm": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "nuttelose": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "afkap": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "afsny": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kgaola": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "afrika-drek": {"severity": "high", "target_entity": "Black South Africans", "language": "Afrikaans"},
            "be-fokken-fok": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "bedonnerde": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "om te verniel": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "befoeterd": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "befok": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "bemoeial": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "bleddie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "blerrie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "bliksem": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "bliksems": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "buffel": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "nare": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "damesseun": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "donner": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "donners": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "donnerse": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "doos": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "doose": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "dose": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "druiloor": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "dwepery": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "egbreekster": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "seaka": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "fokkol": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "fokol": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "fokweet": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "gans": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "isidenge": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "gefok": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "gemoer": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "gepoes": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "grootprater": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "gruwel": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "bogoma": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "hardpoesigheid": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "hoerboer": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "hoereerder": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "holnaai": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "hotnot": {"severity": "high", "target_entity": "Coloured South Africans", "language": "Afrikaans"},
            "hotnots": {"severity": "high", "target_entity": "Coloured South Africans", "language": "Afrikaans"},
            "ilawu": {"severity": "high", "target_entity": "Indian South Africans", "language": "Zulu"},
            "houtkop": {"severity": "high", "target_entity": "White South Africans", "language": "Afrikaans"},
            "houtkoppe": {"severity": "high", "target_entity": "White South Africans", "language": "Afrikaans"},
            "kaffer": {"severity": "high", "target_entity": "Black South Africans", "language": "Afrikaans"},
            "kaffers": {"severity": "high", "target_entity": "Black South Africans", "language": "Afrikaans"},
            "kaffertjie": {"severity": "high", "target_entity": "Black South Africans", "language": "Afrikaans"},
            "kaffer-gebiede": {"severity": "high", "target_entity": "Black South Africans", "language": "Afrikaans"},
            "kaffergebiede": {"severity": "high", "target_entity": "Black South Africans", "language": "Afrikaans"},
            "kafferboetieparty": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kafferland": {"severity": "high", "target_entity": "Black South Africans", "language": "Afrikaans"},
            "kafferrivier": {"severity": "high", "target_entity": "Black South Africans", "language": "Afrikaans"},
            "kakhuis": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kaklelik": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "koffie-moffies": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kontkop": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kwaaddoener": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kwaaddoeners": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "laf": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "lafheid": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "meid": {"severity": "high", "target_entity": "Black South African women", "language": "Afrikaans"},
            "meide": {"severity": "high", "target_entity": "Black South African women", "language": "Afrikaans"},
            "mishandel": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "modderras": {"severity": "high", "target_entity": "Coloured South Africans", "language": "Afrikaans"},
            "moer": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "amasende": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "moere": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "moerse": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "igolo likanyoko": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "moerskont": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "moertoe": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "moffie-musiek": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "moffiegat": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "moffiegeskinder": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "moffiepienk": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "moffieregte": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "moffietaal": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "na-aap": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "geneuk": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "neuk": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "opgeneuk": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "opneuk": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "nimfomaan": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "onkruid": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ontman": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "thena": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "fanyeha": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "ophang": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ousie": {"severity": "high", "target_entity": "Black South African women", "language": "Afrikaans"},
            "papegaai": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "pappekak": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "poep": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "pooier": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "skandseun": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "slak": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "smulpaap": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "so-en-so": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "stok-in-die-modder": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "sub-ras": {"severity": "high", "target_entity": "Black South Africans", "language": "Afrikaans"},
            "tuinbooi": {"severity": "high", "target_entity": "Black South African men", "language": "Afrikaans"},
            "tuinjong": {"severity": "high", "target_entity": "Black South African men", "language": "Afrikaans"},
            "uitgeroei": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "uitroei": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "verbastering": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "peto": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "verkrag": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "vermink": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "verstik": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "versuip": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "walgwors": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "wellustig": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "fokop": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "opfok": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "fok jou": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "msunu kanyoko": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "hamba uyoncela": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "tenang": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "o masepa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "stuk kak": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "bua masepa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "hou jou mond": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ngwana ntja": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "uyabheda": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "witgemors": {"severity": "high", "target_entity": "White South Africans", "language": "Afrikaans"},
            "umlanjwana": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "lummel": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "knoeier": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "snotneusie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "mannetjiesvrou": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "manvrou": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "manvrouens": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "fopdosser": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "paljas": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "verdomp": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ditlhong": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "skande": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "fanatikus": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "vuil": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "fokkit": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "fokkers": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "naaier": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "naaiers": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "onnozele": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "onsedelike": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "onbevoeg": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "jeugoortreder": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "losbek": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "isifebe sendoda": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "mongool": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "isilo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "monster": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "nazi": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "pateties": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "patetiese": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "pedofiel": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "pervers": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "perverse": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "plunderaar": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "umthondo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "gepeupel": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "deugniet": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "swaksinnige": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "vertraagde": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "rower": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "isikhohlakali": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "skelms": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ubolile": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "verrot": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "vrot": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "klaploper": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ditshila": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "gespuis": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "manyala": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "selfsugtig": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "siek": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "sieklik": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "enuka": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "nkgang": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "stink": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "skinderbek": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "verklikker": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "folteraar": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "vernieler": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "adder": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kniesoor": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "papbroek": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "dwaas": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "semau": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "semaumau": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "semautshethe": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "tabanya": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "thakhisa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "thulisa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "bodipa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "bogoma": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "bosutlha": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "gasagasa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "hlakaza": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kgarameletsa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "sunduzela kude": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kgaratlhela": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "zabalaza": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kgwathisa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "shaya": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "laitaka": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "lekalaka": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "umKhalanga": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "lesarwa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "uMthwa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "makgakga": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "matepe": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "matlabua": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "insangu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "motokwane": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "ngwathaganya": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "phula": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "nlxa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "nxa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "nxla": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "nyenyafatsa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "ukubukela phansi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "rotela": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "diganana": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "seganana": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "okufana nomuntu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "sekamotho": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "sephinya": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "umsuzo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "botlhodi": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "ditlhodi": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "oletha amashwa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "setlhodi": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "siess": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "tenegile": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "isambane": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "thakadu": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "bhubhisa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "thubaganya": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "casula": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "tlapisa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "tlhapetswe": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "udakiwe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "bangela ukukhathazeka": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "tlhobaetsa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "hlupha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "tlhorontsha": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "tlontlokwane": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "tshosetsa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "ngizoshad'isifebe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ngyoshad'isifebe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "idl'umnqundu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kukh'isibhanxa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "indunu yenja": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "mdidi'wenja": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "indunu yakho": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "mnqund'wakho": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ngales'shlama": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "uzophul'umnqundu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "uzophula umdidi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "injubaqa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "yinjubaqa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "iphixiphixi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umqambimanga": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isishwapa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ixelegu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ngamaxelegu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isifebe sasendaweni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "unondatshaza": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "nalanoqatiko": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "unoqatiko": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "yeses": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kwamsun'wenja": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "msun'kanyoko": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ngesab'isitabane": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ngesab'istabane": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amafinyila": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "indlwabu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ingquza": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "nquza": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "poes": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "isdwedwe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "iyellowbone": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "jou ma": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kanyoko": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "mmao": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "unyoko": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "mazulu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "mhlathi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "sbumbu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "sdoda": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "sdwaba": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "oneyihlama": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "shlama": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "sihlama": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "uyishlama": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "niks": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "tikiline": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ucansi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ufish": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ukusantuza": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "uneke": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ungangijwayeli": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "jou pa": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ntatao": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "uyihlo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "igrigamba": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "mabahambe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "dudula": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "buyel'ekhaya": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amakwerekwere hambani": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isilo sithi mawashawe amakwerekwere": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isinqe sekwerekwere": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umqundu wekwerekwere": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "fong kong": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "swartgat": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "pikkenien": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "outa": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "aia": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "klonkie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "bantoe": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "nie-blanke": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "swart gevaar": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "baasskap": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kafferboetie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kaffermeid": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kafferwerk": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kafferpak": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "skepsel": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kroeskop": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "boesman": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "gam": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "goffel": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "halfnaatjie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "malau": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "iqheya": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amakula ayizinja": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "usebenzela ikula": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "inzule": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "mina angizithandi ezinye izinhlanga, ikakhulu abelungu namakula": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "dubul' ibhunu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kushawa amabhunu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "plaasjapie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "rooinek": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "soutpiel": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "volksverraaier": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "armblanke": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "swartes is onbevoeg": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "al die slim mense is net wit": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "mens kan nie 'n swartman vertrou nie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kaffers is nie by hulle volle verstand nie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "dis die kaffers wat die probleem is": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "Kaffirs is die grootste gevaar": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "slaan die swartes": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "gaan hulle uitroei": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "swartes kruip eerder net gat as om te werk": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "die donker mense behoort nie die sê te hê nie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "bleddie swartes": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "om 'n gay te wees is die grootste ramp ter wêreld": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "jou fokken lettie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "abantu abamnyama akufanele babe nalutho": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "asibafuni abantu baseningizimu eqenjini lethu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "abesifazane akufanele bathathe izinqumo esontweni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "basadi ga ba tshwanela go tsaya ditshwetso mo kerekeng": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "amantombazane alungiswa ngokubulawa kuphela": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "banyana bone ba kgonwa fela ke go bolawa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "fa o sa mmolae ga a emise bobelete": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "uma ungambulali ngeke ayeke ukufeba": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "awuyena umuntu, uyisilwane": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "nizofa njengezimpukane": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ke tla go kgaola tlhogo": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "ngizokubulala": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "babulale": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "bashise": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "bashaye": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "awuleth' umshini wami": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "impimpi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "trassie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "skeef": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "isishawa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isidalwa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isigoga": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isithulu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "unomnyamazana": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "igolo likanyoko": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "jou ma se poes": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "skebereshe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "inyumba": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umjendevu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "iphara": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "malunde": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "bergie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "isikhotheni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "skhotheni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "moegoe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ungqingili": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "uncukumbili": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "moffiegevaar": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "alle gays moet vermoor word": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "skuim van die aarde": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ikwerekwere": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "igrigamba": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ihambasizwe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "buyela ezweni lakini": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "abaphangi bemisebenzi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ikala-kala": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "lekalaka": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "inyoka yakwelinye izwe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "bazosibulala sonke": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "abangenamaphepha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umShangane": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "phuma ezweni lakithi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isidleke sabafokazi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "bantshontsha amakhasimende ethu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isipaza somfokazi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umuntu wokufika": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "valani izitolo zabo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amasosha omgunyathi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "gijima ubuyele kini": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kaffer": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kaffertjie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ibhunu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "dubul' ibhunu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "hotnot": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "boesman": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "lesarwa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "ikula": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "witvel": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "abelungu mabahambe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umuntu omnyama ongcolile": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "imfene": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "aap": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "inkawu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "swart gevaar": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "rooi gevaar": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "amaMpondo adla abantu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umfana wabelungu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kafferboetie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "isilwane sasendle": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umXhosa oyisela": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "iqaqa lamaZulu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "inja yomXhosa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "inja yomZulu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umShangane ongcolile": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umVenda othakathayo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kwerekwere phuma endlini yami": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "sizowashisa amakwerekwere": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "faka ithayi entanyeni yakhe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "leli zwe ngelethu sodwa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "abahambi abangcolile": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "babhebha abafazi bethu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "bangenisa izidakamizwa ezweni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "inja yaseNigeria": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaSomali awayikhokhi intela": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "mlungu gqoloma": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "wit vark": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kaffermeid": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "meid": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "outa": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "skollie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "gammat": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "slams": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "vuilgoed": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kriminele buitelanders": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "abeSuthu bangamasela ezinkomo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umTswana oyivila": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umNdebele ongafundile": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umShangane othakathayo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "moffie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "isitabane": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "indoda efana nowesifazane": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "sizosilungisa lesi sitabane": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ngumbulalazwe lo mfazi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isifebe saselokishini": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "odayisa ngomzimba": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "injakazi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "teef": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "amaphoyisa athengwe ngamakwerekwere": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amakwerekwere abulala intsha yethu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kaffermeisie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "wit rotte": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "swart rotte": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "plakkers": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "amaphara": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "shisa iphara": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "mayibuye iAfrika ngaphandle kwamakwerekwere": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "dudula": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "Dudula, xosha amakwerekwere": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "bavalele emlilweni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "izinja ezizulazulayo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "abaTsonga abanamalungelo lapha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umVenda ongcolile": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umSuthu wezingubo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umSwazi oyigundane": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isidleke samagundane": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "basilethela izifo zabo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "bacekela phansi imvelo yethu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "abantu abangaveli ndawo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kwerekwere suka endleleni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kaffermeid, fokof": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "bantoe": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "plakkerskampvarke": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "houtkop": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kafferboetie-verraaier": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "meid, bly stil": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "hou jou bek, kaffer": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "Boere is moordenaars": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "Kleurlingbaster": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "koelie, fokof": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "Slamse terroris": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "Joodse geldwolf": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "inja yomlungu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "swart hond": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "izilwane ezingenamaphepha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "sasiphila kahle ningakafiki": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "bulala ikwerekwere": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "shaya ikwerekwere": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "phangani zonke izimpahla zabo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "bavalele ngaphandle kwezwe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "sizobadudula bonke ekuseni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "mabagoduke namhlanje": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isihlahla esingatheli zithelo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "inkukhu yaseMozambique": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "Gaza-rotte": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "umfokazi oyisela": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ikwerekwere lamasimba": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ingane yekwerekwere": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "asibafuni laba bantwana ezikoleni zethu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "baqeda imithi ezibhedlela zethu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ungalinikezi umuthi leli kwerekwere": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "hamba uyobelethela kini": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amadlozi omfokazi awanamandla lapha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "abathakathi bokufika": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "shisa umjondolo wakhe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "xosha le nja emsebenzini": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umShangane onekhala elibanzi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umVenda wodaka": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umsunduzi wezizwe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "inja yomXhosa ayithembeki": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaZulu ayizidlova ezingenangqondo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ibhunu elinuka phu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kaffergoed bly kaffergoed": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kafferverbond": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "amakula anonya kubantu abamnyama": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "inja yomSuthu idla udoti": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umfazi oyisiphukuphuku": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isikhonyane somShona": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "shisa imoto yekwerekwere": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "phuma kuleli tekisi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "awunalo ilungelo lokugibela": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umlomo womSomali": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ikati elimnyama lomShangane": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umZezuru ongcolile": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amasela aseZimbabwe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "abaseNigeria balwa nathi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "khuluma ulimi lwethu noma uhambe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umeqi womngcele": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umsiki wocingo lomngcele": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umthakathi omnyama": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umPedi ongelutho": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "inkukhu yomVenda": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "plaasmoordenaars": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "umlungu akuyena umuntu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "inja yomlungu mayibulawe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "swart barbaar": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "bastervolk": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "Boesman-suiplap": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "suka phambi kwami, mkwerekwere": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amagundane aseLimpopo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ayikho indawo yabafokazi lapha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ukuqothula amakwerekwere": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amakwerekwere asiphuca umhlaba wethu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "udoti wasemngceleni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "abadli bezinja": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "abathengisi bensangu bokufika": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umthakathi omhlophe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "inja emhlophe yobandlululo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "mlungu, musa ukudelela": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kafferhoer": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kaffernasie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "hotnotmeid": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "boesmanjagter": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "koelielorrie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "vuil moffie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "isitabane esigula ngengqondo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "bulala lesi sitabane": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "unuka njengekwerekwere": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umShangane wasehlathini": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaZulu adla ummbila kuphela": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umXhosa onolimi olumbaxambili": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umSuthu wasezintabeni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umTswana wekhabe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amagundane aseZimbabwe antshontsha ithusi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "abakwazi ngisho ukukhuluma isiNgisi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "abashayeli bamatekisi bokufika": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaloli awasiwo awabokufika": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "shisa iloli lalo mfokazi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "voetsek, kwerekwere": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "voetsek, kaffer": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "voetsek, boer": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "voetsek, koelie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "voetsek, moffie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "buyela eHarare": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "buyela eMaputo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "buyela eLagos": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "buyela eKinshasa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "buyela eMogadishu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amakwerekwere aletha inkohlakalo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "sizowathatha amapasi enu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "i-Operation Dudula izonibamba": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kafferkinders huil nie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "skaapkop-kaffer": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kaffermeid-hoerhuis": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "wit aasvoël": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "boerehond": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "Slamse vark": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "Joodse skelm": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "gundane laseGaza, suka lapha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "inja yaseZimbabwe ayifuni ukusebenza": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "abathengisi bezidakamizwa ezibulalayo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ukudla okuphelelwe yisikhathi kwamakwerekwere": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "shisa isitolo sakhe sokudla komgunyathi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amakwerekwere anamabhange angekho emthethweni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "abaholi bethu bathengwe ngemali yamakwerekwere": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kaffergrens": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "plakkersdorp": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "gevaarlike swart barbare": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "wit teef": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "swart teef": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "Kleurlingmeid, fokof": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "moffiebaster": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "isitabane sasemgwaqweni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "indoda engenamsebenzi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isalukazi esingumthakathi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "shisani lesi salukazi esithakathayo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaXhosa anamaqhinga amabi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaZulu afuna impi kuphela": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umSuthu olambile": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umTswana onamanga": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umVenda oyisiwula": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umTsonga oyisiphukuphuku": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "bonke abafokazi mabahambe kusasa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "valani umngcele ngamasosha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isipaza somPakistani kumele sivalwe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amakula anobuqili ngemali": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "abelungu abanaso isimilo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kaffer-plaaswerkers": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kaffer-bastervolk": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "Boere-verdrukkers": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "hotnot-dronklap": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "gammattaal": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "Slamse boemelaar": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "Joodse parasiet": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "swart katkisasie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "amaphara ahlukumeza umphakathi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "bethela iphara esiphambanweni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amakwerekwere aphethe amabhizinisi ethu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "dubula umfokazi emngceleni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umphangi wengcebo yezwe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "inja yaseSomalia edayisa ushevu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umMalawi ongumthakathi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amakwerekwere awasithandi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "bafuna ukusigqilaza ezweni lethu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "phuma kule ndlu, akuyona eyakho": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kaffermeid, ken jou plek": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "jou ma se kafferhare": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kroeshaar-kaffer": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "boer-vabond": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "wit gemors": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ikula eliyisimuncagazi somnotho": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "izitabane azinalo ilungelo lokushada": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "sizolivala leli mosque lenu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "Jode beheer die media": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "amaMpondo ayizilwane ezidla abantu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaSwazi awanangqondo yokubusa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umfazi akangangeni enkundleni yabesilisa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "lo mfazi ogqoka kanje ufuna ukudlwengulwa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "izitabane zicekela phansi amasiko ethu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isilima somuntu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isiwula esiyimpumputhe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isigoga esingenamsebenzi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isithulu esingezwa lutho": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umuntu ophambene ikhanda": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umlandeli wesonto lamanga": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umfundisi oyisela lemali": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umshumayeli wakwelinye izwe okhohlisayo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "sizocekela phansi itende lenu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umZezuru oyenga amantombazane": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umfokazi olambile waseHarare": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "inja yaseMalawi edla imifino": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umlungu ongumqashi oyiqili": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ubugqila basemapulazini amaBhunu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kaffer-vabond in my tuin": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "hotnot-straatboemelaar": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "gammat-gemors": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "moffie-rotte": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "izitabane azinalo ilungelo lokuba abazali": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umfazi makathule lapho amadoda ekhuluma": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umfazi oshaywayo nguyena owonile": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "injakazi ayikwazi ukuhola": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isifebe esivule imilenze": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaXhosa akha uzungu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaZulu alwa ngaphandle kwengqondo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umSuthu wemvu ephuzi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "abeTswana bayizigqila zabelungu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umShangane omnyama khace": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaNdebele obuhlalu obungenamsebenzi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "izwe lamaSwazi elingenalutho": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "shisa imoto yomfundisi oyikwerekwere": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amakwerekwere antshontsha izingane zethu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kaffermeid, vat jou goed en loop": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "Boerebeeste": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "wit skollie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "Kleurling-skolliehond": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "koelie-geldslukker": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "Pakistani-paspoortvervalser": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "moffie-duiwelskind": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ubutabane yisifo esithathelanayo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umfazi uyimpahla yendoda": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "shaya umfazi uma engalaleli": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isifebe esingenamahloni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaXhosa acabanga ukuthi ahlakaniphe ukudlula wonke umuntu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaZulu ayizilwane zemikhonto": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umSuthu ohamba emathuneni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "abeTswana abanomona": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "izingulube zodaka zamaVenda": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umculi womTsonga okhohlisayo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "Ndebele, phuma edolobheni lethu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "inja yomSwazi ayinanhlonipho": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amakwerekwere aphezu kophahla lwendlu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "phonsani amakwerekwere phansi emalolini": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kafferbediende": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kafferpondokkie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "umnikazi wepulazi oyibhunu ongumbulali": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "wit rotte in die parlement": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "Kleurling-dronkenskap": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "gammat-kombers": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "koelie-rioolswemmer": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "Paki-winkelvuilgoed": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "moffievirus": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "le lesbian kumele ilungiswe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "indoda eguqa phambi komfazi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umfazi akakwazi ukuba yinkosi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isifebe esidayisa amaphilisi okukhipha isisu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaXhosa acula ngamaphupho": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaZulu aphuza igazi lezitha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umSuthu wehhashi elikhathele": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "abeTswana abakhuluma isiNgisi esiphukile": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "izigebengu zamaVenda": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaTsonga ayizitha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaNdebele anenkani": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaSwazi akhonza izithombe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amakwerekwere agcwele ezibhedlela zethu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "khiphani ikwerekwere esibhedlela": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kafferwerkers is lui": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kaffer-vabond in die straat": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "Boere-tiran": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "izimuncagazi ezimhlophe ezweni lethu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "Kleurling-tikkop": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "gammat-vuilbroek": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "koelie-parasietwinkel": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "moffie met 'n verrotte siel": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "izitabane azifanele ukuphila": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umfazi makapheke ekhishini kuphela": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ukushaya umfazi kuyisiko lethu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isifebe esingenanhlonipho": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaXhosa azenza ababusi baphakade": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaZulu azobusa ngemikhonto": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umSuthu wengubo edabukile": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "abeTswana bangabahlebi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umakhadzi womVenda ongumthakathi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "inyanga yomTsonga ekhohlisayo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaNdebele akwaNdzundza angamasela": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaSwazi alala ngezisu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amakwerekwere mawagoduke ngesitimela": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "khiphani ikwerekwere efulethini lami": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kafferkinders moet werk, nie leer nie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kafferteef loop kaalvoet": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "amaBhunu angababulali asezweni lethu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amasela amhlophe antshontshe konke": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "Kleurling-smokkelaar": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "gammat-vabond sonder maniere": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "koelie-vuilgoed in die hospitaal": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "geel gevaar": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "moffieslet": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "izitabane azinandawo esontweni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umfazi makathwale umthwalo wokukhulelwa yedwa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umfazi ongenandoda akalutho": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isifebe esiyisilwane": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaXhosa athatha yonke imisebenzi kahulumeni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaZulu asemahostela awanayo inhlonipho": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umSuthu wasemaphandleni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "abeTswana bangamagwala": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umVenda ongumthakathi wempilo yonke": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "izisebenzi ezingamaShangane ezishibhile": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaNdebele angenamakhaya azulazulayo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaSwazi agcina imali emndenini kuphela": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amakwerekwere athengisa amaphilisi anoshevu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "vala indlu yekwerekwere ngezitini": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kafferwerkvolk moet luister": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kafferkinders in die modder": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "amaBhunu angabahlali abangekho emthethweni ezweni lethu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "wit peste wat nooit weggaan nie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "Kleurlingbaster sonder kultuur": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "gammat praat kak": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "koelie-vuilgoed, vrek in die pad": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "Pakistani wat met geld spog": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "homoseksualiteit is 'n sonde teen God": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "izitabane akufanele zisondele ezinganeni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umfazi akangabuzi indoda ukuthi ivelaphi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umfazi ophendulayo ufanelwe yimpama": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isifebe sasemgwaqweni esingenamsebenzi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "impisi yasendle": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ingwenya elambile ebulala abantu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "inhlwathi ecekela phansi umuzi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ukhozi oludla amathumbu abantu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "inja ebomvu yomlilo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "inhlamba kanyoko": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "msunu kanyoko": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "igolo likanyoko": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "jou ma se poes": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "amasimba kanyoko": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amakwerekwere mawaphonswe eziko": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "shisa ibhasi lamakwerekwere": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "asiwaphe amakwerekwere isidlo esibomvu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "khiphani abantwana bawo ezinkulisa zethu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amakwerekwere aphuza wonke amanzi ethu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kafferboetie hoort aan die galg": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "Boerevelletjie so wit soos kryt": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "swart vark aan die spit": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "Kleurling-tarentaal": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "koelie-sakdraer": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "Pakistani-halaalbedrog": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "moffievullishoop": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "izitabane azinawo umphefumulo woqobo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umfazi ozicelela ukushaywa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isifebe esiyenga amadoda emgwaqweni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaXhosa aphanga imfuyo yezinye izizwe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaZulu ayizilwane ezingenamthetho": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umSuthu wobuhlalu bamatshe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "abeTswana abadla inyama ebolile": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ingane yomVenda yasentabeni yabathakathi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umTsonga onamabala esikhumbeni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaNdebele asemaphandleni angenalutho": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaSwazi akhonza izinkomo ukudlula abantu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amakwerekwere mawalayishwe emavenini amaphoyisa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "iLindela yikusasa lakho, kwerekwere": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kafferkinders het geen toekoms nie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ingcindezelo yamaBhunu ayisoze yaphela": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "izilo ezimhlophe eziphuza igazi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "Kleurling-tikmonster": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "gammat se lawaaierige varke": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "geldhonger koelie-tiran": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "Pakistani-gif in die brood": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "moffies is 'n skande vir die nasie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "izitabane azinawo amadlozi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "inyumba": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isifebe esingenakhaya": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaXhosa akhohlisa ezinkantolo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaZulu azobamba izikhali uma kunesidingo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umSuthu wasezintabeni ezibandayo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "abeTswana abayazi impi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umthakathi womVenda ubulala wonke umuntu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isikole senkohlakalo samaTsonga": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "Ndebele, phuma uphele ezweni lethu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaSwazi ayizimpumputhe zobukhosi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amakwerekwere mawathathe izikhwama zawo ahambe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "azikho izimvume zokuhweba zabafokazi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kaffer-plaasvolk bly onder die juk": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "amaBhunu ayizigebengu antshontshe amanzi nomhlaba": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "inyoka emhlophe otshanini base-Afrika": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "Kleurling-tikkop sonder tande": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "gammat-doos": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "koelie-baster van die mark": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "Pakistani-nagkruiper": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "moffies is 'n pes vir die samelewing": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "izitabane azinalo ilungelo lokungcwatshwa esontweni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umfazi ongayilaleli indoda akafanele ukuphila": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isifebe esigibela amatekisi mahhala": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaXhosa aziphatha sengathi izwe lonke ngelawo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaZulu anolaka lwenja elambile": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umSuthu wocezwana lukashoki": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "abeTswana bathanda ubuthongo kunomsebenzi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umthakathi womVenda ugumbuqela amakhosi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaTsonga aphuza imithi yobuthakathi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaNdebele awanabo ubuntu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaSwazi alandela amakhosi njengezimvu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amakwerekwere mawaphonswe ngaphandle ezitimeleni zikagesi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "awekho amatende amakwerekwere emapulazini ethu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kaffertaal is niks werd nie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "Boere-vuilgoed op die trekpad": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "abelungu bantshontshe igolide lethu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "Kleurling-bendelid, vrek in die tronk": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "gammat sonder ware godsdiensbegrip": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "koelie-vuilgoed, uit ons winkelsentrum uit": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "Pakistani-onwettige bankier": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "moffie-nagloerder": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "izitabane azinalo ilungelo lokuya entabeni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umfazi onobuso bemfene": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isifebe esidayisa ngezingane zaso": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaXhosa athumbe umbuso ngenkohlakalo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaZulu azogubha ngemililo yabanye abantu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umSuthu wengubo engcolile": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amadoda abeTswana awanabo ubudoda": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umthakathi womVenda ulala emathuneni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amehlo obusela omTsonga": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amabhantshi aluhlaza amaNdebele angenasithunzi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaSwazi athatha umhlaba ngamanga asebukhosini": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amakwerekwere mawakhishwe emigwaqweni yonke": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ungathengi lutho esitolo somfokazi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kaffermeid, vat jou eie beker": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ibhunu eliwushiqela lizokhokhela izenzo zalo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amasela amhlophe antshontsha ubulungiswa bethu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "Kleurling-skollie, vrek op die sypaadjie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "gammat-vabond sonder maniere of siel": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ikula lesitolo limunca igazi lethu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "Pakistani-smokkelaar van vervalste medisyne": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "moffiemonster in die donker": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "izitabane azifanelwe ukuthula emhlabeni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "mfazi onomsindo, thula ngaphambi kokuba ushaywe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isifebe esivuka ebusuku sizule emathuneni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaXhosa acabanga ukuthi igazi lawo lobukhosi lidlula wonke umuntu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaZulu azophinde athathe umkhonto abuse": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umSuthu wengubo eklebhukile": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "abeTswana abanalo ikusasa elihle": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umthakathi womVenda uqamba amanga ngomlomo wenkukhu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ulimi lwamaTsonga lobuthakathi nenkohliso": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaNdebele ayimizulane engenamhlaba": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amaSwazi akhothama phambi komlungu nenkosi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "imfama": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "isilwane": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "dier": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "isilwanyana": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "phoofolo": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "koelie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "lekula": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "gat": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "umnqundu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "motete": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "ukuzigqaja": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ukratshi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "moikhohomosi": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "boikgogomoso": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "poephol": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "umsunu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "sebono": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "bobbejaan": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "tshwene": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "lelik": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "mbi": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "mobe": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "maswe": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "iqaba": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "barbaar": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "motho ya hlaha": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "sehlaha": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "betha": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "otla": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "abantu abamnyama": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "blinde": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "sefofu": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "boer": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ibhulu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "sha esihogweni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "tsha esihogweni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "tjha diheleng": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "boude": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "iimpundu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "marao": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "marago": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "iphela": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "bees": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kgomo": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "igwala": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "lekwala": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "uhlanya": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "gek": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "impambane": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "lehlanya": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "setsenwa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "misdadiger": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "isikrelemnqa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "senokwane": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "nya": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "nyela": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "idimoni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "demoon": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "modemona": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "usathane": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "duiwel": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "usathana": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "satane": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "fa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "vrek": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "shwa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "swa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "umuntu okhubazekile": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "gestremde": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "umntu okhubazekileyo": {"severity": "high", "target_entity": "", "language": "Xhosa"},
            "seqhwala": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "enyanyekayo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "afskuwelik": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "amasikizi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "hond": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ntšwa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "imbongolo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "donkie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "iesile": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isidakwa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "dronklap": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "inxila": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "letahwa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "letagwa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "omubi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "vervloek": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "boos": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "nyonyeho": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "ikaka": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "masepa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "dikgat": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "motenya": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "injakazi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ukuhleka inhlinini": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amazaizai": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "nyoba": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "fokkof": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "futsek": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "voetsek": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "setabane": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "isifumbu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "boggelrug": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "impisi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "hiëna": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ingcuka": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "phiri": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "umzenzisi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "huigelaar": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "umhanahanisi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "moikaketsi": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "moitimokanyi": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "ikhanka": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "jakkals": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "udyakalashe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "phokojwe": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "doodmaak": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "skaamlippe": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "dilebe": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "sekgoba": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "setshwakga": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "leuenaar": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ixoki": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "moaka": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "maaier": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "baster": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "brak": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "mgodoyi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "aap": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ukubulala": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "moord": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "polao": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "umbulali": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "moordenaar": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "mmolai": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "isimungulu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "stom": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "isimumu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isimuncagazi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isifunxi-gazi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "lenwa-madi": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "segopa-madi": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "ipipi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ntoto": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "ingulube": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kolobe": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "umdlwenguli": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "mmeteledi": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "rot": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "impuku": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "tweba": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "peba": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "gemors": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "inkunkuma": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "matlakala": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "hoerery": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ubuhenyu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "bofebe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "boaka": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "ihule": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "sefebe": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "skiet": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "thunya": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "neger": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "slang": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "nyoka": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "noga": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "gwaza": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "hlaba": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "isiwula": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "onnoselheid": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "bothoto": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "bosematla": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "bedrieër": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "imenemene": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "mothetsi": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "ballas": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "amasende": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "marete": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "isela": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "dief": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "leshodu": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "legodu": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "rampokker": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "umdlwembe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "tsotsi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umkhaza": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "bosluis": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "imbuka": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "verraaier": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "verraaiers": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kwaadsteker": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "leferefere": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "maferefere": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "egolo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "golo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "igolo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ikhekhe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "imomozi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kuku": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "ngegolo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "nnyo": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "nnyoana": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "nyoana": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "poes": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "uyigolo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "wemamagolo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "yegolo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "aasvoël": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "inqe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "lenong": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "abathakathi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "baloi": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "igqwirha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "igqwirhakazi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ligqwirha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "moloi": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "moloingwana": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "ngamagqwirha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umthakathi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "anobugqwirha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "boloi": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "elathakatha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "eyothakatha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "loile": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "loilwe": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "loiwa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "othakatha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ubuthakathi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ukuthakatha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "uyathakatha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "zobugqwirha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "wurm": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "nuttelose": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "afkap": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "afsny": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kgaola": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "afrika-drek": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "be-fokken-fok": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "bedonnerde": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "om te verniel": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "befoeterd": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "befok": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "bemoeial": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "bleddie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "blerrie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "bliksem": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "bliksems": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "buffel": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "nare": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "damesseun": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "donner": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "donners": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "donnerse": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "doos": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "doose": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "dose": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "druiloor": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "dwepery": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "egbreekster": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "seaka": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "fokkol": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "fokol": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "fokweet": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "gans": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "isidenge": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "gefok": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "gemoer": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "gepoes": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "grootprater": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "gruwel": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "bogoma": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "hardpoesigheid": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "hoerboer": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "hoereerder": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "holnaai": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "hotnot": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "hotnots": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ilawu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "houtkop": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "houtkoppe": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kaffer": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kaffers": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kaffertjie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kaffer-gebiede": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kaffergebiede": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kafferboetieparty": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kafferland": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kafferrivier": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kakhuis": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kaklelik": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "koffie-moffies": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kontkop": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kwaaddoener": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kwaaddoeners": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "laf": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "lafheid": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "meid": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "meide": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "mishandel": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "modderras": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "moer": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "amasende": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "moere": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "moerse": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "igolo likanyoko": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "moerskont": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "moertoe": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "moffie-musiek": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "moffiegat": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "moffiegeskinder": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "moffiepienk": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "moffieregte": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "moffietaal": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "na-aap": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "geneuk": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "neuk": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "opgeneuk": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "opneuk": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "nimfomaan": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "onkruid": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ontman": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "thena": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "fanyeha": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "ophang": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ousie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "papegaai": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "pappekak": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "poep": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "pooier": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "skandseun": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "slak": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "smulpaap": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "so-en-so": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "stok-in-die-modder": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "sub-ras": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "tuinbooi": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "tuinjong": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "uitgeroei": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "uitroei": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "verbastering": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "peto": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "verkrag": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "vermink": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "verstik": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "versuip": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "walgwors": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "wellustig": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "fokop": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "opfok": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "fok jou": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "msunu kanyoko": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "hamba uyoncela": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "tenang": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "o masepa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "stuk kak": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "bua masepa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "hou jou mond": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ngwana ntja": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "uyabheda": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "witgemors": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "umlanjwana": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "lummel": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "knoeier": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "snotneusie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "mannetjiesvrou": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "manvrou": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "manvrouens": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "fopdosser": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "paljas": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "verdomp": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ditlhong": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "skande": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "fanatikus": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "vuil": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "fokkit": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "fokkers": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "naaier": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "naaiers": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "onnozele": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "onsedelike": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "onbevoeg": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "jeugoortreder": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "losbek": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "isifebe sendoda": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "mongool": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "isilo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "monster": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "nazi": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "pateties": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "patetiese": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "pedofiel": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "pervers": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "perverse": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "plunderaar": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "umthondo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "gepeupel": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "deugniet": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "swaksinnige": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "vertraagde": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "rower": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "isikhohlakali": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "skelms": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ubolile": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "verrot": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "vrot": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "klaploper": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ditshila": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "gespuis": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "manyala": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "selfsugtig": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "siek": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "sieklik": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "enuka": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "nkgang": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "stink": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "skinderbek": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "verklikker": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "folteraar": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "vernieler": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "adder": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kniesoor": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "papbroek": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "dwaas": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "semau": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "semaumau": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "semautshethe": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "tabanya": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "thakhisa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "thulisa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "bodipa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "bogoma": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "bosutlha": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "gasagasa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "hlakaza": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kgarameletsa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "sunduzela kude": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kgaratlhela": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "zabalaza": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kgwathisa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "shaya": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "laitaka": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "lekalaka": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "umKhalanga": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "lesarwa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "uMthwa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "makgakga": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "matepe": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "matlabua": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "insangu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "motokwane": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "ngwathaganya": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "phula": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "nlxa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "nxa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "nxla": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "nyenyafatsa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "ukubukela phansi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "rotela": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "diganana": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "seganana": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "okufana nomuntu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "sekamotho": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "sephinya": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "umsuzo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "botlhodi": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "ditlhodi": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "oletha amashwa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "setlhodi": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "siess": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "tenegile": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "isambane": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "thakadu": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "bhubhisa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "thubaganya": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "casula": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "tlapisa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "tlhapetswe": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "udakiwe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "bangela ukukhathazeka": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "tlhobaetsa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "hlupha": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "tlhorontsha": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "tlontlokwane": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "tshosetsa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "ngizoshad'isifebe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ngyoshad'isifebe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "idl'umnqundu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kukh'isibhanxa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "indunu yenja": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "mdidi'wenja": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "indunu yakho": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "mnqund'wakho": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ngales'shlama": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "uzophul'umnqundu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "uzophula umdidi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "injubaqa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "yinjubaqa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "iphixiphixi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umqambimanga": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isishwapa": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ixelegu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ngamaxelegu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isifebe sasendaweni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "unondatshaza": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "nalanoqatiko": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "unoqatiko": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "yeses": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kwamsun'wenja": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "msun'kanyoko": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ngesab'isitabane": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ngesab'istabane": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amafinyila": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "indlwabu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ingquza": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "nquza": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "poes": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "isdwedwe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "iyellowbone": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "jou ma": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kanyoko": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "mmao": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "unyoko": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "mazulu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "mhlathi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "sbumbu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "sdoda": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "sdwaba": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "oneyihlama": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "shlama": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "sihlama": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "uyishlama": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "niks": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "tikiline": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ucansi": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ufish": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ukusantuza": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "uneke": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ungangijwayeli": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "jou pa": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "ntatao": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "uyihlo": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "igrigamba": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "mabahambe": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "dudula": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "buyel'ekhaya": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amakwerekwere hambani": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isilo sithi mawashawe amakwerekwere": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "isinqe sekwerekwere": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "umqundu wekwerekwere": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "fong kong": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "swartgat": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "pikkenien": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "outa": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "aia": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "klonkie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "bantoe": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "nie-blanke": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "swart gevaar": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "baasskap": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kafferboetie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kaffermeid": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kafferwerk": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kafferpak": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "skepsel": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kroeskop": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "boesman": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "gam": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "goffel": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "halfnaatjie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "malau": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "iqheya": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "amakula ayizinja": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "usebenzela ikula": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "inzule": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "mina angizithandi ezinye izinhlanga, ikakhulu abelungu namakula": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "dubul' ibhunu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "kushawa amabhunu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "plaasjapie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "rooinek": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "soutpiel": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "volksverraaier": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "armblanke": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "swartes is onbevoeg": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "al die slim mense is net wit": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "mens kan nie 'n swartman vertrou nie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "kaffers is nie by hulle volle verstand nie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "dis die kaffers wat die probleem is": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "Kaffirs is die grootste gevaar": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "slaan die swartes": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "gaan hulle uitroei": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "swartes kruip eerder net gat as om te werk": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "die donker mense behoort nie die sê te hê nie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "bleddie swartes": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "om 'n gay te wees is die grootste ramp ter wêreld": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "jou fokken lettie": {"severity": "high", "target_entity": "", "language": "Afrikaans"},
            "abantu abamnyama akufanele babe nalutho": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "asibafuni abantu baseningizimu eqenjini lethu": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "abesifazane akufanele bathathe izinqumo esontweni": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "basadi ga ba tshwanela go tsaya ditshwetso mo kerekeng": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "amantombazane alungiswa ngokubulawa kuphela": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "banyana bone ba kgonwa fela ke go bolawa": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "fa o sa mmolae ga a emise bobelete": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "uma ungambulali ngeke ayeke ukufeba": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "awuyena umuntu, uyisilwane": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "nizofa njengezimpukane": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "ke tla go kgaola tlhogo": {"severity": "high", "target_entity": "", "language": "Sotho"},
            "ngizokubulala": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "babulale": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "bashise": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "bashaye": {"severity": "high", "target_entity": "", "language": "Zulu"},
            "awuleth' umshini wami": {"severity": "high", "target_entity": "", "language": "Zulu"},
        },
    },
    "risk_scoring": {
        "severity_weights": {"low": 1, "medium": 2, "high": 3, "critical": 4},
        "category_weights": {
            "dehumanizing": 1.5, "derogatory": 1.3, "ethnic_slur": 1.5,
            "ethnicity": 1.0, "slur": 1.4, "violence": 1.5,
            "incitement": 1.5, "misogynistic": 1.4, "stereotype": 1.2,
            "xenophobia": 1.5, "homophobic": 1.4, "religious": 1.0,
            "class_based": 1.1, "ancestry": 1.3, "extremism": 1.5,
            "inflammatory": 1.4, "call_to_action": 1.5,
            "gender_disinfo": 1.4, "structural": 1.2,
        },
        "risk_thresholds": {"low": 3, "medium": 6, "high": 10, "critical": 15}
    },
    "display": {"max_terms_per_category": 50, "show_sa_languages_first": True, "highlight_critical": True}
}


# === STREAMLIT-STYLE DATA PROCESSING FUNCTIONS ===
def infer_platform_from_url(url):
    """Infer platform from URL"""
    if pd.isna(url) or not isinstance(url, str) or not url.startswith("http"):
        return "Unknown"
    url = url.lower()
    platforms = {
        "tiktok.com": "TikTok", "vt.tiktok.com": "TikTok",
        "facebook.com": "Facebook", "fb.watch": "Facebook",
        "twitter.com": "X", "x.com": "X",
        "youtube.com": "YouTube", "youtu.be": "YouTube",
        "instagram.com": "Instagram",
        "telegram.me": "Telegram", "t.me": "Telegram", "telegram.org": "Telegram"
    }
    for key, val in platforms.items():
        if key in url:
            return val
    if any(d in url for d in ["nytimes.com", "bbc.com", "cnn.com", "reuters.com", "aljazeera.com"]):
        return "News/Media"
    return "Media"


def extract_original_text(text):
    """Extract clean original text from post content"""
    if pd.isna(text) or not isinstance(text, str):
        return ""
    cleaned = re.sub(r'^(RT|rt|QT|qt|repost|shared|via|credit)\s*[:@]\s*', '', text, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r'@\w+|http\S+|www\S+|https\S+', '', cleaned).strip()
    cleaned = re.sub(r'\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{4}\b', '', cleaned)
    return re.sub(r'\s+', ' ', cleaned).strip().lower()


def is_original_post(text):
    """Check if post is original (not a repost/retweet)"""
    if pd.isna(text) or not isinstance(text, str):
        return False
    lower = text.strip().lower()
    if not lower:
        return False
    patterns = [
        r'^🔁.reposted', r'\b(reposted|reshared|retweeted)\b',
        r'^(rt|qt|repost)\s[:@\s]', r'^\s*[🔁↪️➡️]\s*@?\w*'
    ]
    if any(re.search(p, lower, flags=re.IGNORECASE) for p in patterns):
        return False
    if len(re.sub(r'http\S+|@\w+', '', text).strip()) < 15:
        return False
    return len(lower) >= 20 and not re.search(r'^\s*["\u201c]|\s*@\w+\s*[":]', lower)


def parse_timestamp_robust(timestamp):
    """Parse timestamp with multiple format support"""
    if pd.isna(timestamp):
        return pd.NaT
    ts_str = re.sub(r'\s+GMT$', '', str(timestamp).strip(), flags=re.IGNORECASE)
    try:
        parsed = pd.to_datetime(ts_str, errors='coerce', utc=True)
        if pd.notna(parsed):
            return parsed
    except:
        pass
    for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%d/%m/%Y %H:%M', '%b %d, %Y %H:%M', '%Y-%m-%d']:
        try:
            parsed = pd.to_datetime(ts_str, format=fmt, errors='coerce', utc=True)
            if pd.notna(parsed):
                return parsed
        except:
            continue
    return pd.NaT


def combine_social_media_data(meltwater_df=None, civicsignals_df=None, tiktok_df=None, openmeasures_df=None, brandwatch_df=None):
    """Combine different platform datasets into unified format"""
    combined = []
    
    def get_col(df, cols):
        for col in cols:
            if col in df.columns:
                return df[col]
        df_cols = [c.lower().strip() for c in df.columns]
        for col in cols:
            norm = col.lower().strip()
            if norm in df_cols:
                return df[df.columns[df_cols.index(norm)]]
        return pd.Series([np.nan]*len(df), index=df.index)
    
    if brandwatch_df is not None and not brandwatch_df.empty:
        bw = pd.DataFrame()
        bw['account_id'] = brandwatch_df.get('Weblog Title', pd.Series(dtype='object')).combine_first(
            brandwatch_df.get('Author', pd.Series(dtype='object'))
        ).combine_first(
            brandwatch_df.get('Full Name', pd.Series(dtype='object'))
        ).astype(str).str.strip().replace('nan', '')
        bw['original_text'] = brandwatch_df.get('Full Text', pd.Series(dtype='object')).astype(str).str.strip().replace('nan', '')
        bw['URL'] = brandwatch_df.get('Url', pd.Series(dtype='object'))
        bw['timestamp_share'] = brandwatch_df.get('Date', pd.Series(dtype='object'))
        page_type = brandwatch_df.get('Page Type', pd.Series(dtype='object')).astype(str).str.lower()
        platform_map = {
            'twitter': 'X', 'x': 'X', 'x.com': 'X', 't.co': 'X',
            'facebook': 'Facebook', 'fb': 'Facebook', 'fb.watch': 'Facebook',
            'instagram': 'Instagram', 'tiktok': 'TikTok',
            'youtube': 'YouTube', 'telegram': 'Telegram', 't.me': 'Telegram'
        }
        bw['Platform'] = page_type.map(platform_map).fillna('Unknown')
        resource_ids = brandwatch_df.get('Resource Id', pd.Series(dtype='object'))
        mention_ids = brandwatch_df.get('Mention Id', pd.Series(dtype='object'))
        bw['content_id'] = resource_ids.combine_first(mention_ids).combine_first(bw['URL']).astype(str).str.strip()
        bw['source_dataset'] = 'Brandwatch'
        combined.append(bw)
    
    if meltwater_df is not None and not meltwater_df.empty:
        mw = pd.DataFrame()
        mw['account_id'] = get_col(meltwater_df, ['influencer'])
        mw['content_id'] = get_col(meltwater_df, ['tweet id', 'post id', 'id']).astype(str).str.strip()
        mw['object_id'] = get_col(meltwater_df, ['hit sentence', 'opening text', 'headline', 'text', 'content'])
        mw['URL'] = get_col(meltwater_df, ['url'])
        mw['timestamp_share'] = get_col(meltwater_df, ['date', 'timestamp', 'alternate date format'])
        mw['source_dataset'] = 'Meltwater'
        combined.append(mw)
    
    if civicsignals_df is not None and not civicsignals_df.empty:
        cs = pd.DataFrame()
        cs['account_id'] = get_col(civicsignals_df, ['media_name', 'author', 'username'])
        cs['content_id'] = get_col(civicsignals_df, ['stories_id', 'post_id', 'id']).astype(str).str.strip()
        cs['object_id'] = get_col(civicsignals_df, ['title', 'text', 'content', 'body'])
        cs['URL'] = get_col(civicsignals_df, ['url', 'link'])
        cs['timestamp_share'] = get_col(civicsignals_df, ['publish_date', 'timestamp', 'date'])
        cs['source_dataset'] = 'Civicsignal'
        combined.append(cs)
    
    if tiktok_df is not None and not tiktok_df.empty:
        tt = pd.DataFrame()
        tt['object_id'] = get_col(tiktok_df, ['text', 'Transcript', 'caption', 'content'])
        tt['account_id'] = get_col(tiktok_df, ['authorMeta/name', 'username', 'creator'])
        tt['content_id'] = get_col(tiktok_df, ['id', 'video_id', 'itemId']).astype(str).str.strip()
        tt['URL'] = get_col(tiktok_df, ['webVideoUrl', 'TikTok Link', 'url'])
        tt['timestamp_share'] = get_col(tiktok_df, ['createTimeISO', 'timestamp', 'date', 'createTime'])
        tt['source_dataset'] = 'TikTok'
        for col in ['playCount', 'diggCount', 'commentCount', 'shareCount', 'repostCount', 'textLanguage']:
            if col in tiktok_df.columns:
                tt[col] = tiktok_df[col]
        for i in range(5):
            hashtag_col = f'hashtags/{i}/name'
            if hashtag_col in tiktok_df.columns:
                tt[f'hashtag_{i}'] = tiktok_df[hashtag_col]
        combined.append(tt)
    
    if openmeasures_df is not None and not openmeasures_df.empty:
        om = pd.DataFrame()
        om['account_id'] = get_col(openmeasures_df, ['context_name', 'channelusername', 'channeltitle'])
        om['content_id'] = get_col(openmeasures_df, ['id', 'url']).astype(str).str.strip()
        om['object_id'] = get_col(openmeasures_df, ['text', 'message', 'body'])
        om['URL'] = get_col(openmeasures_df, ['url'])
        raw_dates = get_col(openmeasures_df, ['created_at', 'date'])
        om['timestamp_share'] = raw_dates.astype(str).str.replace(' @ ', ' ', regex=False)
        om['source_dataset'] = 'OpenMeasure_Telegram'
        combined.append(om)
    
    if combined:
        return pd.concat(combined, ignore_index=True)
    else:
        return pd.DataFrame()


def final_preprocess_and_map_columns(df, coordination_mode="Text Content"):
    """Final preprocessing and column mapping"""
    if df.empty:
        return pd.DataFrame(columns=['account_id','content_id','object_id','URL','timestamp_share','Platform','original_text','Outlet','Channel','cluster','source_dataset','Sentiment'])
    dfp = df.copy()
    if 'Sentiment' in dfp.columns:
        dfp = dfp[dfp['Sentiment'].isin(['Negative', 'Neutral'])]
    if 'object_id' in dfp.columns:
        obj_str = dfp['object_id'].astype(str)
        mask = dfp['object_id'].apply(is_original_post) & (~obj_str.str.contains('🔁', na=False)) & (~obj_str.str.startswith('RT @', na=False))
        dfp = dfp[mask].copy()
        dfp['object_id'] = dfp['object_id'].astype(str).replace('nan','').fillna('')
        dfp = dfp[dfp['object_id'].str.strip() != ""].reset_index(drop=True)
        dfp['original_text'] = dfp['object_id'].apply(extract_original_text) if coordination_mode=="Text Content" else dfp['URL'].astype(str).replace('nan','')
    else:
        if 'text' in dfp.columns:
            dfp['original_text'] = dfp['text'].apply(extract_original_text) if coordination_mode=="Text Content" else dfp['URL'].astype(str).replace('nan','')
        elif 'original_text' not in dfp.columns:
            dfp['original_text'] = ''
    dfp = dfp[dfp['original_text'].str.strip() != ""].reset_index(drop=True)
    dfp['Platform'] = dfp['URL'].apply(infer_platform_from_url)
    if 'source_dataset' in dfp.columns:
        dfp['source_dataset'] = dfp['source_dataset'].fillna('')
        tiktok_mask = dfp['source_dataset'].str.contains('TikTok|tiktok|vt.tiktok', case=False, na=False)
        dfp.loc[tiktok_mask, 'Platform'] = 'TikTok'
        telegram_mask = dfp['source_dataset'].str.contains('Telegram|telegram|t.me|OpenMeasure', case=False, na=False)
        dfp.loc[telegram_mask, 'Platform'] = 'Telegram'
        media_mask = dfp['source_dataset'].str.contains('Media|News|Civicsignal', case=False, na=False)
        dfp.loc[media_mask, 'Platform'] = 'Media'
    dfp['Platform'] = dfp['Platform'].replace('', 'Unknown').fillna('Unknown')
    dfp['Outlet'], dfp['Channel'], dfp['cluster'] = np.nan, np.nan, -1
    if 'Sentiment' not in dfp.columns:
        dfp['Sentiment'] = np.nan
    cols = ['account_id','content_id','object_id','URL','timestamp_share','Platform','original_text','Outlet','Channel','cluster','source_dataset','Sentiment']
    return dfp[[c for c in cols if c in dfp.columns]].copy()


def preprocess_dataframe(df):
    """Generic preprocessing for unknown/custom formats"""
    if df.empty:
        return pd.DataFrame()
    df = df.copy()
    df.columns = [c.lower().strip() if isinstance(c, str) else c for c in df.columns]
    column_mapping = {
        'account_id': ['account', 'username', 'user', 'author', 'handle'],
        'content_id': ['id', 'post_id', 'tweet_id', 'video_id'],
        'object_id': ['text', 'content', 'body', 'message', 'post'],
        'url': ['link', 'post_url', 'permalink'],
        'timestamp_share': ['date', 'created_at', 'posted_at', 'time']
    }
    for target, sources in column_mapping.items():
        if target not in df.columns:
            for source in sources:
                if source in df.columns:
                    df[target] = df[source]
                    break
    return df


def get_election_posts_queryset(request):
    """Centralized date filtering helper."""
    queryset = ProcessedPost.objects.all()
    view_all = request.GET.get('view_all') == 'true'
    if view_all:
        date_range = queryset.aggregate(min_date=Min('timestamp_share'), max_date=Max('timestamp_share'))
        start_date = date_range['min_date'] or timezone.now()
        end_date = date_range['max_date'] or timezone.now()
    else:
        start_date = request.GET.get('start_date')
        end_date = request.GET.get('end_date')
        if start_date and end_date:
            queryset = queryset.filter(timestamp_share__date__range=[start_date, end_date])
            if isinstance(start_date, str):
                start_date = datetime.strptime(start_date, '%Y-%m-%d')
            if isinstance(end_date, str):
                end_date = datetime.strptime(end_date, '%Y-%m-%d')
        else:
            end_dt = timezone.now()
            start_dt = end_dt - timedelta(days=90)
            queryset = queryset.filter(timestamp_share__gte=start_dt)
            start_date = start_dt
            end_date = end_dt
    return queryset.order_by('-timestamp_share'), start_date, end_date


def _date_key(value):
    if hasattr(value, 'date'):
        return value.date().isoformat()
    return str(value) if value else ''


def build_analytics_snapshot_key(kind, start_date, end_date, view_all=False, category=''):
    start_key = _date_key(start_date)
    end_key = _date_key(end_date)
    category_key = category or 'all'
    return f"{kind}:{start_key}:{end_key}:{int(bool(view_all))}:{category_key}"


def get_analytics_snapshot(kind, start_date, end_date, view_all=False, category='', allow_latest_fallback=False):
    key = build_analytics_snapshot_key(kind, start_date, end_date, view_all, category)
    category_key = category or 'all'
    shape_suffix = f":{int(bool(view_all))}:{category_key}"
    snapshot = (
        DashboardAnalyticsSnapshot.objects
        .filter(key=key)
        .values('payload', 'generated_at')
        .first()
    )
    if not snapshot and allow_latest_fallback:
        snapshot = (
            DashboardAnalyticsSnapshot.objects
            .filter(kind=kind, key__endswith=shape_suffix)
            .values('payload', 'generated_at')
            .order_by('-generated_at')
            .first()
        )
    if not snapshot:
        return {}, None
    return snapshot['payload'] or {}, snapshot['generated_at']


def get_materialized_lexicon_matches(posts_queryset, category=''):
    matches = PostLexiconMatch.objects.filter(post__in=posts_queryset)
    if category:
        matches = matches.filter(category__iexact=category)
    return matches


def empty_trend_analysis(message='Analytics are being prepared.'):
    return {
        'chart_json': None, 'trending_categories': [], 'total_categories_tracked': 0,
        'total_posts_scanned': 0, 'spikes_detected': [], 'spike_alerts': [],
        'message': message, 'total_spikes': 0, 'has_significant_spikes': False,
    }


def queue_dashboard_analytics_refresh(skip_narratives=True, skip_home=False, skip_lexicons=False):
    try:
        from django_q.tasks import async_task
        args = ['refresh_dashboard_analytics']
        if skip_lexicons:
            args.append('--skip-lexicons')
        if skip_home:
            args.append('--skip-home')
        if skip_narratives:
            args.append('--skip-narratives')
        async_task('django.core.management.call_command', *args)
        logger.info("Queued dashboard analytics refresh")
        return True
    except Exception as exc:
        logger.warning(f"Could not queue dashboard analytics refresh: {exc}")
        return False


def get_risk_actors_insight(posts_queryset, limit=8):
    """
    Identify risk actors based on:
    1. High post volume (potential spam/coordination)
    2. Repetitive content patterns
    3. High/critical risk levels
    """
    from collections import Counter
    import re
    risky_accounts = []
    
    # Get accounts with high post volume (5+ posts)
    account_stats = posts_queryset.values('account_id').annotate(
        post_count=Count('id'),
        high_risk_count=Count('id', filter=Q(risk_level__in=['high', 'critical'])),
        latest_post=Max('timestamp_share')
    ).filter(
        Q(post_count__gte=5) | Q(high_risk_count__gte=2)  # High volume OR multiple high-risk posts
    ).order_by('-post_count')[:limit]
    
    for acc in account_stats:
        account_id = acc['account_id']
        if not account_id or str(account_id).lower() in ['nan', 'none', '', 'twitter', 'source']:
            continue
        
        # Get account's posts
        account_posts = list(posts_queryset.filter(account_id=account_id))
        
        # Check for content repetition (coordination signal)
        content_hashes = []
        for post in account_posts:
            if post.original_text:
                # Normalize text: remove URLs, mentions, extra spaces
                normalized = re.sub(r'http\S+|@\w+|#\w+', '', post.original_text.lower())
                normalized = re.sub(r'\s+', ' ', normalized).strip()
                if len(normalized) > 20:  # Only consider substantial content
                    content_hashes.append(normalized[:100])  # First 100 chars as hash
        
        # Count duplicate content
        content_counter = Counter(content_hashes)
        duplicate_count = sum(1 for count in content_counter.values() if count > 1)
        repetition_rate = (duplicate_count / len(content_counter) * 100) if content_counter else 0
        
        # Get platforms and sample content
        platforms = list(set(p.platform for p in account_posts if p.platform))[:3]
        sample_post = account_posts[0] if account_posts else None
        
        # Calculate risk score
        risk_score = 0
        if acc['post_count'] >= 10: 
            risk_score += 3
        elif acc['post_count'] >= 5: 
            risk_score += 2
        
        if acc['high_risk_count'] >= 3: 
            risk_score += 3
        elif acc['high_risk_count'] >= 1: 
            risk_score += 1
        
        if repetition_rate > 30: 
            risk_score += 2  # High repetition
        
        # Only include if risk score >= 3
        if risk_score >= 3 and sample_post:
            risky_accounts.append({
                'account_id': str(account_id)[:40],
                'post_count': acc['post_count'],
                'high_risk_count': acc['high_risk_count'],
                'repetition_rate': round(repetition_rate, 1),
                'platforms': ', '.join(platforms) if platforms else 'Unknown',
                'recent_content': sample_post.original_text[:120] + '...' if sample_post.original_text else '—',
                'last_seen': acc['latest_post'].strftime('%Y-%m-%d') if acc['latest_post'] else '—',
                'risk_level': 'high' if acc['high_risk_count'] >= 2 else 'medium',
                'risk_score': risk_score
            })
    
    # Sort by risk score
    risky_accounts.sort(key=lambda x: x['risk_score'], reverse=True)
    
    return risky_accounts[:limit]

def get_platform_distribution(posts_queryset):
    """
    Simplified platform distribution matching Streamlit logic
    """
    # Get raw platform counts directly from database
    platform_counts = posts_queryset.values('platform').annotate(
        count=Count('id')
    ).order_by('-count')
    
    # Build a clean list (similar to Streamlit's value_counts)
    platform_data = []
    for item in platform_counts:
        platform_name = item['platform']
        count = item['count']
        
        # Skip invalid platforms
        if not platform_name or str(platform_name).lower() in ['nan', 'none', '', 'unknown', 'null']:
            continue
        
        # Normalize platform names (same logic as Streamlit's infer_platform_from_url)
        p_lower = str(platform_name).lower().strip()
        
        if p_lower in ['x', 'twitter', 't.co', 'x.com', 'twitter source', 'twitter.com']:
            normalized_name = 'X'
        elif p_lower in ['facebook', 'fb.watch', 'facebook.com', 'fb']:
            normalized_name = 'Facebook'
        elif p_lower in ['telegram', 't.me', 'tg']:
            normalized_name = 'Telegram'
        elif p_lower in ['tiktok', 'tik tok', 'tik-tok']:
            normalized_name = 'TikTok'
        elif p_lower in ['media', 'news', 'news/media', 'civicsignal']:
            normalized_name = 'Media'
        elif p_lower in ['youtube', 'youtu.be', 'yt']:
            normalized_name = 'YouTube'
        elif p_lower in ['instagram', 'insta', 'ig']:
            normalized_name = 'Instagram'
        else:
            normalized_name = str(platform_name).title()
        
        platform_data.append({
            'Platform': normalized_name,
            'Count': count
        })
    
    # Create DataFrame (matching Streamlit structure)
    df = pd.DataFrame(platform_data)
    
    if not df.empty:
        # Group by Platform and sum counts (in case of duplicates after normalization)
        df = df.groupby('Platform', as_index=False)['Count'].sum()
        df = df.sort_values('Count', ascending=False).reset_index(drop=True)
        top_platform = df.iloc[0]['Platform']
    else:
        df = pd.DataFrame(columns=['Platform', 'Count'])
        top_platform = "—"
    
    return df, top_platform
    
def get_top_hashtags(posts_queryset, limit=10):
    """Extract and rank top hashtags from post content"""
    import re
    from collections import Counter
    
    hashtags = []
    for post in posts_queryset:
        if post.original_text:
            # Extract hashtags (case-insensitive, handles #SouthAfrica, #SAElections, etc.)
            found = re.findall(r'#(\w+)', post.original_text)
            hashtags.extend([h.lower() for h in found if len(h) > 1])
    
    # Count and rank
    hashtag_counts = Counter(hashtags)
    return [
        {'tag': tag, 'count': count}
        for tag, count in hashtag_counts.most_common(limit)
        if count >= 2  # Only show hashtags used 2+ times
    ]

def get_pep_analysis_insights(posts_queryset, peps_queryset, extra_officials_list=None, limit=6):
    """
    Enhanced PEP analysis with Groq sentiment analysis and URL links.
    """
    from collections import defaultdict, Counter
    import re
    
    if not extra_officials_list:
        extra_officials_list = []
    
    # Build unified dictionary of names
    officials_to_scan = {}
    for pep in peps_queryset:
        name_lower = pep.name.lower().strip()
        if name_lower:
            officials_to_scan[name_lower] = pep.name
    
    for name in extra_officials_list:
        name_lower = name.lower().strip()
        if name_lower and name_lower not in officials_to_scan:
            officials_to_scan[name_lower] = name
    
    pep_mentions = defaultdict(lambda: {
        'count': 0,
        'platforms': Counter(),
        'hourly_distribution': Counter(),
        'hashtags': Counter(),
        'sample_posts': [],
        'critical_posts': [],  # NEW: Track critical posts separately
        'narrative_clusters': defaultdict(list),
        'sentiment_scores': [],  # NEW: Track sentiment scores
    })
    
    # Scan posts
    for post in posts_queryset[:5000]:
        if not post.original_text:
            continue
        text_lower = post.original_text.lower()
        
        for scan_name, display_name in officials_to_scan.items():
            if scan_name in text_lower:
                data = pep_mentions[display_name]
                data['count'] += 1
                
                platform = post.platform or 'Unknown'
                data['platforms'][platform] += 1
                
                if post.timestamp_share:
                    data['hourly_distribution'][post.timestamp_share.hour] += 1
                
                hashtags = re.findall(r'#(\w+)', post.original_text)
                data['hashtags'].update(hashtags)
                
                # Narrative clustering
                if any(kw in text_lower for kw in ['rigged', 'stolen', 'fraud', 'iec', 'ballot', 'tally', 'vote']):
                    data['narrative_clusters']['Election Integrity'].append(post.original_text[:100])
                if any(kw in text_lower for kw in ['zulu', 'xhosa', 'sotho', 'afrikaans', 'ethnic', 'kwerekwere', 'amapara', 'tribal']):
                    data['narrative_clusters']['Ethnic Dynamics'].append(post.original_text[:100])
                
                # Store sample post with URL
                if len(data['sample_posts']) < 5:
                    data['sample_posts'].append({
                        'text': post.original_text[:150],
                        'platform': platform,
                        'timestamp': post.timestamp_share,
                        'url': post.url if post.url else None,  # Include URL
                        'risk_level': post.risk_level if hasattr(post, 'risk_level') else 'medium'
                    })
                
                # Use Groq to analyze sentiment of this post
                try:
                    from groq import Groq
                    client = Groq(api_key=settings.GROQ_API_KEY)
                    
                    prompt = (
                        f"Analyze the sentiment of this post about {display_name}.\n"
                        f'Text: "{post.original_text[:200]}"\n\n'
                        "Is the sentiment Positive (supportive/praise), Negative (criticism/attack), or Neutral (factual/news)?\n"
                        "Reply with ONLY one word: Positive, Negative, or Neutral."
                    )
                    
                    response = client.chat.completions.create(
                        model="llama-3.3-70b-versatile",
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.1,
                        max_tokens=10
                    )
                    
                    sentiment = response.choices[0].message.content.strip().capitalize()
                    data['sentiment_scores'].append(sentiment)
                    
                    # Track critical posts separately
                    if sentiment == 'Negative' and len(data['critical_posts']) < 3:
                        data['critical_posts'].append({
                            'text': post.original_text[:200],
                            'platform': platform,
                            'timestamp': post.timestamp_share,
                            'url': post.url if post.url else None,
                        })
                    
                except Exception as e:
                    logger.error(f"Groq sentiment analysis failed: {e}")
                    data['sentiment_scores'].append('Neutral')
    
    # Build final results
    results = []
    for display_name, data in sorted(pep_mentions.items(), key=lambda x: x[1]['count'], reverse=True)[:limit]:
        if data['count'] < 2:
            continue
        
        # Calculate overall sentiment
        sentiment_counts = Counter(data['sentiment_scores'])
        total_analyzed = len(data['sentiment_scores'])
        
        if total_analyzed > 0:
            negative_pct = (sentiment_counts.get('Negative', 0) / total_analyzed) * 100
            positive_pct = (sentiment_counts.get('Positive', 0) / total_analyzed) * 100
            
            if negative_pct > 50:
                overall_sentiment = 'Negative'
                sentiment_label = '🔴 High Criticism'
            elif positive_pct > 50:
                overall_sentiment = 'Positive'
                sentiment_label = '🟢 Mostly Positive'
            else:
                overall_sentiment = 'Mixed'
                sentiment_label = '🟡 Mixed Sentiment'
        else:
            overall_sentiment = 'Neutral'
            sentiment_label = '⚪ Neutral'
        
        # Calculate risk score based on negative sentiment percentage
        risk_score = min(10, int(negative_pct / 10))
        
        total_posts = sum(data['platforms'].values())
        platform_breakdown = [
            {'name': plat, 'count': cnt, 'percent': round(cnt/total_posts*100)}
            for plat, cnt in data['platforms'].most_common(3)
        ]
        
        clusters = [{'name': name, 'count': len(posts)} for name, posts in data['narrative_clusters'].items()]
        
        results.append({
            'pep_name': display_name,
            'mention_count': data['count'],
            'platform_breakdown': platform_breakdown,
            'narrative_clusters': clusters,
            'sample_posts': data['sample_posts'],
            'critical_posts': data['critical_posts'],  
            'sentiment': overall_sentiment,
            'sentiment_label': sentiment_label,
            'risk_score': risk_score,
            'negative_percentage': round(negative_pct, 1),
        })
    
    return results 


def extract_first_json_array(text):
    """Extract the first valid JSON array from a string by matching brackets."""
    start = text.find('[')
    if start == -1:
        return None
    
    depth = 0
    in_string = False
    escape_next = False
    
    for i in range(start, len(text)):
        char = text[i]
        
        if escape_next:
            escape_next = False
            continue
            
        if char == '\\':
            escape_next = True
            continue
            
        if char == '"':
            in_string = not in_string
            continue
            
        if in_string:
            continue
            
        if char == '[':
            depth += 1
        elif char == ']':
            depth -= 1
            if depth == 0:
                return text[start:i+1]
    
    return None


def extract_new_trigger_terms_llm(text, existing_matches=None):
    """Use LLM to extract NEW trigger terms not in the lexicon."""
    if not text or len(text.strip()) < 20:
        return []
        
    existing_terms = set()
    if existing_matches:
        existing_terms = set(m['term'].lower() for m in existing_matches)
    for category, terms in CONFIG.get('lexicon', {}).items():
        existing_terms.update(t.lower() for t in terms.keys())
        
    existing_list = ', '.join(list(existing_terms)[:50])
    
    # Explicitly handles "Slur + Name" cases
    prompt = (
        "You are an expert hate speech analyst. Extract NEW trigger terms (slurs, threats, dehumanizing language, harmful adjectives) "
        "from this text that are NOT already in the lexicon.\n\n"
        "TEXT:\n"
        '"' + text + '"\n\n'
        "EXISTING TERMS (do not extract these):\n"
        + existing_list + "\n\n"
        "CRITICAL RULES:\n"
        "- 🚫 NEVER extract personal names, politicians, or specific individuals (e.g., 'Ramaphosa', 'Zuma', 'John Doe').\n"
        "- 🚫 NEVER extract neutral demographic terms like 'youth', 'women', 'workers' unless clearly used as a slur.\n"
        "- ✅ SEPARATE SLURS FROM NAMES: If a harmful adjective or slur is attached to a person's name (e.g., 'Fascist Ramaphosa', 'Thief John'), extract ONLY the harmful word or phrase (e.g., 'Fascist', 'Thief'). Do NOT include the person's name in the extracted term.\n"
        "- ✅ ONLY extract actual slurs, threats, dehumanizing phrases, or weaponized language.\n\n"
        "Return ONLY a valid JSON array. Each object must have:\n"
        '- "term": the exact phrase from the text\n'
        '- "category": one of [ethnic_identity, violence_incitement, dehumanizing, religious_cultural, gender_misogynistic, discriminatory_homophobic, socio_economic_caste, political_groups, foreign_interference, election_governance]\n'
        '- "severity": one of [low, medium, high, critical]\n'
        '- "target_entity": the group being targeted (e.g., Zulu, Xhosa, Women, Foreigners) or empty string\n'
        '- "language": one of [English, Zulu, Xhosa, Afrikaans, Sotho, Tswana, Venda, Tsonga, Ndebele, Swati, Pedi]\n'
        '- "confidence": A number between 0.0 and 1.0 representing your confidence\n\n'
        "EXAMPLE OUTPUT:\n"
        '[\n'
        '  {"term": "kwerekwere", "category": "ethnic_identity", "severity": "high", "target_entity": "Foreigners", "language": "Zulu", "confidence": 0.95}\n'
        ']\n\n'
        "If no new terms found, return: []"
    )
    
    try:
        response = safe_llm_call(prompt, max_tokens=1024)
        if not response:
            return []
            
        json_str = extract_first_json_array(response)
        if not json_str:
            logger.warning(f"No JSON array found in LLM response. Preview: {response[:200]}")
            return []
            
        try:
            extracted_terms = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON: {e}. Preview: {json_str[:200]}")
            return []
            
        if not isinstance(extracted_terms, list):
            return []
            
        valid_terms = []
        valid_categories = ['ethnic_identity', 'violence_incitement', 'dehumanizing',
                           'religious_cultural', 'gender_misogynistic', 'discriminatory_homophobic',
                           'socio_economic_caste', 'political_groups', 'foreign_interference',
                           'election_governance']
        valid_severities = ['low', 'medium', 'high', 'critical']
        valid_languages = ['Amharic', 'Oromo', 'English', 'Tigrinya', 'Somali']
        
        for term_data in extracted_terms:
            if not isinstance(term_data, dict):
                continue
                
            term = term_data.get('term', '').strip()
            if not term or len(term) < 2:
                continue
                
            if term.lower() in existing_terms:
                continue
                
            # Drop terms that look like standard English proper names
            if re.match(r'^[A-Za-z\s]+$', term) and term.istitle():
                logger.info(f"Filtered out likely name: '{term}'")
                continue
                
            category = term_data.get('category', 'uncategorized')
            if category not in valid_categories:
                category = 'uncategorized'
                
            severity = term_data.get('severity', 'medium')
            if severity not in valid_severities:
                severity = 'medium'
                
            language = term_data.get('language', 'Amharic')
            if language not in valid_languages:
                language = 'Amharic'
                
            confidence = term_data.get('confidence', 0.85)
            try:
                confidence = float(confidence)
                confidence = max(0.0, min(1.0, confidence))
            except (ValueError, TypeError):
                confidence = 0.85
                
            valid_terms.append({
                'term': term,
                'category': category,
                'severity': severity,
                'target_entity': term_data.get('target_entity', ''),
                'language': language,
                'confidence': round(confidence, 2),
                'source': 'LLM Extraction',
                'context': text[:300]
            })
            
        if valid_terms:
            logger.info(f"LLM extracted {len(valid_terms)} new trigger terms")
        return valid_terms
        
    except Exception as e:
        logger.error(f"Error in LLM trigger term extraction: {e}")
        return []
        
def export_new_terms_csv(request):
    """Export new trigger terms detected by LLM to CSV"""
    # Get terms from session
    new_terms = request.session.get('new_trigger_terms', [])
    
    if not new_terms:
        messages.warning(request, "No new terms to export. Please scan text first.")
        return redirect('lexicon_management')
    
    # Create CSV response
    response = HttpResponse(content_type='text/csv')
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    response['Content-Disposition'] = f'attachment; filename="new_trigger_terms_{timestamp}.csv"'
    
    # Write CSV
    writer = csv.writer(response)
    writer.writerow(['Term', 'Category', 'Severity', 'Target Entity', 'Language', 'Confidence'])
    
    for term in new_terms:
        writer.writerow([
            term.get('term', ''),
            term.get('category', ''),
            term.get('severity', ''),
            term.get('target_entity', ''),
            term.get('language', ''),
            term.get('confidence', '')
        ])
    
    messages.success(request, f"Exported {len(new_terms)} new trigger terms to CSV")
    return response
    
@login_required
def export_all_lexicon_terms_csv(request):
    """Export ALL lexicon terms (Database + CONFIG) as CSV"""
    import csv
    from django.http import HttpResponse
    from django.utils import timezone
    
    # 1. Get terms from Database
    db_terms = LexiconTerm.objects.filter(is_election_related=True).values(
        'term', 'category', 'severity', 'target_entity', 'language'
    )
    
    # 2. Get terms from CONFIG
    config_terms = []
    for category, terms in CONFIG.get('lexicon', {}).items():
        for term, metadata in terms.items():
            config_terms.append({
                'term': term,
                'category': category,
                'severity': metadata.get('severity', 'medium'),
                'target_entity': metadata.get('target_entity', ''),
                'language': metadata.get('language', 'english'),
            })
    
    # 3. Combine and deduplicate (DB terms take priority)
    seen_terms = set()
    all_terms = []
    
    # Add DB terms first
    for term in db_terms:
        if term['term'] not in seen_terms:
            seen_terms.add(term['term'])
            all_terms.append({
                'term': term['term'],
                'category': term['category'],
                'severity': term['severity'],
                'target_entity': term['target_entity'],
                'language': term['language'],
                'source': 'Database'
            })
    
    # Add CONFIG terms (skip duplicates)
    for term in config_terms:
        if term['term'] not in seen_terms:
            seen_terms.add(term['term'])
            all_terms.append({
                'term': term['term'],
                'category': term['category'],
                'severity': term['severity'],
                'target_entity': term['target_entity'],
                'language': term['language'],
                'source': 'CONFIG'
            })
    
    # 4. Create CSV response
    response = HttpResponse(content_type='text/csv')
    timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
    response['Content-Disposition'] = f'attachment; filename="all_lexicon_terms_{timestamp}.csv"'
    
    # Write CSV with UTF-8 BOM for Excel compatibility (important for SA languages like Zulu, Xhosa, Afrikaans)
    response.write('\ufeff')
    writer = csv.writer(response)
    writer.writerow(['Term', 'Category', 'Severity', 'Target Entity', 'Language', 'Source'])
    
    for term in all_terms:
        writer.writerow([
            term['term'],
            term['category'],
            term['severity'],
            term['target_entity'],
            term['language'],
            term['source']
        ])
    
    messages.success(request, f"Exported {len(all_terms)} lexicon terms to CSV")
    return response
    
def reports_landing(request):
    """Landing page showing all report categories as cards"""
    baseline_reports = MonitoringReport.objects.filter(report_category='baseline').order_by('-uploaded_at')[:3]
    situational_reports = MonitoringReport.objects.filter(report_category='situational').order_by('-uploaded_at')[:3]
    tiktok_reports = MonitoringReport.objects.filter(report_category='tiktok').order_by('-uploaded_at')[:3]
    
    # All reports for archive section
    all_reports = MonitoringReport.objects.all().order_by('-uploaded_at')[:6]
    
    context = {
        'baseline_reports': baseline_reports,
        'situational_reports': situational_reports,
        'tiktok_reports': tiktok_reports,
        'all_reports': all_reports,
        'active_tab': 'reports',
    }
    return render(request, 'dashboard/reports_landing.html', context)

def report_detail(request, report_id):
    """Individual report detail page"""
    report = get_object_or_404(MonitoringReport, id=report_id)
    
    context = {
        'report': report,
        'active_tab': 'reports',
    }
    return render(request, 'dashboard/report_detail.html', context)

def detect_significant_spikes(daily_data, dates, categories, threshold_std=2.0):
    """
    Detect significant spikes across the ENTIRE analysis period.
    FIXED: Handles std=0 (when previous days had 0 mentions) and adds global fallback.
    """
    spikes_detected = []
    for category in categories:
        values = [daily_data[day].get(category, 0) for day in dates]
        if len(values) < 7:
            continue
        
        # Calculate overall mean and std for the entire period (fallback)
        overall_mean = sum(values) / len(values)
        overall_std = (sum((x - overall_mean) ** 2 for x in values) / len(values)) ** 0.5
        
        rolling_window = min(7, len(values) // 3)
        if rolling_window < 3:
            rolling_window = 3
            
        for i, value in enumerate(values):
            if i < rolling_window:
                continue
            
            prev_values = values[i-rolling_window:i]
            mean = sum(prev_values) / len(prev_values)
            std = (sum((x - mean) ** 2 for x in prev_values) / len(prev_values)) ** 0.5
            
            is_spike = False
            z_score = 0
            magnitude = 0
            
            # 1. Check local spike (rolling window)
            if std > 0:
                z_score = (value - mean) / std
                if z_score >= threshold_std and value >= 5:
                    is_spike = True
                    magnitude = value / mean if mean > 0 else value
            elif value >= 5 and mean < 2:
                # If previous period was near 0 and now it's >= 5, it's a massive spike
                is_spike = True
                z_score = 10.0
                magnitude = value
                
            # 2. Fallback: Check global spike (overall period)
            # If local didn't catch it, but it's a massive spike compared to the whole period
            if not is_spike and overall_std > 0:
                global_z = (value - overall_mean) / overall_std
                if global_z >= 3.0 and value >= 10: # Stricter threshold for global
                    is_spike = True
                    z_score = global_z
                    magnitude = value / overall_mean if overall_mean > 0 else value
                    
            if is_spike:
                spikes_detected.append({
                    'category': category,
                    'date': dates[i],
                    'value': value,
                    'mean': round(mean, 2),
                    'std': round(std, 2),
                    'z_score': round(z_score, 2),
                    'spike_magnitude': round(magnitude, 2)
                })
                
    spikes_detected.sort(key=lambda x: x['z_score'], reverse=True)
    return spikes_detected

def get_category_trend_analysis(posts_queryset, days_back=90, cache_suffix="all"):
    """
    OPTIMIZED: Uses random sampling and caching to prevent crashes on large datasets.
    NOW: Detects spikes across entire timeline, not just last 7 days.
    """
    # 1. CHECK CACHE FIRST
    cache_key = f"trend_analysis_{days_back}_{cache_suffix}"
    cached_result = cache.get(cache_key)
    if cached_result:
        return cached_result
    
    # 2. GET DATE RANGE FROM QUERYSET (Respect user's filter)
    date_range = posts_queryset.aggregate(
        min_date=Min('timestamp_share'),
        max_date=Max('timestamp_share')
    )
    
    # Respect the UI's date filter (posts_queryset is already filtered by the view)
    recent_posts = posts_queryset.filter(
        original_text__isnull=False
    ).exclude(original_text='')
    
    total_available = recent_posts.count()
    
    if total_available < 10:
        return {
            'chart_json': None,
            'trending_categories': [],
            'total_categories_tracked': 0,
            'total_posts_scanned': total_available,
            'spikes_detected': [],
            'spike_alerts': [],
            'message': 'Not enough data'
        }
    
    # 3. RANDOM SAMPLING (Increased to 5000 posts)
    sample_size = min(5000, total_available)  # Changed from 1500 to 5000
    post_ids = list(recent_posts.values_list('id', flat=True))
    sampled_ids = random.sample(post_ids, sample_size)
    
    # Fetch only the sampled posts
    posts_to_scan = ProcessedPost.objects.filter(id__in=sampled_ids).iterator()
    
    # 4. SCAN ONLY THE SAMPLE
    daily_data = defaultdict(lambda: defaultdict(int))
    total_scanned = 0
    for post in posts_to_scan:
        if post.original_text and post.timestamp_share:
            # This now includes both lexicon AND LLM-based detection
            matches = scan_text_for_lexicon_terms(post.original_text)
            day = post.timestamp_share.date()
            for match in matches:
                daily_data[day][match['category']] += 1
            total_scanned += 1
    
    if not daily_data:
        return {
            'chart_json': None,
            'trending_categories': [],
            'total_categories_tracked': 0,
            'total_posts_scanned': total_scanned,
            'spikes_detected': [],
            'spike_alerts': [],
            'message': 'No lexicon matches found'
        }
    
    # 5. BUILD CHART DATA
    dates = sorted(daily_data.keys())
    categories = set()
    for day_data in daily_data.values():
        categories.update(day_data.keys())
    
    traces = []
    category_colors = {
        'violence_incitement': '#dc2626', 'dehumanizing': '#ea580c',
        'ethnic_identity': '#d97706', 'political_groups': '#65a30d',
        'election_governance': '#0284c7', 'foreign_interference': '#7c3aed',
        'religious_cultural': '#db2777'
    }
    
    for category in sorted(categories):
        values = [daily_data[day].get(category, 0) for day in dates]
        traces.append({
            'x': [d.strftime('%Y-%m-%d') for d in dates],
            'y': values,
            'type': 'scatter', 'mode': 'lines+markers',
            'name': category.replace('_', ' ').title(),
            'line': {'color': category_colors.get(category, '#6B7280'), 'width': 2.5},
            'marker': {'size': 4}
        })
    
    chart_json = json.dumps({
        'data': traces,
        'layout': {
            'title': f'Weaponized Language Trends (Sampled {total_scanned} posts)',
            'xaxis': {'title': 'Date', 'tickangle': -45, 'gridcolor': '#E5E7EB'},
            'yaxis': {'title': 'Daily Mentions', 'gridcolor': '#E5E7EB'},
            'hovermode': 'x unified', 'height': 450,
            'margin': {'b': 100, 't': 50, 'l': 60, 'r': 20},
            'plot_bgcolor': '#ffffff', 'paper_bgcolor': '#ffffff',
            'legend': {'orientation': 'h', 'yanchor': 'bottom', 'y': 1.02, 'xanchor': 'center', 'x': 0.5}
        }
    })
    
    # 6. DETECT SPIKES ACROSS ENTIRE TIMELINE
    spikes_detected = detect_significant_spikes(daily_data, dates, categories, threshold_std=2.0)
    
    # Generate spike alerts for UI
    spike_alerts = []
    if spikes_detected:
        for spike in spikes_detected[:10]:  # Top 10 spikes
            category_display = spike['category'].replace('_', ' ').title()
            date_str = spike['date'].strftime('%b %d')
            magnitude = spike['spike_magnitude']
            
            if magnitude >= 3.0:
                alert_level = "🚨 Critical"
            elif magnitude >= 2.0:
                alert_level = "⚠️ High"
            else:
                alert_level = "⚡ Moderate"
            
            spike_alerts.append({
                'message': f"{alert_level} {category_display}: {spike['value']} mentions on {date_str} ({magnitude}x above average)",
                'category': category_display,
                'date': date_str,
                'value': spike['value'],
                'magnitude': magnitude,
                'alert_level': alert_level
            })
    
    # 7. CALCULATE TRENDS (Last 7 days vs previous period)
    trending = []
    if len(dates) >= 7:
        recent_7 = dates[-7:]
        previous_period = dates[:-7] if len(dates) > 7 else dates
        
        for category in categories:
            recent_count = sum(daily_data[d].get(category, 0) for d in recent_7)
            recent_avg = recent_count / 7
            
            previous_avg = 0
            if previous_period:
                previous_count = sum(daily_data[d].get(category, 0) for d in previous_period)
                previous_avg = previous_count / len(previous_period)
            
            if previous_avg > 0:
                pct_change = ((recent_avg - previous_avg) / previous_avg) * 100
            elif recent_avg > 0:
                pct_change = 100
            else:
                pct_change = 0
            
            if pct_change > 15 and recent_count >= 2:
                trending.append({
                    'category': category.replace('_', ' ').title(),
                    'pct_change': round(pct_change, 1),
                    'recent_count': recent_count,
                    'severity': _get_category_severity_display(category),
                })
        
        trending.sort(key=lambda x: x['pct_change'], reverse=True)
    
    # 8. BUILD FINAL RESULT
    final_result = {
        'chart_json': chart_json,
        'trending_categories': trending[:5],
        'total_categories_tracked': len(categories),
        'total_posts_scanned': total_scanned,
        'date_range': f"{dates[0].strftime('%b %d')} - {dates[-1].strftime('%b %d, %Y')}" if dates else "",
        'spikes_detected': spikes_detected,
        'spike_alerts': spike_alerts,
        'total_spikes': len(spikes_detected),
        'has_significant_spikes': len(spikes_detected) > 0
    }
    
    # 9. SAVE TO CACHE FOR 60 MINUTES
    cache.set(cache_key, final_result, 3600)
    
    return final_result
       
def _get_category_severity_display(category):
    """Map category to severity level for display"""
    severity_map = {
        'violence_incitement': 'critical',
        'dehumanizing': 'high',
        'ethnic_identity': 'medium',
        'political_groups': 'medium',
        'election_governance': 'low',
        'foreign_interference': 'medium',
        'religious_cultural': 'low'
    }
    return severity_map.get(category, 'medium')
    

def batch_translate_terms_llm(terms_list):
    """
    Use LLM to translate a batch of Amharic/Oromo terms to English with context.
    Uses caching to avoid redundant API calls and improve page load speed.
    """
    if not terms_list:
        return {}
    
    # Check cache first (Cache for 7 days)
    cache_key = "llm_amharic_oromo_translations_v1"
    cached_translations = cache.get(cache_key, {})
    
    # Filter out terms we already translated
    terms_to_translate = [t for t in terms_list if t not in cached_translations]
    
    if not terms_to_translate:
        return cached_translations
    
    # Limit to 50 terms per batch to avoid token limits
    terms_to_translate = terms_to_translate[:50]
    terms_str = "\n".join([f"- {t}" for t in terms_to_translate])
    
    # FIXED PROMPT: Corrected historical context and accurate socio-political/harmful nuance mapping
    prompt = f"""You are an expert linguist specializing in South African languages (Zulu, Xhosa, Afrikaans, Sotho, Tswana, Venda, Tsonga, Ndebele, Swati, Pedi, English) and hate speech/information operations analysis.

These terms have been FLAGGED as potentially harmful in social media posts. Translate them into English, focusing on:
1. The literal meaning
2. How it's weaponized as an INSULT, SLUR, or DEROGATORY term in socio-political tensions (this is CRITICAL)
3. The targeted demographic group or impact

DO NOT provide neutral dictionary definitions. These are being used as narrative weapons.

Terms to translate:
{terms_str}

Return ONLY a valid JSON object where:
- Keys are the exact original terms
- Values are translations that accurately explain the DEROGATORY/OFFENSIVE usage

Example Output:
{{
  "kwerekwere": "Literal: Onomatopoeic term mocking foreign languages. Weaponized use: A highly charged xenophobic slur used to target, dehumanize, and incite violence against foreign nationals in South Africa.",
  "isifebe": "Literal: Prostitute / whore. Weaponized use: A highly offensive, derogatory misogynistic slur used to demean, humiliate, and target women.",
  "meid": "Literal: Maid / domestic worker. Weaponized use: Used derogatorily in Afrikaans to belittle, diminish status, or imply subservience, carrying heavy apartheid-era connotations.",
  "kaffermeid": "Literal: Racial slur + maid. Weaponized use: A severely demeaning, racist, and misogynistic slur combining racial degradation with gendered subjugation."
}}
"""
    try:
        # Use the existing safe_llm_call utility from your codebase
        response = safe_llm_call(prompt, max_tokens=1024)
        if not response:
            return cached_translations
        
        # Strip codeblock wrappers if returned by the LLM
        response_text = response.strip()
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0]
            
        # Extract JSON from response
        json_match = re.search(r'\{.*\}', response_text.strip(), re.DOTALL)
        if json_match:
            new_translations = json.loads(json_match.group(0))
            # Update cache
            cached_translations.update(new_translations)
            cache.set(cache_key, cached_translations, 86400 * 7) # Cache for 7 days
            return cached_translations
        else:
            logger.warning("LLM translation response did not contain valid JSON.")
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse LLM translation JSON: {e}")
    except Exception as e:
        logger.error(f"LLM batch translation failed: {e}")
        
    return cached_translations
    
def analyze_pep_sentiment_groq(sample_texts, pep_name):
    """Use Groq to analyze the actual sentiment/criticality of posts mentioning a PEP"""
    if not sample_texts:
        return "Neutral"

    if not getattr(settings, 'GROQ_API_KEY', ''):
        return "Neutral"
    
    try:
        from groq import Groq
        client = Groq(api_key=settings.GROQ_API_KEY)
        
        combined_text = " | ".join([t[:200] for t in sample_texts[:3]])
        
        # Focus on whether the posts are critical/negative in tone
        prompt = (
            f"Analyze these social media posts that mention {pep_name}.\n\n"
            f'Posts: "{combined_text}"\n\n'
            "Determine the overall tone:\n"
            "- 'Negative' if the posts contain criticism, accusations, attacks, or negative claims (even if directed at someone else)\n"
            "- 'Positive' if the posts are supportive, praising, or positive about the person mentioned\n"
            "- 'Mixed' if there's both positive and negative content\n"
            "- 'Neutral' if it's just factual reporting with no emotional tone\n\n"
            "Reply with ONLY one word: Positive, Negative, Mixed, or Neutral."
        )

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=10
        )
        
        sentiment = response.choices[0].message.content.strip().capitalize()
        if sentiment in ['Positive', 'Negative', 'Mixed', 'Neutral']:
            return sentiment
        return "Neutral"
        
    except Exception as e:
        logger.error(f"Groq sentiment analysis failed for {pep_name}: {e}")
        return "Neutral"

def get_tfgbv_lexicon_terms():
    """
    Load all TFGBV-related terms from the lexicon database.
    Returns a list of (term, severity, target_entity) tuples.
    """
    tfgbv_categories = [
        'gender_misogynistic',
        'discriminatory_homophobic',
    ]
    
    terms = []
    
    # Try loading from Django model first
    try:
        from dashboard.models import LexiconTerm
        tfgbv_terms = LexiconTerm.objects.filter(
            category__in=tfgbv_categories,
            is_active=True
        ).values_list('term', 'severity', 'category')
        
        for term, severity, category in tfgbv_terms:
            terms.append({
                'term': term.lower().strip(),
                'severity': severity,
                'category': category,
            })
    except Exception:
        pass
    
    # Fallback: Load from settings if model is empty
    if not terms:
        lexicon = getattr(settings, 'HATE_SPEECH_LEXICON', {})
        for category in tfgbv_categories:
            category_terms = lexicon.get(category, {})
            for term, meta in category_terms.items():
                terms.append({
                    'term': term.lower().strip(),
                    'severity': meta.get('severity', 'medium'),
                    'category': category,
                })
    
    return terms


def detect_tfgbv_in_text(text, tfgbv_terms):
    """
    Scan text against verified TFGBV lexicon terms.
    Returns list of matched terms with details.
    Only flags ACTUAL hate speech terms, not innocent mentions.
    """
    if not text or not tfgbv_terms:
        return []
    
    text_lower = text.lower()
    matches = []
    
    for entry in tfgbv_terms:
        term = entry['term']
        if not term:
            continue
        
        # Exact match check (term appears in text)
        if term in text_lower:
            matches.append({
                'term': term,
                'severity': entry['severity'],
                'category': entry['category'],
            })
    
    return matches


def get_enhanced_pep_analysis(posts_queryset, peps_queryset, limit=8):
    """
    Enhanced PEP analysis – scans all posts, correct sentiment, real critical posts.
    """
    pep_names = {pep.name.lower().strip(): pep for pep in peps_queryset if pep.name}
    if not pep_names:
        return []
 
    try:
        tfgbv_terms = get_tfgbv_lexicon_terms()
    except Exception:
        tfgbv_terms = []
 
    # Word-boundary aware critical / positive keyword check
    _CRIT_RE = re.compile(
        r'\b(' + '|'.join(re.escape(k) for k in [
            'kill', 'attack', 'hate', 'enemy', 'genocide', 'massacre',
            'slaughter', 'destroy', 'eliminate', 'terrorist', 'extremist',
            'traitor', 'criminal', 'dictator', 'oppressor', 'failed state',
            'kwerekwere', 'amapara', 'kaffer', 'hotnot', 'shisa', 'dudula',
            'corrupt', 'thief', 'betrayal', 'conspiracy', 'rigged', 'fraud',
            'stolen', 'incompetent', 'useless', 'worthless', 'disgrace',
        ]) + r')\b',
        re.IGNORECASE,
    )
    # ... (keep _POS_RE exactly as it is) ...
    _HOSTILE_RE = re.compile(
        r'(down with|blood on hands|puppet of|destroying the country|step down|shisa amakwerekwere|dudula|burn them out)',
        re.IGNORECASE,
    )
    _POS_RE = re.compile(
        r'\b(' + '|'.join(re.escape(k) for k in [
            'congratulate', 'support', 'praise', 'excellent', 'wonderful',
            'thank you', 'appreciate', 'success', 'victory', 'progress',
            'achievement', 'proud', 'honor', 'respect', 'admire', 'inspire',
            'peace', 'unity', 'hope', 'together', 'strong', 'vision',
            'democracy', 'freedom', 'justice', 'fair', 'honest', 'trust',
        ]) + r')\b',
        re.IGNORECASE,
    )
    _HOSTILE_RE = re.compile(
        r'(down with|ውድቀት ለ|ይውረድ|blood on hands|ደምናችሁ|ደም ያፈሰሰ'
        r'|puppet of|የ\w+ ተላላኪ|destroying the country|አገር አፈራሽ'
        r'|step down|ስልጣን ልቀቅ|ልቀቁ)',
        re.IGNORECASE,
    )
 
    pep_data = defaultdict(lambda: {
        'count': 0,
        'platforms': Counter(),
        'hourly_distribution': Counter(),
        'hashtags': Counter(),
        'is_gendered_target': False,
        'tfgbv_matches': [],
        'tfgbv_post_count': 0,
        'narrative_clusters': defaultdict(list),
        'critical_posts': [],      # up to 20 confirmed negative posts
        'positive_posts': [],
        'sample_texts': [],        # first 50 texts for Groq
        'negative_count': 0,
        'positive_count': 0,
        'neutral_count': 0,
    })
 
    # ── Scan ALL posts with iterator (memory efficient) ───────────────────
    total_scanned = 0
    for post in posts_queryset.iterator(chunk_size=500):
        if not post.original_text:
            continue
        total_scanned += 1
        text = post.original_text
        text_lower = text.lower()
 
        for pep_name, pep_obj in pep_names.items():
            if pep_name not in text_lower:
                continue
 
            d = pep_data[pep_obj.name]
            d['count'] += 1
 
            platform = post.platform or 'Unknown'
            d['platforms'][platform] += 1
            if post.timestamp_share:
                d['hourly_distribution'][post.timestamp_share.hour] += 1
            for h in re.findall(r'#(\w+)', text):
                d['hashtags'][h] += 1
 
            # Narrative clusters
            if any(kw in text_lower for kw in ['rigged', 'stolen', 'fraud', 'iec', 'ballot', 'tally', 'vote']):
                d['narrative_clusters']['Election Integrity'].append(text[:100])
            if any(kw in text_lower for kw in ['zulu', 'xhosa', 'sotho', 'afrikaans', 'ethnic', 'kwerekwere', 'amapara', 'tribal']):
                d['narrative_clusters']['Ethnic Dynamics & Conflict'].append(text[:100])
            if any(kw in text_lower for kw in ['corrupt', 'corruption', 'bribe', 'embezzle', 'steal', 'misuse', 'abuse', 'tenderpreneur', 'state capture', 'loot']):
                d['narrative_clusters']['Corruption & Accountability'].append(text[:100])
                
            # TFGBV check
            if tfgbv_terms:
                hits = detect_tfgbv_in_text(text, tfgbv_terms)
                if hits:
                    d['is_gendered_target'] = True
                    d['tfgbv_post_count'] += 1
                    for hit in hits:
                        if not any(m['term'] == hit['term']
                                   for m in d['tfgbv_matches']):
                            d['tfgbv_matches'].append(hit)
 
            # Sentiment scoring
            crit_hits = len(_CRIT_RE.findall(text))
            pos_hits  = len(_POS_RE.findall(text))
            hostile   = bool(_HOSTILE_RE.search(text))
 
            is_negative = (crit_hits > pos_hits and crit_hits > 0) or hostile
            is_positive = (pos_hits > crit_hits and pos_hits > 0) and not hostile
 
            post_dict = {
                'text':       text[:250],
                'platform':   platform,
                'timestamp':  post.timestamp_share,
                'risk_level': getattr(post, 'risk_level', 'medium') or 'medium',
                'url': (post.url if post.url
                        and str(post.url).startswith('http') else None),
            }
 
            if is_negative:
                d['negative_count'] += 1
                if len(d['critical_posts']) < 20:
                    d['critical_posts'].append(post_dict)
            elif is_positive:
                d['positive_count'] += 1
                if len(d['positive_posts']) < 10:
                    d['positive_posts'].append(post_dict)
            else:
                d['neutral_count'] += 1
 
            # Collect sample texts for Groq (first 50 per PEP)
            if len(d['sample_texts']) < 50:
                d['sample_texts'].append(text[:300])
 
    logger.info(f"get_enhanced_pep_analysis: scanned {total_scanned} posts")
 
    # ── Build results ──────────────────────────────────────────────────────
    results = []
    for pep_name, d in sorted(
            pep_data.items(), key=lambda x: x[1]['count'], reverse=True
    )[:limit]:
        if d['count'] < 2:
            continue
 
        total_analyzed = d['negative_count'] + d['positive_count'] + d['neutral_count']
        if total_analyzed == 0:
            continue
 
        neg_pct = d['negative_count'] / total_analyzed * 100
        pos_pct = d['positive_count'] / total_analyzed * 100
 
        # Call Groq ONCE per PEP using a representative sample
        try:
            groq_sentiment = analyze_pep_sentiment_groq(
                d['sample_texts'][:10], pep_name
            )
        except Exception:
            groq_sentiment = None
 
        # Prefer Groq if available, fall back to lexicon counts
        if groq_sentiment in ('Positive', 'Negative', 'Mixed', 'Neutral'):
            overall_sentiment = groq_sentiment
        else:
            if neg_pct > 40:
                overall_sentiment = 'Negative'
            elif pos_pct > 40:
                overall_sentiment = 'Positive'
            elif neg_pct > 20:
                overall_sentiment = 'Mixed'
            else:
                overall_sentiment = 'Neutral'
 
        # Risk score
        risk_score = {'Negative': 8, 'Mixed': 5,
                      'Positive': 2, 'Neutral': 3}.get(overall_sentiment, 3)
        if d['tfgbv_post_count'] > 0:
            risk_score = min(10, risk_score + 2)
        if len(d['critical_posts']) >= 5:
            risk_score = min(10, risk_score + 1)
 
        sentiment_label = {
            'Negative': f'🔴 High Criticism ({neg_pct:.0f}% negative)',
            'Mixed':    f'🟡 Mixed Sentiment (Neg: {neg_pct:.0f}%, Pos: {pos_pct:.0f}%)',
            'Positive': f'🟢 Mostly Positive ({pos_pct:.0f}% positive)',
            'Neutral':  f'⚪ Mostly Neutral',
        }.get(overall_sentiment, '⚪ Neutral')
 
        total_posts = sum(d['platforms'].values())
        platform_breakdown = [
            {'name': pl, 'count': cnt,
             'percent': round(cnt / total_posts * 100) if total_posts else 0}
            for pl, cnt in d['platforms'].most_common(3)
        ]
 
        peak_hour = d['hourly_distribution'].most_common(1)
        peak_hour = peak_hour[0][0] if peak_hour else 0
 
        # sample_posts: show critical ones first, then pad with others
        sample_posts = []
        for p in d['critical_posts'][:3]:
            sample_posts.append({**p, 'sentiment': 'negative'})
        for p in d['positive_posts'][:2]:
            if len(sample_posts) < 5:
                sample_posts.append({**p, 'sentiment': 'positive'})
 
        results.append({
            'pep_name':          pep_name,
            'mention_count':     d['count'],
            'platform_breakdown': platform_breakdown,
            'velocity_alert':    peak_hour in [0, 1, 2, 3, 4, 5],
            'peak_hour':         peak_hour,
            'top_hashtags':      [
                {'tag': t, 'count': c}
                for t, c in d['hashtags'].most_common(5) if c > 1
            ],
            'bot_score':         0,
            'bot_level':         '🟢 Low (Likely Human)',
            'is_gendered_target': d['is_gendered_target'],
            'tfgbv_matches':     d['tfgbv_matches'],
            'tfgbv_post_count':  d['tfgbv_post_count'],
            'narrative_clusters': [
                {'name': n, 'count': len(posts)}
                for n, posts in d['narrative_clusters'].items()
            ],
            'sample_posts':      sample_posts,
            'critical_posts':    d['critical_posts'][:5],
            'risk_score':        risk_score,
            'sentiment':         overall_sentiment,
            'sentiment_label':   sentiment_label,
            'sentiment_breakdown': {
                'negative': round(neg_pct, 1),
                'positive': round(pos_pct, 1),
                'neutral':  round(100 - neg_pct - pos_pct, 1),
            },
            'negative_post_count': d['negative_count'],
            'positive_post_count': d['positive_count'],
            'neutral_post_count':  d['neutral_count'],
        })
 
    return results

_INNOCUOUS_RE = re.compile(
    r'\b(peace|democracy|award|commend|congratulate|showcase|celebrate'
    r'|civic|positive|progress|development|strong|engage|youth|student'
    r'|burundi|kenya|foreign observer|international|observer|commend'
    r'|general election|voting|polling station|civic spirit|youth engagement'
    r'|step towards democracy|democratic)\b',
    re.IGNORECASE,
)
 
 
def _is_genuine_match(term: str, text_lower: str, severity: str,
                       is_innocuous_context: bool) -> bool:
    """
    Return True only when the term is a genuine harmful match.
 
    Rules:
    1. ASCII terms must appear as whole words (word-boundary check).
       This prevents 'war' matching 'award', 'forward', 'cowardly'.
    2. Low/medium-severity terms are suppressed in innocuous contexts.
    3. Non-ASCII (Amharic/Oromo) terms use substring matching as before.
    """
    # Non-ASCII (Ge'ez / Ethiopic script) — substring OK
    if re.search(r'[^\x00-\x7F]', term):
        if term.lower() not in text_lower:
            return False
        # Still suppress low-severity in innocuous context
        if is_innocuous_context and severity in ('low',):
            return False
        return True
 
    # ASCII term — require word boundary
    if not re.search(r'\b' + re.escape(term.lower()) + r'\b', text_lower):
        return False
 
    # Suppress low/medium severity in clearly positive/neutral posts
    if is_innocuous_context and severity in ('low', 'medium'):
        return False
 
    return True
    
class BaseTabMixin:
    """Adds consistent navigation tabs to any class-based view"""
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Define tabs once, use everywhere (NO EMOJIS as per strict design rules)
        context['tabs'] = [
            {'name': 'Home', 'url_name': 'home', 'icon': ''},
            {'name': 'Candidates', 'url_name': 'peps', 'icon': ''},
            {'name': 'Mapped Lexicons', 'url_name': 'lexicons', 'icon': ''},
            {'name': 'Trending Narratives', 'url_name': 'narratives', 'icon': ''},
            {'name': 'Networks & TTPs', 'url_name': 'networks', 'icon': ''},
            {'name': 'Lexicon Management', 'url_name': 'lexicon_management', 'icon': ''},
            {'name': 'Reports', 'url_name': 'reports_landing', 'icon': ''},
        ]
        # Set active_tab if not already set
        if 'active_tab' not in context:
            context['active_tab'] = self.request.resolver_match.url_name
        return context

# ==========================================
# CLASS-BASED VIEWS (SA ELECTIONS)
# ==========================================

class HomeView(TemplateView):
    template_name = 'dashboard/home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['active_tab'] = 'home'
        
        # 1. Get filtered posts
        posts, start_date, end_date = get_election_posts_queryset(self.request)
        
        # 2. Calculate Metrics
        total_posts = posts.count()
        unique_accounts = posts.values('account_id').distinct().count()
        
        # Top Platform
        platform_counts = posts.values('platform').annotate(count=Count('id')).order_by('-count')
        top_platform = platform_counts[0]['platform'] if platform_counts.exists() else 'N/A'
        
        # Alert Level (Simple logic based on high/critical risk posts)
        high_risk_count = posts.filter(risk_level__in=['high', 'critical']).count()
        if high_risk_count > 500:
            alert_level = 'Critical'
        elif high_risk_count > 100:
            alert_level = 'High'
        elif high_risk_count > 20:
            alert_level = 'Medium'
        else:
            alert_level = 'Low'
            
        context['metrics'] = {
            'total_posts': total_posts,
            'unique_accounts': unique_accounts,
            'top_platform': top_platform,
            'alert_level': alert_level,
            'last_update': timezone.now().strftime('%d %b %Y, %H:%M'),
        }
        
        # 3. Generate Charts (Plotly JSON)
        # Note: You need to implement the specific Plotly logic here or use your existing chart helpers
        # For now, we pass empty dicts to prevent errors if helpers are missing
        context['charts'] = {
            'platform': {}, # Replace with plotly_platform_chart(posts)
            'risk': {},     # Replace with plotly_risk_chart(posts)
            'daily': {},    # Replace with plotly_daily_chart(posts)
        }
        
        # 4. Risk Actors
        context['risk_actors'] = get_risk_actors_insight(posts, limit=5)
        
        return context


class NarrativesView(TemplateView):
    template_name = 'dashboard/narratives.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['active_tab'] = 'narratives'
        
        posts, start_date, end_date = get_election_posts_queryset(self.request)
        
        # Use the SA-specific summary function
        summaries = get_sa_summaries(posts, max_clusters=10)
        
        context['summaries'] = summaries
        context['total_clusters'] = len(summaries)
        
        return context


class LexiconsView(TemplateView):
    template_name = 'dashboard/lexicons.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['active_tab'] = 'lexicons'
        
        posts, start_date, end_date = get_election_posts_queryset(self.request)
        
        # Get lexicon matches
        matches = PostLexiconMatch.objects.filter(post__in=posts).select_related('post')
        
        # Category breakdown
        category_counts = matches.values('category').annotate(count=Count('id')).order_by('-count')
        
        # Top terms
        term_counts = matches.values('term', 'category', 'severity').annotate(count=Count('id')).order_by('-count')[:20]
        
        context['matches'] = matches[:100] # Limit for performance
        context['categories'] = category_counts
        context['top_terms'] = term_counts
        context['total_matches'] = matches.count()
        
        return context


class NetworksView(TemplateView):
    template_name = 'dashboard/networks.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['active_tab'] = 'networks'
        
        posts, start_date, end_date = get_election_posts_queryset(self.request)
        
        # Get coordination groups
        coordination_groups = get_coordination_groups(posts, min_accounts=3, max_groups=15)
        
        # Analyze TTPs
        ttps = analyze_ttps(coordination_groups, posts)
        
        # Network Graph Data
        # Note: Ensure generate_network_graph_from_groups is defined in your file
        try:
            graph_data = generate_network_graph_from_groups(coordination_groups)
        except Exception as e:
            logger.error(f"Graph generation failed: {e}")
            graph_data = {'nodes': [], 'edges': []}
            
        context['coordination_groups'] = coordination_groups
        context['ttps'] = ttps
        context['graph_data'] = json.dumps(graph_data)
        context['total_groups'] = len(coordination_groups)
        
        return context


class PEPsView(TemplateView):
    """
    SA Municipal Candidates View.
    Replaces the Ethiopia HoPR/RC structure.
    """
    template_name = 'dashboard/peps.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['active_tab'] = 'peps'
        
        # TODO: Implement the Excel file reading logic here
        # as discussed in the "Candidates Registry" requirements.
        # For now, we pass empty context to prevent crashes.
        
        context['candidates_count'] = 0
        context['municipalities_count'] = 257
        
        return context


class LexiconManagementView(TemplateView):
    template_name = 'dashboard/lexicon_management.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['active_tab'] = 'lexicon_management'
        
        # Get all terms from DB
        terms = LexiconTerm.objects.all().order_by('-created_at')[:100]
        context['terms'] = terms
        
        return context

    def post(self, request, *args, **kwargs):
        # Handle Add/Edit/Delete logic here
        action = request.POST.get('action')
        
        if action == 'add':
            term = request.POST.get('term')
            category = request.POST.get('category')
            severity = request.POST.get('severity')
            language = request.POST.get('language', 'English')
            
            if term and category:
                LexiconTerm.objects.create(
                    term=term,
                    category=category,
                    severity=severity,
                    language=language,
                    is_election_related=True
                )
                messages.success(request, f"Term '{term}' added successfully.")
                
        elif action == 'delete':
            term_id = request.POST.get('term_id')
            LexiconTerm.objects.filter(id=term_id).delete()
            messages.success(request, "Term deleted.")
            
        return redirect('lexicon_management')

class UploadDataView(TemplateView):
    """UI for uploading CSV files - handles both GET and POST"""
    template_name = 'dashboard/upload_data.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['active_tab'] = 'upload'
        context['recent_uploads'] = DataUpload.objects.order_by('-uploaded_at')[:10]
        return context
    
    def post(self, request, *args, **kwargs):
        """Handle file upload via POST"""
        import os
        import uuid
        from django.utils import timezone
        from django.core.cache import cache
        
        logger.info(f"📥 Upload request: data_type={request.POST.get('data_type')}, source={request.POST.get('source_name')}")
        logger.info(f"📁 FILES: {list(request.FILES.keys())}")
        log_event(
            "Upload request received",
            event_type="upload",
            status="started",
            actor=request.user.username if request.user.is_authenticated else "anonymous",
            request_path=request.path,
            metadata={
                "data_type": request.POST.get("data_type"),
                "source_name": request.POST.get("source_name"),
                "file_count": len(request.FILES.getlist("csv_files")),
            },
        )
        
        uploaded_files = request.FILES.getlist('csv_files')
        if not uploaded_files:
            messages.error(request, "No files received.")
            return redirect('upload_data')
        
        results = []
        for uploaded_file in uploaded_files:
            try:
                # Generate unique filename to avoid conflicts
                unique_id = uuid.uuid4().hex[:8]
                timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
                original_name = uploaded_file.name
                name_without_ext = os.path.splitext(original_name)[0]
                ext = os.path.splitext(original_name)[1]
                
                # Create unique filename: originalname_timestamp_uniqueid.ext
                unique_filename = f"{name_without_ext}_{timestamp}_{unique_id}{ext}"
                
                # Save file with unique name
                file_path = default_storage.save(f'uploads/{unique_filename}', uploaded_file)
                full_path = os.path.join(settings.MEDIA_ROOT, file_path)
                
                logger.info(f"🔄 Processing: {original_name} -> {unique_filename}")
                
                # Create upload record
                upload = DataUpload.objects.create(
                    uploaded_file=file_path,
                    original_filename=original_name,
                    uploaded_by=request.user.username if request.user.is_authenticated else 'anonymous',
                    data_type=request.POST.get('data_type', 'custom'),
                    status='processing'
                )
                
                # Process the file
                success, message, count = process_uploaded_csv(
                    full_path, 
                    upload.data_type, 
                    request.POST.get('source_name', 'User Upload')
                )
                
                # Update record
                upload.status = 'completed' if success else 'failed'
                upload.processing_log = message
                upload.records_processed = count if success else 0
                upload.save()
                
                results.append((original_name, success, message, count))
                logger.info(f"{'✅' if success else '❌'} {original_name}: {message}")
                
            except Exception as e:
                logger.error(f"❌ Upload failed for {uploaded_file.name}: {str(e)}", exc_info=True)
                results.append((uploaded_file.name, False, str(e), 0))
        
        
        
        # Show summary in UI
        success_count = sum(1 for _, s, _, _ in results if s)
        if success_count > 0:
            queue_dashboard_analytics_refresh(skip_narratives=True)
        if success_count == len(uploaded_files):
            messages.success(request, f"✅ All {len(uploaded_files)} files processed successfully!")
        elif success_count > 0:
            messages.warning(request, f"⚠️ {success_count}/{len(uploaded_files)} succeeded. Check logs for errors.")
        else:
            messages.error(request, "❌ Failed to process any files. Check terminal logs for details.")
        
        return redirect('upload_data')


class ProcessUploadView(View):
    def post(self, request):
        import os
        import uuid
        from django.utils import timezone
        
        #  Match the HTML form's input names ('files' and 'platform')
        uploaded_files = request.FILES.getlist('files')
        data_type = request.POST.get('platform', 'generic')
        source_name = request.POST.get('source_name', 'User Upload')
        
        logger.info(f"📥 Upload request: data_type={data_type}, source={source_name}")
        logger.info(f"📁 FILES: {list(request.FILES.keys())}")
        
        if not uploaded_files:
            messages.error(request, "No files received. Please check the file input name.")
            return redirect('upload_data')
        
        results = []
        for uploaded_file in uploaded_files:
            try:
                # Generate unique filename
                unique_id = uuid.uuid4().hex[:8]
                timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
                original_name = uploaded_file.name
                name_without_ext = os.path.splitext(original_name)[0]
                ext = os.path.splitext(original_name)[1]
                unique_filename = f"{name_without_ext}_{timestamp}_{unique_id}{ext}"
                
                # Save file to the configured storage backend, then read it back
                # through storage so local files and S3-compatible stores both work.
                file_path = default_storage.save(f'uploads/{unique_filename}', uploaded_file)
                logger.info(f"🔄 Processing: {original_name} -> {unique_filename} ({uploaded_file.size / 1024 / 1024:.2f} MB)")
                log_event(
                    "Upload file saved to storage",
                    event_type="upload.file_saved",
                    status="success",
                    actor=request.user.username if request.user.is_authenticated else "anonymous",
                    source=original_name,
                    object_type="file",
                    object_id=file_path,
                    request_path=request.path,
                    metadata={"stored_path": file_path, "data_type": request.POST.get("data_type", "custom")},
                )
                
                # Create upload record
                upload = DataUpload.objects.create(
                    uploaded_file=file_path,
                    original_filename=original_name,
                    uploaded_by=request.user.username if request.user.is_authenticated else 'anonymous',
                    data_type=data_type,
                    status='processing'
                )
                log_event(
                    "Upload database record created",
                    event_type="upload.record_created",
                    status="success",
                    actor=upload.uploaded_by,
                    source=original_name,
                    object_type="DataUpload",
                    object_id=str(upload.id),
                    request_path=request.path,
                    metadata={"data_type": upload.data_type},
                )

                # === STREAMLIT-STYLE DATA PROCESSING ===
                data_type = upload.data_type

                # === LOAD CSV WITH APPROPRIATE HANDLING ===
                with default_storage.open(file_path, 'rb') as stored_file:
                    if data_type == 'brandwatch':
                        df = pd.read_csv(stored_file, sep=',', low_memory=False, on_bad_lines='skip', encoding_errors='ignore', skiprows=6)
                    else:
                        df = load_data_robustly(stored_file, original_name=original_name)

                logger.info(f"📊 Initial CSV Shape: {df.shape} | Columns: {list(df.columns)}")
                logger.info(f"📊 CSV Shape: {df.shape}")
                logger.info(f"📋 CSV Columns: {list(df.columns)}")
                log_event(
                    "Upload CSV loaded",
                    event_type="upload.csv_loaded",
                    status="success",
                    actor=upload.uploaded_by,
                    source=original_name,
                    object_type="DataUpload",
                    object_id=str(upload.id),
                    request_path=request.path,
                    metadata={"rows": int(df.shape[0]), "columns": list(df.columns), "data_type": data_type},
                )
                
                if df.empty:
                    raise ValueError("DataFrame is empty after loading")
                
                # Combine/Map data based on source
                if data_type == 'meltwater':
                    combined_df = combine_social_media_data(meltwater_df=df)
                elif data_type == 'civicsignals':
                    combined_df = combine_social_media_data(civicsignals_df=df)
                elif data_type == 'tiktok':
                    combined_df = combine_social_media_data(tiktok_df=df)
                elif data_type == 'openmeasure':
                    combined_df = combine_social_media_data(openmeasures_df=df)
                elif data_type == 'brandwatch':
                    combined_df = combine_social_media_data(brandwatch_df=df)
                else:
                    combined_df = preprocess_dataframe(df)

                logger.info(f"📊 COMBINED DATA COLUMNS: {list(combined_df.columns)}")
                logger.info(f"📊 COMBINED DATA SHAPE: {combined_df.shape}")
                log_event(
                    "Upload data combined",
                    event_type="upload.combined",
                    status="success",
                    actor=upload.uploaded_by,
                    source=original_name,
                    object_type="DataUpload",
                    object_id=str(upload.id),
                    request_path=request.path,
                    metadata={"rows": int(combined_df.shape[0]), "columns": list(combined_df.columns), "data_type": data_type},
                )

                # Ensure required columns exist
                required_cols = ['account_id', 'original_text', 'URL', 'timestamp_share', 'Platform']
                for col in required_cols:
                    if col not in combined_df.columns:
                        combined_df[col] = ''
                        logger.warning(f"⚠️ Added missing column: {col}")
                
                # === FINAL PREPROCESSING ===
                processed_df = final_preprocess_and_map_columns(combined_df)
                logger.info(f"📊 Processed Data Shape: {processed_df.shape} | Columns: {list(processed_df.columns)}")

                logger.info(f"📊 PROCESSED DATA COLUMNS: {list(processed_df.columns)}")
                logger.info(f"📊 PROCESSED DATA SHAPE: {processed_df.shape}")
                log_event(
                    "Upload data preprocessed",
                    event_type="upload.preprocessed",
                    status="success",
                    actor=upload.uploaded_by,
                    source=original_name,
                    object_type="DataUpload",
                    object_id=str(upload.id),
                    request_path=request.path,
                    metadata={"rows": int(processed_df.shape[0]), "columns": list(processed_df.columns), "data_type": data_type},
                )
                
                # Parse timestamps with error handling
                if 'timestamp_share' in processed_df.columns:
                    processed_df['timestamp_share'] = processed_df['timestamp_share'].apply(
                        lambda x: parse_timestamp_robust(x) if pd.notna(x) else pd.NaT
                    )
                
                # Save to Database with detailed skip tracking
                count = 0
                urls_saved = 0
                skipped_empty = 0
                skipped_dup = 0
                errors = []
                
                for idx, row in processed_df.iterrows():
                    try:
                        text_val = str(row.get('original_text', '')).strip()
                        if not text_val or text_val.lower() in ['nan', 'none', '']:
                            skipped_empty += 1
                            continue
                        
                        cid = str(row.get('content_id', '')).strip()
                        url_val = str(row.get('url') or row.get('URL', '')).strip()
                        
                        # Check duplicates
                        if cid and cid.lower() != 'nan' and ProcessedPost.objects.filter(content_id=cid).exists():
                            skipped_dup += 1
                            continue
                        if url_val.startswith('http') and ProcessedPost.objects.filter(url=url_val).exists():
                            skipped_dup += 1
                            continue
                        
                        source_obj, _ = DataSource.objects.get_or_create(name=source_name)
                        url_value = url_val[:500] if url_val.startswith('http') else None
                        if url_value:
                            urls_saved += 1
                        # Convert Pandas NaT to None for Django
                        ts_val = row.get('timestamp_share')
                        if pd.isna(ts_val) or str(ts_val) == 'NaT':
                            ts_val = None
                            
                        ProcessedPost.objects.create(
                            account_id=str(row.get('account_id', ''))[:100],
                            content_id=str(cid).strip()[:100] if cid else None,
                            original_text=str(row.get('original_text', '')).strip(),
                            url=url_value,
                            platform=str(row.get('Platform', 'Unknown')),
                            timestamp_share=ts_val,  # <--- NOW SAFE FOR DJANGO
                            source_dataset=source_obj,
                            is_election_related=is_election_related(str(row.get('original_text', '')))
                        )
                        count += 1
                    except Exception as row_error:
                        errors.append(f"Row {idx}: {str(row_error)}")
                        logger.error(f"❌ Error processing row {idx}: {row_error}")
                        log_event(
                            "Upload row failed",
                            level="ERROR",
                            event_type="upload.row_failed",
                            status="failed",
                            actor=upload.uploaded_by,
                            source=original_name,
                            object_type="DataUpload",
                            object_id=str(upload.id),
                            request_path=request.path,
                            metadata={"row_index": int(idx), "error": str(row_error)},
                        )
                        continue
                
                logger.info(f"✅ Saved: {count} | Skipped Empty: {skipped_empty} | Skipped Duplicates: {skipped_dup}")
                
                # Update record
                upload.status = 'completed'
                upload.processing_log = f"Saved: {count}, Empty: {skipped_empty}, Duplicates: {skipped_dup}"
                upload.records_processed = count
                upload.save()
                
                results.append((original_name, True, f"Processed {count} posts", count))
                logger.info(f"✅ {original_name}: Processed {count} posts")
                log_event(
                    "Upload processing completed",
                    event_type="upload.completed",
                    status="success",
                    actor=upload.uploaded_by,
                    source=original_name,
                    object_type="DataUpload",
                    object_id=str(upload.id),
                    request_path=request.path,
                    metadata={"records_processed": count, "urls_saved": urls_saved, "row_errors": len(errors)},
                )
                
            except Exception as e:
                logger.error(f"❌ Upload failed for {uploaded_file.name}: {str(e)}", exc_info=True)
                if 'upload' in locals():
                    upload.status = 'failed'
                    upload.processing_log = str(e)
                    upload.save()
                    object_id = str(upload.id)
                    actor = upload.uploaded_by
                else:
                    object_id = ''
                    actor = request.user.username if request.user.is_authenticated else 'anonymous'
                log_event(
                    "Upload processing failed",
                    level="ERROR",
                    event_type="upload.failed",
                    status="failed",
                    actor=actor,
                    source=uploaded_file.name,
                    object_type="DataUpload",
                    object_id=object_id,
                    request_path=request.path,
                    metadata={"error": str(e)},
                    exc_info=True,
                )
                results.append((uploaded_file.name, False, str(e), 0))
        
        # Show summary in UI
        success_count = sum(1 for _, s, _, c in results if s and c > 0)
        total_saved = sum(c for _, s, _, c in results if s)
        
        if total_saved > 0:
            queue_dashboard_analytics_refresh(skip_narratives=True)
            messages.success(request, f"✅ Successfully saved {total_saved} new posts!")
        elif not any(s for _, s, _, _ in results):
            messages.error(request, "❌ Failed to process files. Check terminal logs for details.")
        else:
            messages.warning(request, f"⚠️ File processed, but 0 new posts were saved. They may be duplicates or have empty text. Check logs.")
        log_event(
            "Upload request finished",
            event_type="upload.summary",
            status="success" if total_saved > 0 else "warning",
            actor=request.user.username if request.user.is_authenticated else "anonymous",
            request_path=request.path,
            metadata={"files": len(uploaded_files), "success_count": success_count, "total_saved": total_saved},
        )
        
        return redirect('upload_data')
        
class ClearDataView(View):
    """Clear all uploaded data from database"""
    def post(self, request):
        # Only clear ProcessedPost, keep other data if needed
        ProcessedPost.objects.all().delete()
        # Optional: Also clear upload history
        # DataUpload.objects.all().delete()
        
        messages.success(request, "✅ All post data cleared successfully. You can now upload fresh data.")
        return redirect('upload_data')

class InvestigativeReportsView(TemplateView):
    """Dedicated view for investigative analysis reports"""
    template_name = 'dashboard/investigative_reports.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get all monitoring reports
        from dashboard.models import MonitoringReport
        reports = MonitoringReport.objects.all().order_by('-uploaded_at')
        
        # Calculate stats
        total_reports = reports.count()
        critical_reports = reports.filter(risk_level='critical').count()
        high_reports = reports.filter(risk_level='high').count()
        this_month = reports.filter(uploaded_at__gte=timezone.now().replace(day=1)).count()
        
        context.update({
            'active_tab': 'investigative_reports',
            'reports': reports,
            'stats': {
                'total': total_reports,
                'critical': critical_reports,
                'high': high_reports,
                'this_month': this_month,
            }
        })
        return context

class PEPsAnalysisView(TemplateView):
    template_name = 'dashboard/peps_analysis.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        end_date = timezone.now()
        start_date = end_date - timedelta(days=90)
        pep_payload, pep_generated_at = get_analytics_snapshot(
            'peps',
            start_date,
            end_date,
            view_all=False,
            allow_latest_fallback=True,
        )
        context['pep_analysis'] = pep_payload.get('pep_analysis', [])
        context['pep_analysis_generated_at'] = pep_generated_at
        context['active_tab'] = 'peps'
        
        return context
        
# === API Endpoints ===

def scan_text_api(request):
    """API endpoint for real-time hate speech scanning"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    text = request.POST.get('text', '')
    if not text:
        return JsonResponse({'error': 'No text provided'}, status=400)
    
    matches = scan_text_for_lexicon_terms(text)
    risk = calculate_risk_score(matches) if matches else {'score': 0, 'level': 'low'}
    
    return JsonResponse({
        'matches': matches,
        'risk': risk,
        'term_count': len(matches)
    })


def export_posts_api(request):
    """API endpoint to export filtered posts as CSV"""
    queryset, start_date, end_date = get_election_posts_queryset(request)
    
    # Convert to DataFrame
    posts = list(queryset.values())
    df = pd.DataFrame(posts)
    
    # Convert to CSV
    import io
    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False)
    csv_buffer.seek(0)
    
    response = HttpResponse(csv_buffer.getvalue(), content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="sa_election_posts_{start_date.date()}_{end_date.date()}.csv"'    
    return response


def generate_network_graph(request):
    """API endpoint to generate coordination network graph smoothly for 14k+ records"""
    import re
    from collections import defaultdict

    # Get configuration parameters
    min_connections = int(request.GET.get('min_connections', 2))
    top_n = int(request.GET.get('top_n', 50))
    
    # Check parameters for specific selected cluster rows
    cluster_filter = request.GET.get('cluster') or request.GET.get('group_id') or request.GET.get('coordination_group')
    
    # Base query for election dataset
    query_kwargs = {'is_election_related': True}
    
    if cluster_filter and cluster_filter.strip():
        min_connections = 1  # Relax constraint for deep-dive group views
        if hasattr(ProcessedPost, 'coordination_group_id'):
            query_kwargs['coordination_group_id'] = cluster_filter
        elif hasattr(ProcessedPost, 'coordination_group'):
            query_kwargs['coordination_group'] = cluster_filter
        else:
            try:
                query_kwargs['cluster'] = int(cluster_filter)
            except ValueError:
                pass
    else:
        if hasattr(ProcessedPost, 'cluster'):
            query_kwargs['cluster__gte'] = 0

    # Pull only necessary fields into memory in one quick query
    posts_data = ProcessedPost.objects.filter(**query_kwargs).values('account_id', 'original_text')
    
    G = nx.Graph()
    rt_pattern = re.compile(r'RT\s+@([a-zA-Z0-9_]+)', re.IGNORECASE)
    
    # Map identical text fields to accounts in a single pass
    text_to_accounts = defaultdict(set)
    
    for post in posts_data:
        acc = post['account_id']
        text = post['original_text'] or ''
        
        if not acc or acc == 'unknown':
            continue
            
        # 1. Immediate Retweet Edge connection logic
        rt_match = rt_pattern.search(text)
        if rt_match:
            target_user = rt_match.group(1)
            if acc != target_user:
                if G.has_edge(acc, target_user):
                    G[acc][target_user]['weight'] += 1
                else:
                    G.add_edge(acc, target_user, weight=1)
                    
        # 2. Collect for identical text grouping analysis
        text_to_accounts[text].add(acc)

    # 3. Process identical text groups to track multi-account co-sharing patterns
    for acc_set in text_to_accounts.values():
        if len(acc_set) >= 2:
            acc_list = list(acc_set)
            for i in range(len(acc_list)):
                for j in range(i + 1, len(acc_list)):
                    if G.has_edge(acc_list[i], acc_list[j]):
                        G[acc_list[i]][acc_list[j]]['weight'] += 1
                    else:
                        G.add_edge(acc_list[i], acc_list[j], weight=1)

    # Filter out nodes based on minimum degree connection limits
    nodes_to_keep = [n for n, d in G.degree() if d >= min_connections]
    G = G.subgraph(nodes_to_keep).copy()
    
    if G.number_of_edges() == 0:
        return JsonResponse({'nodes': [], 'edges': [], 'message': 'No coordination links found for this selection'})
    
    # Isolate top N nodes
    top_nodes = sorted(G.degree(), key=lambda x: x[1], reverse=True)[:top_n]
    top_node_names = [n for n, _ in top_nodes]
    G_top = G.subgraph(top_node_names).copy()
    
    # Format structural node list response data payload
    node_data = [{'id': node, 'degree': G_top.degree(node)} for node in G_top.nodes()]
    
    # Format structural edge list response data payload
    edge_data = [
        {
            'source': u,
            'target': v,
            'weight': data.get('weight', 1)
        }
        for u, v, data in G_top.edges(data=True)
    ]
    
    return JsonResponse({
        'nodes': node_data,
        'edges': edge_data,
        'stats': {
            'total_nodes': G_top.number_of_nodes(),
            'total_edges': G_top.number_of_edges(),
            'avg_degree': sum(d for _, d in G_top.degree()) / G_top.number_of_nodes() if G_top.number_of_nodes() > 0 else 0
        }
    })

def ttp_radar_data_api(request):
    """Return election-related posts as JSON for the TTP Radar UI"""
    from django.http import JsonResponse
    from dashboard.models import ProcessedPost
    from django.utils import timezone
    
    try:
        country = request.GET.get('country', 'South Africa')
        limit = min(int(request.GET.get('limit', 100)), 500)
        
        posts = ProcessedPost.objects.filter(
            is_election_related=True
        ).order_by('-timestamp_share')[:limit]
        
        formatted = []
        for p in posts:
            formatted.append({
                'account_id': p.account_id or 'unknown',
                'content_id': f"post_{p.id}",
                'original_text': p.original_text or '',
                'URL': p.url or '',
                'timestamp_share': p.timestamp_share.isoformat() if p.timestamp_share else None,
                'Platform': p.platform or 'Unknown',
                'target_country': getattr(p, 'target_country', 'South Africa') or 'South Africa',
            })
        
        return JsonResponse({
            'records': formatted,
            'count': len(formatted),
            'country': country,
            'generated_at': timezone.now().isoformat()
        })
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"ttp_radar_data_api error: {e}", exc_info=True)
        return JsonResponse({
            'error': str(e),
            'records': [],
            'count': 0
        }, status=500)

@login_required
def trigger_llm_scan_api(request):
    """API endpoint to trigger background LLM scan"""
    from .utils.background_tasks import start_background_llm_scan
    from .models import ProcessedPost
    
    # Get recent posts (last 500)
    recent_posts = ProcessedPost.objects.filter(
        is_election_related=True
    ).order_by('-timestamp_share')[:500]
    
    post_ids = list(recent_posts.values_list('id', flat=True))
    
    started = start_background_llm_scan(post_ids, user_id=request.user.id)
    
    if started:
        return JsonResponse({
            'success': True,
            'message': f'Scan started for {len(post_ids)} posts',
            'posts_count': len(post_ids)
        })
    else:
        return JsonResponse({
            'success': False,
            'message': 'Scan already in progress. Please wait.'
        }, status=429)
       
def scan_text_afro_xlmr_api(request):
    """API endpoint for AFRO-XLMR hate speech scanning"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    text = request.POST.get('text', '')
    if not text:
        return JsonResponse({'error': 'No text provided'}, status=400)
    
    # Use AFRO-XLMR for detection
    afro_result = detect_hate_speech_afro_xlmr(text)
    
    # Also run lexicon-based detection for comparison
    lexicon_matches = scan_text_for_lexicon_terms(text)
    lexicon_risk = calculate_risk_score(lexicon_matches)
    
    # Combine results
    return JsonResponse({
        'afro_xlmr': afro_result,
        'lexicon': {
            'matches': lexicon_matches,
            'risk': lexicon_risk
        },
        'model_used': 'AFRO-XLMR',
        'text_length': len(text)
    })
