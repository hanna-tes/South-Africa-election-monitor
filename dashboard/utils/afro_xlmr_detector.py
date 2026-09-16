import os
import joblib
import logging

try:
    import torch
    import torch.nn as nn
    from transformers import AutoTokenizer, AutoModel
    ML_DEPS_AVAILABLE = True
except ImportError:
    torch = None
    nn = None
    AutoTokenizer = None
    AutoModel = None
    ML_DEPS_AVAILABLE = False

logger = logging.getLogger(__name__)

# Model configuration
MODEL_NAME = "Davlan/afro-xlmr-base"
MODEL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'models_cache', 'afro_xlmr'
)
CHECKPOINT_PATH = os.path.join(MODEL_DIR, 'hateguard_finetuned_v7.pt')
LABEL_ENCODER_PATH = os.path.join(MODEL_DIR, 'label_encoder.pkl')

if ML_DEPS_AVAILABLE:
    class AfroXLMRClassifier(nn.Module):
        """Matches the architecture from your Colab notebook"""
        def __init__(self, num_classes=20, dropout=0.4):
            super().__init__()
            self.encoder = AutoModel.from_pretrained(MODEL_NAME)
            hidden = self.encoder.config.hidden_size
            self.classifier = nn.Sequential(
                nn.Linear(hidden, 256),
                nn.LayerNorm(256),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Linear(256, num_classes)
            )

        def forward(self, input_ids, attention_mask):
            out = self.encoder(input_ids=input_ids, attention_mask=attention_mask)
            # Returns a raw Tensor, NOT a HuggingFace SequenceClassifierOutput
            return self.classifier(out.last_hidden_state[:, 0, :])


class AfroXlmrDetector:
    def __init__(self):
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.model = None
        self.tokenizer = None
        self.classes = []
        self._load_model()

    def _load_model(self):
        try:
            if not ML_DEPS_AVAILABLE:
                logger.warning("AFRO-XLMR disabled because torch/transformers are not installed.")
                return

            if not os.path.exists(CHECKPOINT_PATH):
                logger.error(f"❌ Checkpoint not found at {CHECKPOINT_PATH}")
                return

            logger.info(f"⏳ Loading AFRO-XLMR from checkpoint...")
            checkpoint = torch.load(CHECKPOINT_PATH, map_location=self.device, weights_only=False)
            
            num_classes = checkpoint.get('num_classes', 20)
            self.classes = checkpoint.get('classes', [])
            
            # Initialize and load model
            self.model = AfroXLMRClassifier(num_classes=num_classes).to(self.device)
            self.model.load_state_dict(checkpoint['model_state'])
            self.model.eval()
            
            # Load tokenizer
            self.tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
            
            # Load label encoder if available
            if os.path.exists(LABEL_ENCODER_PATH):
                self.le = joblib.load(LABEL_ENCODER_PATH)
                self.classes = list(self.le.classes_)
            
            logger.info(f"✅ Model loaded successfully on {self.device}")
            
        except Exception as e:
            logger.error(f"❌ Failed to load model: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def detect(self, text: str) -> dict:
        if not self.model or not self.tokenizer:
            return {
                'is_hate_speech': False,
                'confidence': 0.0,
                'category': 'error',
                'severity': 'low',
                'error': 'Model not loaded'
            }

        try:
            inputs = self.tokenizer(
                text, return_tensors='pt', truncation=True, padding=True, max_length=128
            ).to(self.device)

            with torch.no_grad():
                # outputs is a raw Tensor here, NOT a HuggingFace output object
                outputs = self.model(**inputs)
                
                # Apply softmax directly to the tensor
                probabilities = torch.nn.functional.softmax(outputs, dim=1)
                confidence, predicted_class = torch.max(probabilities, 1)
            
            pred_idx = predicted_class.item()
            
            if self.classes and pred_idx < len(self.classes):
                category = self.classes[pred_idx]
            else:
                category = f'Class_{pred_idx}'

            category = {'Stractural': 'Structural'}.get(category, category)
            
            is_hate = category not in ['Neutral', 'Normal', 'normal']
            
            severity_map = {
                'Violence': 'critical', 'Inciteful': 'critical', 'Call for action': 'critical',
                'Dehumanization': 'critical', 'Extremism': 'high', 'Ethnic slur': 'high',
                'Slur': 'high', 'Misognistic': 'high', 'Deragatory': 'medium',
                'Inflammatory': 'high', 'Gender disinformation': 'high', 'Stereotype': 'high',
                'Homophobic': 'high', 'Ethnicity': 'high', 'Xenophobia': 'high', 'Religion': 'high',
                'Ancestry': 'medium', 'Class': 'low', 'Structural': 'low',
                'Neutral': 'low', 'Normal': 'low'
            }
            severity = severity_map.get(category, 'medium')
            
            return {
                'is_hate_speech': is_hate,
                'confidence': round(confidence.item(), 4),
                'category': category,
                'severity': severity
            }
            
        except Exception as e:
            logger.error(f"AFRO-XLMR detection error: {e}")
            return {
                'is_hate_speech': False,
                'confidence': 0.0,
                'category': 'error',
                'severity': 'low',
                'error': str(e)
            }


# Singleton instance
_detector_instance = None

def get_detector():
    global _detector_instance
    if _detector_instance is None:
        _detector_instance = AfroXlmrDetector()
    return _detector_instance
