import os
import json
import logging
from web3 import Web3
from fastapi.testclient import TestClient
from daemon.main import app
from daemon.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_x402")

client = TestClient(app)

# Local Anvil configuration for testing
RPC_URL = settings.rpc_provider_url
w3 = Web3(Web3.HTTPProvider(RPC_URL))

# Client Key for paying the fee (Account #1 in Anvil)
CLIENT_KEY = os.environ.get("CLIENT_KEY") or "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
client_account = w3.eth.account.from_key(CLIENT_KEY)

# Mock USDT on Anvil
USDT_ADDRESS = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
USDT_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"}
        ],
        "name": "transfer",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]
usdt_contract = w3.eth.contract(address=USDT_ADDRESS, abi=USDT_ABI)

def main():
    logger.info("=== 1. Test Without Payment (Expect 402) ===")
    
    payload = {
        "client_address": client_account.address,
        "target_contract": "0x0000000000000000000000000000000000000000",
        "calldata_hex": "0x",
        "value_wei": 0,
        "coverage_requested": 1000000, # 1 USDT
        "timeout_duration": 3600
    }
    
    response = client.post("/v1/insurance/quote", json=payload)
    if response.status_code != 402:
        logger.error(f"Expected 402, got {response.status_code}")
        return
        
    payment_req = response.json().get("payment_requirements")
    logger.info(f"Received 402 Payment Required: {json.dumps(payment_req, indent=2)}")
    
    fee_amount = float(payment_req["amount"])
    pay_to = payment_req["pay_to_address"]
    fee_amount_raw = int(fee_amount * (10**6))
    
    logger.info("=== 2. Mocking Payment Transaction ===")
    tx_hash_hex = "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
    
    # Mock the web3 get_transaction_receipt call in the middleware
    from unittest.mock import MagicMock, patch
    
    mock_receipt = MagicMock()
    mock_receipt.status = 1
    
    mock_log = MagicMock()
    mock_log.address = "0x779ded0c9e1022225f8e0630b35a9b54be713736" # Mainnet USDT0
    mock_log.topics = [
        bytes.fromhex("ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"), # Transfer
        bytes.fromhex("00000000000000000000000059c6995e998f97a5a0044966f0945389dc9e86da"), # From
        bytes.fromhex("000000000000000000000000" + pay_to.lower().replace("0x", "")) # To
    ]
    # Data is the amount (0.01 * 10**6 = 10000 -> 0x2710)
    mock_log.data = bytes.fromhex("0000000000000000000000000000000000000000000000000000000000002710")
    
    mock_receipt.logs = [mock_log]
    
    with patch('web3.eth.Eth.get_transaction_receipt') as mock_receipt_method:
        mock_receipt_method.return_value = mock_receipt
        
        logger.info("=== 3. Test With Payment (Expect 200) ===")
        headers = {"X-Payment-Tx": tx_hash_hex}
        response_paid = client.post("/v1/insurance/quote", json=payload, headers=headers)
        if response_paid.status_code != 200:
            logger.error(f"Expected 200, got {response_paid.status_code} - {response_paid.text}")
            return
        logger.info(f"Access granted! Response: {json.dumps(response_paid.json(), indent=2)}")
        
        logger.info("=== 4. Test Replay Attack (Expect 403) ===")
        response_replay = client.post("/v1/insurance/quote", json=payload, headers=headers)
        if response_replay.status_code != 403:
            logger.error(f"Expected 403 Replay Attack, got {response_replay.status_code}")
            return
        logger.info("Replay attack successfully blocked!")
        
    logger.info("All x402 payment flow tests passed successfully!")

if __name__ == "__main__":
    main()
