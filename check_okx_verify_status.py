import json
import hmac
import base64
import hashlib
import requests
from datetime import datetime, timezone

# --- 1. Fill in your OKX Dev Portal details here ---
API_KEY = "616b12fe-297f-4acf-aa49-3f7fe0c19bd5"
API_SECRET = "A9D9CD63D99CF03458ACBD50FD736731"
API_PASSPHRASE = "Oceanline_363"
GUID = "0x4b218726007858fc77fb2aa476bd547d13f14670" # Your exact ticket stub
# ---------------------------------------------------

# Build the payload OKX expects
payload = {
    "chainShortName": "XLAYER",
    "guid": GUID
}
body_str = json.dumps(payload, separators=(',', ':'))

# Generate the OKX signature
timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
request_path = "/api/v5/xlayer/contract/check-verify-result"
prehash_string = timestamp + "POST" + request_path + body_str

mac = hmac.new(bytes(API_SECRET, encoding='utf8'), bytes(prehash_string, encoding='utf-8'), digestmod=hashlib.sha256)
sign = base64.b64encode(mac.digest()).decode('utf-8')

# Send it
headers = {
    "Content-Type": "application/json",
    "OK-ACCESS-KEY": API_KEY,
    "OK-ACCESS-SIGN": sign,
    "OK-ACCESS-TIMESTAMP": timestamp,
    "OK-ACCESS-PASSPHRASE": API_PASSPHRASE
}

print("Checking verification status...")
response = requests.post("https://web3.okx.com" + request_path, headers=headers, data=body_str)

print("\n--- OKX Response ---")
print(f"Status Code: {response.status_code}")
print(response.json())