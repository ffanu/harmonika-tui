"""Web builder artifact generation templates.

Pure in-memory generation only: every handler returns file artifacts
(relative path + string content). No filesystem, network, or shell access
happens inside this package; the root runtime owns atomic writes into an
isolated workspace.
"""

from . import safe  # noqa: F401
