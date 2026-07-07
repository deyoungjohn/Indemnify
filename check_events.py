import asyncio
import os
import sys
import json
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider

async def main():
    rpc_url = "https://rpc.xlayer.tech"
    escrow_address = "0x4B218726007858FC77fb2Aa476bd547d13f14670"
    
    w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
    escrow_checksum = w3.to_checksum_address(escrow_address)
    
    abi = [
        {
            "name": "PolicyCreated",
            "type": "event",
            "anonymous": False,
            "inputs": [
                {"indexed": True, "name": "policyId", "type": "uint256"},
                {"indexed": True, "name": "client", "type": "address"},
                {"indexed": True, "name": "asset", "type": "address"},
                {"indexed": False, "name": "coverageAmount", "type": "uint256"},
                {"indexed": False, "name": "premiumPaid", "type": "uint256"},
                {"indexed": False, "name": "startTimestamp", "type": "uint256"},
                {"indexed": False, "name": "timeoutDuration", "type": "uint256"}
            ]
        }
    ]
    
    contract = w3.eth.contract(address=escrow_checksum, abi=abi)
    print("Fetching PolicyCreated events in block 64592842...")
    try:
        events = await contract.events.PolicyCreated().get_logs(
            from_block=64592842,
            to_block=64592842
        )
        print(f"Found {len(events)} events.")
        for e in events:
            print(e)
    except Exception as ex:
        print("Error:", ex)

if __name__ == "__main__":
    asyncio.run(main())
