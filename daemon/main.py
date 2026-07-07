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
from daemon.x402_middleware import X402Middleware

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
    asset: Optional[str] = Field(default=None, description="ERC20 asset address. If not provided, uses pool asset.")

class InsuranceQuoteResponse(BaseModel):
    premium_amount: int
    quote_id: str
    deadline: int
    signature: str

# ---------------------------------------------------------------------------
# FastAPI Application Setup
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Project Indemnify - Off-Chain Risk Middleware Daemon",
    description="Risk simulation, threat analysis, and cryptographic underwriting engine for M2M economy.",
    version="1.0.0"
)

# Register x402 Payment Middleware
app.add_middleware(
    X402Middleware,
    protected_paths={"/v1/risk/simulate", "/v1/insurance/quote"}
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

@app.post("/v1/insurance/quote", response_model=InsuranceQuoteResponse)
async def api_generate_quote(payload: InsuranceQuoteRequest):
    """
    Computes P_fail, dynamic premium, and returns an oracle-signed quote matching ParametricEscrow.sol interface.
    """
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

        # 4. Generate unique quote ID (bytes32) and deadline (timestamp + 300s)
        quote_id_bytes = signer.generate_quote_id()
        quote_id_hex = "0x" + quote_id_bytes.hex()
        deadline = int(time.time()) + 300

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

        latency = (time.perf_counter() - start_time) * 1000
        logger.info(f"Generated insurance quote in {latency:.2f}ms. Premium: {premium_amount}")
        
        return {
            "premium_amount": premium_amount,
            "quote_id": quote_id_hex,
            "deadline": deadline,
            "signature": "0x" + signature_hex
        }
    except Exception as e:
        logger.error(f"Failed generating insurance quote: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Quote generation failed: {str(e)}"
        )

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
                        "asset": {"type": "string", "description": "Stablecoin asset address. Optional."}
                    },
                    "required": ["client_address", "target_contract", "calldata_hex", "value_wei", "coverage_requested", "timeout_duration"]
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
                deadline = int(time.time()) + 300

                signature_hex = signer.sign_quote(
                    client_address=req.client_address,
                    asset=asset_addr,
                    coverage_amount=req.coverage_requested,
                    premium_amount=premium_amount,
                    timeout_duration=req.timeout_duration,
                    deadline=deadline,
                    quote_id=quote_id_bytes
                )

                output = {
                    "premium_amount": premium_amount,
                    "quote_id": quote_id_hex,
                    "deadline": deadline,
                    "signature": "0x" + signature_hex
                }
                return [TextContent(type="text", text=json.dumps(output, indent=2))]

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
