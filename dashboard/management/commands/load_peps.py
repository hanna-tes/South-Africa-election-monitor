import csv
from django.core.management.base import BaseCommand
from dashboard.models import PEP
from django.utils import timezone

class Command(BaseCommand):
    help = 'Load PEPs/Candidates from CSV file into the database'

    def add_arguments(self, parser):
        parser.add_argument('file_path', type=str, help='Absolute or relative path to the CSV file')

    def handle(self, *args, **kwargs):
        file_path = kwargs['file_path']
        created_count = 0
        updated_count = 0
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Handle possible header variations
                    name = (row.get('Name (English)') or row.get('full_name_en') or row.get('name', '')).strip()
                    if not name:
                        continue
                        
                    defaults = {
                        'title': row.get('Position', row.get('role', '')),
                        'x_link': row.get('X (Twitter) Link') if row.get('X (Twitter) Link') not in ['No verified personal account found', 'None', ''] else None,
                        'x_verified': row.get('Verified X (Twitter) Account (Yes/No)', '').lower() == 'yes',
                        'facebook_link': row.get('Facebook Link') if row.get('Facebook Link') not in ['No verified personal account found', 'None', ''] else None,
                        'facebook_verified': row.get('Verified Facebook Account (Yes/No)', '').lower() == 'yes',
                        'confidence_level': row.get('Confidence', 'medium').lower(),
                        'last_updated': timezone.now()  # Required for the sync badge
                    }
                    
                    obj, created = PEP.objects.update_or_create(name=name, defaults=defaults)
                    if created:
                        created_count += 1
                    else:
                        updated_count += 1
                        
            self.stdout.write(self.style.SUCCESS(
                f'✅ Done! Created: {created_count} | Updated: {updated_count} | Total processed: {created_count + updated_count}'
            ))
            
        except FileNotFoundError:
            self.stderr.write(self.style.ERROR(f'❌ File not found: {file_path}'))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'❌ Error processing file: {e}'))
