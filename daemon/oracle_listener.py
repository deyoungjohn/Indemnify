import os
import sys
import json
import asyncio
import logging
from typing import Dict, Any, List

from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider
from web3.exceptions import ContractLogicError, Web3Exception

# Fallback config loading: try to import settings from existing daemon config
try:
    from daemon.config import settings
    default_rpc = settings.rpc_provider_url
    default_escrow = settings.escrow_address
    default_key = settings.oracle_private_key
    default_chain_id = settings.chain_id
except ImportError:
    default_rpc = "https://rpc.xlayer.tech"
    default_escrow = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
    default_key = ""
    default_chain_id = 196

# Environment variables mapping will be loaded dynamically at runtime in class initialization

# ERC-4337 UserOperationEvent topic: keccak256("UserOperationEvent(bytes32,address,address,uint256,bool,uint256,uint256)")
USER_OP_EVENT_TOPIC = "0x49628fd1471006c1482da88028e9ce4dbb080b815c9b0344d39e5a8e6ec1419f"

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("indemnify.oracle_listener")

# Attempt to load the full ABI from the compiler output
def load_escrow_abi() -> List[Dict[str, Any]]:
    possible_paths = [
        os.path.join(os.path.dirname(__file__), "../out/ParametricEscrow.sol/ParametricEscrow.json"),
        os.path.join(os.getcwd(), "out/ParametricEscrow.sol/ParametricEscrow.json"),
        os.path.join(os.getcwd(), "../out/ParametricEscrow.sol/ParametricEscrow.json"),
    ]
    for path in possible_paths:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    artifact = json.load(f)
                    abi = artifact.get("abi")
                    if abi:
                        logger.info(f"Loaded ParametricEscrow ABI from {path}")
                        return abi
            except Exception as e:
                logger.warning(f"Error reading ABI from {path}: {e}")
                
    logger.info("Foundry compile output not found. Using minimal fallback ABI for ParametricEscrow.")
    return [
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
            "name": "settlePolicy",
            "type": "function",
            "inputs": [
                {"name": "policyId", "type": "uint256"},
                {"name": "tier", "type": "uint8"}
            ],
            "outputs": [],
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

class OracleListener:
    def __init__(self):
        self.rpc_url = os.environ.get("RPC_URL") or os.environ.get("RPC_PROVIDER_URL") or default_rpc
        self.escrow_address = AsyncWeb3.to_checksum_address(
            os.environ.get("ESCROW_CONTRACT_ADDRESS") or default_escrow
        )
        self.chain_id = int(os.environ.get("CHAIN_ID") or default_chain_id)
        
        # Verify private key
        key = os.environ.get("ORACLE_PRIVATE_KEY") or os.environ.get("INDEMNIFY_ORACLE_KEY") or default_key
        if not key:
            raise ValueError("ORACLE_PRIVATE_KEY environment variable is required.")
        
        self.oracle_private_key = key
        if self.oracle_private_key.startswith("0x"):
            self.oracle_private_key = self.oracle_private_key[2:]
            
        self.w3 = None
        self.contract = None
        self.oracle_account = None
        self.oracle_address = None
        
        # Nonce tracking
        self.nonce_lock = asyncio.Lock()
        self.oracle_nonce = None
        
        # Active policy tracking queue (dict)
        self.active_policies: Dict[int, Dict[str, Any]] = {}
        
        # Service states
        self.running = True
        self.last_processed_block = None
        self.catchup_complete = False
        
    async def connect(self):
        """Initializes connection to the RPC provider."""
        if self.rpc_url.startswith("ws://") or self.rpc_url.startswith("wss://"):
            from web3.providers import AsyncWebSocketProvider
            logger.info("Initializing WebSocket connection...")
            self.w3 = AsyncWeb3(AsyncWebSocketProvider(self.rpc_url))
        else:
            logger.info("Initializing HTTP connection...")
            self.w3 = AsyncWeb3(AsyncHTTPProvider(self.rpc_url))
            
        self.oracle_account = self.w3.eth.account.from_key(self.oracle_private_key)
        self.oracle_address = self.oracle_account.address
        
        abi = load_escrow_abi()
        self.contract = self.w3.eth.contract(address=self.escrow_address, abi=abi)
        
    async def reconnect(self):
        """Re-establishes connection after failure."""
        try:
            if self.w3 and hasattr(self.w3.provider, 'disconnect'):
                await self.w3.provider.disconnect()
        except Exception as e:
            logger.warning(f"Error disconnecting old provider: {e}")
            
        await self.connect()
        # Reset local nonce to trigger refetch
        async with self.nonce_lock:
            self.oracle_nonce = None

    async def catchup_active_policies(self, current_block: int):
        """
        Scans historical logs for recent PolicyCreated events,
        queries their on-chain state, and populates the active policies queue.
        """
        # Look back 500 blocks or up to block 0
        from_block = max(0, current_block - 500)
        logger.info(f"Scanning blocks {from_block} to {current_block} for active policies...")
        
        try:
            events = []
            chunk_size = 100
            for start in range(from_block, current_block + 1, chunk_size):
                end = min(start + chunk_size - 1, current_block)
                chunk_events = await self.contract.events.PolicyCreated().get_logs(from_block=start, to_block=end)
                events.extend(chunk_events)
            
            for event in events:
                policy_id = event['args']['policyId']
                
                # Check current on-chain status
                try:
                    policy_data = await self.contract.functions.policies(policy_id).call()
                    # Struct returns: clientAddress, asset, coverageAmount, premiumPaid, startTimestamp, timeoutDuration, riskBracketTier, status
                    status = policy_data[7]  # enum PolicyStatus: 0 = Active
                    
                    if status == 0:
                        client = AsyncWeb3.to_checksum_address(event['args']['client'])
                        startTimestamp = event['args']['startTimestamp']
                        timeoutDuration = event['args']['timeoutDuration']
                        
                        # Retrieve original transaction to get the creation nonce and hash
                        tx = await self.w3.eth.get_transaction(event['transactionHash'])
                        creation_nonce = tx['nonce']
                        
                        self.active_policies[policy_id] = {
                            "client": client,
                            "startTimestamp": startTimestamp,
                            "timeoutDuration": timeoutDuration,
                            "creation_nonce": creation_nonce,
                            "creation_block": event['blockNumber'],
                            "creation_tx_hash": event['transactionHash'].hex()
                        }
                        logger.info(f"Reconstructed active policy {policy_id} for client {client} (Creation Nonce: {creation_nonce})")
                except Exception as e:
                    logger.error(f"Error checking status for policy {policy_id}: {e}")
                    
            # SECOND PASS: Scan blocks since creation for missed client target transactions
            if self.active_policies:
                min_creation_block = min([p['creation_block'] for p in self.active_policies.values()])
                logger.info(f"Scanning blocks {min_creation_block} to {current_block} for missed target transactions...")
                for b_num in range(min_creation_block, current_block + 1):
                    try:
                        block = await self.w3.eth.get_block(b_num, full_transactions=True)
                        
                        # Fetch ERC-4337 UserOperationEvents for this block
                        aa_logs = await self.w3.eth.get_logs({
                            'fromBlock': b_num,
                            'toBlock': b_num,
                            'topics': [USER_OP_EVENT_TOPIC]
                        })
                        
                        aa_senders_by_tx = {}
                        for log in aa_logs:
                            if len(log['topics']) > 2:
                                sender = AsyncWeb3.to_checksum_address("0x" + log['topics'][2].hex()[-40:])
                                tx_hex = log['transactionHash'].hex()
                                if tx_hex not in aa_senders_by_tx:
                                    aa_senders_by_tx[tx_hex] = set()
                                aa_senders_by_tx[tx_hex].add(sender)
                        
                        for tx in block['transactions']:
                            tx_from = AsyncWeb3.to_checksum_address(tx['from'])
                            tx_hash_hex = tx['hash'].hex()
                            aa_senders = aa_senders_by_tx.get(tx_hash_hex, set())
                            
                            for policy_id, policy in list(self.active_policies.items()):
                                is_client_tx = (tx_from.lower() == policy['client'].lower()) or (policy['client'] in aa_senders)
                                if is_client_tx and tx_hash_hex != policy['creation_tx_hash']:
                                    logger.info(f"Historical client tx detected for policy {policy_id} in block {b_num}. Settling...")
                                    try:
                                        receipt = await self.w3.eth.get_transaction_receipt(tx['hash'])
                                        status = receipt['status']
                                        tier = 0 if status == 1 else 3
                                        asyncio.create_task(self.settle_policy(policy_id, tier))
                                        self.active_policies.pop(policy_id, None)
                                    except Exception as e:
                                        logger.error(f"Error settling missed tx for policy {policy_id}: {e}")
                    except Exception as e:
                        logger.error(f"Error scanning historical block {b_num}: {e}")
                        
            logger.info(f"Historical catch-up complete. Tracking {len(self.active_policies)} active policies.")
        except Exception as e:
            logger.error(f"Error fetching historical PolicyCreated events: {e}")

    async def settle_policy(self, policy_id: int, tier: int):
        """
        Constructs, signs, and broadcasts a settlePolicy transaction.
        Uses a mutex lock to guarantee strict nonce safety.
        """
        async with self.nonce_lock:
            # Initialize or fetch the latest nonce if not loaded
            if self.oracle_nonce is None:
                self.oracle_nonce = await self.w3.eth.get_transaction_count(self.oracle_address, 'pending')
                
            nonce = self.oracle_nonce
            tx_func = self.contract.functions.settlePolicy(policy_id, tier)
            
            # Estimate gas limit
            try:
                gas_limit = await tx_func.estimate_gas({"from": self.oracle_address})
                gas_limit = int(gas_limit * 1.2)  # 20% safety margin
            except Exception as e:
                logger.warning(f"Gas estimation failed for policy {policy_id}: {e}. Falling back to default.")
                gas_limit = 200000
                
            tx_params = {
                "from": self.oracle_address,
                "nonce": nonce,
                "gas": gas_limit,
                "chainId": self.chain_id
            }
            
            # Try to build EIP-1559 transaction fees
            try:
                fee_history = await self.w3.eth.fee_history(1, "latest")
                base_fee = fee_history["baseFeePerGas"][-1]
                
                try:
                    priority_fee = await self.w3.eth.max_priority_fee
                except Exception:
                    priority_fee = self.w3.to_wei(2, "gwei")
                    
                max_priority_fee = min(priority_fee, self.w3.to_wei(10, "gwei"))  # Cap at 10 Gwei
                max_fee = (base_fee * 2) + max_priority_fee
                max_fee = min(max_fee, self.w3.to_wei(100, "gwei"))  # Cap total at 100 Gwei
                
                tx_params["maxFeePerGas"] = max_fee
                tx_params["maxPriorityFeePerGas"] = max_priority_fee
            except Exception as e:
                logger.warning(f"EIP-1559 pricing unavailable: {e}. Falling back to legacy gas price.")
                try:
                    gas_price = await self.w3.eth.gas_price
                    gas_price = min(gas_price, self.w3.to_wei(100, "gwei"))  # Cap legacy fee at 100 Gwei
                except Exception:
                    gas_price = self.w3.to_wei(20, "gwei")
                tx_params["gasPrice"] = gas_price

            try:
                # Build transaction
                tx = await tx_func.build_transaction(tx_params)
                
                # Sign transaction
                signed_tx = self.w3.eth.account.sign_transaction(tx, private_key=self.oracle_private_key)
                
                # Broadcast raw transaction
                tx_hash = await self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
                logger.info(f"Settle TX broadcasted for policy {policy_id} (Tier {tier}). Hash: {tx_hash.hex()}. Nonce: {nonce}")
                
                # Increment nonce upon successful broadcast
                self.oracle_nonce += 1
                return tx_hash
            except Exception as e:
                logger.error(f"Error broadcasting transaction for policy {policy_id} with nonce {nonce}: {e}")
                # Reset local nonce to force reload on next broadcast
                self.oracle_nonce = None
                raise e

    async def process_block(self, block_num: int):
        """Processes logs and transactions in a single block."""
        logger.info(f"Processing block {block_num}...")
        
        # 1. Check for new policies created in this block
        try:
            new_events = await self.contract.events.PolicyCreated().get_logs(
                from_block=block_num,
                to_block=block_num
            )
            for event in new_events:
                policy_id = event['args']['policyId']
                client = AsyncWeb3.to_checksum_address(event['args']['client'])
                startTimestamp = event['args']['startTimestamp']
                timeoutDuration = event['args']['timeoutDuration']
                
                # Query creation transaction for nonce and hash
                tx = await self.w3.eth.get_transaction(event['transactionHash'])
                creation_nonce = tx['nonce']
                
                self.active_policies[policy_id] = {
                    "client": client,
                    "startTimestamp": startTimestamp,
                    "timeoutDuration": timeoutDuration,
                    "creation_nonce": creation_nonce,
                    "creation_block": block_num,
                    "creation_tx_hash": event['transactionHash'].hex()
                }
                logger.info(f"New policy detected: ID {policy_id} | Client {client} | Creation Nonce {creation_nonce}")
        except Exception as e:
            logger.error(f"Failed to scan block {block_num} for events: {e}")
            raise e
            
        # If no active policies are tracked, we can skip transaction scan
        if not self.active_policies:
            return
            
        # 2. Retrieve block detail to scan transaction list
        try:
            block = await self.w3.eth.get_block(block_num, full_transactions=True)
            block_timestamp = block['timestamp']
            
            # Fetch ERC-4337 UserOperationEvents for this block
            aa_logs = await self.w3.eth.get_logs({
                'fromBlock': block_num,
                'toBlock': block_num,
                'topics': [USER_OP_EVENT_TOPIC]
            })
            
            aa_senders_by_tx = {}
            for log in aa_logs:
                if len(log['topics']) > 2:
                    sender = AsyncWeb3.to_checksum_address("0x" + log['topics'][2].hex()[-40:])
                    tx_hex = log['transactionHash'].hex()
                    if tx_hex not in aa_senders_by_tx:
                        aa_senders_by_tx[tx_hex] = set()
                    aa_senders_by_tx[tx_hex].add(sender)
                    
        except Exception as e:
            logger.error(f"Failed to fetch block details for block {block_num}: {e}")
            raise e
            
        # Scan block transactions for client next outbound transactions
        for tx in block['transactions']:
            tx_from = AsyncWeb3.to_checksum_address(tx['from'])
            tx_hash = tx['hash']
            tx_hash_hex = tx_hash.hex()
            aa_senders = aa_senders_by_tx.get(tx_hash_hex, set())
            
            # Use copy of dict to allow removal during iteration
            for policy_id, policy in list(self.active_policies.items()):
                is_client_tx = (tx_from.lower() == policy['client'].lower()) or (policy['client'] in aa_senders)
                if is_client_tx and tx_hash_hex != policy['creation_tx_hash']:
                    # We found the client's next outbound transaction!
                    logger.info(f"Detected client {policy['client']} target transaction in block {block_num}. Hash: {tx_hash_hex}.")
                    
                    try:
                        receipt = await self.w3.eth.get_transaction_receipt(tx_hash)
                        status = receipt['status']
                        
                        # Tier 0 for success, Tier 3 for revert
                        tier = 0 if status == 1 else 3
                        
                        logger.info(f"Client transaction evaluated: Policy {policy_id} | Receipt Status {status} -> Settling with Tier {tier}")
                        
                        # Trigger async settlement
                        asyncio.create_task(self.settle_policy(policy_id, tier))
                        
                        # Remove from active queue immediately to avoid double detection
                        self.active_policies.pop(policy_id, None)
                    except Exception as e:
                        logger.error(f"Error evaluating client transaction receipt for policy {policy_id}: {e}")
                        
        # 3. Check for policy timeouts
        for policy_id, policy in list(self.active_policies.items()):
            elapsed = block_timestamp - policy['startTimestamp']
            if elapsed >= policy['timeoutDuration']:
                logger.info(f"Policy {policy_id} reached timeout window ({elapsed}s >= {policy['timeoutDuration']}s) without target transaction. Settling with Tier 0.")
                asyncio.create_task(self.settle_policy(policy_id, 0))
                self.active_policies.pop(policy_id, None)

    async def start(self):
        """Starts the daemon execution loop with robust reconnect backoffs."""
        await self.connect()
        logger.info(f"Oracle daemon is active. Oracle address: {self.oracle_address}")
        
        backoff = 1.0
        while self.running:
            try:
                current_block = await self.w3.eth.block_number
                
                # Perform historical catch-up
                if not self.catchup_complete:
                    await self.catchup_active_policies(current_block)
                    self.last_processed_block = current_block
                    self.catchup_complete = True
                    backoff = 1.0  # Reset backoff on successful loop start
                    
                # Main continuous polling loop
                while self.running:
                    try:
                        tip_block = await self.w3.eth.block_number
                        latest_block = max(0, tip_block - 2) # 2-block lag for indexing safety
                        if latest_block > self.last_processed_block:
                            for block_num in range(self.last_processed_block + 1, latest_block + 1):
                                await self.process_block(block_num)
                                self.last_processed_block = block_num
                            backoff = 1.0  # Reset backoff on successful processing
                            
                        await asyncio.sleep(2.0)
                    except Exception as e:
                        logger.error(f"Error in block processing iteration: {e}")
                        raise e  # Propagate to trigger outer reconnect sequence
                        
            except (Web3Exception, Exception) as e:
                if not self.running:
                    break
                logger.error(f"Daemon RPC disconnected: {e}. Attempting reconnect in {backoff:.1f}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)  # Cap exponential backoff at 60s
                
                try:
                    await self.reconnect()
                except Exception as rec_err:
                    logger.error(f"Reconnection attempt failed: {rec_err}")

    def stop(self):
        """Gracefully halts the daemon."""
        logger.info("Stopping Oracle Listener Daemon...")
        self.running = False

async def main():
    listener = OracleListener()
    try:
        await listener.start()
    except KeyboardInterrupt:
        listener.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Daemon terminated by user.")
