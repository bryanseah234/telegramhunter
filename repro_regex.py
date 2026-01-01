import re

# Copy of logic from scanners.py
TOKEN_PATTERN = re.compile(r'\b\d{8,10}:[A-Za-z0-9_-]{35}\b')

def _is_valid_token(token_str: str) -> bool:
    try:
        if token_str.startswith("gAAAA"): return False
        if ":" not in token_str: return False
        if token_str.count(":") != 1: return False
        parts = token_str.split(":", 1)
        bot_id, secret = parts
        
        if not bot_id.isdigit(): return False
        if len(bot_id) < 8 or len(bot_id) > 10: return False
        if len(bot_id) > 1 and bot_id.startswith("0"): return False
        
        if len(secret) != 35:
            print(f"DEBUG: Secret length is {len(secret)}, expected 35")
            return False
        
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
        if not all(c in allowed for c in secret):
            print("DEBUG: Invalid chars in secret")
            return False
        
        if not secret.startswith("AA"): 
            print("DEBUG: Does not start with AA")
            return False
        
        is_pure_hex = all(c in "0123456789abcdefABCDEF" for c in secret)
        if is_pure_hex: return False

        return True
    except Exception as e:
        print(f"DEBUG: Exception {e}")
        return False

target_url = "https://pumped-beneficial-check.glitch.me/?bot_token=8100197511:AAHHQ6-MU1P5OCWTy8aMai3FazHyzCMezfM&chat_id=5612499816"
token = "8100197511:AAHHQ6-MU1P5OCWTy8aMai3FazHyzCMezfM"

print(f"Testing URL: {target_url}")
found = TOKEN_PATTERN.findall(target_url)
print(f"Regex found: {found}")

if found:
    for t in found:
        valid = _is_valid_token(t)
        print(f"Token '{t}' is valid? {valid}")
else:
    print("Regex FAILED to find token in URL.")
    
# Test token directly
print(f"\nTesting token directly: {token}")
print(f"Match? {TOKEN_PATTERN.match(token)}")
print(f"Valid? {_is_valid_token(token)}")
