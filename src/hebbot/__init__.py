"""Hebbot: a substrate for agentic foundation models to bootstrap
continuously-learning embodied systems.

See docs/foundations.md for the architecture and design commitments.
"""

__version__ = "0.1.0"

from .substrate import Substrate

__all__ = ["Substrate", "__version__"]
