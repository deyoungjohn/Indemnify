# SYSTEM CONTEXT INITIALIZATION: PROJECT INDEMNIFY
**Target Platform:** OKX AI Marketplace (X Layer)  
**Role:** Senior Smart Contract & AI Systems Architect  
**Objective:** Internalize the project architecture, operational parameters, and mathematical models defined below. **CRITICAL DIRECTIVE:** Read, parse, and commit this specification to memory. Acknowledge understanding with a structural summary. Do not generate code, build file directories, or execute implementation steps until explicitly commanded by subsequent module prompts.

---

## 1. Architectural Overview & Objective
**Indemnify** is an autonomous parametric risk mitigation and insurance underwriter designed explicitly for the machine-to-machine ($M2M$) economy on the OKX AI Marketplace. In an ecosystem where autonomous software agents execute complex multi-step and cross-chain transactions, operational risks (slippage, bridge deadlocks, transaction reverts, smart contract malicious updates) result in direct capital destruction. 

Indemnify resolves this by acting as a two-tier safety rail:
1. **An Off-Chain Analytical Layer:** A Model Context Protocol (MCP) middleware daemon that intercepts outbound agent transactions, simulates execution, and scores risk.
2. **An On-Chain Settlement Layer:** A suite of Solidity smart contracts deployed on X Layer that manages underwriter liquidity pools and locks premiums in parametric escrow contracts.

---

## 2. On-Chain Settlement Layer (Solidity Architecture)
The smart contract framework bypasses traditional binary (pass/fail) insurance execution, moving instead to a highly granular, event-driven parametric settlement model.

### 2.1 Component Breakdown
*   `UnderwriterPool.sol`: Manages capital deposited by liquidity providers ($LPs$). It computes dynamic APY yields derived from collected policy premiums and manages the capital reserve ratios required to guarantee active underwritten liabilities.
*   `ParametricEscrow.sol`: The core engine responsible for policy minting, premium locking, and programmatic claim disbursement.

### 2.2 Advanced Financial Mechanics
*   **Non-Binary Bracketed Risk Matrices:** Multi-agent workflows frequently suffer from partial execution failures (e.g., a cross-chain agent successfully bridges assets to a target layer but fails to execute the final decentralized exchange swap due to a sudden liquidity crunch). The escrow contract implements a bracketed risk tiering system structurally modeled after **Asian Handicap** sports wagering mechanics. Rather than an all-or-nothing settlement, payouts are prorated dynamically based on the specific tier of execution achieved before failure occurs.
*   **Dynamic Escrow Release (Programmatic Partial Cashout):** For extended, long-running agent workflows (e.g., 48-hour arbitrage loops or multi-step staking strategies), execution can enter unpredicted stalled states without explicitly triggering a hard on-chain revert. Client agents can invoke a programmatic termination clause. The contract evaluates the elapsed block time and current execution status via decentralized oracles, allowing the agent to execute a partial cashout—retrieving a dynamically calculated percentage of their escrowed premium to restore capital velocity.

---

## 3. Off-Chain Middleware & Analytical Layer (Python/MCP)
The analytical infrastructure is built to run as a high-performance, low-latency asynchronous service.

### 3.1 Model Context Protocol (MCP) Specification
The off-chain daemon exposes a standardized MCP server boundary allowing any client agent in the OKX ecosystem to evaluate transaction health via the following RPC routes:
*   `/v1/risk/simulate`: Accepts the raw transaction hex payload, current wallet state, and the target destination contract address. It spins up an isolated, sandboxed local EVM fork, computes control-flow graphs ($CFG$) and abstract syntax trees ($AST$), and returns a comprehensive JSON threat matrix alongside a calculated probability of transaction failure ($P_{\text{fail}}$).
*   `/v1/insurance/quote`: Consumes the $P_{\text{fail}}$ metric and the client's requested coverage amount to produce an immutable, time-locked stablecoin premium quote.

### 3.2 System Administration & Deployment Footprint
To accommodate lean environments, bare-metal nodes, and distributed infrastructure, the off-chain risk engine is engineered as a lightweight Linux daemon. It avoids heavy container orchestration overhead by being packaged natively for standard Linux package management. It must be cleanly compileable and distributable via standard Debian/Ubuntu repositories, installable via:
```bash
apt-get install indemnify-daemon
```

---

## 4. Operational Data Flow & Lifecycle
Every underwritten transaction moves through a strict, deterministic lifecycle:

```text
[Client Agent] ---> (1) Exports Raw Hex Payload ---> [Indemnify MCP Server]
                                                            |
                                                (2) Runs Local EVM Simulation
                                                (3) Computes P_fail & Threat Matrix
                                                            |
[Client Agent] <--- (4) Returns Premium Quote <-------------+
      |
(5) Approves Premium & Signs Escrow TX
      |
      v
[ParametricEscrow.sol] ---> (6) Locks Premium & Reserves Pool Capital
      |
      +---> Case A: Success ----> Releases Premium to Pool; Closes Policy.
      +---> Case B: Stalled ----> Client invokes Partial Cashout; Pro-rata refund.
      +---> Case C: Revert  ----> Oracle submits proof; Triggers Bracketed Payout.
```

---

## 5. System Target Variables & Constants
When modeling the system components in subsequent tasks, assume the following strict system constraints:
*   **Target Gas Environment:** X Layer (EVM-compatible ZK-Rollup). Code must optimize for minimal storage writes to reduce L1 calldata fee overhead.
*   **Simulation Latency Target:** $\le 200\text{ms}$ execution time for `/v1/risk/simulate`.
*   **Development Framework:** Foundry (for Smart Contracts), FastAPI / AsyncIO (for MCP Server).

---
**CONFIRMATION DIRECTIVE:** Acknowledge receipt of this system initialization. Summarize the structural relationship between the non-binary bracketed risk matrix and the off-chain MCP simulation architecture to confirm complete alignment. **Do not write code yet.**
