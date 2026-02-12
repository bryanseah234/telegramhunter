import os
import sys
import json
from supabase import create_client

# Ensure project root is in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings

def check_schema():
    print("🔍 Inspecting Live Supabase Schema...")
    
    url = settings.SUPABASE_URL
    key = settings.SUPABASE_SERVICE_ROLE_KEY
    supabase = create_client(url, key)

    tables = ["discovered_credentials", "exfiltrated_messages"]
    
    for table_name in tables:
        print(f"\n📋 Table: {table_name}")
        try:
            # Attempt to select a single row to see keys
            response = supabase.table(table_name).select("*").limit(1).execute()
            data = response.data
            
            if data:
                # If we have data, we can see the columns clearly
                row = data[0]
                print(f"    ✅ Columns found ({len(row)}):")
                for key in sorted(row.keys()):
                    print(f"       - {key}")
            else:
                # If table is empty, we can't easily see columns without inserting dummy data
                # But we can try to get metadata via RPC if available, or just warn.
                print("    ⚠️ Table is empty. Cannot verify columns via SELECT *.")
                
                # Try to fetch DB metadata if user has a function for it (unlikely but worth a try)
                # Or try to run an explain/describe via postgrest? No direct way.
                # We will rely on the "Silent Failure" test - try to insert a row with chat_id
                if table_name == "exfiltrated_messages":
                    print("    🧪 specific test: Checking if 'chat_id' is accepted...")
                    try:
                        dummy = {
                            "credential_id": "00000000-0000-0000-0000-000000000000", # Invalid UUID likely to fail FK but check payload first
                            "telegram_msg_id": 999999,
                            "chat_id": 123456 # The controversial column
                        }
                        # We expect this to fail with "Column not found" or "FK constraint"
                        supabase.table(table_name).insert(dummy).execute()
                    except Exception as e:
                        err = str(e)
                        if "column" in err and "does not exist" in err:
                             print(f"    ❌ CONFIRMED: 'chat_id' column DOES NOT EXIST.")
                        else:
                             print(f"    ℹ️ Insert result: {err}")

        except Exception as e:
            print(f"    ❌ Error inspecting table: {e}")

if __name__ == "__main__":
    check_schema()
