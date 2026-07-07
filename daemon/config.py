import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # RPC provider URL (X Layer Mainnet, local Anvil fork, or testnet)
    rpc_provider_url: str = Field(
        default="https://rpc.xlayer.tech",
        validation_alias="RPC_PROVIDER_URL"
    )

    # Oracle Private Key for EIP-191 signatures (loaded from env)
    oracle_private_key: str = Field(
        default=os.environ.get("ORACLE_PRIVATE_KEY") or os.environ.get("INDEMNIFY_ORACLE_KEY") or "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
        validation_alias="ORACLE_PRIVATE_KEY"
    )

    # ParametricEscrow contract address
    escrow_address: str = Field(
        default=os.environ.get("ESCROW_CONTRACT_ADDRESS") or "0x5FbDB2315678afecb367f032d93F642f64180aa3",
        validation_alias="ESCROW_CONTRACT_ADDRESS"
    )

    # Chain ID: 196 for X Layer Mainnet, 195 for Testnet, 31337 for local Anvil
    chain_id: int = Field(
        default=196,
        validation_alias="CHAIN_ID"
    )

    # Pricing Constants
    base_premium_rate: float = 0.01     # 1% base premium
    volatility_multiplier: float = 1.5  # Scales P_fail to premium

    # Fixed Underwriter Margin (default value if not provided, scale by decimals at runtime)
    fixed_underwriter_margin: float = Field(
        default=0.01,
        validation_alias="FIXED_UNDERWRITER_MARGIN"
    )

    # x402 Payment Settings
    x402_fee_usdt: float = Field(
        default=0.01,
        validation_alias="X402_FEE_USDT"
    )
    
    # Defaulting to Oracle/Deployer public address as treasury
    x402_treasury_address: str = Field(
        default=os.environ.get("TREASURY_ADDRESS") or "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266", # Default Anvil Deployer
        validation_alias="X402_TREASURY_ADDRESS"
    )

settings = Settings()

# Telemetry warning for public or unauthenticated RPC nodes
import logging
telemetry_logger = logging.getLogger("indemnify.config")
url_lower = settings.rpc_provider_url.lower()
public_indicators = ["public", "free", "okx.com", "xlayer.tech", "ankr.com", "cloudflare", "public-node"]
is_probably_public = any(ind in url_lower for ind in public_indicators) or url_lower.count("/") < 3

if is_probably_public:
    telemetry_logger.warning(
        f"SECURITY/PERFORMANCE WARNING: The configured RPC_PROVIDER_URL ({settings.rpc_provider_url}) "
        "appears to be a public, unauthenticated endpoint. "
        "To consistently satisfy the strict 180ms local EVM simulation latency check "
        "required by the OKX AI Marketplace, a premium, high-throughput dedicated RPC node "
        "infrastructure (e.g. Alchemy, Infura, or private bare-metal node) is strongly recommended."
    )

