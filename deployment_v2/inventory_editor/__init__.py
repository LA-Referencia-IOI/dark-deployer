"""Interfaces for editing deployment topology documents.

The document model deliberately has no dependency on a terminal UI toolkit so a
future web, curses, or prompt-based editor can reuse the same safe save and
validation behavior.
"""

from .document import InventoryDocument, InventoryDocumentError
from .templates import create_from_template, template_names

__all__ = [
    "InventoryDocument",
    "InventoryDocumentError",
    "create_from_template",
    "template_names",
]
