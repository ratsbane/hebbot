"""hebbot.skills — primitives and composed skills.

Importing this module does not register anything; call
`hebbot.skills.primitives.register_all(substrate)` and
`hebbot.skills.composed.register_all(substrate)` explicitly.
"""

from . import composed, primitives

__all__ = ["primitives", "composed"]
