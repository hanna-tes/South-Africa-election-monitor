#!/usr/bin/env python
"""
Test script for AFRO-XLMR hate speech detector
Run from project root: python test_afro_xlmr.py
"""
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Set the correct Django settings module
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'election_monitor.settings')

import django
django.setup()

from dashboard.utils.hate_speech_detector import get_hate_speech_detector

def test_detector():
    """Test the AFRO-XLMR detector with sample texts"""
    
    print("=" * 70)
    print("🧪 Testing AFRO-XLMR Hate Speech Detector")
    print("=" * 70)
    
    # Initialize detector
    detector = get_hate_speech_detector()
    if detector is None:
        print("❌ Failed to load model. Check the path and try again.")
        return
    
    print("✅ Model loaded successfully!\n")
    
    # Test cases
    test_cases = [
        # Amharic examples (Fixed: ሰች -> ሰዎች, ዝብ -> ህዝብ, ይር -> ይኑር)
        ("እነዚህ ሰዎች እንስሳ ናቸው። መግደል አለብን።", "Amharic - Dehumanization + Violence"),
        ("የኦሮሞ ህዝብ በጣም ጥሩ ነው ሰላም ይኑር።", "Amharic - Neutral/Positive"),
        ("ትግሬ ሌባ ነው ወያኔ ይውደም።", "Amharic - Ethnic slur + Incitement"),
        
        # Oromo examples
        ("Namni kun hamaa dha. Ajjeesuu qabna.", "Oromo - Dehumanization + Violence"),
        ("Oromoon nagaa fi jaalala.", "Oromo - Neutral/Positive"),
        
        # English examples
        ("These people are animals and should be eliminated", "English - Dehumanization"),
        ("The election was fair and transparent", "English - Neutral"),
        ("Kill all the woyane supporters now", "English - Violence + Incitement"),
        
        # Mixed / Ethnic slurs (Fixed: ነፍኛ -> ነፍጠኛ, ላ -> ሌባ)
        ("ነፍጠኛ ፋኖ ሌባ ናቸው።", "Mixed - Ethnic slurs"),
    ]
    
    print(f"Running {len(test_cases)} test cases...\n")
    
    for text, description in test_cases:
        result = detector.detect(text)
        
        print(f"📝 {description}")
        print(f"   Text: {text[:60]}{'...' if len(text) > 60 else ''}")
        print(f"   Category: {result.get('category', 'N/A')}")
        print(f"   Confidence: {result.get('confidence', 0)*100:.1f}%")
        print(f"   Severity: {result.get('severity', 'N/A')}")
        print(f"   Is Hate Speech: {'🚨 YES' if result.get('is_hate_speech') else '✅ NO'}")
        if result.get('error'):
            print(f"   ⚠️  Error: {result.get('error')}")
        print("-" * 70)
    
    print("\n✅ Testing complete!")


if __name__ == "__main__":
    test_detector()
