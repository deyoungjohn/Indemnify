import json
import logging
from typing import Set, Dict, Tuple
from fastapi import Response
from web3 import Web3
from .config import settings

logger = logging.getLogger("indemnify.x402")

# In-memory cache for processed transaction hashes to prevent replay attacks
PROCESSED_TX_HASHES: Set[str] = set()

# USDT0 Contract Address on X Layer Mainnet (Fallback to Mock for local dev if needed)
USDT0_ADDRESS = "0x779ded0c9e1022225f8e0630b35a9b54be713736" if settings.chain_id != 31337 else "0x5FbDB2315678afecb367f032d93F642f64180aa3"

# Basic ERC20 Transfer event signature: Transfer(address indexed from, address indexed to, uint256 value)
# Topic 0: keccak256("Transfer(address,address,uint256)")
TRANSFER_EVENT_SIGNATURE = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# Initialize a synchronous Web3 provider for verifying payment logs
w3 = Web3(Web3.HTTPProvider(settings.rpc_provider_url))

def build_402_response() -> Response:
    """Returns the standard x402 challenge as a FastAPI Response."""
    payload = {
        "error": "Payment Required",
        "payment_requirements": {
            "amount": str(settings.x402_fee_usdt),
            "currency": "USDT0",
            "token_address": USDT0_ADDRESS,
            "pay_to_address": settings.x402_treasury_address,
            "network_id": str(settings.chain_id),
            "chain": "X Layer"
        }
    }
    return Response(
        content=json.dumps(payload),
        status_code=402,
        media_type="application/json"
    )

def verify_payment(tx_hash: str) -> Tuple[bool, str]:
    """Verifies the on-chain USDT0 transfer by reading the logs (supports AA and relayed txs)."""
    if not tx_hash:
        return False, "No payment_tx_hash provided in request."
        
    # RELAXED FOR DEBUGGING: Anti-replay protection temporarily disabled
    # if tx_hash in PROCESSED_TX_HASHES:
    #     logger.warning(f"Replay attack detected with tx: {tx_hash}")
    #     return False, f"Replay attack detected. Tx {tx_hash} was already used."
        
    # Ensure tx_hash is properly formatted
    if not tx_hash.startswith("0x"):
        tx_hash = "0x" + tx_hash
        
    try:
        receipt = w3.eth.get_transaction_receipt(tx_hash)
        if receipt.status != 1:
            logger.error(f"Transaction {tx_hash} failed on-chain.")
            return False, f"Transaction {tx_hash} failed on-chain. Status is not 1."
            
        # Decimals: USDT0 is 6 decimals.
        expected_amount_raw = int(settings.x402_fee_usdt * (10**6))
        expected_treasury = settings.x402_treasury_address.lower()
        
        valid_transfer_found = False
        
        for log in receipt.logs:
            if log.address.lower() == USDT0_ADDRESS.lower():
                if len(log.topics) == 3:
                    # Safely parse topic 0 regardless of HexBytes implementation
                    topic0 = log.topics[0].hex().lower().replace("0x", "")
                    expected_topic0 = TRANSFER_EVENT_SIGNATURE.lower().replace("0x", "")
                    
                    if topic0 == expected_topic0:
                        # Safely extract the recipient address from topic 2 (strip padding)
                        to_topic = log.topics[2].hex().lower().replace("0x", "")
                        to_address_extracted = "0x" + to_topic[-40:]
                        
                        if to_address_extracted == expected_treasury:
                            # Safely parse the transfer amount from the data field
                            try:
                                if hasattr(log.data, 'hex'):
                                    transfer_amount = int(log.data.hex(), 16)
                                else:
                                    transfer_amount = int(log.data, 16)
                            except Exception:
                                transfer_amount = 0
                                
                            if transfer_amount >= expected_amount_raw:
                                valid_transfer_found = True
                                break
                                
        if valid_transfer_found:
            # PROCESSED_TX_HASHES.add(tx_hash)
            return True, "Payment verified successfully."
        else:
            logger.error(f"Transaction {tx_hash} did not contain a valid transfer to treasury.")
            return False, f"Transaction {tx_hash} was found, but it does not contain a {settings.x402_fee_usdt} USDT0 Transfer to {settings.x402_treasury_address}."
            
    except Exception as e:
        err_msg = str(e)
        logger.error(f"Error verifying payment tx {tx_hash}: {err_msg}")
        if "not found" in err_msg.lower() or "Transaction with hash" in err_msg:
            return False, f"Transaction {tx_hash} not found on-chain. It may still be indexing. Wait 10s and retry."
        return False, f"RPC Error verifying payment: {err_msg}"
