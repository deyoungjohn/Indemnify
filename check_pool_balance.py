import asyncio
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider

async def main():
    rpc_url = "https://rpc.xlayer.tech"
    w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
    
    usdt_address = w3.to_checksum_address("0x779ded0c9e1022225f8e0630b35a9b54be713736")
    pool_address = w3.to_checksum_address("0x53b3BaBa09e5Ba7420cB33015DE78cA23244B4B5")
    
    erc20_abi = [{"name": "balanceOf", "type": "function", "inputs": [{"name": "account", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view"}]
    usdt_contract = w3.eth.contract(address=usdt_address, abi=erc20_abi)
    
    bal = await usdt_contract.functions.balanceOf(pool_address).call()
    print(f"Pool USDT Balance: {bal / 10**6} USDT")

if __name__ == "__main__":
    asyncio.run(main())
