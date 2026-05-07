from __future__ import annotations

from .core import (
    DEFAULT_OIDC_SCOPE,
    AuthlibOAuthRegistry,
    AuthlibRemoteApp,
    FunAuth,
    FunAuthIdentity,
    FunAuthResult,
    FunLoginResult,
    OidcProvider,
    google_provider,
    microsoft_entra_provider,
)

__all__ = [
    "DEFAULT_OIDC_SCOPE",
    "AuthlibOAuthRegistry",
    "AuthlibRemoteApp",
    "FunAuth",
    "FunAuthIdentity",
    "FunAuthResult",
    "FunLoginResult",
    "OidcProvider",
    "google_provider",
    "microsoft_entra_provider",
]
