from __future__ import annotations


class SkillOrchestratorError(RuntimeError):
    """Base exception for production adapter and bootstrap failures."""


class ConfigurationError(SkillOrchestratorError):
    """Raised when required runtime configuration is missing or invalid."""


class ProviderError(SkillOrchestratorError):
    """Base exception for provider integration failures."""


class TransientProviderError(ConnectionError, ProviderError):
    """Retryable network or upstream availability error."""


class ProviderAuthError(ProviderError):
    """Authentication or authorization failure."""


class ProviderResponseError(ProviderError):
    """Unexpected or invalid upstream response payload."""
