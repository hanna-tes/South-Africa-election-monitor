import os
import pandas as pd
from django.core.management.base import BaseCommand
from django.conf import settings
from dashboard.models import ElectionOfficeholder
from django.db import transaction

class Command(BaseCommand):
    help = 'Import 2026 Election Candidate data from Excel files into ElectionOfficeholder (Clean Import)'

    def handle(self, *args, **kwargs):
        peps_dir = os.path.join(settings.MEDIA_ROOT, 'peps')
        
        files_to_import = [
            'HoPR_Candidates.xlsx',
            'Regional_Candidates.xlsx',
            'RC_Members.xlsx',
            'Executive_Members.xlsx'
        ]
        
        for filename in files_to_import:
            filepath = os.path.join(peps_dir, filename)
            if not os.path.exists(filepath):
                self.stdout.write(self.style.WARNING(f"⚠️ File not found: {filepath}"))
                continue
            
            self.stdout.write(f"\n📂 Processing: {filename}")
            
            # 1. CLEAR OLD DATA for this specific file to prevent duplicates/skew
            deleted_count, _ = ElectionOfficeholder.objects.filter(source_file=filename).delete()
            if deleted_count > 0:
                self.stdout.write(self.style.WARNING(f"   🗑️ Deleted {deleted_count} old records for {filename}"))

            try:
                xls = pd.ExcelFile(filepath)
                total_saved = 0
                total_skipped = 0
                
                for sheet_name in xls.sheet_names:
                    df = pd.read_excel(xls, sheet_name=sheet_name)
                    if df.empty:
                        continue
                    
                    col_order = [str(c).strip() for c in df.columns]
                    records_to_create = []
                    
                    for idx, row in df.iterrows():
                        raw_data = {}
                        all_empty = True
                        
                        for col in col_order:
                            val = row.get(col)
                            if pd.isna(val):
                                raw_data[col] = None
                            elif isinstance(val, (int, float, bool)):
                                raw_data[col] = val
                                all_empty = False
                            else:
                                s = str(val).strip()
                                if s.lower() in ['nan', 'none', '']:
                                    raw_data[col] = None
                                else:
                                    raw_data[col] = s
                                    all_empty = False
                        
                        # Skip completely empty rows
                        if all_empty or all(v is None for v in raw_data.values()):
                            total_skipped += 1
                            continue
                        
                        records_to_create.append(ElectionOfficeholder(
                            source_file=filename,
                            source_sheet=sheet_name,
                            row_index=int(idx),
                            column_order=col_order,
                            raw_data=raw_data
                        ))
                    
                    if records_to_create:
                        with transaction.atomic():
                            ElectionOfficeholder.objects.bulk_create(records_to_create, batch_size=500)
                        total_saved += len(records_to_create)
                
                self.stdout.write(self.style.SUCCESS(f"   ✅ SAVED: {total_saved} records | SKIPPED (empty): {total_skipped}"))
                
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"   ❌ Error processing {filename}: {e}"))
        
        final_count = ElectionOfficeholder.objects.count()
        self.stdout.write(self.style.SUCCESS(f"\n🎉 Import complete! Total ElectionOfficeholder records in DB: {final_count}"))
