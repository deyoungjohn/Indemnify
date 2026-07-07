import pytest
import pytest_asyncio
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from web3.exceptions import Web3Exception

from daemon.oracle_listener import OracleListener, load_escrow_abi

def test_load_escrow_abi():
    abi = load_escrow_abi()
    assert isinstance(abi, list)
    assert len(abi) > 0
    
    # Assert essential methods and events are in ABI
    event_names = [item.get("name") for item in abi if item.get("type") == "event"]
    function_names = [item.get("name") for item in abi if item.get("type") == "function"]
    
    assert "PolicyCreated" in event_names
    assert "settlePolicy" in function_names
    assert "policies" in function_names

@pytest.fixture
def mock_oracle_env():
    with patch.dict("os.environ", {
        "RPC_URL": "http://mock-rpc-node.io",
        "ESCROW_CONTRACT_ADDRESS": "0x5FbDB2315678afecb367f032d93F642f64180aa3",
        "ORACLE_PRIVATE_KEY": "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
        "CHAIN_ID": "31337"
    }):
        yield

@pytest.fixture
def mock_w3_setup():
    w3 = MagicMock()
    
    # Mock account
    account = MagicMock()
    account.address = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
    w3.eth.account.from_key.return_value = account
    
    # Mock basic functions
    w3.eth.get_transaction_count = AsyncMock(return_value=42)
    w3.eth.fee_history = AsyncMock(return_value={"baseFeePerGas": [1000000000]})
    w3.eth.max_priority_fee = AsyncMock(return_value=1500000000)
    w3.eth.gas_price = AsyncMock(return_value=2000000000)
    w3.eth.block_number = AsyncMock(return_value=100)
    w3.eth.get_transaction = AsyncMock(return_value={"nonce": 15})
    
    # Mock contract
    contract = MagicMock()
    
    # Events mocking
    policy_created_event = MagicMock()
    policy_created_event.get_logs = AsyncMock(return_value=[])
    contract.events.PolicyCreated = MagicMock(return_value=policy_created_event)
    
    # Functions mocking
    settle_policy_func = MagicMock()
    settle_policy_func.estimate_gas = AsyncMock(return_value=100000)
    settle_policy_func.build_transaction = AsyncMock(return_value={"nonce": 42})
    contract.functions.settlePolicy = MagicMock(return_value=settle_policy_func)
    
    policies_func = MagicMock()
    policies_func.call = AsyncMock(return_value=("0x70997970C51812dc3A010C7d01b50e0d17dc79C8", "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC", 1000, 50, 1700000000, 3600, 0, 0))
    contract.functions.policies = MagicMock(return_value=policies_func)
    
    w3.eth.contract.return_value = contract
    
    return w3, contract

@pytest.mark.asyncio
async def test_listener_init_and_connect(mock_oracle_env, mock_w3_setup):
    mock_w3, mock_contract = mock_w3_setup
    listener = OracleListener()
    
    assert listener.rpc_url == "http://mock-rpc-node.io"
    assert listener.escrow_address == "0x5FbDB2315678afecb367f032d93F642f64180aa3"
    assert listener.chain_id == 31337
    
    with patch("daemon.oracle_listener.AsyncWeb3") as mock_w3_class:
        mock_w3_class.return_value = mock_w3
        await listener.connect()
        
        assert listener.w3 == mock_w3
        assert listener.oracle_address == "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
        assert listener.contract == mock_contract

@pytest.mark.asyncio
async def test_listener_historical_catchup(mock_oracle_env, mock_w3_setup):
    mock_w3, mock_contract = mock_w3_setup
    listener = OracleListener()
    listener.w3 = mock_w3
    listener.contract = mock_contract
    
    # Mock PolicyCreated event log retrieval
    mock_event = {
        "args": {
            "policyId": 1,
            "client": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
            "asset": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
            "coverageAmount": 1000,
            "premiumPaid": 50,
            "startTimestamp": 1700000000,
            "timeoutDuration": 3600
        },
        "transactionHash": b"\x01" * 32,
        "blockNumber": 100
    }
    
    # Configure event mock specifically for this test
    mock_contract.events.PolicyCreated.return_value.get_logs = AsyncMock(return_value=[mock_event])
    
    await listener.catchup_active_policies(105)
    
    assert 1 in listener.active_policies
    policy = listener.active_policies[1]
    assert policy["client"] == "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    assert policy["creation_nonce"] == 15
    assert policy["startTimestamp"] == 1700000000

@pytest.mark.asyncio
async def test_process_block_new_policy(mock_oracle_env, mock_w3_setup):
    mock_w3, mock_contract = mock_w3_setup
    listener = OracleListener()
    listener.w3 = mock_w3
    listener.contract = mock_contract
    
    # Mock event log in the current block
    mock_event = {
        "args": {
            "policyId": 2,
            "client": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
            "asset": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
            "coverageAmount": 1000,
            "premiumPaid": 50,
            "startTimestamp": 1700000000,
            "timeoutDuration": 3600
        },
        "transactionHash": b"\x02" * 32
    }
    
    mock_contract.events.PolicyCreated.return_value.get_logs = AsyncMock(return_value=[mock_event])
    listener.w3.eth.get_block = AsyncMock(return_value={"timestamp": 1700000100, "transactions": []})
    
    await listener.process_block(101)
    
    assert 2 in listener.active_policies
    assert listener.active_policies[2]["creation_nonce"] == 15
    assert listener.active_policies[2]["client"] == "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

