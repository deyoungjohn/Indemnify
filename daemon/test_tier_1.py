import asyncio
import os
import sys
import time
import logging
import httpx
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

async def send_tx(w3, account, tx_func, chain_id):
    nonce = await w3.eth.get_transaction_count(account.address)
    try:
        gas_est = await tx_func.estimate_gas({"from": account.address})
        gas = int(gas_est * 1.25)
    except Exception as e:
        logger.warning(f"Gas estimation failed: {e}. Using default gas.")
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
    
    logger.info(f"TX broadcasted. Hash: {tx_hash.hex()}. Waiting for block confirmation...")
    receipt = await w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt["status"] != 1:
        # Check revert reason
        try:
            tx_obj = await w3.eth.get_transaction(tx_hash)
            await w3.eth.call(
                {
                    "to": tx_obj["to"],
                    "from": tx_obj["from"],
                    "value": tx_obj["value"],
                    "data": tx_obj["input"],
                    "gas": tx_obj["gas"],
                    "gasPrice": tx_obj.get("gasPrice", 0),
                    "maxFeePerGas": tx_obj.get("maxFeePerGas", 0),
                    "maxPriorityFeePerGas": tx_obj.get("maxPriorityFeePerGas", 0)
                },
                tx_obj["blockNumber"] - 1
            )
            revert_reason = "Unknown"
        except Exception as e:
            revert_reason = str(e)
        
        raise RuntimeError(f"Transaction reverted on-chain. Reason: {revert_reason}. Hash: {tx_hash.hex()}")
        
    logger.info(f"Transaction confirmed successfully in block {receipt['blockNumber']}.")
    return receipt

def load_abi(contract_name: str, fallback: list) -> list:
    out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out", f"{contract_name}.sol", f"{contract_name}.json")
    if os.path.exists(out_path):
        import json
        with open(out_path, "r") as f:
            return json.load(f)["abi"]
    return fallback

ESCROW_FALLBACK_ABI = [
    {
        "name": "PolicyCreated",
        "type": "event",
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "policyId", "type": "uint256"},
            {"indexed": True, "name": "client", "type": "address"},
            {"indexed": True, "name": "asset", "type": "address"},
            {"indexed": False, "name": "coverageAmount", "type": "uint256"},
            {"indexed": False, "name": "premiumPaid", "type": "uint256"},
            {"indexed": False, "name": "startTimestamp", "type": "uint256"},
            {"indexed": False, "name": "timeoutDuration", "type": "uint256"}
        ]
    },
    {
        "name": "createPolicy",
        "type": "function",
        "inputs": [
            {"name": "asset", "type": "address"},
            {"name": "coverageAmount", "type": "uint256"},
            {"name": "premiumAmount", "type": "uint256"},
            {"name": "timeoutDuration", "type": "uint256"},
            {"name": "deadline", "type": "uint256"},
            {"name": "quoteId", "type": "bytes32"},
            {"name": "signature", "type": "bytes"}
        ],
        "outputs": [{"name": "policyId", "type": "uint256"}],
        "stateMutability": "nonpayable"
    },
    {
        "name": "policies",
        "type": "function",
        "inputs": [{"name": "id", "type": "uint256"}],
        "outputs": [
            {"name": "clientAddress", "type": "address"},
            {"name": "asset", "type": "address"},
            {"name": "coverageAmount", "type": "uint256"},
            {"name": "premiumPaid", "type": "uint256"},
            {"name": "startTimestamp", "type": "uint256"},
            {"name": "timeoutDuration", "type": "uint256"},
            {"name": "riskBracketTier", "type": "uint8"},
            {"name": "status", "type": "uint8"}
        ],
        "stateMutability": "view"
    },
    {
        "name": "settlePolicy",
        "type": "function",
        "inputs": [
            {"name": "policyId", "type": "uint256"},
            {"name": "tier", "type": "uint8"}
        ],
        "outputs": [],
        "stateMutability": "nonpayable"
    }
]

