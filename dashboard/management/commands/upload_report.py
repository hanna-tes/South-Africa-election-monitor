import os
import re
import logging
from django.core.management.base import BaseCommand
from dashboard.models import MonitoringReport
from dashboard.utils.app_logging import log_event
from dashboard.utils.llm_service import safe_llm_call

logger = logging.getLogger(__name__)

def extract_text(file_path):
    """Extract text from PDF, DOCX, or TXT"""
    ext = os.path.splitext(file_path)[1].lower()
    
    if ext == '.txt':
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
    elif ext == '.pdf':
        try:
            import pdfplumber
            with pdfplumber.open(file_path) as pdf:
                return '\n'.join(page.extract_text() or '' for page in pdf.pages)
        except ImportError:
            raise ImportError("Install pdfplumber: pip install pdfplumber")
    elif ext in ['.docx', '.doc']:
        try:
            from docx import Document
            doc = Document(file_path)
            return '\n'.join(p.text for p in doc.paragraphs)
        except ImportError:
            raise ImportError("Install python-docx: pip install python-docx")
    raise ValueError(f"Unsupported file type: {ext}")

def generate_executive_summary(full_text):
    """Use LLM to generate a concise executive summary (2-3 sentences)"""
    context = full_text[:3000] if len(full_text) > 3000 else full_text
    
    prompt = f"""You are an election monitoring analyst. Write a concise 2-3 sentence executive summary of this report.

**Focus on:**
- Main electoral risks identified
- Key information environment challenges
- Most critical findings for decision-makers

**Report excerpt:**
{context}

Return ONLY the summary text (2-3 sentences), no JSON, no bullet points, no explanations.
Summary:"""
    
    try:
        summary = safe_llm_call(prompt).strip()
        # Clean up any extra formatting
        summary = re.sub(r'^["\']|["\']$', '', summary).strip()
        return summary if len(summary) > 50 else full_text[:300] + "..."
    except Exception as e:
        logger.error(f"LLM summary failed: {e}")
        log_event(
            "Report upload LLM summary failed",
            level="ERROR",
            event_type="command.upload_report.summary_failed",
            status="failed",
            metadata={"error": str(e)},
            exc_info=True,
        )
        # Fallback: first 300 chars
        return full_text[:300] + "..." if full_text else "Summary not available."

def assess_risk_level(full_text):
    """Context-aware risk assessment"""
    prompt = f"""Analyze this election monitoring report and assign a risk level.

**RISK GUIDELINES:**
- critical: Incitement to violence, coordinated hate speech, threats to electoral integrity
- high: Widespread disinformation, ethnic/political polarization, coordinated manipulation
- medium: Bias, unverified claims, moderate polarization, standard political criticism
- low: Factual reporting, neutral analysis, procedural updates

**Report excerpt:**
{full_text[:2000]}

Return ONLY the risk level string: "low", "medium", "high", or "critical".
JSON:"""
    
    try:
        response = safe_llm_call(prompt)
        match = re.search(r'low|medium|high|critical', response.lower())
        if match:
            return match.group()
    except Exception as e:
        logger.error(f"LLM risk assessment failed: {e}")
        log_event(
            "Report upload LLM risk assessment failed",
            level="ERROR",
            event_type="command.upload_report.risk_failed",
            status="failed",
            metadata={"error": str(e)},
            exc_info=True,
        )
    
    # Fallback keyword scan
    t = full_text.lower()
    if any(w in t for w in ['violence', 'kill', 'incitement', 'civil war', 'genocide', 'hate speech']):
        return 'critical'
    if any(w in t for w in ['polarization', 'disinformation', 'manipulation', 'distrust', 'coordinated']):
        return 'high'
    if any(w in t for w in ['bias', 'protest', 'unverified', 'criticism']):
        return 'medium'
    return 'low'

