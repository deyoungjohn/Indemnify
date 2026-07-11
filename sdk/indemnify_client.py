"""
sdk/indemnify_client.py
=======================
Indemnify Protocol — Python SDK Client
Version: 1.0.0
Network:  X Layer Mainnet (Chain ID 196)

Overview
--------
This module provides IndemnifyClient, a lightweight asynchronous Python client
for the Indemnify risk engine REST API. It is designed to be embedded directly
into any OKX AI Marketplace agent or third-party DeFi automation system.

Integration Pattern
-------------------
1. Instantiate IndemnifyClient with your daemon's base URL.
2. Before executing any high-risk transaction, call `await client.get_quote(...)`.
3. Inspect the returned InsuranceQuote dataclass:
   - Check `quote.P_fail` against your risk threshold.
   - Check `quote.premium_amount` against your capital budget.
4. If the transaction is approved, call `approve_and_create_policy()` to
   submit the premium to ParametricEscrow.sol on-chain.
5. In the event of failure, handle `RiskSimulationFailed` gracefully.

Dependencies
------------
    pip install httpx eth-account web3

License: MIT
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional, Any, Dict

import httpx

# ---------------------------------------------------------------------------
# Module-Level Logger
# ---------------------------------------------------------------------------
logger = logging.getLogger("indemnify.sdk")


# ===========================================================================
# Custom Exception Hierarchy
# ===========================================================================

class IndemnifyError(Exception):
    """Base exception for all Indemnify SDK errors."""


class RiskSimulationFailed(IndemnifyError):
    """
    Raised when the Indemnify risk daemon is unreachable, returns a server
    error, or when the risk simulation itself encounters a fatal internal
    failure (e.g., EVM fork unavailable).

    Attributes
    ----------
    status_code : Optional[int]
        HTTP status code returned by the daemon, if available.
    detail : str
        Human-readable error description suitable for agent logs.
    """

    def __init__(self, detail: str, status_code: Optional[int] = None):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"RiskSimulationFailed(status={status_code}): {detail}")


class QuoteExpiredError(IndemnifyError):
    """
    Raised when an agent attempts to use an InsuranceQuote whose `deadline`
    has already passed. The agent MUST request a fresh quote before proceeding.
    """


class RiskThresholdExceeded(IndemnifyError):
    """
    Raised when the computed P_fail score exceeds the client's configured
    `max_p_fail_bps` threshold. The agent MUST abort the transaction.

    Attributes
    ----------
    p_fail : int
        The P_fail score (basis points) returned by the risk engine.
    threshold : int
        The configured maximum tolerable P_fail threshold.
    detected_vectors : List[str]
        Risk vectors identified during the simulation.
    """

    def __init__(self, p_fail: int, threshold: int, detected_vectors: List[str]):
        self.p_fail = p_fail
        self.threshold = threshold
        self.detected_vectors = detected_vectors
        super().__init__(
            f"P_fail {p_fail} bps exceeds threshold {threshold} bps. "
            f"Detected vectors: {detected_vectors}. Transaction ABORTED."
        )


class PremiumBudgetExceeded(IndemnifyError):
    """
    Raised when the quoted premium_amount exceeds the client's configured
    `max_premium_budget` cap. The agent MUST abort the transaction.

    Attributes
    ----------
    premium_amount : int
        The quoted premium in asset base units.
    budget : int
        The configured maximum acceptable premium.
    """

    def __init__(self, premium_amount: int, budget: int):
        self.premium_amount = premium_amount
        self.budget = budget
        super().__init__(
            f"Premium {premium_amount} exceeds budget cap {budget}. "
            f"Transaction ABORTED."
        )


# ===========================================================================
# Data Transfer Objects
# ===========================================================================

@dataclass
class InsuranceQuote:
    """
    A fully validated, oracle-signed insurance quote returned by the
    Indemnify risk engine. This object contains everything required to
    call `ParametricEscrow.sol::createPolicy()` on-chain.

    Attributes
    ----------
    premium_amount : int
        Premium owed by the agent in ERC-20 asset base units (e.g., wei for
        18-decimal tokens). Pass this as `premiumAmount` to `createPolicy()`.
    quote_id : str
        Unique bytes32 hex string (0x-prefixed). Pass as `quoteId`.
        This is a nonce — do NOT reuse it. The escrow contract will revert.
    deadline : int
        Unix timestamp after which this quote is invalid on-chain.
        `createPolicy()` must be called before this timestamp.
    signature : str
        EIP-191 oracle signature (65-byte, 0x-prefixed hex). Pass verbatim
        as `signature` to `createPolicy()`. Any modification will cause
        on-chain signature verification to fail.
    P_fail : int
        Failure probability in basis points (0–10000). The agent's
        go/no-go decision MUST be based on this value.
    detected_vectors : List[str]
        Informational list of risk vectors detected during EVM simulation.
    generated_at : float
        Local Unix timestamp of when this quote was received by the client.
    """

    premium_amount: int
    quote_id: str
    deadline: int
    signature: str
    P_fail: int
    detected_vectors: List[str] = field(default_factory=list)
    generated_at: float = field(default_factory=time.time)

    @property
    def is_expired(self) -> bool:
        """Returns True if the quote's on-chain deadline has passed."""
        return time.time() > self.deadline

    @property
    def seconds_until_expiry(self) -> float:
        """Returns remaining validity window in seconds. Negative if expired."""
        return self.deadline - time.time()

    def assert_not_expired(self) -> None:
        """
        Raises QuoteExpiredError if the quote is no longer valid.
        Call this immediately before submitting the on-chain transaction.
        """
        if self.is_expired:
            raise QuoteExpiredError(
                f"Quote {self.quote_id} expired at Unix timestamp {self.deadline}. "
                f"Current time: {int(time.time())}. Request a fresh quote."
            )

    def to_create_policy_args(self) -> Dict[str, Any]:
        """
        Serializes the quote into a dictionary matching the parameter names
        of `ParametricEscrow.sol::createPolicy()`. This is the canonical
        integration point for on-chain submission via web3.py or ethers.js.

        Returns
        -------
        dict
            Keys: quoteId, premiumAmount, deadline, signature.
            The caller must supply `asset`, `coverageAmount`, and
            `timeoutDuration` separately (they are policy-level params
            not embedded in the quote object).
        """
        self.assert_not_expired()
        return {
            "quoteId": self.quote_id,
            "premiumAmount": self.premium_amount,
            "deadline": self.deadline,
            "signature": self.signature,
        }


