import sys
import json
import logging
import time
from typing import Dict, Any, Optional, List
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from daemon.config import settings
from daemon.risk_engine import RiskEngine
from daemon.signer import CryptographicSigner
from daemon.x402_middleware import verify_payment, build_402_response
from web3 import Web3

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("indemnify.daemon")

# Initialize modules
risk_engine = RiskEngine()
signer = CryptographicSigner()

# Synchronous Web3 instance for pure ABI encoding (AsyncWeb3 contracts don't support encode_abi)
_sync_w3 = Web3()

ESCROW_ABI = [
    {
        "inputs": [],
        "name": "policyCount",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "name": "policies",
        "outputs": [
            {"internalType": "address", "name": "clientAddress", "type": "address"},
            {"internalType": "address", "name": "asset", "type": "address"},
            {"internalType": "uint256", "name": "coverageAmount", "type": "uint256"},
            {"internalType": "uint256", "name": "premiumPaid", "type": "uint256"},
            {"internalType": "uint256", "name": "startTimestamp", "type": "uint256"},
            {"internalType": "uint256", "name": "timeoutDuration", "type": "uint256"},
            {"internalType": "uint8", "name": "riskBracketTier", "type": "uint8"},
            {"internalType": "enum ParametricEscrow.PolicyStatus", "name": "status", "type": "uint8"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "asset", "type": "address"},
            {"internalType": "uint256", "name": "coverageAmount", "type": "uint256"},
            {"internalType": "uint256", "name": "premiumAmount", "type": "uint256"},
            {"internalType": "uint256", "name": "timeoutDuration", "type": "uint256"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"},
            {"internalType": "bytes32", "name": "quoteId", "type": "bytes32"},
            {"internalType": "bytes", "name": "signature", "type": "bytes"}
        ],
        "name": "createPolicy",
        "outputs": [{"internalType": "uint256", "name": "policyId", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [{"internalType": "uint256", "name": "policyId", "type": "uint256"}],
        "name": "terminatePolicy",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]

# ---------------------------------------------------------------------------
# Pydantic Schemas for FastAPI REST API
# ---------------------------------------------------------------------------
class RiskSimulateRequest(BaseModel):
    client_address: str = Field(..., description="Client address submitting the transaction")
    target_contract: str = Field(..., description="Contract being interacted with")
    calldata_hex: str = Field(..., description="Hex encoded calldata payload")
    value_wei: int = Field(default=0, description="Value sent in wei")
    coverage_requested: int = Field(..., description="Coverage amount requested")

class RiskSimulateResponse(BaseModel):
    is_executable: bool
    detected_vectors: List[str]
    P_fail: int

class InsuranceQuoteRequest(BaseModel):
    client_address: str = Field(..., description="Client address submitting the transaction")
    target_contract: str = Field(..., description="Contract being interacted with")
    calldata_hex: str = Field(..., description="Hex encoded calldata payload")
    value_wei: int = Field(default=0, description="Value sent in wei")
    coverage_requested: int = Field(..., description="Coverage amount requested")
    timeout_duration: int = Field(..., description="Timeout duration in seconds")
    payment_tx_hash: str = Field(..., description="The transaction hash of the 0.01 USDT0 fee payment")
    asset: Optional[str] = Field(default=None, description="ERC20 asset address. If not provided, uses pool asset.")

class InsuranceQuoteResponse(BaseModel):
    premium_amount: int
    quote_id: str
    deadline: int
    signature: str
    approve_target_address: str
    approve_calldata: str
    escrow_contract_address: str
    tx_calldata: str

# ---------------------------------------------------------------------------
# FastAPI Application Setup
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Project Indemnify - Off-Chain Risk Middleware Daemon",
    description="Risk simulation, threat analysis, and cryptographic underwriting engine for M2M economy.",
    version="1.0.0"
)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled exception occurred: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "InternalServerError",
            "message": "The risk daemon encountered an unexpected internal error.",
            "details": str(exc)
        }
    )

@app.post("/v1/risk/simulate", response_model=RiskSimulateResponse)
async def api_simulate_risk(payload: RiskSimulateRequest):
    """
    Dry-runs the transaction payload, validates bytecode, and returns threat matrix with failure probability.
    """
    start_time = time.perf_counter()
    try:
        result = await risk_engine.simulate_transaction_risk(
            client_address=payload.client_address,
            target_contract=payload.target_contract,
            calldata_hex=payload.calldata_hex,
            value_wei=payload.value_wei,
            coverage_requested=payload.coverage_requested
        )
        latency = (time.perf_counter() - start_time) * 1000
        logger.info(f"Simulated risk in {latency:.2f}ms. P_fail: {result['P_fail']}")
        return result
    except Exception as e:
        logger.error(f"Failed simulating transaction risk: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "is_executable": False,
                "detected_vectors": ["INTERNAL_SIMULATION_ERROR"],
                "P_fail": 10000,
                "error": str(e)
            }
        )

