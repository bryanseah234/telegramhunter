
import os
import asyncio
from supabase import create_client, Client
from dotenv import load_dotenv

# Load env variables
load_dotenv()


db_url: str = os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or os.environ.get("SUPABASE_URL")
anon_key: str = os.environ.get("NEXT_PUBLIC_SUPABASE_KEY") or os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")
service_key: str = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

if not db_url:
    print("Error: Missing SUPABASE_URL")
    exit(1)

if not anon_key:
    # try looking for it
    print("Warning: Missing defaults for anon key, printing env keys...")
    # print([k for k in os.environ.keys() if 'SUPABASE' in k])


supabase_anon: Client = create_client(db_url, anon_key) if anon_key else None
supabase_service: Client = create_client(db_url, service_key) if service_key else None

async def check_rls():
    if supabase_service:
        print(f"\n--- Checking access with SERVICE_ROLE key (should work) ---")
        try:
            # Try to select 1 row (safe fields)
            response = supabase_service.table("discovered_credentials").select("id, created_at, source, meta").limit(1).execute()
            print(f"Service role read success. Count: {len(response.data)}")
        except Exception as e:
            print(f"Service role failed: {e}")

    if not supabase_anon:
        print("Skipping Anon check due to missing key")
        return

    print(f"\n--- Checking access with ANON key (mimicking frontend) ---")
    
    # Try to SELECT count
    try:
        response = supabase_anon.table("discovered_credentials").select("count", count="exact").execute()
        print(f"Count response: {response}")
    except Exception as e:
        print(f"Count failed: {e}")
    
    # Try to select 1 row (safe fields)
    try:
        response = supabase_anon.table("discovered_credentials").select("id, created_at, source, meta").limit(1).execute()
        
        if len(response.data) > 0:
            print("✅ SUCCESS: Can read discovered_credentials")
            print(f"Sample: {response.data[0]}")
        else:
            # Check if table is empty via service role
            print("❌ FAILED: No data returned (could be empty table or RLS blocking)")
    except Exception as e:
        print(f"Read failed: {e}")

    # Try the join query from Sidebar
    print("\nChecking the specific JOIN query from Sidebar...")
    
    try:
        response = supabase_anon.from_("exfiltrated_messages")\
            .select("credential_id, discovered_credentials(id, created_at, source, meta)")\
            .limit(5)\
            .execute()
            
        print(f"Join query response data length: {len(response.data)}")
        if len(response.data) > 0:
            # print("First item keys: ", response.data[0].keys())
            # print("First item discovered_credentials: ", response.data[0].get('discovered_credentials'))
            
            if response.data[0].get('discovered_credentials') is None:
                print("❌ discovered_credentials is NULL -> RLS is likely blocking the join.")
            else:
                print("✅ discovered_credentials is populated.")
                
    except Exception as e:
        print(f"Error executing join: {e}")

if __name__ == "__main__":
    asyncio.run(check_rls())
