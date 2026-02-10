from app.core.database import db
import sys

def wipe_data():
    print("⚠️  WARNING: This will delete ALL discovered credentials and messages!")
    print("    Use this if your encryption key was lost and old data is unreadable.")
    print("    Type 'DELETE' to confirm:")
    
    choice = input("> ")
    if choice.strip() != "DELETE":
        print("❌ Aborted.")
        return

    print("🗑️  Wiping 'exfiltrated_messages'...")
    # Delete all rows (neq 0 is a hack to select all if no better way, 
    # but supabase-py usually needs a condition. id > 0 works for int ids, 
    # but these are UUIDs or BigInts? Let's try .neq('id', '00000000-0000-0000-0000-000000000000'))
    try:
        # Better: Delete based on a condition that is always true? 
        # Or just specific IDs. Since we want to wipe ALL, we might need a stored procedure or loop.
        # But for 'discovered_credentials', we can delete where ID is not null.
        
        # 1. Messages first (Foreign Key constraint)
        db.table("exfiltrated_messages").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
        print("    ✅ Messages wiped.")
        
        # 2. Credentials
        print("🗑️  Wiping 'discovered_credentials'...")
        db.table("discovered_credentials").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
        print("    ✅ Credentials wiped.")
        
        print("\n✨ System is clean. You can now start scanning with your NEW key.")
        
    except Exception as e:
        print(f"❌ Error wiping data: {e}")
        print("   If the delete failed, you might need to use the Supabase Dashboard.")

if __name__ == "__main__":
    wipe_data()
