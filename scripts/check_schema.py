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

    tables = ["telegram_accounts", "discovered_credentials", "exfiltrated_messages"]
    
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
                print("    ⚠️ Table is empty. Cannot verify columns via SELECT *.")
                try:
                    meta = supabase.table("information_schema.columns")\
                        .select("column_name")\
                        .eq("table_name", table_name)\
                        .execute()
                    cols = [r.get("column_name") for r in (meta.data or []) if r.get("column_name")]
                    if cols:
                        print(f"    ✅ Columns found ({len(cols)}):")
                        for col in sorted(cols):
                            print(f"       - {col}")
                    else:
                        print("    ⚠️ No column metadata returned.")
                except Exception as e:
                    print(f"    ⚠️ Column metadata lookup failed: {e}")

        except Exception as e:
            print(f"    ❌ Error inspecting table: {e}")

if __name__ == "__main__":
    check_schema()
