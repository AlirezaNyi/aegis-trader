"""Build a JevPort from application settings."""

from __future__ import annotations

from aegis.config.settings import Settings
from aegis.interfaces.jev import JevPort, UnavailableJevPort
from aegis.jev.client import TypesafeSystemOneClient


def build_jev_port(settings: Settings) -> JevPort:
    """Return System One client when TYPESAFE_API_KEY is set; else unavailable stub."""
    if not settings.jev_configured:
        return UnavailableJevPort()
    return TypesafeSystemOneClient(
        api_key=settings.typesafe_api_key,
        model=settings.jev_model,
        timeout_seconds=settings.jev_timeout_seconds,
        max_retries=settings.jev_max_retries,
    )
