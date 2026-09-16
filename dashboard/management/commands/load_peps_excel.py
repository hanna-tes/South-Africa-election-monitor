import pandas as pd
from django.core.management.base import BaseCommand
from django.utils import timezone
from dashboard.models import PEP

class Command(BaseCommand):
    help = 'Load PEPs from CSV or Excel files into the database'

    def add_arguments(self, parser):
        parser.add_argument('file_path', type=str, help='Path to CSV or Excel file')

    def handle(self, *args, **kwargs):
        file_path = kwargs['file_path']
        self.stdout.write(f"📂 Processing: {file_path}")

        try:
            # 1. Auto-detect format & load safely
            if file_path.endswith('.csv'):
                df = pd.read_csv(file_path, encoding='utf-8', on_bad_lines='skip')
            elif file_path.endswith('.xlsx') or file_path.endswith('.xls'):
                df = pd.read_excel(file_path, engine='openpyxl')
            else:
                self.stdout.write(self.style.ERROR("❌ Unsupported file format. Use .csv or .xlsx"))
                return

            self.stdout.write(f"✅ Loaded {len(df)} rows. Cleaning & saving...")

            # 2. Clean & save to DB
            saved, skipped = 0, 0
            for _, row in df.iterrows():
                name = str(row.get('Name (English)', row.get('full_name_en', ''))).strip()
                if not name or name.lower() in ['nan', 'none', '']:
                    skipped += 1
                    continue

                # Handle "No verified account" placeholders
                def clean_link(val):
                    v = str(val).strip()
                    return None if v.lower() in ['no verified personal account found', 'none found', 'none', 'nan', ''] else v

                PEP.objects.update_or_create(
                    name=name,
                    defaults={
                        'title': str(row.get('Position', row.get('role', ''))).strip(),
                        'x_link': clean_link(row.get('X (Twitter) Link')),
                        'facebook_link': clean_link(row.get('Facebook Link')),
                        'confidence_level': str(row.get('Confidence', 'medium')).lower().strip(),
                        'last_updated': timezone.now()
                    }
                )
                saved += 1

            self.stdout.write(self.style.SUCCESS(f"✅ Done: {saved} PEPs saved/updated | {skipped} skipped"))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Error processing file: {e}"))
            import traceback
            traceback.print_exc()
