import base64
import os

SESSION_FILE = "user_session.session"

def export_session():
    if not os.path.exists(SESSION_FILE):
        print(f"❌ Error: {SESSION_FILE} not found. Run 'scripts/login_user.py' first.")
        return

    with open(SESSION_FILE, "rb") as f:
        data = f.read()
        encoded = base64.b64encode(data).decode('utf-8')
        
    print("\n✅ Session Exported via Base64!")
    print("--------------------------------------------------")
    print("USER_SESSION_STRING")
    print("--------------------------------------------------")
    print(encoded)
    print("--------------------------------------------------")
    print("Copy the string above and add it to your Railway variables as 'USER_SESSION_STRING'.")

if __name__ == "__main__":
    export_session()
