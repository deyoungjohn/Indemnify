import asyncio
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider

async def main():
    w3 = AsyncWeb3(AsyncHTTPProvider('https://rpc.xlayer.tech'))
    abi = [{'inputs': [], 'name': 'escrow', 'outputs': [{'internalType': 'address', 'name': '', 'type': 'address'}], 'stateMutability': 'view', 'type': 'function'}]
    contract = w3.eth.contract(address='0x7fF1E8ED6A006685C1f350De9A3FE69e1A4e02D1', abi=abi)
    print(await contract.functions.escrow().call())

if __name__ == "__main__":
    asyncio.run(main())
