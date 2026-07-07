import pytest
import pytest_asyncio
import asyncio
import json
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from web3 import AsyncWeb3
from web3.exceptions import ContractLogicError
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak

from daemon.config import settings
from daemon.signer import CryptographicSigner
from daemon.risk_engine import RiskEngine
from daemon.main import app

# Setup FastAPI test client
client = TestClient(app)

@pytest.fixture
def mock_signer():
    # Use standard anvil default private key for tests
    return CryptographicSigner("0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80")

def test_signer_initialization(mock_signer):
    # Verify address matches the private key
    # Anvil account 0 address is 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266
    assert mock_signer.get_oracle_address().lower() == "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266"

def test_generate_quote_id(mock_signer):
    quote_id = mock_signer.generate_quote_id()
    assert isinstance(quote_id, bytes)
    assert len(quote_id) == 32

def test_sign_quote_e2e_recovery(mock_signer):
    # Verify signature recovery matches on-chain behavior
    client_addr = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    asset = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
    coverage = 1000 * 10**6
    premium = 50 * 10**6
    timeout = 3600
    deadline = 9999999999
    quote_id = mock_signer.generate_quote_id()
    chain_id = 196
    escrow = "0x5FbDB2315678afecb367f032d93F642f64180aa3"

    signature_hex = mock_signer.sign_quote(
        client_address=client_addr,
        asset=asset,
        coverage_amount=coverage,
        premium_amount=premium,
        timeout_duration=timeout,
        deadline=deadline,
        quote_id=quote_id,
        chain_id=chain_id,
        escrow_address=escrow
    )

    # Check length (signature hex must be 130 hex characters / 65 bytes)
    assert len(signature_hex) == 130

    # Let's perform standard EIP-191 recovery in python to verify correctness
    from eth_abi.packed import encode_packed
    packed_data = encode_packed(
        ['address', 'address', 'uint256', 'uint256', 'uint256', 'uint256', 'bytes32', 'uint256', 'address'],
        [client_addr, asset, coverage, premium, timeout, deadline, quote_id, chain_id, escrow]
    )
    msg_hash = keccak(packed_data)
    signable_message = encode_defunct(primitive=msg_hash)
    
    recovered_addr = Account.recover_message(signable_message, signature=bytes.fromhex(signature_hex))
    assert recovered_addr.lower() == mock_signer.get_oracle_address().lower()

def test_sign_settlement_e2e_recovery(mock_signer):
    policy_id = 42
    tier = 2
    chain_id = 196
    escrow = "0x5FbDB2315678afecb367f032d93F642f64180aa3"

    signature_hex = mock_signer.sign_settlement(
        policy_id=policy_id,
        tier=tier,
        chain_id=chain_id,
        escrow_address=escrow
    )

    assert len(signature_hex) == 130

    from eth_abi.packed import encode_packed
    packed_data = encode_packed(
        ['uint256', 'uint8', 'uint256', 'address'],
        [policy_id, tier, chain_id, escrow]
    )
    msg_hash = keccak(packed_data)
    signable_message = encode_defunct(primitive=msg_hash)

    recovered_addr = Account.recover_message(signable_message, signature=bytes.fromhex(signature_hex))
    assert recovered_addr.lower() == mock_signer.get_oracle_address().lower()

# Async testing of RiskEngine
@pytest.mark.asyncio
async def test_risk_engine_empty_bytecode():
    # Setup risk engine with mocked Web3 calls
    engine = RiskEngine()
    engine.w3 = AsyncMock()
    
    # Mock eth_getCode to return empty bytes (representing EOA/no contract)
    engine.w3.eth.get_code.return_value = b""

    result = await engine.simulate_transaction_risk(
        client_address="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        target_contract="0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
        calldata_hex="0x",
        value_wei=0,
        coverage_requested=1000
    )

    assert result["is_executable"] is False
    assert "EMPTY_BYTECODE" in result["detected_vectors"]
    assert result["P_fail"] == 10000

@pytest.mark.asyncio
async def test_risk_engine_revert():
    engine = RiskEngine()
    engine.w3 = AsyncMock()
    
    # Mock eth_getCode to return some bytecode (representing contract exists)
    engine.w3.eth.get_code.return_value = b"\x60\x80"
    
    # Mock eth_call to raise ContractLogicError (revert)
    engine.w3.eth.call.side_effect = ContractLogicError("Execution reverted: Slippage tolerance exceeded")

    result = await engine.simulate_transaction_risk(
        client_address="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        target_contract="0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
        calldata_hex="0x",
        value_wei=0,
        coverage_requested=1000
    )

    assert result["is_executable"] is False
    assert "TRANSACTION_REVERTED" in result["detected_vectors"]
    assert result["P_fail"] == 10000
    assert "Slippage tolerance exceeded" in result["revert_reason"]