@pytest.mark.asyncio
async def test_process_block_evaluate_success_client_tx(mock_oracle_env, mock_w3_setup):
    mock_w3, mock_contract = mock_w3_setup
    listener = OracleListener()
    listener.w3 = mock_w3
    listener.contract = mock_contract
    
    # Set up active policy tracking
    listener.active_policies[3] = {
        "client": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        "startTimestamp": 1700000000,
        "timeoutDuration": 3600,
        "creation_nonce": 20,
        "creation_block": 100
    }
    
    # Mock block with a target outbound transaction from client with higher nonce (21)
    mock_tx = {
        "from": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        "nonce": 21,
        "hash": b"\x03" * 32
    }
    listener.w3.eth.get_block = AsyncMock(return_value={
        "timestamp": 1700000100,
        "transactions": [mock_tx]
    })
    
    # Mock transaction receipt (status = 1: success)
    listener.w3.eth.get_transaction_receipt = AsyncMock(return_value={"status": 1})
    
    # Patch settle_policy to intercept settlement calls
    with patch.object(listener, "settle_policy", new_callable=AsyncMock) as mock_settle:
        await listener.process_block(101)
        
        # Wait a tiny fraction to allow scheduled asyncio tasks to run
        await asyncio.sleep(0.01)
        mock_settle.assert_called_once_with(3, 0)
        
        # Policy must be removed from active queue
        assert 3 not in listener.active_policies

@pytest.mark.asyncio
async def test_process_block_evaluate_revert_client_tx(mock_oracle_env, mock_w3_setup):
    mock_w3, mock_contract = mock_w3_setup
    listener = OracleListener()
    listener.w3 = mock_w3
    listener.contract = mock_contract
    
    # Set up active policy tracking
    listener.active_policies[4] = {
        "client": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        "startTimestamp": 1700000000,
        "timeoutDuration": 3600,
        "creation_nonce": 20,
        "creation_block": 100
    }
    
    # Mock block with client transaction with higher nonce (21)
    mock_tx = {
        "from": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        "nonce": 21,
        "hash": b"\x04" * 32
    }
    listener.w3.eth.get_block = AsyncMock(return_value={
        "timestamp": 1700000100,
        "transactions": [mock_tx]
    })
    
    # Mock transaction receipt (status = 0: revert)
    listener.w3.eth.get_transaction_receipt = AsyncMock(return_value={"status": 0})
    
    with patch.object(listener, "settle_policy", new_callable=AsyncMock) as mock_settle:
        await listener.process_block(101)
        await asyncio.sleep(0.01)
        
        # settlePolicy called with policyId=4 and tier=3 (revert)
        mock_settle.assert_called_once_with(4, 3)
        assert 4 not in listener.active_policies

@pytest.mark.asyncio
async def test_process_block_timeout(mock_oracle_env, mock_w3_setup):
    mock_w3, mock_contract = mock_w3_setup
    listener = OracleListener()
    listener.w3 = mock_w3
    listener.contract = mock_contract
    
    # Set up active policy tracking
    listener.active_policies[5] = {
        "client": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        "startTimestamp": 1700000000,
        "timeoutDuration": 3600,
        "creation_nonce": 20,
        "creation_block": 100
    }
    
    # Mock block details where timestamp exceeds timeout
    listener.w3.eth.get_block = AsyncMock(return_value={
        "timestamp": 1700003601,
        "transactions": []
    })
    
    with patch.object(listener, "settle_policy", new_callable=AsyncMock) as mock_settle:
        await listener.process_block(105)
        
        # Settle policy should NOT be called
        mock_settle.assert_not_called()
        
        # Policy must be dropped from active queue due to timeout
        assert 5 not in listener.active_policies

@pytest.mark.asyncio
async def test_settle_policy_concurrency_and_nonce(mock_oracle_env, mock_w3_setup):
    mock_w3, mock_contract = mock_w3_setup
    listener = OracleListener()
    listener.w3 = mock_w3
    listener.contract = mock_contract
    listener.oracle_address = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
    listener.oracle_private_key = "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    listener.chain_id = 31337
    
    # Set initial nonce
    listener.oracle_nonce = 42
    
    mock_signed = MagicMock()
    mock_signed.raw_transaction = b"raw_signed_tx_payload"
    listener.w3.eth.account.sign_transaction = MagicMock(return_value=mock_signed)
    
    listener.w3.eth.send_raw_transaction = AsyncMock(return_value=b"\xaa" * 32)
    
    # Settle policy call
    tx_hash = await listener.settle_policy(10, 0)
    
    assert tx_hash == b"\xaa" * 32
    # Verify local nonce got incremented sequentially
    assert listener.oracle_nonce == 43
