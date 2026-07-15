import sys
import logging
from web3 import Web3

w3 = Web3(Web3.HTTPProvider("https://rpc.xlayer.tech"))
tx_hash = "0x6a2621e1cd9a58ce917afcd3cdc5ad88387b8220704fe800be521415e0d1c3bd"
USDT0_ADDRESS = "0x779ded0c9e1022225f8e0630b35a9b54be713736".lower()
TRANSFER_EVENT_SIGNATURE = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef".lower()
expected_treasury = "0xdbb63358192bde9ff8c782ab9d35d25d35e8abfe".lower()
expected_amount_raw = 10000

print(f"Checking tx: {tx_hash}")
try:
    receipt = w3.eth.get_transaction_receipt(tx_hash)
    print(f"Status: {receipt.status}")
    for log in receipt.logs:
        print(f"Log address: {log.address.lower()}")
        if log.address.lower() == USDT0_ADDRESS:
            print(f"Topics length: {len(log.topics)}")
            if len(log.topics) == 3:
                topic0 = log.topics[0].hex().lower().replace("0x", "")
                expected_topic0 = TRANSFER_EVENT_SIGNATURE.replace("0x", "")
                print(f"Topic0: {topic0}")
                if topic0 == expected_topic0:
                    to_topic = log.topics[2].hex().lower().replace("0x", "")
                    to_address_extracted = "0x" + to_topic[-40:]
                    print(f"Extracted to: {to_address_extracted}")
                    print(f"Expected to:  {expected_treasury}")
                    if to_address_extracted == expected_treasury:
                        try:
                            if hasattr(log.data, 'hex'):
                                transfer_amount = int(log.data.hex(), 16)
                            else:
                                transfer_amount = int(log.data, 16)
                            print(f"Amount: {transfer_amount} (Expected >= {expected_amount_raw})")
                        except Exception as e:
                            print(f"Error parsing amount: {e}")
except Exception as e:
    print(f"Error: {e}")
