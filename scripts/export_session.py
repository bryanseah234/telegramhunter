import base64
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESSION_FILE = os.path.join(BASE_DIR, "user_session.session")

def export_session():
    if not os.path.exists(SESSION_FILE):
        print(f"❌ Error: {SESSION_FILE} not found. Run 'scripts/login_user.py' first.")
        return

    with open(SESSION_FILE, "rb") as f:
        data = f.read()
        encoded = base64.b64encode(data).decode('utf-8')
        
    print("\n✅ Session Exported via Base64!")
    print("--------------------------------------------------")
    
    if len(encoded) < 30000:
        print("USER_SESSION_STRING")
        print("--------------------------------------------------")
        print(encoded)
        print("--------------------------------------------------")
        print("Copy the string above to 'USER_SESSION_STRING'.")
    else:
        print("⚠️ Session is too large for a single variable (>32KB).")
        print("Please add the following separate variables to Railway:")
        print("--------------------------------------------------")
        
        chunk_size = 30000
        chunks = [encoded[i:i+chunk_size] for i in range(0, len(encoded), chunk_size)]
        
        for idx, chunk in enumerate(chunks, 1):
            var_name = f"USER_SESSION_STRING_{idx}"
            print(f"\n{var_name}:")
            print(chunk)
            print("-" * 20)
            
    print("\n(The application will automatically stitch them together)")

if __name__ == "__main__":
    export_session()
