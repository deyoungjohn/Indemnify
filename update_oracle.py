import asyncio
import os
import sys
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider

async def main():
    print("\n--- Project Indemnify: Fix On-Chain Oracle Address ---")
    deployer_key = input("Enter Deployer/Contract Owner Private Key: ").strip()
    if not deployer_key:
        print("Deployer key is required.")
        sys.exit(1)

    rpc_url = "https://rpc.xlayer.tech"
    escrow_address = "0x4B218726007858FC77fb2Aa476bd547d13f14670"
    
    # The public address of the oracle wallet the user funded
    new_oracle_address = "0xDd3C5F463d71fb06D7bE749F904A4090E080f407"

    w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
    account = w3.eth.account.from_key(deployer_key)
    chain_id = await w3.eth.chain_id

    print(f"\nConnecting to RPC: {rpc_url}")
    print(f"Using Deployer Address: {account.address}")
    
    escrow_checksum = w3.to_checksum_address(escrow_address)
    new_oracle_checksum = w3.to_checksum_address(new_oracle_address)
    
    abi = [{
        "name": "setOracleAddress",
        "type": "function",
        "inputs": [{"name": "newOracle", "type": "address"}],
        "outputs": [],
        "stateMutability": "nonpayable"
    }]
    
    contract = w3.eth.contract(address=escrow_checksum, abi=abi)
    tx_func = contract.functions.setOracleAddress(new_oracle_checksum)
    
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

    print(f"\nUpdating Oracle Address in Escrow -> {new_oracle_checksum}...")
    tx = await tx_func.build_transaction(tx_params)
    signed_tx = w3.eth.account.sign_transaction(tx, private_key=account.key)
    tx_hash = await w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    
    print(f"Transaction broadcasted. Hash: {tx_hash.hex()}. Waiting for confirmation...")
    receipt = await w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    
    if receipt["status"] != 1:
        print(f"Transaction reverted on-chain. Hash: {tx_hash.hex()}")
        sys.exit(1)
        
    print(f"Transaction confirmed successfully! The Oracle Address is now set to your funded wallet.")

if __name__ == "__main__":
    asyncio.run(main())
