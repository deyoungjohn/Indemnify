import os
import sys
import json
import time
import asyncio
import logging
import httpx
from typing import Dict, Any, List

from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("indemnify.test_client_flow")

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

RPC_URL = os.environ.get("RPC_URL") or os.environ.get("RPC_PROVIDER_URL") or "https://rpc.xlayer.tech"
ESCROW_ADDRESS = os.environ.get("ESCROW_CONTRACT_ADDRESS")
USDT_ADDRESS = os.environ.get("USDT_ADDRESS") or "0x779ded0c9e1022225f8e0630b35a9b54be713736"  # X Layer Mainnet USDT0
CLIENT_KEY = os.environ.get("CLIENT_PRIVATE_KEY")
API_URL = "http://127.0.0.1:8000"

# ABI fetchers
def load_abi(filename: str, fallback_abi: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    possible_paths = [
        os.path.join(os.path.dirname(__file__), f"../out/{filename}.sol/{filename}.json"),
        os.path.join(os.getcwd(), f"out/{filename}.sol/{filename}.json"),
    ]
    for path in possible_paths:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    artifact = json.load(f)
                    abi = artifact.get("abi")
                    if abi:
                        return abi
            except Exception:
                pass
    return fallback_abi

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
        "inputs": [{"name": "", "type": "uint256"}],
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
    }
]

ERC20_ABI = [
    {
        "name": "decimals",
        "type": "function",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view"
    },
    {
        "name": "approve",
        "type": "function",
        "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable"
    },
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
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"}
        ],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view"
    }
]

async def send_tx(w3, account, tx_func, chain_id):
    """Signs and broadcasts transaction, waiting for block confirmation."""
    nonce = await w3.eth.get_transaction_count(account.address)
    
    # Estimate gas
    try:
        gas_est = await tx_func.estimate_gas({"from": account.address})
        gas = int(gas_est * 1.2)
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
    
    logger.info(f"TX broadcasted. Hash: {tx_hash.hex()}. Waiting for block confirmation...")
    receipt = await w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    
    if receipt["status"] != 1:
        # Dry-run call to extract revert reason
        try:
            call_tx = {k: v for k, v in tx.items() if k in ["from", "to", "data", "value"]}
            await w3.eth.call(call_tx, receipt["blockNumber"] - 1)
            revert_reason = "Unknown (dry-run succeeded but on-chain tx reverted)"
        except Exception as call_err:
            revert_reason = str(call_err)
        
        logger.error(f"Transaction reverted on-chain. Revert Reason: {revert_reason}")
        raise RuntimeError(f"Transaction reverted on-chain. Reason: {revert_reason}. Hash: {tx_hash.hex()}")
    
    logger.info(f"Transaction confirmed successfully in block {receipt['blockNumber']}.")
    return receipt

