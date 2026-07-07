import asyncio
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider

async def main():
    rpc_url = "https://rpc.xlayer.tech"
    w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
    
    escrow_address = w3.to_checksum_address("0x4B218726007858FC77fb2Aa476bd547d13f14670")
    
    abi = [{"type": "function", "name": "policies", "inputs": [{"name": "id", "type": "uint256"}], "outputs": [
        {"name": "clientAddress", "type": "address"},
        {"name": "asset", "type": "address"},
        {"name": "coverageAmount", "type": "uint256"},
        {"name": "premiumPaid", "type": "uint256"},
        {"name": "startTimestamp", "type": "uint256"},
        {"name": "timeoutDuration", "type": "uint256"},
        {"name": "riskBracketTier", "type": "uint8"},
        {"name": "status", "type": "uint8"}
    ], "stateMutability": "view"}]
    
    contract = w3.eth.contract(address=escrow_address, abi=abi)
    
    # Query Policy 2 and 3 and 4
    for i in range(1, 6):
        try:
            pol = await contract.functions.policies(i).call()
            if pol[0] != "0x0000000000000000000000000000000000000000":
                print(f"Policy {i}: Tier={pol[6]}, Status={pol[7]}, Coverage={pol[2]}, Premium={pol[3]}")
        except Exception as e:
            pass

if __name__ == "__main__":
    asyncio.run(main())
