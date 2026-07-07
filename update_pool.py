import asyncio
import os
import sys
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider

async def main():
    print("\n--- Project Indemnify: Register Underwriter Pool in Escrow ---")
    deployer_key = input("Enter Deployer/Contract Owner Private Key: ").strip()
    if not deployer_key:
        print("Deployer key is required.")
        sys.exit(1)

    rpc_url = "https://rpc.xlayer.tech"
    escrow_address = "0x4B218726007858FC77fb2Aa476bd547d13f14670"
    usdt_address = "0x779ded0c9e1022225f8e0630b35a9b54be713736"
    pool_address = "0x7fF1E8ED6A006685C1f350De9A3FE69e1A4e02D1"  # The pool you deployed earlier

    w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
    account = w3.eth.account.from_key(deployer_key)
    chain_id = await w3.eth.chain_id

    print(f"\nConnecting to RPC: {rpc_url}")
    print(f"Using Deployer Address: {account.address}")
    
    escrow_checksum = w3.to_checksum_address(escrow_address)
    usdt_checksum = w3.to_checksum_address(usdt_address)
    pool_checksum = w3.to_checksum_address(pool_address)
    
    abi = [{
        "name": "registerPool",
        "type": "function",
        "inputs": [{"name": "asset", "type": "address"}, {"name": "pool", "type": "address"}],
        "outputs": [],
        "stateMutability": "nonpayable"
    }]
    
    contract = w3.eth.contract(address=escrow_checksum, abi=abi)
    tx_func = contract.functions.registerPool(usdt_checksum, pool_checksum)
    
    nonce = await w3.eth.get_transaction_count(account.address)
    
    tx_params = {
        "from": account.address,
        "nonce": nonce,
        "chainId": chain_id
    }
    
    try:
        fee_history = await w3.eth.fee_history(1, "latest")
        base_fee = fee_history["baseFeePerGas"][-1]
        try:
            priority_fee = await w3.eth.max_priority_fee
        except Exception:
            priority_fee = w3.to_wei(2, "gwei")
        tx_params["maxFeePerGas"] = (base_fee * 2) + priority_fee
        tx_params["maxPriorityFeePerGas"] = priority_fee
    except Exception:
        tx_params["gasPrice"] = await w3.eth.gas_price
        
    try:
        gas_est = await tx_func.estimate_gas({"from": account.address})
        tx_params["gas"] = int(gas_est * 1.25)
    except Exception:
        tx_params["gas"] = 100000

    print(f"\nRegistering USDT Pool in Escrow -> {pool_checksum}...")
    tx = await tx_func.build_transaction(tx_params)
    signed_tx = w3.eth.account.sign_transaction(tx, private_key=account.key)
    tx_hash = await w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    
    print(f"Transaction broadcasted. Hash: {tx_hash.hex()}. Waiting for confirmation...")
    receipt = await w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    
    if receipt["status"] != 1:
        print(f"Transaction reverted on-chain. Hash: {tx_hash.hex()}")
        sys.exit(1)
        
    print(f"Transaction confirmed successfully! The USDT pool is now registered in the Escrow contract.")

if __name__ == "__main__":
    asyncio.run(main())
