from web3 import Web3

w3 = Web3(Web3.HTTPProvider("https://rpc.xlayer.tech"))
tx_hash = "0x0a79be925200bb281dab52831be297bfc6af4fd94a54ea5ae399026c4dc3ce94"

receipt = w3.eth.get_transaction_receipt(tx_hash)
print(f"Receipt status: {receipt.status}")

USDT0_ADDRESS = "0x779ded0c9e1022225f8e0630b35a9b54be713736"
TRANSFER_EVENT_SIGNATURE = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
treasury_address = "0xdbb63358192bde9ff8c782ab9d35d25d35e8abfe"
treasury_address_padded = "0x000000000000000000000000" + treasury_address.lower().replace("0x", "")

valid_transfer_found = False

for log in receipt.logs:
    print(f"Log address: {log.address}")
    if log.address.lower() == USDT0_ADDRESS.lower():
        print(f"Match USDT0. Topics len: {len(log.topics)}")
        if len(log.topics) == 3:
            topic0 = log.topics[0].hex()
            print(f"Topic 0: {topic0}")
            if topic0 == TRANSFER_EVENT_SIGNATURE.replace("0x", ""):
                to_address = "0x" + log.topics[2].hex()
                print(f"To address: {to_address} vs expected: {treasury_address_padded}")
                if to_address.lower() == treasury_address_padded:
                    try:
                        # Assuming log.data is HexBytes or bytes
                        transfer_amount = int(log.data.hex(), 16)
                    except AttributeError:
                        # If it's a string
                        transfer_amount = int(log.data, 16)
                    print(f"Amount: {transfer_amount}")
                    if transfer_amount >= 10000:
                        valid_transfer_found = True
                        break

print(f"Valid transfer found: {valid_transfer_found}")
