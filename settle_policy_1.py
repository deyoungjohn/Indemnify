import asyncio
import os
import sys
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

async def send_tx(w3, account, tx_func, chain_id):
    nonce = await w3.eth.get_transaction_count(account.address)
    gas_est = await tx_func.estimate_gas({"from": account.address})
    gas = int(gas_est * 1.25)
    
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
    print(f"Settling Policy 1... Hash: {tx_hash.hex()}")
    await w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

async def main():
    rpc_url = "https://rpc.xlayer.tech"
    oracle_key = os.environ.get("ORACLE_PRIVATE_KEY")
    escrow_address = "0x4B218726007858FC77fb2Aa476bd547d13f14670"
    
    w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
    oracle_account = w3.eth.account.from_key(oracle_key)
    chain_id = await w3.eth.chain_id
    
    abi = [{"name": "settlePolicy", "type": "function", "inputs": [{"name": "policyId", "type": "uint256"}, {"name": "tier", "type": "uint8"}], "outputs": [], "stateMutability": "nonpayable"}]
    contract = w3.eth.contract(address=w3.to_checksum_address(escrow_address), abi=abi)
    
    # Tier 0 = Success (0% payout, premium goes to pool)
    tx_func = contract.functions.settlePolicy(1, 0)
    await send_tx(w3, oracle_account, tx_func, chain_id)
    print("Policy 1 successfully settled as Tier 0 (Transaction Succeeded).")

if __name__ == "__main__":
    asyncio.run(main())
