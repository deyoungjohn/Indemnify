import os
import sys
import json
import asyncio
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ESCROW_ADDRESS = os.environ.get("ESCROW_CONTRACT_ADDRESS")
USDT_ADDRESS = os.environ.get("USDT_ADDRESS") or "0x779ded0c9e1022225f8e0630b35a9b54be713736"  # Default X Layer Mainnet USDT

# Fallback list of public X Layer RPC URLs to test in order
FALLBACK_RPCS = [
    "https://rpc.xlayer.tech",
    "https://xlayerrpc.okx.com",
    "https://xlayer.drpc.org",
    "https://196.rpc.thirdweb.com"
]

# Fallback minimal ABI for querying variables
MINIMAL_ABI = [
    {
        "name": "owner",
        "type": "function",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view"
    },
    {
        "name": "oracleAddress",
        "type": "function",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view"
    },
    {
        "name": "assetToPool",
        "type": "function",
        "inputs": [{"name": "", "type": "address"}],
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view"
    }
]

async def get_active_connection():
    """Tries connecting to RPC endpoints sequentially with a timeout failover."""
    env_rpc = os.environ.get("RPC_URL") or os.environ.get("RPC_PROVIDER_URL")
    urls = [env_rpc] if env_rpc else FALLBACK_RPCS
    
    for url in urls:
        print(f"Connecting to RPC: {url}...")
        w3 = AsyncWeb3(AsyncHTTPProvider(url, request_kwargs={"timeout": 5.0}))
        try:
            # Query chain_id with a strict 5-second timeout to bypass hung/congested RPCs
            chain_id = await asyncio.wait_for(w3.eth.chain_id, timeout=5.0)
            print(f"Connected successfully! (Chain ID: {chain_id})")
            return w3, url
        except Exception as e:
            print(f"Connection failed or timed out for {url}: {e}\n")
            
    print("Error: All public RPC endpoints are currently unreachable.")
    sys.exit(1)

async def main():
    global ESCROW_ADDRESS
    print("\n--- Project Indemnify On-Chain Escrow Inspector ---")
    
    if not ESCROW_ADDRESS:
        ESCROW_ADDRESS = input("Enter ParametricEscrow Contract Address: ").strip()
        
    if not ESCROW_ADDRESS:
        print("Error: ESCROW_CONTRACT_ADDRESS is required.")
        sys.exit(1)

    w3, active_rpc = await get_active_connection()
    
    try:
        escrow_checksum = w3.to_checksum_address(ESCROW_ADDRESS)
        usdt_checksum = w3.to_checksum_address(USDT_ADDRESS)
    except ValueError as e:
        print(f"Error: Invalid address format. Details: {e}")
        sys.exit(1)

    escrow_contract = w3.eth.contract(address=escrow_checksum, abi=MINIMAL_ABI)

    print(f"\nQuerying Escrow Contract at: {escrow_checksum}\n" + "-"*50)
    
    # 1. Query Owner
    try:
        owner = await escrow_contract.functions.owner().call()
        print(f"Contract Owner (Deployer Wallet): {owner}")
        print("  -> NOTE: You MUST use the private key corresponding to this owner wallet")
        print("     address as the DEPLOYER_PRIVATE_KEY to change configuration.")
    except Exception as e:
        print(f"Failed to query owner(): {e}")

    # 2. Query Oracle Address
    try:
        oracle = await escrow_contract.functions.oracleAddress().call()
        print(f"Registered Oracle Address: {oracle}")
    except Exception as e:
        print(f"Failed to query oracleAddress(): {e}")

    # 3. Query Registered Pool for USDT
    try:
        pool = await escrow_contract.functions.assetToPool(usdt_checksum).call()
        if pool == "0x0000000000000000000000000000000000000000":
            print(f"Registered USDT Pool: None (0x000...000)")
            print("  -> You need to deploy an UnderwriterPool contract for USDT first, then register it.")
        else:
            print(f"Registered USDT Pool (UnderwriterPool Address): {pool}")
            print(f"  -> Use this address as your USDT_POOL_ADDRESS.")
    except Exception as e:
        print(f"Failed to query assetToPool(): {e}")

if __name__ == "__main__":
    asyncio.run(main())
