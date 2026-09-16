# dashboard/utils/hate_speech_detector.py
import os
import logging
import numpy as np

try:
    import torch
    from transformers import AutoTokenizer, AutoModelForSequenceClassification
    ML_DEPS_AVAILABLE = True
except ImportError:
    torch = None
    AutoTokenizer = None
    AutoModelForSequenceClassification = None
    ML_DEPS_AVAILABLE = False

logger = logging.getLogger(__name__)

# Mapped from your notebook (Cell 5)
ID2LABEL = {
    0: 'Ancestry', 1: 'Ethnicity', 2: 'Gender disinformation', 3: 'Homophobic',
    4: 'Misognistic', 5: 'Religion', 6: 'Xenophobia', 7: 'Deragatory',
    8: 'Dehumanization', 9: 'Ethnic slur', 10: 'Slur', 11: 'Stereotype',
    12: 'Call for action', 13: 'Inciteful', 14: 'Violence', 15: 'Class',
    16: 'Extremism', 17: 'Inflammatory', 18: 'Structural', 19: 'Neutral'
}

# CORRECTED: Scientifically accurate taxonomy splitting demographic targets from attack methods
GROUP_RULES = {
    # Targeted Identity Vectors: True demographic, inherent identity, or background dimensions
    'Ancestry': 'Targeted Identity Vectors',
    'Ethnicity': 'Targeted Identity Vectors',
    'Ethnic slur': 'Targeted Identity Vectors',
    'Gender disinformation': 'Targeted Identity Vectors',
    'Homophobic': 'Targeted Identity Vectors',
    'Misognistic': 'Targeted Identity Vectors',
    'Religion': 'Targeted Identity Vectors',
    'Xenophobia': 'Targeted Identity Vectors',
    
    # Expressive Hostility: Forms of rhetorical attack, tropes, and linguistic hostility methods
    'Dehumanization': 'Expressive Hostility',
    'Deragatory': 'Expressive Hostility',
    'Slur': 'Expressive Hostility',
    'Stereotype': 'Expressive Hostility',
    
    # Incitement & Mobilization: Categories directly inciting overt real-world actions or violence
    'Call for action': 'Incitement & Mobilization',
    'Inciteful': 'Incitement & Mobilization',
    'Violence': 'Incitement & Mobilization',
    
    # Ideological Radicalization: Political, systematic, structural, or class-based radicalization fields
    'Class': 'Ideological Radicalization',
    'Extremism': 'Ideological Radicalization',
    'Inflammatory': 'Ideological Radicalization',
    'Structural': 'Ideological Radicalization',
    
    # Neutral: Safe non-harmful text instances
    'Neutral': 'Neutral'
}

SEVERITY_MAP = {
    'Violence': 'critical', 
    'Inciteful': 'critical', 
    'Call for action': 'critical', 
    'Dehumanization': 'critical',
    'Extremism': 'high', 
    'Ethnic slur': 'high', 
    'Slur': 'high', 
    'Misognistic': 'high',
    'Deragatory': 'medium', 
    'Inflammatory': 'high', 
    'Gender disinformation': 'high',
    'Stereotype': 'high', 
    'Homophobic': 'high', 
    'Ethnicity': 'high', 
    'Xenophobia': 'high', 
    'Religion': 'high',
    'Ancestry': 'medium', 
    'Class': 'low', 
    'Structural': 'low',
    'Neutral': 'low'
}

_UTILS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_UTILS_DIR)  
MODEL_PATH = os.path.join(_PROJECT_DIR, 'models_cache', 'afro_xlmr')

class HateSpeechDetector:
    def __init__(self, model_path):
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        logger.info(f"Loading Lexicon LLM from {model_path} on {self.device}")
        
        # Load exactly as done in your notebook
        self.tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_path, local_files_only=True).to(self.device)
        self.model.eval()

    def detect(self, text):
        """Matches the classify_text logic from your notebook"""
        if not text or len(text.strip()) < 10:
            return {'category': 'Neutral', 'confidence': 0.0, 'severity': 'low', 'group': 'Neutral'}

        inputs = self.tokenizer(
            text, return_tensors='pt', truncation=True, padding=True, max_length=128
        ).to(self.device)

        with torch.no_grad():
            logits = self.model(**inputs).logits

        probs = torch.nn.functional.softmax(logits, dim=-1).squeeze().cpu().numpy()
        top_idx = int(np.argsort(probs)[::-1][0])
        confidence = float(probs[top_idx])

        category = ID2LABEL.get(top_idx, 'Neutral')
        group = GROUP_RULES.get(category, 'Unknown')

        return {
            'category': category,
            'group': group,
            'confidence': confidence,
            'severity': SEVERITY_MAP.get(category, 'medium')
        }

# Singleton pattern to keep the model in memory
_detector_instance = None

def get_hate_speech_detector():
    global _detector_instance
    if _detector_instance is None:
        if not ML_DEPS_AVAILABLE:
            logger.warning("AFRO-XLMR detector disabled because torch/transformers are not installed.")
            return None

        # We will place your model files in this folder on EC2
        _BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        model_path = os.path.join(_BASE_DIR, 'models_cache', 'afro_xlmr')
        if not os.path.exists(model_path):
            logger.error(f"Model path {model_path} not found. Ensure you uploaded the model folder.")
            return None 
        _detector_instance = HateSpeechDetector(model_path)
    return _detector_instance
