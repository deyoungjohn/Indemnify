from fastapi.testclient import TestClient
from daemon.main import app

client = TestClient(app)

print("Test 3: Intent-Based Swap")
intent_payload = {
    "client_address": "0x2348d825ca5a45bd1301d6eeca5436102ed5735f",
    "coverage_requested": 500000,
    "timeout_duration": 300,
    "intent": {
        "action": "swap",
        "protocol": "quickswap",
        "token_in": "0x779ded0c9e1022225f8e0630b35a9b54be713736",
        "token_out": "0x1F2231A858880d4681caEBCAeBA500E5a9d82136",
        "amount_in": 1000000
    }
}
headers = {"X-402-Payment": "0xabc123"}
response = client.post("/v1/insurance/quote", json=intent_payload, headers=headers)
print(f"Status: {response.status_code}")
# The response should ideally be 200 OK or 400 with a payment error since "0xabc123" is a fake hash.
# If it's a payment error, we know the intent parsing and header extraction succeeded!
print(f"Body: {response.text}\n")
