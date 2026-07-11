import asyncio
import os
import sys
import json
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider

async def send_tx(w3, account, tx_func, chain_id):
    nonce = await w3.eth.get_transaction_count(account.address)
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
    print("\n--- Project Indemnify: Recover Funds Script ---")
    deployer_key = input("Enter Deployer Private Key: ").strip()
    client_address = input("Enter Client Wallet Address to receive the refund: ").strip()
    pool_address = input("Enter UnderwriterPool Address (the new pool): ").strip()
    
    rpc_url = "https://rpc.xlayer.tech"
    w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
    deployer = w3.eth.account.from_key(deployer_key)
    chain_id = await w3.eth.chain_id
    
    usdt_address = w3.to_checksum_address("0x779ded0c9e1022225f8e0630b35a9b54be713736")
    pool_checksum = w3.to_checksum_address(pool_address)
    client_checksum = w3.to_checksum_address(client_address)

    # ABI for withdrawing
    pool_abi = [
        {"type": "function", "name": "freeLiquidity", "inputs": [], "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view"},
        {"type": "function", "name": "withdraw", "inputs": [{"name": "assets", "type": "uint256"}, {"name": "receiver", "type": "address"}, {"name": "owner", "type": "address"}], "outputs": [{"name": "shares", "type": "uint256"}], "stateMutability": "nonpayable"}
    ]
    pool_contract = w3.eth.contract(address=pool_checksum, abi=pool_abi)
    
    # 1. Check max withdrawable amount for Deployer
    max_assets = await pool_contract.functions.freeLiquidity().call()
    print(f"\nMax Withdrawable from Pool: {max_assets / 10**6:.6f} USDT")
    
    if max_assets == 0:
        print("No funds to withdraw from the pool! They might have already been withdrawn.")
        return
        
    # 2. Withdraw everything to Deployer Wallet
    print(f"Withdrawing {max_assets / 10**6:.6f} USDT from Pool to Deployer Wallet...")
    withdraw_func = pool_contract.functions.withdraw(max_assets, deployer.address, deployer.address)
    await send_tx(w3, deployer, withdraw_func, chain_id)
    
    # 3. Transfer 5.48 USDT back to Client
    refund_amount = 5480000  # 5.48 USDT
    print(f"\nTransferring {refund_amount / 10**6:.6f} USDT from Deployer to Client Wallet...")
    
    erc20_abi = [
        {"name": "transfer", "type": "function", "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}], "outputs": [{"name": "", "type": "bool"}], "stateMutability": "nonpayable"}
    ]
    usdt_contract = w3.eth.contract(address=usdt_address, abi=erc20_abi)
    
    transfer_func = usdt_contract.functions.transfer(client_checksum, refund_amount)
    await send_tx(w3, deployer, transfer_func, chain_id)
    
    print("\nFunds successfully recovered and sent to Client Wallet!")

if __name__ == "__main__":
    asyncio.run(main())
