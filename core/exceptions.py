"""
exceptions.py
=============
Citrus's custom exception hierarchy.

Every domain-specific error raised anywhere in the app should be one
of these (or a subclass), so callers can catch precisely what they
expect instead of a bare Exception. This module has zero internal
dependencies, so any layer of the app can safely import from it
without introducing a circular import.
"""


class CitrusError(Exception):
    """Base class for all Citrus-specific errors."""


class ConfigurationError(CitrusError):
    """Raised when configuration is missing, invalid, or unusable."""


class SystemInfoError(CitrusError):
    """Raised when a system-information collector cannot gather data."""


class NetworkError(CitrusError):
    """Raised when a network-related operation fails."""


class GitError(CitrusError):
    """Raised when a git operation fails."""


class ServiceError(CitrusError):
    """Raised when a managed service fails to start, stop, or respond."""


class UIError(CitrusError):
    """Raised when a UI rendering operation fails unexpectedly."""