@dataclass
class RiskSimulation:
    """
    Result of a lightweight EVM dry-run via `/v1/risk/simulate`.
    Does not include a signed quote. Use this for pre-flight checks.

    Attributes
    ----------
    is_executable : bool
        True if the transaction completed without reverting in simulation.
    P_fail : int
        Failure probability in basis points.
    detected_vectors : List[str]
        Risk vectors identified during the simulation run.
    """

    is_executable: bool
    P_fail: int
    detected_vectors: List[str] = field(default_factory=list)


# ===========================================================================
# IndemnifyClient — Primary SDK Interface
# ===========================================================================

class IndemnifyClient:
    """
    Asynchronous HTTP client for the Indemnify risk engine REST API.

    This client wraps the `/v1/insurance/quote` and `/v1/risk/simulate`
    endpoints and provides structured error handling, automatic retries
    with exponential backoff, and quote validation logic.

    Parameters
    ----------
    base_url : str
        Base URL of the Indemnify daemon. Default: ``http://127.0.0.1:8000``.
        In production, this should point to your Nginx-terminated HTTPS
        endpoint (e.g., ``https://risk.indemnify.example.com``).
    max_p_fail_bps : int
        Maximum tolerable failure probability in basis points (0–10000).
        Quotes with P_fail above this value will raise `RiskThresholdExceeded`.
        Default: 7500 (75%). Adjust based on agent risk appetite.
    max_premium_budget : Optional[int]
        Maximum tolerable premium in ERC-20 base units. Quotes exceeding
        this value will raise `PremiumBudgetExceeded`. Default: None (no cap).
    timeout_seconds : float
        HTTP request timeout in seconds. Default: 10.0.
    max_retries : int
        Number of retry attempts on transient HTTP errors (5xx, connection
        errors). Default: 3.
    retry_backoff_base : float
        Base delay (seconds) for exponential backoff between retries.
        Actual delay = retry_backoff_base * (2 ** attempt). Default: 1.0.

    Example
    -------
    >>> import asyncio
    >>> from sdk.indemnify_client import IndemnifyClient, RiskThresholdExceeded
    >>>
    >>> async def main():
    ...     client = IndemnifyClient(base_url="https://risk.indemnify.example.com")
    ...     async with client:
    ...         try:
    ...             quote = await client.get_quote(
    ...                 client_address="0xYourAgentAddress",
    ...                 target_contract="0xDEXRouterAddress",
    ...                 calldata_hex="0x38ed1739...",
    ...                 coverage_requested=1_000_000_000_000_000_000_000,
    ...                 timeout_duration=3600,
    ...             )
    ...             args = quote.to_create_policy_args()
    ...             # Submit args to ParametricEscrow.sol::createPolicy()
    ...         except RiskThresholdExceeded as e:
    ...             print(f"ABORT: {e}")
    ...
    >>> asyncio.run(main())
    """

    # USDT0 asset address on X Layer Mainnet
    DEFAULT_ASSET: str = "0x779ded0c9e1022225f8e0630b35a9b54be713736"

    # P_fail basis-point tier boundaries
    P_FAIL_LOW_THRESHOLD: int = 2500
    P_FAIL_MEDIUM_THRESHOLD: int = 5000
    P_FAIL_HIGH_THRESHOLD: int = 7500

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        max_p_fail_bps: int = 7500,
        max_premium_budget: Optional[int] = None,
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
        retry_backoff_base: float = 1.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._max_p_fail_bps = max_p_fail_bps
        self._max_premium_budget = max_premium_budget
        self._timeout = httpx.Timeout(timeout_seconds)
        self._max_retries = max_retries
        self._retry_backoff_base = retry_backoff_base

        # Internal httpx async client — initialized lazily or via context manager
        self._http_client: Optional[httpx.AsyncClient] = None

        logger.info(
            "IndemnifyClient initialized. Base URL: %s | Max P_fail: %d bps | "
            "Premium cap: %s",
            self._base_url,
            self._max_p_fail_bps,
            max_premium_budget if max_premium_budget is not None else "unlimited",
        )

    # -----------------------------------------------------------------------
    # Async Context Manager Support
    # -----------------------------------------------------------------------

    async def __aenter__(self) -> "IndemnifyClient":
        """Opens the underlying HTTP connection pool."""
        self._http_client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Indemnify-SDK/1.0.0 (OKX-Agent)",
            },
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Closes the underlying HTTP connection pool."""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    # -----------------------------------------------------------------------
    # Internal Helpers
    # -----------------------------------------------------------------------

    def _get_client(self) -> httpx.AsyncClient:
        """Returns the active HTTP client, creating one if needed."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "Indemnify-SDK/1.0.0 (OKX-Agent)",
                },
            )
        return self._http_client

    async def _post_with_retry(
        self,
        path: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Executes an HTTP POST with exponential-backoff retry logic.

        Retries on:
          - httpx.ConnectError / httpx.TimeoutException (network-level failures)
          - HTTP 429 (rate limit) — respects Retry-After header if present
          - HTTP 5xx (server-side transient errors)

        Raises
        ------
        RiskSimulationFailed
            On permanent failure after exhausting all retry attempts, or on
            HTTP 4xx errors that are not retriable (e.g., 422 invalid params).
        """
        client = self._get_client()
        last_exception: Optional[Exception] = None

        for attempt in range(self._max_retries + 1):
            try:
                response = await client.post(path, json=payload)

                # Handle rate limiting with Retry-After backoff
                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", self._retry_backoff_base * (2 ** attempt)))
                    logger.warning(
                        "Rate limited by Indemnify daemon (attempt %d/%d). "
                        "Backing off %.1fs.",
                        attempt + 1, self._max_retries + 1, retry_after,
                    )
                    if attempt < self._max_retries:
                        await asyncio.sleep(retry_after)
                        continue
                    raise RiskSimulationFailed(
                        f"Rate limit exceeded after {self._max_retries} retries.",
                        status_code=429,
                    )

                # Non-retriable client errors
                if 400 <= response.status_code < 500:
                    try:
                        error_body = response.json()
                        detail = error_body.get("detail", response.text)
                    except Exception:
                        detail = response.text
                    raise RiskSimulationFailed(
                        f"Client error from Indemnify daemon: {detail}",
                        status_code=response.status_code,
                    )

                # Server-side transient errors — retry
                if response.status_code >= 500:
                    backoff = self._retry_backoff_base * (2 ** attempt)
                    logger.warning(
                        "Indemnify daemon returned HTTP %d (attempt %d/%d). "
                        "Retrying in %.1fs.",
                        response.status_code, attempt + 1,
                        self._max_retries + 1, backoff,
                    )
                    if attempt < self._max_retries:
                        await asyncio.sleep(backoff)
                        continue
                    raise RiskSimulationFailed(
                        f"Daemon returned HTTP {response.status_code} after "
                        f"{self._max_retries} retries: {response.text}",
                        status_code=response.status_code,
                    )

                return response.json()

            except httpx.ConnectError as e:
                backoff = self._retry_backoff_base * (2 ** attempt)
                logger.warning(
                    "Connection error to Indemnify daemon (attempt %d/%d): %s. "
                    "Retrying in %.1fs.",
                    attempt + 1, self._max_retries + 1, e, backoff,
                )
                last_exception = e
                if attempt < self._max_retries:
                    await asyncio.sleep(backoff)

            except httpx.TimeoutException as e:
                backoff = self._retry_backoff_base * (2 ** attempt)
                logger.warning(
                    "Request to Indemnify daemon timed out (attempt %d/%d). "
                    "Retrying in %.1fs.",
                    attempt + 1, self._max_retries + 1, backoff,
                )
                last_exception = e
                if attempt < self._max_retries:
                    await asyncio.sleep(backoff)

        raise RiskSimulationFailed(
            f"Indemnify daemon unreachable after {self._max_retries} retries: "
            f"{last_exception}",
            status_code=None,
        ) from last_exception

    # -----------------------------------------------------------------------
    # Public API — Risk Assessment
    # -----------------------------------------------------------------------

    @staticmethod
    def classify_risk(p_fail: int) -> str:
        """
        Translates a raw P_fail basis-point score into a human-readable
        risk classification string.

        Parameters
        ----------
        p_fail : int
            Failure probability in basis points (0–10000).

        Returns
        -------
        str
            One of: "LOW", "MEDIUM", "HIGH", "CRITICAL".
        """
        if p_fail < 2500:
            return "LOW"
        elif p_fail < 5000:
            return "MEDIUM"
        elif p_fail < 7500:
            return "HIGH"
        return "CRITICAL"

    async def simulate_risk(
        self,
        client_address: str,
        target_contract: str,
        calldata_hex: str,
        coverage_requested: int,
        value_wei: int = 0,
    ) -> RiskSimulation:
        """
        Performs a lightweight EVM dry-run via `/v1/risk/simulate`.

        Use this for pre-flight risk intelligence without generating or
        committing to a signed premium quote.

        Parameters
        ----------
        client_address : str
            Checksummed EVM address of the originating agent wallet.
        target_contract : str
            Checksummed EVM address of the target smart contract.
        calldata_hex : str
            Hex-encoded ABI calldata (with or without ``0x`` prefix).
        coverage_requested : int
            Hypothetical coverage amount in ERC-20 base units.
        value_wei : int
            Native ETH value (in wei) accompanying the call. Default: 0.

        Returns
        -------
        RiskSimulation
            Contains `is_executable`, `P_fail`, and `detected_vectors`.

        Raises
        ------
        RiskSimulationFailed
            On daemon unreachability or internal simulation error.
        """
        payload = {
            "client_address": client_address,
            "target_contract": target_contract,
            "calldata_hex": calldata_hex,
            "value_wei": value_wei,
            "coverage_requested": coverage_requested,
        }

        logger.info(
            "Simulating risk for target_contract=%s (coverage=%d)",
            target_contract, coverage_requested,
        )

        data = await self._post_with_retry("/v1/risk/simulate", payload)

        sim = RiskSimulation(
            is_executable=data.get("is_executable", False),
            P_fail=data.get("P_fail", 10000),
            detected_vectors=data.get("detected_vectors", []),
        )

        logger.info(
            "Risk simulation complete. P_fail=%d bps (%s). Vectors=%s",
            sim.P_fail,
            self.classify_risk(sim.P_fail),
            sim.detected_vectors,
        )
        return sim

    async def get_quote(
        self,
        client_address: str,
        target_contract: str,
        calldata_hex: str,
        coverage_requested: int,
        timeout_duration: int,
        value_wei: int = 0,
        asset: Optional[str] = None,
        enforce_thresholds: bool = True,
    ) -> InsuranceQuote:
        """
        Requests a full oracle-signed insurance quote from the Indemnify
        risk engine via `/v1/insurance/quote`.

        This is the **primary integration point** for autonomous agents.
        It performs the full risk simulation, computes the dynamic premium,
        and returns a signed quote ready for on-chain submission.

        Parameters
        ----------
        client_address : str
            Checksummed EVM address of the agent wallet that will hold
            the policy. Embedded in the oracle signature — cannot be changed.
        target_contract : str
            Checksummed EVM address of the smart contract being insured.
        calldata_hex : str
            Hex-encoded ABI calldata of the transaction to insure.
        coverage_requested : int
            Maximum payout coverage in ERC-20 asset base units.
        timeout_duration : int
            Policy timeout window in seconds (60–604800). For cross-chain
            bridges, use the worst-case finality time + 20% buffer.
        value_wei : int
            Native ETH value (in wei) sent with the transaction. Default: 0.
        asset : Optional[str]
            ERC-20 asset address for premium denomination. Defaults to
            USDT0 (0x779ded0c9e1022225f8e0630b35a9b54be713736).
        enforce_thresholds : bool
            If True, automatically raises `RiskThresholdExceeded` when
            P_fail >= max_p_fail_bps, and `PremiumBudgetExceeded` when
            premium_amount > max_premium_budget. Default: True.
            Set to False only if you want to handle threshold logic manually.

        Returns
        -------
        InsuranceQuote
            Fully validated, oracle-signed quote. Call
            ``quote.to_create_policy_args()`` to get the on-chain arguments.

        Raises
        ------
        RiskSimulationFailed
            On daemon unreachability or a server-side simulation failure.
        RiskThresholdExceeded
            If P_fail exceeds `max_p_fail_bps` (when enforce_thresholds=True).
        PremiumBudgetExceeded
            If premium exceeds `max_premium_budget` (when enforce_thresholds=True).
        """
        payload: Dict[str, Any] = {
            "client_address": client_address,
            "target_contract": target_contract,
            "calldata_hex": calldata_hex,
            "value_wei": value_wei,
            "coverage_requested": coverage_requested,
            "timeout_duration": timeout_duration,
        }
        if asset is not None:
            payload["asset"] = asset

        logger.info(
            "Requesting insurance quote. target=%s, coverage=%d, timeout=%ds",
            target_contract, coverage_requested, timeout_duration,
        )

        data = await self._post_with_retry("/v1/insurance/quote", payload)

        quote = InsuranceQuote(
            premium_amount=data["premium_amount"],
            quote_id=data["quote_id"],
            deadline=data["deadline"],
            signature=data["signature"],
            P_fail=data.get("P_fail", -1),
            detected_vectors=data.get("detected_vectors", []),
        )

        risk_label = self.classify_risk(quote.P_fail) if quote.P_fail >= 0 else "UNKNOWN"
        logger.info(
            "Quote received. quote_id=%s | P_fail=%d bps (%s) | "
            "premium=%d | expires_in=%.0fs",
            quote.quote_id,
            quote.P_fail,
            risk_label,
            quote.premium_amount,
            quote.seconds_until_expiry,
        )

        # Threshold enforcement — guards the agent from proceeding unsafely
        if enforce_thresholds:
            if quote.P_fail >= 0 and quote.P_fail >= self._max_p_fail_bps:
                logger.error(
                    "RISK THRESHOLD EXCEEDED: P_fail=%d bps >= threshold=%d bps. "
                    "Aborting transaction. Vectors: %s",
                    quote.P_fail, self._max_p_fail_bps, quote.detected_vectors,
                )
                raise RiskThresholdExceeded(
                    p_fail=quote.P_fail,
                    threshold=self._max_p_fail_bps,
                    detected_vectors=quote.detected_vectors,
                )

            if (
                self._max_premium_budget is not None
                and quote.premium_amount > self._max_premium_budget
            ):
                logger.error(
                    "PREMIUM BUDGET EXCEEDED: premium=%d > budget=%d. "
                    "Aborting transaction.",
                    quote.premium_amount, self._max_premium_budget,
                )
                raise PremiumBudgetExceeded(
                    premium_amount=quote.premium_amount,
                    budget=self._max_premium_budget,
                )

        return quote

    async def close(self) -> None:
        """
        Explicitly closes the underlying HTTP client connection pool.
        Not required when using the async context manager (``async with``).
        """
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None
            logger.debug("IndemnifyClient HTTP connection pool closed.")
