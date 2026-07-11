"""
Indemnify Protocol — Python SDK
================================
Import the client directly::

    from sdk.indemnify_client import IndemnifyClient, InsuranceQuote
    from sdk.indemnify_client import RiskSimulationFailed, RiskThresholdExceeded, PremiumBudgetExceeded
"""
from .indemnify_client import (
    IndemnifyClient,
    InsuranceQuote,
    RiskSimulation,
    RiskSimulationFailed,
    RiskThresholdExceeded,
    PremiumBudgetExceeded,
    QuoteExpiredError,
    IndemnifyError,
)

__version__ = "1.0.0"
__all__ = [
    "IndemnifyClient",
    "InsuranceQuote",
    "RiskSimulation",
    "RiskSimulationFailed",
    "RiskThresholdExceeded",
    "PremiumBudgetExceeded",
    "QuoteExpiredError",
    "IndemnifyError",
]

