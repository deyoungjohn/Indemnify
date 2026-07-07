import os
import sys
import json
import asyncio
import logging
from typing import Dict, Any, List

from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("indemnify.admin_setup")

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

RPC_URL = os.environ.get("RPC_URL") or os.environ.get("RPC_PROVIDER_URL") or "https://rpc.xlayer.tech"
ESCROW_ADDRESS = os.environ.get("ESCROW_CONTRACT_ADDRESS")
DEPLOYER_KEY = os.environ.get("DEPLOYER_PRIVATE_KEY")
USDT_ADDRESS = os.environ.get("USDT_ADDRESS") or "0x779ded0c9e1022225f8e0630b35a9b54be713736"  # Default X Layer Mainnet USDT
USDT_POOL_ADDRESS = os.environ.get("USDT_POOL_ADDRESS")
ORACLE_ADDRESS = os.environ.get("ORACLE_ADDRESS")
DEPOSIT_AMOUNT = float(os.environ.get("DEPOSIT_AMOUNT") or "5.0")  # Default to depositing 5 USDT

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
        "name": "setOracleAddress",
        "type": "function",
        "inputs": [{"name": "newOracle", "type": "address"}],
        "outputs": [],
        "stateMutability": "nonpayable"
    },
    {
        "name": "registerPool",
        "type": "function",
        "inputs": [{"name": "asset", "type": "address"}, {"name": "pool", "type": "address"}],
        "outputs": [],
        "stateMutability": "nonpayable"
    }
]

POOL_FALLBACK_ABI = [
    {
        "name": "deposit",
        "type": "function",
        "inputs": [{"name": "assets", "type": "uint256"}, {"name": "receiver", "type": "address"}],
        "outputs": [{"name": "shares", "type": "uint256"}],
        "stateMutability": "nonpayable"
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
        "name": "balanceOf",
        "type": "function",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view"
    },
    {
        "name": "approve",
        "type": "function",
        "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable"
    }
]

async def send_tx(w3, account, tx_func, chain_id):
    """Signs and broadcasts a transaction, waiting for block confirmation."""
    nonce = await w3.eth.get_transaction_count(account.address)
    
    # Estimate gas
    try:
        gas_est = await tx_func.estimate_gas({"from": account.address})
        gas = int(gas_est * 1.25)
    except Exception:
        gas = 250000

    # Build params
    tx_params = {
        "from": account.address,
        "nonce": nonce,
        "gas": gas,
        "chainId": chain_id
    }
    
    # Gas pricing (EIP-1559 support)
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
    
    logger.info(f"Transaction broadcasted. Hash: {tx_hash.hex()}. Waiting for confirmation...")
    receipt = await w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    
    if receipt["status"] != 1:
        raise RuntimeError(f"Transaction reverted on-chain. Hash: {tx_hash.hex()}")
    
    logger.info(f"Transaction confirmed successfully in block {receipt['blockNumber']}.")
    return receipt