@app.post("/v1/insurance/quote")
async def api_generate_quote(payload: InsuranceQuoteRequest):
    """
    Computes P_fail, dynamic premium, and returns an oracle-signed quote matching ParametricEscrow.sol interface.
    """
    # 0. Check 402 Payment
    if payload.payment_tx_hash:
        success, reason = verify_payment(payload.payment_tx_hash)
        if not success:
            return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"error": reason})
    else:
        return build_402_response()

    start_time = time.perf_counter()
    try:
        # 1. Run risk simulation to get P_fail
        sim_result = await risk_engine.simulate_transaction_risk(
            client_address=payload.client_address,
            target_contract=payload.target_contract,
            calldata_hex=payload.calldata_hex,
            value_wei=payload.value_wei,
            coverage_requested=payload.coverage_requested
        )
        p_fail = sim_result["P_fail"]

        # 2. Get asset token decimals dynamically
        asset_addr = payload.asset or settings.escrow_address  # Fallback to escrow address if not provided
        # If simulation failed because RPC is unreachable or timed out, bypass decimals fetch to avoid extra timeout
        if any(v in sim_result.get("detected_vectors", []) for v in ["RPC_CODE_FETCH_FAILED", "RPC_LATENCY_TIMEOUT_EXCEEDED"]):
            decimals = 18
        else:
            decimals = await risk_engine.get_token_decimals(asset_addr)

        # 3. Apply mathematical pricing formula
        # Premium = (CoverageRequested * P_fail) / 10000 + FixedUnderwriterMargin
        fixed_margin_scaled = int(settings.fixed_underwriter_margin * (10 ** decimals))
        premium_amount = int((payload.coverage_requested * p_fail) // 10000) + fixed_margin_scaled

        # 4. Generate unique quote ID (bytes32) and deadline (timestamp + 3600s = 1 hour)
        quote_id_bytes = signer.generate_quote_id()
        quote_id_hex = "0x" + quote_id_bytes.hex()
        deadline = int(time.time()) + 3600

        # 5. Sign the payload using EIP-191 oracle key
        signature_hex = signer.sign_quote(
            client_address=payload.client_address,
            asset=asset_addr,
            coverage_amount=payload.coverage_requested,
            premium_amount=premium_amount,
            timeout_duration=payload.timeout_duration,
            deadline=deadline,
            quote_id=quote_id_bytes
        )

        # 6. Pre-encode Intent-Based Calldata using synchronous Web3
        _escrow_contract = _sync_w3.eth.contract(address=_sync_w3.to_checksum_address(settings.escrow_address), abi=ESCROW_ABI)
        tx_calldata = _escrow_contract.encode_abi("createPolicy", args=[
            _sync_w3.to_checksum_address(asset_addr),
            payload.coverage_requested,
            premium_amount,
            payload.timeout_duration,
            deadline,
            quote_id_bytes,
            bytes.fromhex(signature_hex)
        ])
        
        # 7. Pre-encode ERC20 Approve Intent
        ERC20_ABI = [{"inputs": [{"internalType": "address", "name": "spender", "type": "address"}, {"internalType": "uint256", "name": "amount", "type": "uint256"}], "name": "approve", "outputs": [{"internalType": "bool", "name": "", "type": "bool"}], "stateMutability": "nonpayable", "type": "function"}]
        _erc20_contract = _sync_w3.eth.contract(address=_sync_w3.to_checksum_address(asset_addr), abi=ERC20_ABI)
        approve_calldata = _erc20_contract.encode_abi("approve", args=[
            _sync_w3.to_checksum_address(settings.escrow_address),
            premium_amount
        ])

        latency = (time.perf_counter() - start_time) * 1000
        logger.info(f"Generated insurance quote in {latency:.2f}ms. Premium: {premium_amount}")
        
        return {
            "premium_amount": premium_amount,
            "quote_id": quote_id_hex,
            "deadline": deadline,
            "signature": "0x" + signature_hex,
            "approve_target_address": asset_addr,
            "approve_calldata": approve_calldata,
            "escrow_contract_address": settings.escrow_address,
            "tx_calldata": tx_calldata
        }
    except Exception as e:
        logger.error(f"Failed generating insurance quote: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Quote generation failed: {str(e)}"
        )

async def fetch_client_policies_from_chain(client_address: str):
    """Helper function to fetch all policies for a given client from the Escrow contract."""
    client_address = client_address.lower()
    contract = risk_engine.w3.eth.contract(
        address=risk_engine.w3.to_checksum_address(settings.escrow_address),
        abi=ESCROW_ABI
    )
    
    try:
        count = await contract.functions.policyCount().call()
    except Exception as e:
        return {"error": f"Failed to fetch policyCount: {e}"}
        
    policies_found = []
    # Loop backwards to get recent policies first. Cap at checking last 500 to avoid long hangs.
    min_id = max(1, count - 500)
    for i in range(count, min_id - 1, -1):
        try:
            policy = await contract.functions.policies(i).call()
            if policy[0].lower() == client_address:
                # Pre-encode intent-based termination calldata for the agent
                _sync_escrow = _sync_w3.eth.contract(
                    address=_sync_w3.to_checksum_address(settings.escrow_address),
                    abi=ESCROW_ABI
                )
                terminate_calldata = _sync_escrow.encode_abi("terminatePolicy", args=[i])
                
                policies_found.append({
                    "policy_id": i,
                    "asset": policy[1],
                    "coverage_amount": policy[2],
                    "premium_paid": policy[3],
                    "start_timestamp": policy[4],
                    "timeout_duration": policy[5],
                    "risk_bracket_tier": policy[6],
                    "status": policy[7], # 0=Active, 1=Settled, 2=Refunded, 3=Claimed
                    "terminate_calldata": terminate_calldata,
                    "escrow_contract_address": settings.escrow_address
                })
        except Exception as e:
            logger.error(f"Error fetching policy {i}: {e}")
            continue
            
    return {"policies": policies_found}

@app.get("/v1/agent/policies")
async def api_get_client_policies(client_address: str):
    """
    Retrieves all policies associated with a client address and provides intent-based termination calldata.
    """
    result = await fetch_client_policies_from_chain(client_address)
    if "error" in result:
        return JSONResponse(status_code=500, content=result, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    return JSONResponse(status_code=200, content=result, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

@app.get("/v1/agent/runbook")
async def api_get_runbook():
    """
    Returns the official guide on how to integrate with Indemnify using an AA wallet.
    """
    runbook = """
# Project Indemnify Integration Workflow

This runbook equips agents with the exact workflow needed to successfully interact with the Indemnify Parametric Insurance protocol using an OKX Onchain OS AA (Account Abstraction) wallet.

## The Protocol Constraints (Must Read)
1. **402 Payment Required:** Every quote requires a 0.01 USDT0 fee. Payments are routed via the AA wallet, so the top-level transaction will go to the AA router.
2. **Anti-Replay Protection:** The server will reject reused payment hashes. You must broadcast a fresh payment for every unique quote.
3. **Pass the Hash:** After broadcasting the 402 payment, **wait ~10 seconds** before requesting the quote to allow RPC nodes to index it. Pass the exact transaction hash in the `payment_tx_hash` field of your request payload. Do NOT use HTTP headers.
4. **Relaxed 1-Hour Expiry:** Quotes expire in exactly 1 hour (`block.timestamp > deadline`). You have plenty of time to debug execution without triggering `SignatureExpired`.
5. **Intent-Based Execution:** Do NOT attempt to manually encode the ABI. The quote response provides all targets and calldata. You must execute two raw `contract-call` intents in order:
   a) Target `approve_target_address` with input data `approve_calldata` (Approves the escrow contract).
   b) Target `escrow_contract_address` with input data `tx_calldata` (Creates the policy).
6. **`msg.sender` Match:** Request the quote using your **AA Wallet address** as the `client_address`, otherwise `ECDSA.recover` will revert with `InvalidSignature`.
7. **Intent-Based Termination:** If your strategy requires manually terminating a policy, you MUST use the `get_client_policies` tool first. This tool returns your active policies along with `terminate_calldata`. To terminate, execute a raw `contract-call`:
   `contract-call --contract <escrow_contract_address> --input-data <terminate_calldata>`
"""
    return {"runbook": runbook.strip()}

# ---------------------------------------------------------------------------
# Model Context Protocol (MCP) Server Integration
# ---------------------------------------------------------------------------
try:
    from mcp.server import Server
    from mcp.types import Tool, TextContent

    mcp_server = Server("indemnify-risk-daemon")

    @mcp_server.list_tools()
    async def list_tools() -> List[Tool]:
        return [
            Tool(
                name="simulate_transaction_risk",
                description="Dry-runs EVM payloads, checks bytecode, and returns threat matrix with failure probability.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "client_address": {"type": "string", "description": "Address of client account."},
                        "target_contract": {"type": "string", "description": "Address of target contract."},
                        "calldata_hex": {"type": "string", "description": "Transaction hex data payload."},
                        "value_wei": {"type": "integer", "description": "Transaction value in wei."},
                        "coverage_requested": {"type": "integer", "description": "Requested coverage amount."}
                    },
                    "required": ["client_address", "target_contract", "calldata_hex", "value_wei", "coverage_requested"]
                }
            ),
            Tool(
                name="generate_insurance_quote",
                description="Computes P_fail, dynamic premium, and returns oracle-signed quote matching ParametricEscrow.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "client_address": {"type": "string", "description": "Address of client account."},
                        "target_contract": {"type": "string", "description": "Address of target contract."},
                        "calldata_hex": {"type": "string", "description": "Transaction hex data payload."},
                        "value_wei": {"type": "integer", "description": "Transaction value in wei."},
                        "coverage_requested": {"type": "integer", "description": "Requested coverage amount."},
                        "timeout_duration": {"type": "integer", "description": "Timeout window in seconds."},
                        "payment_tx_hash": {"type": "string", "description": "The transaction hash of the 0.01 USDT0 fee payment."},
                        "asset": {"type": "string", "description": "Stablecoin asset address. Optional."}
                    },
                    "required": ["client_address", "target_contract", "calldata_hex", "value_wei", "coverage_requested", "timeout_duration", "payment_tx_hash"]
                }
            ),
            Tool(
                name="get_agent_runbook",
                description="MUST READ before calling generate_insurance_quote! Returns the official guide on how to integrate with Indemnify using an AA wallet, avoiding 5-minute expirations and intent-based calldata execution.",
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            ),
            Tool(
                name="get_client_policies",
                description="Queries the blockchain to retrieve all insurance policies created by a specific client, including their policy ID, status, and timeout details. ALWAYS use this to find which policy IDs to terminate.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "client_address": {"type": "string", "description": "Address of client account."}
                    },
                    "required": ["client_address"]
                }
            )
        ]

    @mcp_server.call_tool()
    async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
        try:
            if name == "simulate_transaction_risk":
                req = RiskSimulateRequest(**arguments)
                res = await risk_engine.simulate_transaction_risk(
                    client_address=req.client_address,
                    target_contract=req.target_contract,
                    calldata_hex=req.calldata_hex,
                    value_wei=req.value_wei,
                    coverage_requested=req.coverage_requested
                )
                return [TextContent(type="text", text=json.dumps(res, indent=2))]

            elif name == "generate_insurance_quote":
                req = InsuranceQuoteRequest(**arguments)
                
                # Check 402 Payment
                if req.payment_tx_hash:
                    success, reason = verify_payment(req.payment_tx_hash)
                    if not success:
                        return [TextContent(type="text", text=json.dumps({"error": "Payment Verification Failed", "message": reason}))]
                else:
                    return [TextContent(type="text", text=json.dumps({"error": "Payment Required", "message": "No payment_tx_hash provided."}))]

                # Compute quote
                sim_res = await risk_engine.simulate_transaction_risk(
                    client_address=req.client_address,
                    target_contract=req.target_contract,
                    calldata_hex=req.calldata_hex,
                    value_wei=req.value_wei,
                    coverage_requested=req.coverage_requested
                )
                p_fail = sim_res["P_fail"]
                asset_addr = req.asset or settings.escrow_address
                # If simulation failed because RPC is unreachable or timed out, bypass decimals fetch to avoid extra timeout
                if any(v in sim_res.get("detected_vectors", []) for v in ["RPC_CODE_FETCH_FAILED", "RPC_LATENCY_TIMEOUT_EXCEEDED"]):
                    decimals = 18
                else:
                    decimals = await risk_engine.get_token_decimals(asset_addr)

                fixed_margin_scaled = int(settings.fixed_underwriter_margin * (10 ** decimals))
                premium_amount = int((req.coverage_requested * p_fail) // 10000) + fixed_margin_scaled

                quote_id_bytes = signer.generate_quote_id()
                quote_id_hex = "0x" + quote_id_bytes.hex()
                deadline = int(time.time()) + 3600

                signature_hex = signer.sign_quote(
                    client_address=req.client_address,
                    asset=asset_addr,
                    coverage_amount=req.coverage_requested,
                    premium_amount=premium_amount,
                    timeout_duration=req.timeout_duration,
                    deadline=deadline,
                    quote_id=quote_id_bytes
                )

                _escrow_contract = _sync_w3.eth.contract(address=_sync_w3.to_checksum_address(settings.escrow_address), abi=ESCROW_ABI)
                tx_calldata = _escrow_contract.encode_abi("createPolicy", args=[
                    _sync_w3.to_checksum_address(asset_addr),
                    req.coverage_requested,
                    premium_amount,
                    req.timeout_duration,
                    deadline,
                    quote_id_bytes,
                    bytes.fromhex(signature_hex)
                ])
                
                ERC20_ABI = [{"inputs": [{"internalType": "address", "name": "spender", "type": "address"}, {"internalType": "uint256", "name": "amount", "type": "uint256"}], "name": "approve", "outputs": [{"internalType": "bool", "name": "", "type": "bool"}], "stateMutability": "nonpayable", "type": "function"}]
                _erc20_contract = _sync_w3.eth.contract(address=_sync_w3.to_checksum_address(asset_addr), abi=ERC20_ABI)
                approve_calldata = _erc20_contract.encode_abi("approve", args=[
                    _sync_w3.to_checksum_address(settings.escrow_address),
                    premium_amount
                ])

                output = {
                    "premium_amount": premium_amount,
                    "quote_id": quote_id_hex,
                    "deadline": deadline,
                    "signature": "0x" + signature_hex,
                    "approve_target_address": asset_addr,
                    "approve_calldata": approve_calldata,
                    "escrow_contract_address": settings.escrow_address,
                    "tx_calldata": tx_calldata
                }
                return [TextContent(type="text", text=json.dumps(output, indent=2))]

            elif name == "get_agent_runbook":
                runbook = """
# Project Indemnify Integration Workflow

This runbook equips agents with the exact workflow needed to successfully interact with the Indemnify Parametric Insurance protocol using an OKX Onchain OS AA (Account Abstraction) wallet.

## The Protocol Constraints (Must Read)
1. **402 Payment Required:** Every quote requires a 0.01 USDT0 fee. Payments are routed via the AA wallet, so the top-level transaction will go to the AA router.
2. **Anti-Replay Protection:** The server will reject reused payment hashes. You must broadcast a fresh payment for every unique quote.
3. **Pass the Hash:** After broadcasting the 402 payment, **wait ~10 seconds** before requesting the quote to allow RPC nodes to index it. Pass the exact transaction hash in the `payment_tx_hash` field of your request payload. Do NOT use HTTP headers.
4. **Relaxed 1-Hour Expiry:** Quotes expire in exactly 1 hour (`block.timestamp > deadline`). You have plenty of time to debug execution without triggering `SignatureExpired`.
5. **Intent-Based Execution:** Do NOT attempt to manually encode the ABI. The quote response provides all targets and calldata. You must execute two raw `contract-call` intents in order:
   a) Target `approve_target_address` with input data `approve_calldata` (Approves the escrow contract).
   b) Target `escrow_contract_address` with input data `tx_calldata` (Creates the policy).
6. **`msg.sender` Match:** Request the quote using your **AA Wallet address** as the `client_address`, otherwise `ECDSA.recover` will revert with `InvalidSignature`.
7. **Intent-Based Termination:** If your strategy requires manually terminating a policy, you MUST use the `get_client_policies` tool first. This tool returns your active policies along with `terminate_calldata`. To terminate, execute a raw `contract-call`:
   `contract-call --contract <escrow_contract_address> --input-data <terminate_calldata>`
"""
                return [TextContent(type="text", text=runbook)]

            elif name == "get_client_policies":
                result = await fetch_client_policies_from_chain(arguments["client_address"])
                return [TextContent(type="text", text=json.dumps(result, indent=2))]

            else:
                raise ValueError(f"Unknown tool: {name}")
        except Exception as e:
            logger.error(f"Error handling MCP tool call '{name}': {e}")
            return [TextContent(type="text", text=json.dumps({"error": str(e)}, indent=2))]

except ImportError:
    logger.warning("mcp library not installed, MCP server interface will be disabled.")
    mcp_server = None

# ---------------------------------------------------------------------------
# CLI & Server Execution Entrypoints
# ---------------------------------------------------------------------------
async def run_stdio_mcp():
    """Runs the MCP server over standard input/output (stdio)."""
    if mcp_server is None:
        print("Error: 'mcp' SDK is not installed.", file=sys.stderr)
        sys.exit(1)
    
    from mcp.server.stdio import stdio_server
    logger.info("Starting Indemnify MCP Server over Stdio...")
    async with stdio_server() as (read_stream, write_stream):
        await mcp_server.run(
            read_stream,
            write_stream,
            mcp_server.create_initialization_options()
        )

if __name__ == "__main__":
    # If launched with --mcp or --stdio, run stdio-based MCP server
    if "--mcp" in sys.argv or "--stdio" in sys.argv:
        import asyncio
        asyncio.run(run_stdio_mcp())
    else:
        # Otherwise run FastAPI server via uvicorn
        import uvicorn
        logger.info(f"Starting Indemnify FastAPI server on 127.0.0.1:8000...")
        uvicorn.run("daemon.main:app", host="127.0.0.1", port=8000, reload=False)
