"""Mundsen Agent core package.

The package is intentionally side-effect free on import. Runtime directories,
configuration files and network clients are initialized only by explicit
callers.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.3.0"
