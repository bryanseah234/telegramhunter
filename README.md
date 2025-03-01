# Matkap  
Matkap - hunt down malicious Telegram bots  


Matkap is a powerful tool designed to infiltrate and monitor Telegram bots by utilizing the Telegram Bot API and Telethon. It automates the process of capturing messages from malicious bots and forwarding them to your own account.
<br>
## Disclaimer (Legal & Ethical Use)
Matkap is intended for educational and research purposes only. This tool is designed to help cybersecurity professionals analyze and understand Telegram bot interactions, particularly those that may pose security risks.

🔹 By using Matkap, you agree to the following terms:

You must not use this tool for illegal activities or unauthorized access.
You assume full responsibility for any actions performed with this tool.
The developers and contributors are not liable for any misuse, damages, or legal consequences arising from the use of Matkap.
Ensure you comply with Telegram's API Terms of Service and all applicable laws in your jurisdiction.
📌 If you do not agree with these terms, you should not use this tool.


## 📌 Features
 
- **FOFA & URLScan Integration** – Searches for leaked Bot Tokens / Chat IDs in websites
- **export logs** - export hunt logs
  
  




https://github.com/user-attachments/assets/44599ccd-4b99-461b-9967-913908882771



![image](https://github.com/user-attachments/assets/3b89f9c9-a7a5-48c4-b27d-ef2fc4d128dd)





## 🛠 Installation

### 🔹 Prerequisites
Before running **Matkap**, ensure you have the following:

- **Python 3.7+** installed on your system.
- **Pip** to install packages.
- An account on [my.telegram.org/apps](https://my.telegram.org/apps) to get your **Telegram API** credentials (`api_id`, `api_hash`, `phone_number`).
- **(Optional)** [FOFA Account](https://fofa.info/) & [URLScan Account](https://urlscan.io/) if you want scanning functionality:
  - **FOFA_EMAIL**, **FOFA_KEY** for FOFA
  - **URLSCAN_API_KEY** for URLScan

### 🔹 Telegram API Credentials (Using a `.env` File)

1. **Visit** [my.telegram.org/apps](https://my.telegram.org/apps) and log in with your phone number.  
2. **Create a new application** and note the following:
   - **api_id**
   - **api_hash**
   - **phone_number** (the Telegram account you want to use).
3. In your project folder, create a **`.env`** file and add:
   ```dotenv
   TELEGRAM_API_ID=123456
   TELEGRAM_API_HASH=your_api_hash
   TELEGRAM_PHONE=+900000000000

   # (Optional) For FOFA & URLScan:
   FOFA_EMAIL=your_fofa_email
   FOFA_KEY=your_fofa_key
   URLSCAN_API_KEY=your_urlscan_api_key





```bash
# Clone the repository
>>git clone https://github.com/0x6rss/matkap.git

# Navigate into the project folder
>>cd matkap

# Create and fill out your .env file 
# with TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE 
# (and FOFA_EMAIL, FOFA_KEY, URLSCAN_API_KEY if you plan to use them)

# Install dependencies
>>pip install -r requirements.txt

# Run Matkap
>>python matkap.py
```

## Usage

1. **Start Attack**  
   - Enter the malicious bot token (e.g., `bot12345678:ABC...`) and optionally a Chat ID for forwarding.  
   - Click **Start Attack** to validate and interact with the malicious bot.

2. **Forward All Messages**  
   - Forward older messages by iterating through message IDs.  
   - You can **Stop** or **Resume** forwarding at any time.

3. **Hunt With FOFA**  
   - Searches for exposed Bot Tokens / Chat IDs on sites indexed by FOFA (`body="api.telegram.org"`).  
   - Results appear in the **Process Log**.

4. **Hunt With URLScan**  
   - Similarly hunts for exposed tokens / chat IDs referencing `domain:api.telegram.org` using **URLScan**.  
   - Also logs them in the **Process Log**.

5. **Export Logs**  
   - Click **Export Logs** to save the **Process Log** to a `fofa_logs.txt` file.