class Command(BaseCommand):
    help = 'Upload election monitoring report with summary + full report link'

    def add_arguments(self, parser):
        parser.add_argument('file_path', type=str)
        parser.add_argument('--title', type=str)
        parser.add_argument('--analyst', type=str, default='Internal Analyst')
        parser.add_argument('--type', type=str, default='Investigative', choices=['Investigative', 'Monthly', 'Special'])
        parser.add_argument('--url', type=str, help='Full report URL (Google Drive, Google Doc, PDF link, etc.)')

    def handle(self, *args, **options):
        file_path = options['file_path']
        if not os.path.exists(file_path):
            self.stderr.write(f"❌ File not found: {file_path}")
            log_event(
                "Report upload skipped missing file",
                level="ERROR",
                event_type="command.upload_report.missing_file",
                status="failed",
                source=file_path,
            )
            return

        title = options['title'] or os.path.splitext(os.path.basename(file_path))[0].replace('_', ' ').title()
        full_report_url = options.get('url', '').strip()
        log_event(
            "Report upload started",
            event_type="command.upload_report.started",
            status="started",
            source=file_path,
            metadata={"title": title, "analyst": options['analyst'], "report_type": options['type'], "has_url": bool(full_report_url)},
        )
        
        self.stdout.write(f"📄 Processing: {title}")
        self.stdout.write("🔍 Extracting text from document...")
        
        try:
            raw_text = extract_text(file_path)
            if not raw_text or len(raw_text.strip()) < 100:
                self.stderr.write("⚠️ Could not extract meaningful text.")
                log_event(
                    "Report upload could not extract meaningful text",
                    level="WARNING",
                    event_type="command.upload_report.extract_empty",
                    status="warning",
                    source=file_path,
                    metadata={"title": title, "characters": len(raw_text or "")},
                )
                return
            
            self.stdout.write(f"✅ Extracted {len(raw_text):,} characters")
            log_event(
                "Report upload extracted text",
                event_type="command.upload_report.extracted",
                status="success",
                source=file_path,
                metadata={"title": title, "characters": len(raw_text)},
            )
            
            # Generate executive summary via LLM
            self.stdout.write("🤖 Generating AI executive summary...")
            summary = generate_executive_summary(raw_text)
            log_event(
                "Report upload generated summary",
                event_type="command.upload_report.summary",
                status="success",
                source=file_path,
                metadata={"title": title, "summary_length": len(summary)},
            )
            
            # Assess risk level
            self.stdout.write("🤖 Assessing contextual risk level...")
            risk_level = assess_risk_level(raw_text)
            log_event(
                "Report upload assessed risk",
                event_type="command.upload_report.risk",
                status="success",
                source=file_path,
                metadata={"title": title, "risk_level": risk_level},
            )
            
            # Save to database - SIMPLE & CLEAN
            report = MonitoringReport.objects.create(
                title=title,
                source_analyst=options['analyst'],
                file_path=file_path,
                report_type=options['type'],
                
                # Store FULL extracted text (for reference/expandable view)
                extracted_text=raw_text[:10000],  # First 10k chars for preview
                
                # AI-generated executive summary
                summary=summary,
                
                # ✅ Full report URL (Google Drive, Google Doc, etc.)
                full_report_url=full_report_url if full_report_url else None,
                
                # Keep other fields empty for now (can be filled via admin)
                key_findings=[],
                weaponised_narratives=[],
                actor_spotlight=[],
                ttp_infrastructure=[],
                mentioned_entities=[],
                sample_urls=[],
                risk_level=risk_level,
                is_processed=True
            )
            
            self.stdout.write(f"\n✅ SUCCESS! Report saved.")
            self.stdout.write(f"📊 Risk Level: {report.risk_level.upper()}")
            self.stdout.write(f"📋 Summary: {summary[:100]}...")
            if full_report_url:
                self.stdout.write(f"🔗 Full Report: {full_report_url}")
            self.stdout.write(f"🔗 View: http://localhost:8505/investigative-reports/")
            log_event(
                "Report upload completed",
                event_type="command.upload_report.completed",
                status="success",
                source=file_path,
                object_type="MonitoringReport",
                object_id=str(report.id),
                metadata={"title": title, "risk_level": risk_level, "has_url": bool(full_report_url)},
            )
            
        except Exception as e:
            self.stderr.write(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
            log_event(
                "Report upload failed",
                level="ERROR",
                event_type="command.upload_report.failed",
                status="failed",
                source=file_path,
                metadata={"title": title, "error": str(e)},
                exc_info=True,
            )
