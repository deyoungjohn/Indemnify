from enum import Enum
from typing import Optional, Tuple
from pydantic import BaseModel, Field
from eth_abi import encode

class IntentAction(str, Enum):
    SWAP = "swap"
    SUPPLY = "supply"
    TRANSFER = "transfer"
    APPROVE = "approve"

class TransactionIntent(BaseModel):
    action: IntentAction
    protocol: Optional[str] = Field(default=None, description="e.g. quickswap, uniswap, aave, curve, potatoeswap")
    token_in: str
    token_out: Optional[str] = None
    amount_in: int
    amount_out_min: int = 0
    recipient: Optional[str] = None
    
    # Additional optional fields for specific protocols
    curve_i: Optional[int] = None
    curve_j: Optional[int] = None

# Verified Addresses on X Layer (Mainnet 196)
ROUTERS = {
    "quickswap": "0x4B9f4d2435Ef65559567e5DbFC1BbB37abC43B57", # QuickSwap V3 Algebra Router
    "uniswap": "0xE592427A0AEce92De3Edee1F18E0157C05861564",   # Uniswap V3 Router (if deployed)
    "aave": "0xE3F3Caefdd7180F884c01E57f65Df979Af84f116",      # Aave V3 Pool (from aave-address-book)
    "sushiswap": "0xAC4c6e212A361c968F1725b4d055b47E63F80b75",     # Sushiswap V2 Router
    "potatoeswap": "0xB45D0149249488333E3F3f9F359807F4b810C1FC" # PotatoSwap V2 Router
}

def parse_intent(intent: TransactionIntent) -> Tuple[str, str]:
    """
    Parses a human-readable TransactionIntent into a (target_contract, calldata_hex) tuple.
    """
    action = intent.action.lower()
    protocol = intent.protocol.lower() if intent.protocol else None
    recipient = intent.recipient or "0x0000000000000000000000000000000000000000"

    if action == "swap":
        if not protocol:
            raise ValueError("Protocol must be specified for swap intents.")
            
        if protocol in ["quickswap", "uniswap"]:
            # exactInputSingle((address,address,address,uint256,uint256,uint256,uint160))
            router = ROUTERS[protocol]
            params = (
                intent.token_in,
                intent.token_out,
                recipient,
                9999999999, # deadline
                intent.amount_in,
                intent.amount_out_min,
                0 # limitSqrtPrice
            )
            encoded = encode(['(address,address,address,uint256,uint256,uint256,uint160)'], [params])
            calldata = "0x414bf389" + encoded.hex()
            return router, calldata
            
        elif protocol == "potatoeswap":
            # swapExactTokensForTokens(uint256,uint256,address[],address,uint256)
            router = ROUTERS["potatoeswap"]
            path = [intent.token_in, intent.token_out]
            encoded = encode(
                ['uint256', 'uint256', 'address[]', 'address', 'uint256'],
                [intent.amount_in, intent.amount_out_min, path, recipient, 9999999999]
            )
            calldata = "0x38ed1739" + encoded.hex()
            return router, calldata
            
        elif protocol == "curve":
            # exchange(int128,int128,uint256,uint256)
            router = ROUTERS["curve"]
            if intent.curve_i is None or intent.curve_j is None:
                raise ValueError("Curve swaps require curve_i and curve_j indices.")
            encoded = encode(
                ['int128', 'int128', 'uint256', 'uint256'],
                [intent.curve_i, intent.curve_j, intent.amount_in, intent.amount_out_min]
            )
            calldata = "0x3df02124" + encoded.hex()
            return router, calldata
            
        else:
            raise ValueError(f"Unsupported protocol for swap: {protocol}. Supported: quickswap, uniswap, curve, potatoeswap")

    elif action == "supply":
        if protocol == "aave":
            # supply(address asset, uint256 amount, address onBehalfOf, uint16 referralCode)
            router = ROUTERS["aave"]
            encoded = encode(
                ['address', 'uint256', 'address', 'uint16'],
                [intent.token_in, intent.amount_in, recipient, 0]
            )
            calldata = "0x617ba037" + encoded.hex()
            return router, calldata
        else:
            raise ValueError(f"Unsupported protocol for supply: {protocol}. Supported: aave")

    elif action == "transfer":
        encoded = encode(['address', 'uint256'], [recipient, intent.amount_in])
        calldata = "0xa9059cbb" + encoded.hex() # transfer(address,uint256)
        return intent.token_in, calldata
        
    elif action == "approve":
        encoded = encode(['address', 'uint256'], [recipient, intent.amount_in])
        calldata = "0x095ea7b3" + encoded.hex() # approve(address,uint256)
        return intent.token_in, calldata
        
    else:
        raise ValueError(f"Unsupported action: {action}")
