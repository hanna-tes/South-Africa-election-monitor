import os
import pandas as pd
import numpy as np
from django.core.management.base import BaseCommand
from dashboard.models import ElectionOfficeholder
from django.db import transaction

class Command(BaseCommand):
    help = 'Load Excel sheets exactly as-is into database'

    def add_arguments(self, parser):
        parser.add_argument('folder', type=str)

    def clean_row(self, row):
        cleaned = {}
        for col, val in row.items():
            if pd.isna(val) or (isinstance(val, float) and np.isnan(val)):
                cleaned[col] = None
            elif isinstance(val, (np.integer, np.floating)):
                cleaned[col] = int(val) if isinstance(val, np.integer) else float(val)
            else:
                str_val = str(val).strip()
                cleaned[col] = None if str_val.lower() in ['nan', 'none', 'null', ''] else str_val
        return cleaned

    def handle(self, *args, **options):
        folder = options['folder']
        if not os.path.isdir(folder):
            self.stderr.write(f"❌ Folder not found: {folder}")
            return

        for filename in sorted(os.listdir(folder)):
            if not filename.endswith('.xlsx'):
                continue
            filepath = os.path.join(folder, filename)
            self.stdout.write(f"\n📂 Processing: {filename}")

            ElectionOfficeholder.objects.filter(source_file=filename).delete()
            xl = pd.ExcelFile(filepath)
            records_to_create = []

            for sheet_name in xl.sheet_names:
                df = pd.read_excel(xl, sheet_name=sheet_name)
                if df.empty: continue
                
                # ✅ Save exact column order as strings
                col_order = [str(c).strip() for c in df.columns]

                for idx, row in df.iterrows():
                    if all(pd.isna(v) or str(v).strip().lower() in ['nan', 'none', 'null', ''] for v in row.values):
                        continue
                    
                    records_to_create.append(ElectionOfficeholder(
                        source_file=filename,
                        source_sheet=sheet_name,
                        row_index=idx,
                        column_order=col_order,  # ✅ Preserve order
                        raw_data=self.clean_row(row)
                    ))

            if records_to_create:
                with transaction.atomic():
                    ElectionOfficeholder.objects.bulk_create(records_to_create, batch_size=500)
                self.stdout.write(f"  ✅ Imported {len(records_to_create)} rows from {len(xl.sheet_names)} sheets")

        self.stdout.write("\n🏁 Import complete.")
