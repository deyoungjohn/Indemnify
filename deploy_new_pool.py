import asyncio
import os
import sys
import json
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider

def load_pool_bytecode() -> str:
    possible_paths = [
        os.path.join(os.path.dirname(__file__), "out/UnderwriterPool.sol/UnderwriterPool.json"),
        os.path.join(os.getcwd(), "out/UnderwriterPool.sol/UnderwriterPool.json"),
    ]
    for path in possible_paths:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    artifact = json.load(f)
                    bytecode = artifact.get("bytecode", {}).get("object")
                    if bytecode:
                        return bytecode
            except Exception:
                pass
    raise RuntimeError("UnderwriterPool bytecode not found. Please compile the contracts using forge build.")

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
        try:
            priority_fee = await w3.eth.max_priority_fee
        except Exception:
            priority_fee = w3.to_wei(2, "gwei")
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
    print("\n--- Project Indemnify: Fix Immutable Escrow Bug ---")
    deployer_key = input("Enter Deployer/Contract Owner Private Key: ").strip()
    if not deployer_key:
        print("Deployer key is required.")
        sys.exit(1)

    rpc_url = "https://rpc.xlayer.tech"
    w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
    account = w3.eth.account.from_key(deployer_key)
    chain_id = await w3.eth.chain_id
    
    # EXACT Addresses
    usdt_address = w3.to_checksum_address("0x779ded0c9e1022225f8e0630b35a9b54be713736")
    escrow_address = w3.to_checksum_address("0x4B218726007858FC77fb2Aa476bd547d13f14670")

    # 1. DEPLOY NEW POOL
    print("\n1. Deploying a NEW pool bound to Escrow 0x4B21...")
    pool_bytecode = load_pool_bytecode()
    # Bare minimum ABI to deploy
    pool_abi = [
        {"type": "constructor", "inputs": [{"name": "assetAddress", "type": "address"}, {"name": "escrowAddress", "type": "address"}, {"name": "name_", "type": "string"}, {"name": "symbol_", "type": "string"}]},
        {"type": "function", "name": "deposit", "inputs": [{"name": "assets", "type": "uint256"}, {"name": "receiver", "type": "address"}], "outputs": [{"name": "shares", "type": "uint256"}], "stateMutability": "nonpayable"}
    ]
    PoolContract = w3.eth.contract(abi=pool_abi, bytecode=pool_bytecode)
    deploy_func = PoolContract.constructor(usdt_address, escrow_address, "Indemnify USDT Pool", "iUSDT")
    receipt = await send_tx(w3, account, deploy_func, chain_id)
    new_pool_address = receipt["contractAddress"]
    print(f"-> NEW POOL DEPLOYED AT: {new_pool_address}")

    # 2. REGISTER POOL IN ESCROW
    print(f"\n2. Registering NEW pool {new_pool_address} into Escrow {escrow_address}...")
    escrow_abi = [{"name": "registerPool", "type": "function", "inputs": [{"name": "asset", "type": "address"}, {"name": "pool", "type": "address"}], "outputs": [], "stateMutability": "nonpayable"}]
    escrow_contract = w3.eth.contract(address=escrow_address, abi=escrow_abi)
    reg_func = escrow_contract.functions.registerPool(usdt_address, new_pool_address)
    await send_tx(w3, account, reg_func, chain_id)
    print("-> Pool registered successfully!")

    # 3. DEPOSIT 5 USDT
    print("\n3. Depositing 5 USDT to the NEW pool...")
    usdt_abi = [
        {"name": "approve", "type": "function", "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "outputs": [{"name": "", "type": "bool"}], "stateMutability": "nonpayable"},
        {"name": "allowance", "type": "function", "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view"}
    ]
    usdt_contract = w3.eth.contract(address=usdt_address, abi=usdt_abi)
    
    amount = 5 * (10 ** 6) # 5 USDT
    current_allowance = await usdt_contract.functions.allowance(account.address, new_pool_address).call()
    if current_allowance > 0 and current_allowance < amount:
        print("Resetting USDT allowance to 0...")
        await send_tx(w3, account, usdt_contract.functions.approve(new_pool_address, 0), chain_id)
    
    if current_allowance < amount:
        print("Approving 5 USDT for the new pool...")
        await send_tx(w3, account, usdt_contract.functions.approve(new_pool_address, amount), chain_id)

    new_pool_contract = w3.eth.contract(address=new_pool_address, abi=pool_abi)
    deposit_func = new_pool_contract.functions.deposit(amount, account.address)
    await send_tx(w3, account, deposit_func, chain_id)
    print("-> 5 USDT Deposited Successfully!")
    print("\nALL DONE! You can now run test_client_flow.py!")

if __name__ == "__main__":
    asyncio.run(main())
