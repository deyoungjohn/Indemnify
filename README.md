# Project Indemnify
Autonomous Parametric Risk Underwriting & Settlement Engine for the M2M Economy on OKX X Layer.

---

## Technical Architecture Overview
Project Indemnify provides transaction-level insurance for autonomous software agents executing workflows on X Layer:
1. **On-Chain Settlement Layer (Solidity)**:
   - `ParametricEscrow.sol`: Manages active policies, holds premium payments, and programmatically routes claim payouts or premium releases using dynamic, bracketed risk payout structures (Asian Handicap style).
   - `UnderwriterPool.sol`: Manages capital reserves deposited by LPs, dynamic yield distributions, and capital lockups/releases.
2. **Off-Chain Simulation Daemon (FastAPI/MCP)**:
   - Evaluates client transaction risk profiles in real-time by running sandboxed EVM dry-runs and heuristics, generating cryptographically signed quotes.
3. **Event-Driven Oracle Listener (Python/AsyncIO)**:
   - Daemon that monitors on-chain `PolicyCreated` events, tracks subsequent client transactions, evaluates execution status (success/revert), and executes programmatic policy settlements.

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

### Deploy to Local Node (Anvil)
1. Start Anvil:
   ```bash
   anvil
   ```
2. Run deployment script:
   ```bash
   forge script script/DeployIndemnify.s.sol --rpc-url http://127.0.0.1:8545 --broadcast
   ```

---

## 2. Event-Driven Oracle Listener Daemon

The Oracle Listener (`oracle_listener.py`) is an asynchronous background process that monitors on-chain events, validates client execution outcomes, and triggers settlements.

### Required Environment Variables

To run the Oracle Listener, configure the following environment variables:

| Environment Variable | Description | Example / Default |
|----------------------|-------------|-------------------|
| `RPC_URL` | X Layer RPC Provider URL (HTTP or WebSockets) | `http://127.0.0.1:8545` (Anvil) |
| `ESCROW_CONTRACT_ADDRESS` | Deployed address of `ParametricEscrow` | `0x5FbDB2315678afecb367f032d93F642f64180aa3` |
| `ORACLE_PRIVATE_KEY` | Private key of the Oracle account (`msg.sender == oracleAddress`) | `0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80` |
| `CHAIN_ID` | X Layer chain ID (196: Mainnet, 195: Testnet, 31337: Anvil) | `31337` |

### Setting Up Environment

Create a `.env` file in the root of the project:
```env
RPC_URL=http://127.0.0.1:8545
ESCROW_CONTRACT_ADDRESS=0x5FbDB2315678afecb367f032d93F642f64180aa3
ORACLE_PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
CHAIN_ID=31337
```

### Running the Daemon

#### 1. Direct Execution
Ensure your virtual environment is active and dependencies are installed:
```bash
# Activate virtual environment
source daemon/.venv/bin/activate  # On Linux/macOS
# OR
.\daemon\.venv\Scripts\Activate.ps1  # On Windows PowerShell

# Run the listener
python -m daemon.oracle_listener
```

#### 2. Running as a Persistent Background Process (Linux Systemd)
For production environments, package the daemon as a systemd service:

Create `/etc/systemd/system/indemnify-oracle.service`:
```ini
[Unit]
Description=Indemnify Event-Driven Oracle Listener Daemon
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/path/to/Indemnify
EnvironmentFile=/path/to/Indemnify/.env
ExecStart=/path/to/Indemnify/daemon/.venv/bin/python -m daemon.oracle_listener
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable indemnify-oracle.service
sudo systemctl start indemnify-oracle.service
sudo systemctl status indemnify-oracle.service
```

#### 3. Running as a Background Process (Nohup / Disown)
For quick testing on Linux/macOS servers without systemd permissions:
```bash
nohup python -m daemon.oracle_listener > oracle_listener.log 2>&1 &
echo $! > oracle_listener.pid
```
To stop the process:
```bash
kill $(cat oracle_listener.pid)
```

