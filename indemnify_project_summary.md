# Project Indemnify: Technical Architecture & System Summary

**Target Network:** OKX X Layer (EVM ZK-Rollup)
**Core Asset:** USDT0 (`0x779ded0c9e1022225f8e0630b35a9b54be713736` | 6 Decimals)
**Stack:** Foundry (Solidity), Python (FastAPI, Web3.py, Asynchronous Polling)

This document serves as a high-level developer handover for the Indemnify parametric risk mitigation protocol. The system provides M2M (machine-to-machine) smart contract execution insurance, utilizing a non-binary bracketed risk matrix to provide dynamic payouts based on transaction failure states.

---

## 1. On-Chain Settlement Layer (Smart Contracts)

### `ParametricEscrow.sol`
The core state machine that orchestrates policy lifecycles.
* **`createPolicy()`**: Clients pass in the desired coverage, premium, timeout, and an **Oracle cryptographic signature**. The contract uses `ECDSA.recover` (via EIP-191) to verify the signature against the registered Oracle address. It then transfers the premium from the client and triggers the pool to reserve the coverage capital.
* **`settlePolicy()`**: Restricted strictly to the Oracle. Accepts a `policyId` and a `tier`. It maps the tier to a basis point multiplier (e.g., Tier 1 = 35%, Tier 2 = 75%, Tier 3 = 100%, Tier 0 = 0%). It forwards the payout instruction to the pool and directs 100% of the premium to the pool as LP yield.
* **`partialCashout()`**: Allows clients to terminate stalled, long-running agent workflows and retrieve a prorated percentage of their premium without a hard revert.

### `UnderwriterPool.sol`
The liquidity vault managing underwriter capital.
* **Capital Protection**: Operates strictly on a 1:1 fully collateralized basis. Exposes a `freeLiquidity()` view function (`totalAssets - reservedCapital`).
* **`deposit()` / `withdraw()`**: Standard ERC4626-like functions. LPs cannot withdraw capital that is currently locked in an active policy.
* **`payout()` & `reserveCapital()`**: Restricted to the Escrow contract. Handles the physical `safeTransfer` of USDT to the client upon settlement and adjusts the `reservedCapital` accounting.

---

## 2. Off-Chain Analytical Layer (Python MCP Daemon)

A lightweight, asynchronous backend daemon that intercepts transactions, prices risk, and programmatically resolves on-chain policies.

### `daemon/main.py` & `daemon/risk_engine.py` (FastAPI)
* **`/v1/insurance/quote`**: Consumes raw transaction calldata. Computes a Probability of Failure (`P_fail`) based on historical heuristics, simulated reverts, or contract complexities.
* **Mathematical Pricing**: Premium is calculated via `(CoverageRequested * P_fail) / 10000 + FixedUnderwriterMargin`. 
* **`daemon/signer.py`**: Hashes the quote parameters using `keccak256` and signs it using the Oracle's private key.

### `daemon/oracle_listener.py` (The Autonomous Resolver)
* Runs entirely in the background, polling the X Layer RPC.
* **Indexing**: Scans for `PolicyCreated` events emitted by the Escrow. To bypass RPC `400 Bad Request` limits, it uses a 500-block pagination window and maintains a 2-block lag to ensure data is fully indexed.
* **State Tracking**: Once an active policy is detected, it parses the block history for the client's subsequent transaction (the "target transaction").
* **Resolution Engine**: Evaluates the receipt of the target transaction. If it succeeds (`status == 1`), it pushes `settlePolicy(Tier 0)` to unlock capital. If it reverts (`status == 0`), it pushes `settlePolicy(Tier 3)` to trigger the 100% payout. 

---

## 3. Operational Tooling & Scripts

We have built a robust suite of Python scripts to interact with, manage, and test the protocol on Mainnet.

### Protocol Administration
* `deploy_new_pool.py`: Deploys a new `UnderwriterPool` and executes `registerPool()` on the Escrow to link an asset (e.g., USDT0).
* `update_oracle.py`: Securely transitions the Escrow's trusted Oracle signer address.
* `fund_pool.py` / `recover_funds.py`: Allows Deployers/LPs to safely deposit or withdraw liquidity using the `freeLiquidity()` math.

### End-to-End Testing Suite
* `test_tier_0.py`: E2E test. Buys a policy, executes a successful target transaction, and relies on the `oracle_listener` to settle it as Tier 0 (Premium $\rightarrow$ Pool).
* `test_tier_3.py`: E2E test. Buys a policy, executes an intentionally reverted target transaction, and relies on the listener to settle it as Tier 3 (100% Coverage $\rightarrow$ Client).
* `test_tier_1.py` / `test_tier_2.py`: Manual tier tests. Buys a policy but deliberately omits a target transaction. Uses the Oracle key locally to execute a manual `settlePolicy()` with Tier 1 (35%) or Tier 2 (75%) to verify the Escrow's partial payout mathematics.

### Diagnostic Tools
* `check_policies.py`: Reads the raw `policies(id)` struct from the Escrow to verify exact on-chain liability mapping.
* `check_transfers.py`: Uses `eth_getLogs` to scrape specific ERC20 `Transfer` events directly from the Pool to the Client to verify payouts hit the wallet.
* `check_pool_balance.py`: A simple read-only script to verify exact USDT decimals on the UnderwriterPool.
