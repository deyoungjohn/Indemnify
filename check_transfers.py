import asyncio
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider

async def main():
    rpc_url = "https://rpc.xlayer.tech"
    w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
    
    usdt_address = w3.to_checksum_address("0x779ded0c9e1022225f8e0630b35a9b54be713736")
    pool_address = w3.to_checksum_address("0x53b3baba09e5ba7420cb33015de78ca23244b4b5")
    client_address = w3.to_checksum_address("0xdbb63358192bde9ff8c782ab9d35d25d35e8abfe")
    
    erc20_abi = [
        {"name": "Transfer", "type": "event", "anonymous": False, "inputs": [{"indexed": True, "name": "from", "type": "address"}, {"indexed": True, "name": "to", "type": "address"}, {"indexed": False, "name": "value", "type": "uint256"}]}
    ]
    usdt_contract = w3.eth.contract(address=usdt_address, abi=erc20_abi)
    
    print(f"Checking Transfer events for USDT from Pool {pool_address} to Client {client_address}...")
    # Get current block
    tip = await w3.eth.block_number
    # Query last 10000 blocks
    start_block = max(0, tip - 10000)
    
    try:
        events = []
        chunk_size = 100
        for b in range(start_block, tip + 1, chunk_size):
            chunk_events = await usdt_contract.events.Transfer().get_logs(from_block=b, to_block=min(b + chunk_size - 1, tip))
            events.extend(chunk_events)
            
        found = False
        for e in events:
            if e['args']['from'].lower() == pool_address.lower() and e['args']['to'].lower() == client_address.lower():
                print(f"FOUND TRANSFER: {e['args']['value'] / 10**6} USDT transferred to Client in Block {e['blockNumber']}")
                found = True
        if not found:
            print("No Transfer events found from the Pool to the Client in the last 10,000 blocks.")
    except Exception as e:
        print(f"Error querying events: {e}")

if __name__ == "__main__":
    asyncio.run(main())
