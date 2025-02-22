# Matkap  
Matkap - hunt down malicious Telegram bots  


Matkap is a powerful tool designed to infiltrate and monitor Telegram bots by utilizing the Telegram Bot API and Telethon. It automates the process of capturing messages from malicious bots and forwarding them to your own account.
Disclaimer (Legal & Ethical Use)
Matkap is intended for educational and research purposes only. This tool is designed to help cybersecurity professionals analyze and understand Telegram bot interactions, particularly those that may pose security risks.

🔹 By using Matkap, you agree to the following terms:

You must not use this tool for illegal activities or unauthorized access.
You assume full responsibility for any actions performed with this tool.
The developers and contributors are not liable for any misuse, damages, or legal consequences arising from the use of Matkap.
Ensure you comply with Telegram's API Terms of Service and all applicable laws in your jurisdiction.
📌 If you do not agree with these terms, you should not use this tool.



![image](https://github.com/user-attachments/assets/f752b834-4a0a-48aa-9b3f-49a0307cfaa1)  

## 📌 Features  
- ✅ **Bot Token Validation** (`getMe`)  
- ✅ **Automated Interaction** (Send `/start` via **Telethon**)  
- ✅ **Message Extraction** (`getUpdates` to retrieve `chat_id` & `message_id`)  
- ✅ **Old Message Forwarding** (Try lower `message_id` values until a past message is found)  
- ✅ **Full Message Forwarding** (`1` to `last_message_id`)  
- ✅ **Stop & Resume** (Pause forwarding & continue later)  
- ✅ **Graphical Interface** (Tkinter-based GUI with logs & optional logo)  

## 🛠 Installation  

### 🔹 Prerequisites  
Before running **Matkap**, ensure you have the following:  

- **Python 3.7+** installed on your system.  
- **Required dependencies** (Install them using the command below):  
  ```bash
  pip install requests telethon pillow
🔹 Telegram API Credentials
Before using Matkap, you must obtain your Telegram API credentials:

Visit my.telegram.org/apps.
Log in with your phone number.
Create a new application and note the following values:
api_id
api_hash
phone_number (your Telegram account).
Insert these values into matkap.py (e.g., api_id = 123456, api_hash = "your_hash").
🔹 Cloning & Running Matkap
To start using Matkap, follow these steps:




```bash
>>git clone https://github.com/0x6rss/matkap.git  
>>cd matkap  
>>python matkap.py  


## Disclaimer
##