def load_pool_bytecode() -> str:
    possible_paths = [
        os.path.join(os.path.dirname(__file__), "../out/UnderwriterPool.sol/UnderwriterPool.json"),
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

async def main():
    # Prompt user for parameters if not loaded from env
    global ESCROW_ADDRESS, DEPLOYER_KEY, USDT_POOL_ADDRESS, ORACLE_ADDRESS
    
    print("\n--- Project Indemnify On-Chain Setup Admin Tool ---")
    
    if not DEPLOYER_KEY:
        DEPLOYER_KEY = input("Enter Deployer/Contract Owner Private Key: ").strip()
    if not ESCROW_ADDRESS:
        ESCROW_ADDRESS = input("Enter ParametricEscrow Contract Address: ").strip()
    if not ORACLE_ADDRESS:
        ORACLE_ADDRESS = input("Enter Oracle Public Wallet Address: ").strip()
    if USDT_POOL_ADDRESS is None:
        USDT_POOL_ADDRESS = input("Enter UnderwriterPool USDT Address (Press Enter to deploy new for Real USDT): ").strip()

    if not all([DEPLOYER_KEY, ESCROW_ADDRESS, ORACLE_ADDRESS]):
        logger.error("Deployer private key, Escrow contract address, and Oracle address are required.")
        sys.exit(1)

    # Initialize web3
    w3 = AsyncWeb3(AsyncHTTPProvider(RPC_URL))
    deployer_account = w3.eth.account.from_key(DEPLOYER_KEY)
    chain_id = await w3.eth.chain_id
    
    logger.info(f"Connecting to RPC: {RPC_URL} (Chain ID: {chain_id})")
    logger.info(f"Using Deployer Address: {deployer_account.address}")
    
    # Check OKB balance for gas
    balance = await w3.eth.get_balance(deployer_account.address)
    logger.info(f"Deployer OKB Balance: {w3.from_wei(balance, 'ether'):.6f} OKB")
    if balance == 0:
        logger.error("Deployer balance is 0. Please fund it with OKB to pay for gas.")
        sys.exit(1)

    # Clean addresses
    escrow_checksum = w3.to_checksum_address(ESCROW_ADDRESS)
    usdt_checksum = w3.to_checksum_address(USDT_ADDRESS)
    oracle_checksum = w3.to_checksum_address(ORACLE_ADDRESS)

    # Instantiate contracts
    escrow_abi = load_abi("ParametricEscrow", ESCROW_FALLBACK_ABI)
    pool_abi = load_abi("UnderwriterPool", POOL_FALLBACK_ABI)
    
    escrow_contract = w3.eth.contract(address=escrow_checksum, abi=escrow_abi)
    
    # Determine if we should use existing pool or deploy new
    pool_checksum = None
    deploy_new = False
    
    if USDT_POOL_ADDRESS:
        try:
            pool_checksum = w3.to_checksum_address(USDT_POOL_ADDRESS)
            pool_contract = w3.eth.contract(address=pool_checksum, abi=pool_abi)
            underlying_token_addr = await pool_contract.functions.underlyingAsset().call()
            underlying_checksum = w3.to_checksum_address(underlying_token_addr)
            
            if underlying_checksum.lower() != usdt_checksum.lower():
                logger.warning(f"Existing pool at {pool_checksum} points to {underlying_checksum} (not real USDT: {usdt_checksum}).")
                deploy_new = True
            else:
                logger.info(f"Using existing pool at {pool_checksum} pointing to Real USDT.")
        except Exception as e:
            logger.warning(f"Could not verify existing pool at '{USDT_POOL_ADDRESS}': {e}.")
            deploy_new = True
    else:
        deploy_new = True

    if deploy_new:
        logger.info("\n--- Deploying new UnderwriterPool for Real USDT ---")
        try:
            pool_bytecode = load_pool_bytecode()
        except Exception as e:
            logger.error(f"Cannot deploy pool: {e}")
            sys.exit(1)
            
        pool_class = w3.eth.contract(abi=pool_abi, bytecode=pool_bytecode)
        tx_func = pool_class.constructor(
            usdt_checksum,
            escrow_checksum,
            "Indemnify USDT Share",
            "indUSDT"
        )
        
        logger.info("Broadcasting UnderwriterPool deployment transaction...")
        receipt = await send_tx(w3, deployer_account, tx_func, chain_id)
        pool_checksum = w3.to_checksum_address(receipt["contractAddress"])
        logger.info(f"NEW UnderwriterPool deployed at: {pool_checksum}")
        print(f"\n>>> SAVE THIS ADDRESS! Your new USDT_POOL_ADDRESS is: {pool_checksum}\n")
        
    # Instantiate the active pool contract
    pool_contract = w3.eth.contract(address=pool_checksum, abi=pool_abi)
    underlying_checksum = usdt_checksum
    token_contract = w3.eth.contract(address=underlying_checksum, abi=ERC20_ABI)

    # 1. setOracleAddress(ORACLE_ADDRESS)
    logger.info(f"\n1. Configuring Oracle Address in Escrow -> {oracle_checksum}...")
    try:
        tx_func = escrow_contract.functions.setOracleAddress(oracle_checksum)
        await send_tx(w3, deployer_account, tx_func, chain_id)
    except Exception as e:
        logger.error(f"Failed to set Oracle Address: {e}")

    # 2. registerPool(asset, pool_address)
    logger.info(f"\n2. Registering UnderwriterPool on Escrow (Asset: {underlying_checksum} -> Pool: {pool_checksum})...")
    try:
        tx_func = escrow_contract.functions.registerPool(underlying_checksum, pool_checksum)
        await send_tx(w3, deployer_account, tx_func, chain_id)
    except Exception as e:
        logger.error(f"Failed to register Pool: {e}")

    # 3. Approve and Deposit Liquidity into UnderwriterPool
    decimals = await token_contract.functions.decimals().call()
    deposit_assets = int(DEPOSIT_AMOUNT * (10 ** decimals))
    logger.info(f"\n3. Depositing Liquidity into LP Pool...")
    logger.info(f"Targeting: {DEPOSIT_AMOUNT} USDT ({deposit_assets} raw)")
    
    # 3a. Check real balance
    try:
        balance = await token_contract.functions.balanceOf(deployer_account.address).call()
        logger.info(f"Deployer real USDT balance: {balance / (10**decimals)} USDT (raw: {balance})")
        if balance < deposit_assets:
            logger.error(f"Error: Insufficient real USDT balance. Wallet only has {balance / (10**decimals)} USDT, but you requested to deposit {DEPOSIT_AMOUNT} USDT.")
            sys.exit(1)
    except Exception as e:
        logger.warning(f"Could not check real USDT balance: {e}. Attempting approval/deposit anyway...")

    # 3b. Approve USDT
    logger.info(f"Step 3b: Approving UnderwriterPool to spend {DEPOSIT_AMOUNT} USDT...")
    try:
        tx_func = token_contract.functions.approve(pool_checksum, deposit_assets)
        await send_tx(w3, deployer_account, tx_func, chain_id)
    except Exception as e:
        logger.error(f"Approval failed: {e}")
        sys.exit(1)
        
    # 3c. Deposit
    logger.info(f"Step 3c: Depositing USDT to vault shares for {deployer_account.address}...")
    try:
        tx_func = pool_contract.functions.deposit(deposit_assets, deployer_account.address)
        await send_tx(w3, deployer_account, tx_func, chain_id)
    except Exception as e:
        logger.error(f"Deposit failed: {e}")
        sys.exit(1)

    logger.info("\n--- On-chain System Configuration & Liquidity Provisioning Completed! ---")

if __name__ == "__main__":
    asyncio.run(main())
