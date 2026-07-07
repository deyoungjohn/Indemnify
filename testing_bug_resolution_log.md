# Indemnify: End-to-End Testing Bug & Resolution Log

This document concisely tracks the major technical issues encountered during the integration testing of the **ParametricEscrow**, **UnderwriterPool**, and the **Oracle MCP Daemon** on X Layer Mainnet, and how they were resolved.

### 1. The Asset Discrepancy (Mock USDT $\rightarrow$ USDT $\rightarrow$ USDT0)
* **Issue:** The contracts were initially configured for a 18-decimal Mock USDT. When migrating to X Layer Mainnet, there was confusion between bridged USDT and native USDT0. Furthermore, the test scripts were multiplying values by `10**18`.
* **Resolution:** Standardized the entire stack on X Layer's official `USDT0` (`0x779ded0c9e1022225f8e0630b35a9b54be713736`) which uses `6` decimals. Updated the Python daemon and test scripts to scale values dynamically using `10**decimals`.

### 2. The `0xdcec4050` (InvalidSignature) Revert
* **Issue:** The client script repeatedly reverted during `createPolicy()` with the hex code `0xdcec4050`. 
* **Resolution:** Decoded the custom Foundry error to `InvalidSignature()`. The `ParametricEscrow` contract was still configured with the Deployer's address as the Oracle. Wrote `update_oracle.py` to execute `updateOracleAddress()` on-chain, syncing it with the Python Daemon's private key.

### 3. Missing `PolicyCreated` Event in `web3.py` ABI
* **Issue:** The `test_client_flow.py` script crashed when trying to parse the transaction receipt to get the `policyId`, throwing a `MismatchedABI` warning and discarding the logs.
* **Resolution:** The ABI exported by Foundry and loaded into the script lacked the event definition. Added the exact `PolicyCreated` event interface to the `ESCROW_FALLBACK_ABI` array inside the Python scripts.

### 4. Oracle Listener RPC Polling Failures (`400 Bad Request`)
* **Issue:** The `oracle_listener.py` background daemon crashed with `400 Bad Request` from the public X Layer RPC when fetching `eth_getLogs`. 
* **Resolution:** Public RPCs reject massive block range queries and requests for un-indexed blocks. Fixed by implementing a strict 500-block pagination limit and adding a **2-block indexing lag** (`current_block - 2`) to ensure the RPC had time to sync state.

### 5. Pool Free Liquidity vs. Total Assets
* **Issue:** `recover_funds.py` reverted on-chain when the Deployer attempted to withdraw test liquidity. 
* **Resolution:** The script was trying to withdraw the entire pool balance. However, `UnderwriterPool` logically locks active coverage amounts as `reservedCapital`. Updated the recovery script to query `freeLiquidity()` and only withdraw un-reserved funds.

### 6. The Tier 3 Payout Math Bug (Coverage vs. Premium)
* **Issue:** The Oracle successfully settled a Tier 3 (100% payout) policy, but the Client wallet only received `0.01 USDT` instead of the expected `0.1 USDT`.
* **Resolution:** Audited the on-chain structs via `check_policies.py`. Discovered the test scripts were accidentally passing the `premium_amount` variable into the `coverageAmount` argument slot of `createPolicy()`. Fixed the scripts to correctly pass `coverage_raw` as the second argument.

### 7. FastAPI JSON Key Extraction Error
* **Issue:** `test_tier_1.py` crashed with `KeyError: 'premium'` when parsing the API response.
* **Resolution:** The FastAPI endpoint explicitly returns the JSON key as `"premium_amount"`. Updated the test scripts to correctly parse `quote_data["premium_amount"]`.

---
*All systems are now fully synchronized, tested across Tiers 0, 1, 2, and 3, and safely committed to version control.*
