import asyncio
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider

async def main():
    rpc_url = "https://rpc.xlayer.tech"
    w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
    
    escrow_address = w3.to_checksum_address("0x4B218726007858FC77fb2Aa476bd547d13f14670")
    usdt_address = w3.to_checksum_address("0x779ded0c9e1022225f8e0630b35a9b54be713736")
    
    abi = [{"type": "function", "name": "assetToPool", "inputs": [{"name": "", "type": "address"}], "outputs": [{"name": "", "type": "address"}], "stateMutability": "view"}]
    contract = w3.eth.contract(address=escrow_address, abi=abi)
    
    pool = await contract.functions.assetToPool(usdt_address).call()
    print(f"Registered Pool for USDT: {pool}")

if __name__ == "__main__":
    asyncio.run(main())
