from django.core.management.base import BaseCommand
from dashboard.utils.csv_processor import process_uploaded_csv
import os

class Command(BaseCommand):
    help = 'Import and process a CSV file into the database'
    
    def add_arguments(self, parser):
        parser.add_argument('file_path', type=str, help='Path to CSV file')
        parser.add_argument('--type', type=str, default='custom', 
                          help='Data type: meltwater, civicsignal, tiktok, openmeasure, custom')
        parser.add_argument('--name', type=str, default='Bulk Import', 
                          help='Source name for the data')
    
    def handle(self, *args, **options):
        file_path = options['file_path']
        data_type = options['type']
        source_name = options['name']
        
        if not os.path.exists(file_path):
            self.stderr.write(self.style.ERROR(f"File not found: {file_path}"))
            return
        
        self.stdout.write(f"🔄 Processing {file_path}...")
        success, message, count = process_uploaded_csv(file_path, data_type, source_name)
        
        if success:
            self.stdout.write(self.style.SUCCESS(f"✅ {message}"))
        else:
            self.stderr.write(self.style.ERROR(f"❌ {message}"))
