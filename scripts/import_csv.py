
import os
import glob
import csv
import sys

# Ensure app can be imported
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.workers.tasks.scanner_tasks import _save_credentials

def process_csv_files():
    # Look in data/ folder and root
    search_paths = [
        "*.csv",
        "data/*.csv"
    ]
    
    csv_files = []
    for path in search_paths:
        csv_files.extend(glob.glob(path))
        
    unique_files = list(set(csv_files))
    
    if not unique_files:
        print("ℹ️ No CSV files found to import.")
        return

    total_imported = 0
    
    for filepath in unique_files:
        print(f"\n📂 Processing {filepath}...")
        
        results = []
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                # auto-detect dialect or just assume standard
                reader = csv.DictReader(f)
                
                # Check headers
                if not reader.fieldnames:
                    print(f"   ⚠️ Skipping empty or invalid CSV: {filepath}")
                    continue
                    
                for row in reader:
                    # Support multiple header styles
                    token = row.get('token') or row.get('Token') or row.get('BOT_TOKEN')
                    chat_id = row.get('chat_id') or row.get('chatId') or row.get('CID')
                    
                    if not token:
                        continue
                        
                    # Cleanup strings
                    token = token.strip()
                    if chat_id:
                        chat_id = chat_id.strip()
                        if chat_id.lower() in ['none', 'null', '', 'nan']:
                            chat_id = None
                            
                    results.append({
                        "token": token,
                        "chat_id": chat_id,
                        "meta": {
                            "source_file": os.path.basename(filepath),
                            "origin": "manual_import"
                        }
                    })
                    
            if results:
                print(f"   ⚡ Found {len(results)} rows. Validating & Importing...")
                # _save_credentials checks DB existence + Validates via getMe
                saved = _save_credentials(results, "csv_import")
                total_imported += saved
                print(f"   ✅ Saved {saved} new/updated credentials from this file.")
            else:
                print("   ℹ️ No valid rows found.")
                
        except Exception as e:
            print(f"   ❌ Error reading file: {e}")

    print(f"\n🏁 Import Complete. Total records saved: {total_imported}")

if __name__ == "__main__":
    process_csv_files()
