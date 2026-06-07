"""hebbot.skills — primitives and (later) higher-level skill helpers.

Importing this module does not register anything; call
`hebbot.skills.primitives.register_all(substrate)` explicitly.
"""

from . import primitives

__all__ = ["primitives"]
