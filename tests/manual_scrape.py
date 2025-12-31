import asyncio
import sys
import os

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.workers.celery_app import app
from app.core.config import settings

def manual_enrich_trigger():
    print("🚀 Manually Triggering Scraping Task...")
    
    # Payload simulating a discovered credential
    # You can replace the token with a real one you want to test
    payload = {
        "token": "7084570073:AAH_XXXXXXXXXXXXX_REPLACE_WITH_REAL_TOKEN", 
        "source": "manual_test",
        "url": "http://localhost/manual_test"
    }
    
    # We trigger the 'enrich_credential' task which:
    # 1. Enriches the token (finds bot ID, username)
    # 2. DISCOVERS CHATS (Scraping part 1)
    # 3. Triggers exfiltration (Scraping part 2)
    
    task_name = "flow.enrich_credential"
    print(f"Sending task: {task_name}")
    print(f"Payload: {payload}")
    
    task = app.send_task(task_name, args=[payload])
    print(f"✅ Task Sent! ID: {task.id}")
    print("Check worker logs for output: docker-compose logs -f worker")

if __name__ == "__main__":
    manual_enrich_trigger()
