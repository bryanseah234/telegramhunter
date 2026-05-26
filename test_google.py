import urllib.request, json, os, sys
key = os.environ.get("GOOGLE_SEARCH_KEY", "")
cx = os.environ.get("GOOGLE_CSE_ID", "")
if not key or not cx:
    print("MISSING_ENV")
    sys.exit(0)
url = f"https://www.googleapis.com/customsearch/v1?key=***&cx={cx}&q=test&num=1"
try:
    with urllib.request.urlopen(url, timeout=10) as r:
        d = json.loads(r.read())
        info = d.get("searchInformation", {})
        n = info.get("totalResults", "0")
        print(f"OK n={n}")
except urllib.error.HTTPError as e:
    body = json.loads(e.read())
    err = body.get("error", {})
    print(f"FAIL http={e.code} status={err.get('status')} msg={err.get('message')[:80]}")
except Exception as e:
    print(f"ERR {type(e).__name__}: {str(e)[:80]}")
