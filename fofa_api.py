import os
import base64
import requests
import re
from dotenv import load_dotenv

load_dotenv()

FOFA_EMAIL = os.getenv("FOFA_EMAIL", "")
FOFA_KEY = os.getenv("FOFA_KEY", "")


BOT_PATTERN = re.compile(r'(?:bot)?\d+:[A-Za-z0-9_-]{35,}')


CHAT_ID_PATTERN = re.compile(
    r'(?:["\'](-?\d{7,14})["\'])'          
    r'|(?:chat_id|from_chat_id)\W+(-?\d{7,14})'  
)


def search_fofa_and_hunt():
    if not (FOFA_EMAIL and FOFA_KEY):
        return [("Error: FOFA_EMAIL or FOFA_KEY missing!", [], [])]

    query = 'body="api.telegram.org"'
    qbase64 = base64.b64encode(query.encode()).decode()
    url = (f"https://fofa.info/api/v1/search/all?"
           f"email={FOFA_EMAIL}&key={FOFA_KEY}&qbase64={qbase64}"
           f"&fields=host,ip,port&page=1&size=50")

    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if data.get("error"):
            return [(f"FOFA API Error: {data.get('errmsg')}", [], [])]

        if not data.get("results"):
            return [("No results from FOFA", [], [])]

        results_list = []
        for row in data["results"]:

            host = row[0]
            ip = row[1] if len(row) > 1 else ""
            port = row[2] if len(row) > 2 else ""


            if host.startswith("http://") or host.startswith("https://"):
                site_url = host
            else:
                if port in ("443","8443"):
                    site_url = f"https://{host}"
                    if port != "443":
                        site_url += f":{port}"
                elif port in ("80","8080"):
                    site_url = f"http://{host}"
                    if port not in ("80"):
                        site_url += f":{port}"
                else:
                    site_url = f"http://{host}"
                    if port and port.isdigit():
                        site_url += f":{port}"

            try:
                r2 = requests.get(site_url, timeout=10, verify=False)
                html = r2.text

                found_tokens = BOT_PATTERN.findall(html)


                chat_matches = CHAT_ID_PATTERN.findall(html)
                found_chats = []
                for (g1, g2) in chat_matches:
                    if g1:
                        found_chats.append(g1)
                    elif g2:
                        found_chats.append(g2)

                results_list.append((site_url, found_tokens, found_chats))

            except Exception as ex:
                err_msg = f"Error fetching {site_url} => {ex}"
                results_list.append((err_msg, [], []))

        return results_list

    except Exception as e:
        return [(f"FOFA Request Error: {e}", [], [])]
