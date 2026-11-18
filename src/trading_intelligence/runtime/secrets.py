"""Secret-resolution contracts for server-side adapters.

A :class:`SecretReference` is safe to persist and display only after redaction; a secret
*value* is not.  This module deliberately offers no browser, dashboard, log, or JSON
serialization path for resolved values.
"""
from __future__ import annotations

import os
from typing import Mapping, Protocol
from urllib.parse import urlparse

from ..integrations import SecretReference


class SecretResolver(Protocol):
    """Resolve an approved secret reference inside a trusted server process only."""

    def resolve(self, reference: SecretReference) -> str:
        raise NotImplementedError


class EnvironmentSecretResolver:
    """Resolve ``sm://env/NAME`` from a server process environment.

    A deployment Secret Manager (for example a platform's encrypted server runtime
    variables) injects the actual value into ``NAME``.  The client/dashboard sees only
    the ``sm://env/...`` reference and this resolver never returns a value in status
    responses.  ``vault://`` and other providers must use a dedicated resolver rather
    than silently falling back to environment lookup.
    """

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._environ = os.environ if environ is None else environ

    def resolve(self, reference: SecretReference) -> str:
        parsed = urlparse(reference.uri)
        if parsed.scheme != "sm" or parsed.netloc != "env":
            raise ValueError("EnvironmentSecretResolver accepts only sm://env/SECRET_NAME references.")
        name = parsed.path.removeprefix("/")
        if not name or "/" in name or not name.replace("_", "").isalnum() or name.upper() != name:
            raise ValueError("Secret environment name must be uppercase letters, digits, and underscores.")
        value = self._environ.get(name)
        if value is None or not value.strip():
            raise LookupError(f"Server secret reference is not bound: {reference.uri}")
        return value


class StaticSecretResolver:
    """Fixture/test resolver; production deployments should use a real secret manager."""

    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)

    def resolve(self, reference: SecretReference) -> str:
        try:
            value = self._values[reference.uri]
        except KeyError as exc:
            raise LookupError(f"No fixture secret bound for reference: {reference.uri}") from exc
        if not value:
            raise LookupError("Fixture secret is empty.")
        return value
