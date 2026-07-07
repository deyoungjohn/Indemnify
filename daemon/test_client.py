import httpx
import json
import time

def run_tests():
    client_address = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    target_contract = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
    
    print("Sending POST /v1/risk/simulate...")
    start = time.perf_counter()
    try:
        r = httpx.post("http://127.0.0.1:8000/v1/risk/simulate", json={
            "client_address": client_address,
            "target_contract": target_contract,
            "calldata_hex": "0x",
            "value_wei": 0,
            "coverage_requested": 1000
        }, timeout=10.0)
        print(f"Status Code: {r.status_code}")
        print(f"Response in {(time.perf_counter() - start)*1000:.2f}ms:")
        print(json.dumps(r.json(), indent=2))
    except Exception as e:
        print(f"Error: {e}")

    print("\nSending POST /v1/insurance/quote...")
    start = time.perf_counter()
    try:
        r = httpx.post("http://127.0.0.1:8000/v1/insurance/quote", json={
            "client_address": client_address,
            "target_contract": target_contract,
            "calldata_hex": "0x",
            "value_wei": 0,
            "coverage_requested": 1000000,
            "timeout_duration": 3600
        }, timeout=10.0)
        print(f"Status Code: {r.status_code}")
        print(f"Response in {(time.perf_counter() - start)*1000:.2f}ms:")
        print(json.dumps(r.json(), indent=2))
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    run_tests()