async def main():
    global CLIENT_KEY, ESCROW_ADDRESS
    
    print("\n--- Project Indemnify End-to-End Client Test Flow ---")
    
    if not CLIENT_KEY:
        CLIENT_KEY = input("Enter Client Agent Private Key: ").strip()
    if not ESCROW_ADDRESS:
        ESCROW_ADDRESS = input("Enter ParametricEscrow Contract Address: ").strip()

    if not all([CLIENT_KEY, ESCROW_ADDRESS]):
        logger.error("Client private key and Escrow contract address are required.")
        sys.exit(1)

    # Initialize web3
    w3 = AsyncWeb3(AsyncHTTPProvider(RPC_URL))
    client_account = w3.eth.account.from_key(CLIENT_KEY)
    chain_id = await w3.eth.chain_id
    
    logger.info(f"Connecting to RPC: {RPC_URL} (Chain ID: {chain_id})")
    logger.info(f"Client Wallet Address: {client_account.address}")
    
    # Check OKB Balance
    okb_bal = await w3.eth.get_balance(client_account.address)
    logger.info(f"Client OKB Balance: {w3.from_wei(okb_bal, 'ether'):.6f} OKB")
    if okb_bal == 0:
        logger.error("Client wallet has 0 OKB. Please fund it with OKB to pay for gas.")
        sys.exit(1)

    escrow_checksum = w3.to_checksum_address(ESCROW_ADDRESS)
    usdt_checksum = w3.to_checksum_address(USDT_ADDRESS)
    
    usdt_contract = w3.eth.contract(address=usdt_checksum, abi=ERC20_ABI)
    escrow_contract = w3.eth.contract(address=escrow_checksum, abi=load_abi("ParametricEscrow", ESCROW_FALLBACK_ABI))

    # Check USDT Balance
    decimals = await usdt_contract.functions.decimals().call()
    usdt_bal = await usdt_contract.functions.balanceOf(client_account.address).call()
    logger.info(f"Client USDT Balance: {usdt_bal / (10**decimals):.2f} USDT")
    
    # Ask for policy parameters
    coverage_requested = float(input("\nEnter coverage amount in USDT (e.g. 1.0): ").strip() or "1.0")
    coverage_raw = int(coverage_requested * (10 ** decimals))
    
    if usdt_bal < (coverage_raw / 10): # Estimate premium is roughly 10% or less of coverage + margin
        logger.warning("Your USDT balance might be low to pay the premium. Proceeding anyway...")

    # 1. Fetch quote from FastAPI daemon
    logger.info(f"\n[1/5] Fetching insurance quote from FastAPI Daemon at {API_URL}...")
    quote_payload = {
        "client_address": client_account.address,
        "target_contract": client_account.address,  # Dummy target (sending tx to self)
        "calldata_hex": "0x",
        "value_wei": 100000000000000,  # 0.0001 OKB value transfer
        "coverage_requested": coverage_raw,
        "timeout_duration": 180,  # 3 minute timeout
        "asset": usdt_checksum
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(f"{API_URL}/v1/insurance/quote", json=quote_payload, timeout=10.0)
            if response.status_code != 200:
                logger.error(f"Failed to fetch quote. Server returned: {response.text}")
                sys.exit(1)
            quote = response.json()
        except Exception as e:
            logger.error(f"FastAPI daemon is unreachable. Make sure uvicorn is running on port 8000. Error: {e}")
            sys.exit(1)

    premium_amount = int(quote["premium_amount"])
    quote_id = bytes.fromhex(quote["quote_id"][2:])
    deadline = int(quote["deadline"])
    signature = bytes.fromhex(quote["signature"][2:])

    logger.info("Quote fetched successfully!")
    logger.info(f"  Premium: {premium_amount / (10**decimals):.6f} USDT")
    logger.info(f"  Quote ID: {quote['quote_id']}")
    logger.info(f"  Deadline: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(deadline))}")

    # 2. Approve Escrow Contract
    logger.info(f"\n[2/5] Approving Escrow contract to pull {premium_amount / (10**decimals):.6f} USDT...")
    
    # Many USDT implementations require resetting allowance to 0 first if it's currently non-zero
    current_allowance = await usdt_contract.functions.allowance(client_account.address, escrow_checksum).call()
    if current_allowance > 0 and current_allowance != premium_amount:
        logger.info("Resetting existing USDT allowance to 0 first...")
        reset_tx = usdt_contract.functions.approve(escrow_checksum, 0)
        await send_tx(w3, client_account, reset_tx, chain_id)
        
    tx_func = usdt_contract.functions.approve(escrow_checksum, premium_amount)
    await send_tx(w3, client_account, tx_func, chain_id)

    # 3. Create Policy
    logger.info(f"\n[3/5] Purchasing policy via createPolicy() on Escrow contract...")
    tx_func = escrow_contract.functions.createPolicy(
        usdt_checksum,
        coverage_raw,
        premium_amount,
        180,  # 3 minutes timeout duration
        deadline,
        quote_id,
        signature
    )
    receipt = await send_tx(w3, client_account, tx_func, chain_id)
    
    # Parse policy ID from receipt logs
    # PolicyCreated event signature: PolicyCreated(uint256,address,address,uint256,uint256,uint256,uint256)
    # We can fetch it by parsing events
    try:
        logs = escrow_contract.events.PolicyCreated().process_receipt(receipt)
        policy_id = logs[0]["args"]["policyId"]
        logger.info(f"Policy purchased successfully! POLICY ID: {policy_id}")
    except Exception:
        policy_id = int(input("Could not parse Policy ID from logs. Enter Policy ID manually: ").strip())

    # 4. Trigger target transaction (sending value to self to trigger next transaction)
    logger.info(f"\n[4/5] Broadcasting target underwritten transaction to the blockchain...")
    logger.info("  This transaction matches the parameters used in the quote.")
    
    target_tx = {
        "from": client_account.address,
        "to": client_account.address,
        "value": 100000000000000,  # 0.0001 OKB
        "nonce": await w3.eth.get_transaction_count(client_account.address),
        "gas": 21000,
        "chainId": chain_id
    }
    
    try:
        fee_history = await w3.eth.fee_history(1, "latest")
        base_fee = fee_history["baseFeePerGas"][-1]
        try:
            priority_fee = await w3.eth.max_priority_fee
        except Exception:
            priority_fee = w3.to_wei(2, "gwei")
        target_tx["maxFeePerGas"] = (base_fee * 2) + priority_fee
        target_tx["maxPriorityFeePerGas"] = priority_fee
    except Exception:
        target_tx["gasPrice"] = await w3.eth.gas_price

    signed_target = w3.eth.account.sign_transaction(target_tx, private_key=client_account.key)
    target_hash = await w3.eth.send_raw_transaction(signed_target.raw_transaction)
    logger.info(f"Target transaction broadcasted! Hash: {target_hash.hex()}")
    logger.info("Waiting for block confirmation...")
    
    target_receipt = await w3.eth.wait_for_transaction_receipt(target_hash, timeout=120)
    logger.info(f"Target transaction confirmed in block {target_receipt['blockNumber']}.")

    # 5. Monitor on-chain settlement
    logger.info(f"\n[5/5] Monitoring policy {policy_id} for Oracle settlement...")
    logger.info("  Watch your Oracle Listener terminal! It should detect the transaction and trigger settlement.")
    
    settled = False
    for attempt in range(15):  # Poll for up to 30 seconds
        await asyncio.sleep(2.0)
        policy_data = await escrow_contract.functions.policies(policy_id).call()
        status = policy_data[7]  # Status index 7 (0=Active, 1=Settled, 2=Refunded, 3=Claimed)
        
        if status == 1:
            logger.info("SUCCESS: Policy has been settled on-chain (Tier 0 - Success)!")
            logger.info("Reserved capital has been released back to the UnderwriterPool.")
            settled = True
            break
        elif status == 3:
            logger.info("CLAIM TRIGGERED: Policy has been claimed on-chain (Tier 3 - Revert)!")
            logger.info("Coverage payout has been transferred to your Client Wallet.")
            settled = True
            break
            
    if not settled:
        logger.warning("Policy status is still Active. Check the Oracle Listener logs for any broadcast errors.")

if __name__ == "__main__":
    asyncio.run(main())
