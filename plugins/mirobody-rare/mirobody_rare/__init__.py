"""mirobody-rare: rare-disease coding on top of mirobody.

Layer 1 of docs/rare-mvp-plan.md: free-text clinical assertions → HPO terms
(D1a + D1b), disease names / phenotype sets → ORPHA (D8), ORPHA → HGNC gene.
Nothing here imports the agent layer; the library modules it reuses from
mirobody (`lexical`, `zh_fold`) are the numpy-only ones.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
