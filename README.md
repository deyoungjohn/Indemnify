# Project Indemnify
Autonomous Parametric Risk Underwriting & Settlement Engine for the M2M Economy on OKX X Layer.

Indemnify acts as a real-time financial safety net for autonomous software agents executing complex workflows (DeFi, bridging, staking) on X Layer. It programmatically insures transactions against unpredictable stalls or execution failures.

---

## Technical Architecture Overview
1. **On-Chain Settlement Layer (Solidity / X Layer)**:
   - `ParametricEscrow.sol`: Manages active policies, holds premium payments, and programmatically routes claim payouts or premium releases using non-binary parametric risk tiering.
   - `UnderwriterPool.sol`: Manages capital reserves deposited by Liquidity Providers (LPs) and dynamic yield distributions.

2. **Off-Chain Simulation & Risk Daemon (FastAPI/MCP)**:
   - Built to integrate directly with the **OKX AI Marketplace** via the **A2A Network** and **X402 Payment Middleware**.
   - Accepts transaction intents, runs isolated EVM simulations, and returns cryptographically signed, dynamically priced underwriting quotes in real-time.
   - Includes custom `RequestValidationError` handling to perfectly align with OKX's automated validation bots.

3. **OKX OnchainOS Integration**:
   - Packaged with an `indemnify.yaml` skill file for consumer agents.
   - Fully compatible with Account Abstraction (AA) Smart Wallets. X402 payment verification relies purely on raw `Transfer` event logs, completely decoupling verification from router addresses (`tx.to`).

---

## 1. On-Chain Settlement Layer Setup (Foundry)

### Build Contracts
```bash
forge build
```

### Run Tests
```bash
forge test
```

### Verify Contracts on X Layer (OKLink)
Due to standard API limitations on X Layer, use the OKLink source code plugin:
```bash
forge verify-contract <CONTRACT_ADDRESS> <CONTRACT_PATH>:<CONTRACT_NAME> \
  --verifier oklink \
  --verifier-url "https://www.oklink.com/api/v5/explorer/contract/verify-source-code-plugin/XLAYER"
```

---

## 2. Off-Chain Daemon & API (FastAPI)

The `main.py` daemon serves the `/v1/insurance/quote` endpoint.

### Required Environment Variables

Create a `.env` file in the root of the project:
```env
RPC_URL=https://xlayerrpc.okx.com
ESCROW_CONTRACT_ADDRESS=0x...
ORACLE_PRIVATE_KEY=0x...
CHAIN_ID=196
```

### Running the API
Ensure your virtual environment is active and dependencies are installed:
```bash
# Activate virtual environment
source daemon/.venv/bin/activate  # On Linux/macOS
# OR
.\daemon\.venv\Scripts\Activate.ps1  # On Windows PowerShell

# Run the FastAPI server
uvicorn daemon.main:app --host 0.0.0.0 --port 8000
```

### Exposing to the OKX Marketplace
To test or host the A2MCP endpoint publicly for the OKX Marketplace, we use a Cloudflare Tunnel:
```bash
cloudflared tunnel --url http://localhost:8000
```
Then update your `indemnify.yaml` and Marketplace Listing with the generated `.trycloudflare.com` URL.

---

## Development Notes & A2A Debugging
If you encounter issues during local OKX A2A Node.js initialization (e.g., NPM `ETIMEDOUT` in WSL 2, or missing `node:sqlite` modules), please refer to our internal playbooks documented in `.agents/AGENTS.md`. We heavily recommend **Node.js >= v22.14.0**.
