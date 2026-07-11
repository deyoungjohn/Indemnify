import asyncio
import os
import sys
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider

async def send_tx(w3, account, tx_func, chain_id):
    nonce = await w3.eth.get_transaction_count(account.address, "pending")
    try:
        gas_est = await tx_func.estimate_gas({"from": account.address})
        gas = int(gas_est * 1.25)
    except Exception:
        gas = 250000

    tx_params = {
        "from": account.address,
        "nonce": nonce,
        "gas": gas,
        "chainId": chain_id
    }
    
    try:
        fee_history = await w3.eth.fee_history(1, "latest")
        base_fee = fee_history["baseFeePerGas"][-1]
        priority_fee = await w3.eth.max_priority_fee
        tx_params["maxFeePerGas"] = (base_fee * 2) + priority_fee
        tx_params["maxPriorityFeePerGas"] = priority_fee
    except Exception:
        tx_params["gasPrice"] = await w3.eth.gas_price

    tx = await tx_func.build_transaction(tx_params)
    signed_tx = w3.eth.account.sign_transaction(tx, private_key=account.key)
    tx_hash = await w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    print(f"Transaction broadcasted. Hash: {tx_hash.hex()}. Waiting...")
    receipt = await w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt["status"] != 1:
        raise RuntimeError(f"Transaction reverted on-chain. Hash: {tx_hash.hex()}")
    print(f"Transaction confirmed in block {receipt['blockNumber']}.")
    return receipt

async def main():
    print("\n--- Project Indemnify: Fund Pool Script ---")
    deployer_key = input("Enter Deployer Private Key: ").strip()
    pool_address = input("Enter UnderwriterPool Address (the new pool): ").strip()
    
    rpc_url = "https://rpc.xlayer.tech"
    w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
    deployer = w3.eth.account.from_key(deployer_key)
    chain_id = await w3.eth.chain_id
    
    usdt_address = w3.to_checksum_address("0x779ded0c9e1022225f8e0630b35a9b54be713736")
    pool_checksum = w3.to_checksum_address(pool_address)
    
    erc20_abi = [
        {"name": "approve", "type": "function", "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "outputs": [{"name": "", "type": "bool"}], "stateMutability": "nonpayable"},
        {"name": "allowance", "type": "function", "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view"}
    ]
    usdt_contract = w3.eth.contract(address=usdt_address, abi=erc20_abi)
    
    amount = 5 * (10 ** 6) # 5 USDT
    
    print("\nApproving 5 USDT for the pool...")
    current_allowance = await usdt_contract.functions.allowance(deployer.address, pool_checksum).call()
    if current_allowance > 0 and current_allowance < amount:
        print("Resetting allowance...")
        await send_tx(w3, deployer, usdt_contract.functions.approve(pool_checksum, 0), chain_id)
        
    if current_allowance < amount:
        await send_tx(w3, deployer, usdt_contract.functions.approve(pool_checksum, amount), chain_id)
        
    print("\nDepositing 5 USDT into the Pool...")
    pool_abi = [
        {"type": "function", "name": "deposit", "inputs": [{"name": "assets", "type": "uint256"}, {"name": "receiver", "type": "address"}], "outputs": [{"name": "shares", "type": "uint256"}], "stateMutability": "nonpayable"}
    ]
    pool_contract = w3.eth.contract(address=pool_checksum, abi=pool_abi)
    deposit_func = pool_contract.functions.deposit(amount, deployer.address)
    await send_tx(w3, deployer, deposit_func, chain_id)
    
    print("\nSuccessfully funded the pool with 5 USDT! You can now run tests.")

if __name__ == "__main__":
    asyncio.run(main())
