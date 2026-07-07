import asyncio
import logging
import httpx
from typing import Dict, Any, List, Tuple
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider
from web3.exceptions import ContractLogicError, Web3Exception
from daemon.config import settings

logger = logging.getLogger("indemnify.risk_engine")

def _is_timeout(e: Exception) -> bool:
    """Helper to detect latency timeout exceptions."""
    err_str = str(e).lower()
    return (
        isinstance(e, (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException))
        or "timeout" in err_str
        or "timed out" in err_str
        or "latency" in err_str
    )


ERC20_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    }
]

class RiskEngine:
    """
    Indemnify Risk Engine.
    Simulates transactions against live EVM state and applies threat heuristics.
    """

    def __init__(self, rpc_url: str = None):
        self.rpc_url = rpc_url or settings.rpc_provider_url
        import os
        timeout = float(os.environ.get("RPC_TIMEOUT") or "10.0")
        self.w3 = AsyncWeb3(AsyncHTTPProvider(
            self.rpc_url,
            request_kwargs={"timeout": timeout},
            exception_retry_configuration=None
        ))

    async def get_token_decimals(self, token_address: str) -> int:
        """Fetches decimals for an ERC20 token dynamically, defaulting to 18 if call fails."""
        try:
            checksum_addr = AsyncWeb3.to_checksum_address(token_address)
            contract = self.w3.eth.contract(address=checksum_addr, abi=ERC20_ABI)
            return await contract.functions.decimals().call()
        except Exception as e:
            logger.warning(f"Failed to fetch decimals for token {token_address}, defaulting to 18. Error: {e}")
            return 18

    async def simulate_transaction_risk(
        self,
        client_address: str,
        target_contract: str,
        calldata_hex: str,
        value_wei: int,
        coverage_requested: int
    ) -> Dict[str, Any]:
        """
        Simulates the transaction and analyzes risk vectors.
        Returns a threat matrix and P_fail (in basis points, 0-10000).
        """
        detected_vectors = []
        is_executable = True
        p_fail = 0

        # Standardize inputs
        try:
            client_checksum = AsyncWeb3.to_checksum_address(client_address)
            target_checksum = AsyncWeb3.to_checksum_address(target_contract)
        except ValueError as e:
            return {
                "is_executable": False,
                "detected_vectors": ["INVALID_ADDRESS_FORMAT"],
                "P_fail": 10000,
                "error": str(e)
            }

        # 1. Bytecode Validation
        try:
            bytecode = await self.w3.eth.get_code(target_checksum)
            if not bytecode or bytecode.hex() in ["0x", ""]:
                # EOA or non-existent contract
                detected_vectors.append("EMPTY_BYTECODE")
                is_executable = False
                p_fail = 10000
                return {
                    "is_executable": is_executable,
                    "detected_vectors": detected_vectors,
                    "P_fail": p_fail
                }
        except Exception as e:
            logger.error(f"Error calling eth_getCode: {e}")
            if _is_timeout(e):
                return {
                    "is_executable": False,
                    "detected_vectors": ["RPC_LATENCY_TIMEOUT_EXCEEDED"],
                    "P_fail": 10000
                }
            # Network error or timeout, immediately return failure to prevent sequential blocking calls
            detected_vectors.append("RPC_CODE_FETCH_FAILED")
            return {
                "is_executable": False,
                "detected_vectors": detected_vectors,
                "P_fail": 10000,
                "error": f"RPC node unreachable: {e}"
            }

        # 2. Local EVM Simulation
        # Construct transaction dict
        tx_data = {
            "from": client_checksum,
            "to": target_checksum,
            "data": calldata_hex,
            "value": value_wei
        }

        # Check if calldata starts with standard AMM swap signatures
        # e.g., swapExactTokensForTokens (0x38ed5735), swapExactETHForTokens (0x7ff36ab5), etc.
        calldata_clean = calldata_hex.lower()
        if calldata_clean.startswith("0x"):
            calldata_clean = calldata_clean[2:]
            
        is_amm_swap = False
        for sig in ["38ed5735", "7ff36ab5", "18cba2db", "5c11d795", "8c65f084", "a4e277f2"]:
            if calldata_clean.startswith(sig):
                is_amm_swap = True
                break

        try:
            # We can execute simulation via eth_call
            # A successful simulation returns a result, a reverted simulation throws ContractLogicError
            await self.w3.eth.call(tx_data)
        except ContractLogicError as cle:
            # The transaction explicitly reverted
            detected_vectors.append("TRANSACTION_REVERTED")
            is_executable = False
            p_fail = 10000
            return {
                "is_executable": is_executable,
                "detected_vectors": detected_vectors,
                "P_fail": p_fail,
                "revert_reason": str(cle)
            }
        except Exception as e:
            if _is_timeout(e):
                return {
                    "is_executable": False,
                    "detected_vectors": ["RPC_LATENCY_TIMEOUT_EXCEEDED"],
                    "P_fail": 10000
                }
            # Other errors (e.g. invalid sender, gas limit, insufficient funds)
            detected_vectors.append("SIMULATION_EXECUTION_FAILED")
            # If the transaction cannot execute due to node errors or insufficient funds, it's high risk
            is_executable = False
            p_fail = 10000
            return {
                "is_executable": is_executable,
                "detected_vectors": detected_vectors,
                "P_fail": p_fail,
                "error": str(e)
            }

        # 3. Code/Bytecode Heuristic Scanning
        # Search the target bytecode for specific risk patterns (delegatecall, selfdestruct)
        # DELEGATECALL opcode is 0xf4
        # SELFDESTRUCT opcode is 0xff
        bytecode_hex = bytecode.hex()
        
        # delegatecall check
        if "f4" in bytecode_hex:
            detected_vectors.append("DELEGATECALL_VECTOR")
            p_fail += 2500  # +25% risk

        # selfdestruct check
        if "ff" in bytecode_hex:
            detected_vectors.append("SELFDESTRUCT_VECTOR")
            p_fail += 3000  # +30% risk

        # AMM swap slip check
        if is_amm_swap:
            detected_vectors.append("AMM_SLIPPAGE_RISK")
            p_fail += 1500  # +15% risk

        # Base execution risk (if no other vectors detected)
        if not detected_vectors:
            p_fail = 200  # 2% base risk

        # Cap p_fail at 10000 (100% risk)
        p_fail = min(p_fail, 10000)

        return {
            "is_executable": is_executable,
            "detected_vectors": detected_vectors,
            "P_fail": p_fail
        }
