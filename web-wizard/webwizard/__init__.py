"""Local, read-only operator workbench for dARK deployment v3.

Everything in this package is a consumer of ``deployment_v3`` in read-only
mode. It never imports ``deployment_v3.runner`` and never performs an
operational action.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
