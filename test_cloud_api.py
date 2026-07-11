import asyncio
import sys
import os

# Add root directory to python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sdk.indemnify_client import IndemnifyClient, RiskThresholdExceeded, PremiumBudgetExceeded

async def test_cloud_endpoint(api_url: str):
    print(f"============================================================")
    print(f"🧪 Testing Indemnify Cloud Endpoint: {api_url}")
    print(f"============================================================")
    
    # Initialize the client pointing to the cloud URL
    client = IndemnifyClient(base_url=api_url, max_p_fail_bps=8000)
    
    # Dummy data for the test (using a random contract to simulate a standard transfer or swap)
    client_address = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    target_contract = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
    calldata_hex = "0x"
    coverage = 1000000000000000000  # 1 Asset token
    
    async with client:
        print("\n1️⃣  Running Lightweight Risk Simulation (/v1/risk/simulate)...")
        try:
            sim = await client.simulate_risk(
                client_address=client_address,
                target_contract=target_contract,
                calldata_hex=calldata_hex,
                coverage_requested=coverage
            )
            print("✅ Simulation Success!")
            print(f"   P_fail: {sim.P_fail} bps ({client.classify_risk(sim.P_fail)})")
            print(f"   Executable: {sim.is_executable}")
            print(f"   Detected Vectors: {sim.detected_vectors}")
        except Exception as e:
            print(f"❌ Simulation Failed: {e}")
            return

        print("\n2️⃣  Requesting Oracle-Signed Quote (/v1/insurance/quote)...")
        try:
            quote = await client.get_quote(
                client_address=client_address,
                target_contract=target_contract,
                calldata_hex=calldata_hex,
                coverage_requested=coverage,
                timeout_duration=3600
            )
            print("✅ Quote Received & Oracle Signature Valid!")
            print(f"   Quote ID: {quote.quote_id}")
            print(f"   Premium:  {quote.premium_amount} (base units)")
            print(f"   Deadline: {quote.deadline} (Expires in {quote.seconds_until_expiry:.0f}s)")
            print(f"   Signature: {quote.signature[:15]}...{quote.signature[-10:]}")
            
            print("\n3️⃣  Ready for On-Chain Escrow!")
            print("   Arguments for ParametricEscrow.createPolicy():")
            for k, v in quote.to_create_policy_args().items():
                print(f"      - {k}: {v}")
                
        except RiskThresholdExceeded as e:
            print(f"⚠️ Risk Threshold Exceeded (Expected behavior if risk is high): {e}")
        except PremiumBudgetExceeded as e:
            print(f"⚠️ Premium Budget Exceeded: {e}")
        except Exception as e:
            print(f"❌ Quote Generation Failed: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_cloud_api.py <http://YOUR_EC2_IP_OR_DOMAIN:8000>")
        sys.exit(1)
        
    url = sys.argv[1]
    asyncio.run(test_cloud_endpoint(url))
