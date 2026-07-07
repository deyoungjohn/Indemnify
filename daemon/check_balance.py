import os
import sys
import json
import asyncio
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider

RPC_URL = "https://rpc.xlayer.tech"
DEPLOYER_ADDRESS = "0xC1012a56A20A7481718092a7f65cEA1fd2ebE4E7"
POOL_ADDRESS = "0xCcFC9454797e03573680719cD7B13f2Ec0E8f66e"

POOL_ABI = [
    {
        "name": "underlyingAsset",
        "type": "function",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view"
    }
]

ERC20_ABI = [
    {
        "name": "balanceOf",
        "type": "function",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view"
    },
    {
        "name": "allowance",
        "type": "function",
        "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view"
    },
    {
        "name": "decimals",
        "type": "function",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view"
    },
    {
        "name": "symbol",
        "type": "function",
        "inputs": [],
        "outputs": [{"name": "", "type": "string"}],
        "stateMutability": "view"
    }
]

async def main():
    w3 = AsyncWeb3(AsyncHTTPProvider(RPC_URL))
    
    deployer_checksum = w3.to_checksum_address(DEPLOYER_ADDRESS)
    pool_checksum = w3.to_checksum_address(POOL_ADDRESS)
    
    pool = w3.eth.contract(address=pool_checksum, abi=POOL_ABI)
    
    try:
        # Query the underlying asset token that the pool was deployed with
        underlying_token_addr = await pool.functions.underlyingAsset().call()
        underlying_checksum = w3.to_checksum_address(underlying_token_addr)
        
        token = w3.eth.contract(address=underlying_checksum, abi=ERC20_ABI)
        
        decimals = await token.functions.decimals().call()
        symbol = await token.functions.symbol().call()
        balance = await token.functions.balanceOf(deployer_checksum).call()
        allowance = await token.functions.allowance(deployer_checksum, pool_checksum).call()
        
        print("\n--- Diagnostic Asset & Balance Report ---")
        print(f"UnderwriterPool Address: {pool_checksum}")
        print(f"Pool's Configured Asset: {underlying_checksum} (Symbol: {symbol})")
        print(f"Deployer Address: {deployer_checksum}")
        print(f"Deployer Asset Balance: {balance / (10**decimals)} {symbol} ({balance} raw)")
        print(f"Pool Asset Allowance: {allowance / (10**decimals)} {symbol} ({allowance} raw)")
    except Exception as e:
        print(f"Error querying contracts: {e}")

if __name__ == "__main__":
    asyncio.run(main())