@pytest.mark.asyncio
async def test_risk_engine_threat_heuristics():
    engine = RiskEngine()
    engine.w3 = AsyncMock()
    
    # Mock eth_getCode to return bytecode with DELEGATECALL (f4) and SELFDESTRUCT (ff)
    # Bytecode includes \xf4 and \xff
    engine.w3.eth.get_code.return_value = b"\x60\x80\xf4\x60\x00\xff"
    engine.w3.eth.call.return_value = b"success"

    result = await engine.simulate_transaction_risk(
        client_address="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        target_contract="0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
        calldata_hex="0x",
        value_wei=0,
        coverage_requested=1000
    )

    assert result["is_executable"] is True
    assert "DELEGATECALL_VECTOR" in result["detected_vectors"]
    assert "SELFDESTRUCT_VECTOR" in result["detected_vectors"]
    # 2500 (delegatecall) + 3000 (selfdestruct) = 5500
    assert result["P_fail"] == 5500

# FastAPI HTTP Endpoints test via TestClient
@patch("daemon.main.risk_engine")
def test_api_simulate_endpoint(mock_engine_api):
    # Mock simulate_transaction_risk response
    mock_engine_api.simulate_transaction_risk = AsyncMock(return_value={
        "is_executable": True,
        "detected_vectors": [],
        "P_fail": 200
    })

    payload = {
        "client_address": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        "target_contract": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
        "calldata_hex": "0xa9059cbb00000000000000000000000070997970c51812dc3a010c7d01b50e0d17dc79c800000000000000000000000000000000000000000000000000000000000f4240",
        "value_wei": 0,
        "coverage_requested": 1000000
    }

    response = client.post("/v1/risk/simulate", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["is_executable"] is True
    assert data["P_fail"] == 200
    assert data["detected_vectors"] == []

@patch("daemon.main.risk_engine")
def test_api_quote_endpoint(mock_engine_api):
    # Mock simulate_transaction_risk response
    mock_engine_api.simulate_transaction_risk = AsyncMock(return_value={
        "is_executable": True,
        "detected_vectors": [],
        "P_fail": 500  # 5% fail risk
    })
    # Mock get_token_decimals to return 6 (e.g. USDT)
    mock_engine_api.get_token_decimals = AsyncMock(return_value=6)

    payload = {
        "client_address": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        "target_contract": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
        "calldata_hex": "0x",
        "value_wei": 0,
        "coverage_requested": 1000 * 10**6,  # 1000 USDT
        "timeout_duration": 3600,
        "asset": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
    }

    response = client.post("/v1/insurance/quote", json=payload)
    assert response.status_code == 200
    data = response.json()
    
    # Premium calculation:
    # CoverageRequested * P_fail / 10000 + FixedUnderwriterMargin (scaled)
    # 1000 USDT * 500 / 10000 + 10 USDT
    # = 50 USDT + 10 USDT = 60 USDT = 60 * 10**6
    assert data["premium_amount"] == 60 * 10**6
    assert data["quote_id"].startswith("0x")
    assert len(data["quote_id"]) == 66  # "0x" + 64 hex characters
    assert data["deadline"] > 0
    assert data["signature"].startswith("0x")

@pytest.mark.asyncio
async def test_risk_engine_latency_timeout():
    import httpx
    engine = RiskEngine()
    engine.w3 = AsyncMock()
    
    # Simulate a slow network response triggering httpx.ReadTimeout (representing > 180ms delay)
    engine.w3.eth.get_code.side_effect = httpx.ReadTimeout("Mocked RPC read timeout after 180ms")

    result = await engine.simulate_transaction_risk(
        client_address="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        target_contract="0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
        calldata_hex="0x",
        value_wei=0,
        coverage_requested=1000
    )

    assert result["is_executable"] is False
    assert "RPC_LATENCY_TIMEOUT_EXCEEDED" in result["detected_vectors"]
    assert result["P_fail"] == 10000

@pytest.mark.asyncio
async def test_risk_engine_asyncio_timeout():
    engine = RiskEngine()
    engine.w3 = AsyncMock()
    
    # Simulate a connection timing out via asyncio.TimeoutError
    engine.w3.eth.get_code.side_effect = asyncio.TimeoutError("Connection timed out")

    result = await engine.simulate_transaction_risk(
        client_address="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        target_contract="0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
        calldata_hex="0x",
        value_wei=0,
        coverage_requested=1000
    )

    assert result["is_executable"] is False
    assert "RPC_LATENCY_TIMEOUT_EXCEEDED" in result["detected_vectors"]
    assert result["P_fail"] == 10000