ERC20_ABI = [
    {"name": "approve", "type": "function", "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "outputs": [{"name": "", "type": "bool"}], "stateMutability": "nonpayable"},
    {"name": "allowance", "type": "function", "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view"},
    {"name": "decimals", "type": "function", "inputs": [], "outputs": [{"name": "", "type": "uint8"}], "stateMutability": "view"},
    {"name": "balanceOf", "type": "function", "inputs": [{"name": "account", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view"}
]

async def main():
    print("\n--- Project Indemnify Tier 1 Test Script (35% Payout) ---")
    API_URL = "http://127.0.0.1:8000"
    RPC_URL = "https://rpc.xlayer.tech"
    
    CLIENT_KEY = os.environ.get("CLIENT_PRIVATE_KEY")
    ORACLE_KEY = os.environ.get("ORACLE_PRIVATE_KEY")
    ESCROW_ADDRESS = os.environ.get("ESCROW_CONTRACT_ADDRESS")
    
    if not CLIENT_KEY:
        CLIENT_KEY = input("Enter Client Agent Private Key: ").strip()
    if not ORACLE_KEY:
        ORACLE_KEY = input("Enter Oracle Private Key: ").strip()
    if not ESCROW_ADDRESS:
        ESCROW_ADDRESS = input("Enter ParametricEscrow Contract Address: ").strip()
        
    USDT_ADDRESS = "0x779ded0c9e1022225f8e0630b35a9b54be713736"

    w3 = AsyncWeb3(AsyncHTTPProvider(RPC_URL))
    client_account = w3.eth.account.from_key(CLIENT_KEY)
    oracle_account = w3.eth.account.from_key(ORACLE_KEY)
    chain_id = await w3.eth.chain_id
    
    escrow_checksum = w3.to_checksum_address(ESCROW_ADDRESS)
    usdt_checksum = w3.to_checksum_address(USDT_ADDRESS)
    
    usdt_contract = w3.eth.contract(address=usdt_checksum, abi=ERC20_ABI)
    escrow_contract = w3.eth.contract(address=escrow_checksum, abi=load_abi("ParametricEscrow", ESCROW_FALLBACK_ABI))

    decimals = await usdt_contract.functions.decimals().call()
    
    coverage_requested = float(input("\nEnter coverage amount in USDT (e.g. 1.0): ").strip() or "1.0")
    coverage_raw = int(coverage_requested * (10 ** decimals))
    
    logger.info(f"\n[1/4] Fetching insurance quote from FastAPI Daemon...")
    quote_payload = {
        "client_address": client_account.address,
        "target_contract": client_account.address,
        "calldata_hex": "0x",
        "value_wei": 100000000000000,
        "coverage_requested": coverage_raw,
        "timeout_duration": 180,
        "asset": usdt_checksum
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(f"{API_URL}/v1/insurance/quote", json=quote_payload, timeout=10.0)
        quote_data = response.json()
        premium_amount = int(quote_data["premium_amount"])
        quote_id = bytes.fromhex(quote_data["quote_id"].replace("0x", ""))
        deadline = quote_data["deadline"]
        signature = bytes.fromhex(quote_data["signature"].replace("0x", ""))

    logger.info(f"Quote fetched! Premium: {premium_amount / (10**decimals):.6f} USDT")
    
    logger.info("\n[2/4] Approving Escrow contract to pull premium...")
    current_allowance = await usdt_contract.functions.allowance(client_account.address, escrow_checksum).call()
    if current_allowance > 0 and current_allowance != premium_amount:
        reset_tx = usdt_contract.functions.approve(escrow_checksum, 0)
        await send_tx(w3, client_account, reset_tx, chain_id)
    tx_func = usdt_contract.functions.approve(escrow_checksum, premium_amount)
    await send_tx(w3, client_account, tx_func, chain_id)

    logger.info("\n[3/4] Purchasing policy via createPolicy() on Escrow contract...")
    tx_func = escrow_contract.functions.createPolicy(
        usdt_checksum,
        coverage_raw,
        premium_amount,
        180,
        deadline,
        quote_id,
        signature
    )
    receipt = await send_tx(w3, client_account, tx_func, chain_id)
    
    try:
        logs = escrow_contract.events.PolicyCreated().process_receipt(receipt)
        policy_id = logs[0]["args"]["policyId"]
        logger.info(f"Policy purchased successfully! POLICY ID: {policy_id}")
    except Exception:
        policy_id = int(input("Could not parse Policy ID from logs. Enter Policy ID manually: ").strip())

    logger.info("\n[4/4] MANUALLY SETTLING AS TIER 1 (35% Payout) USING ORACLE KEY...")
    logger.info("Because we are manually settling this via script, we bypass the need for a target transaction.")
    
    # Delay to allow Oracle Listener to ignore it (since there's no client target tx)
    await asyncio.sleep(2)
    
    settle_func = escrow_contract.functions.settlePolicy(policy_id, 1) # Tier 1
    await send_tx(w3, oracle_account, settle_func, chain_id)
    
    logger.info("\nSUCCESS: Policy settled as TIER 1!")
    logger.info(f"You should have received exactly 35% of your {coverage_requested} USDT coverage back to your Client Wallet.")

if __name__ == "__main__":
    asyncio.run(main())
