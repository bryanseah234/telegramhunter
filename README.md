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



https://github.com/user-attachments/assets/44599ccd-4b99-461b-9967-913908882771



![image](https://github.com/user-attachments/assets/df682873-4c02-4631-b278-fa7a100c57dc)




## 🛠 Installation  

### 🔹 Prerequisites  
Before running **Matkap**, ensure you have the following:  

- **Python 3.7+** installed on your system.  
- **Required dependencies** (Install them using the command below):  

### 🔹 Telegram API Credentials (Using a .env File)

Before using **Matkap**, you must obtain your **Telegram API credentials** from [my.telegram.org/apps](https://my.telegram.org/apps):

1. **Visit** [my.telegram.org/apps](https://my.telegram.org/apps).  
2. **Log in** with your phone number.  
3. **Create a new application** and note the following values:
   - **api_id**
   - **api_hash**
   - **phone_number** (your Telegram account)
4. **Create a `.env` file** in your project folder and add these credentials:
   ```env
   TELEGRAM_API_ID=123456
   TELEGRAM_API_HASH=your_hash
   TELEGRAM_PHONE=+900000000000








```bash
# Clone the repository
>>git clone https://github.com/0x6rss/matkap.git

# Navigate into the project folder
>>cd matkap

# Create and fill out your .env file with TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE

# Install dependencies
>>pip install -r requirements.txt

# Run Matkap
>>python matkap.py












