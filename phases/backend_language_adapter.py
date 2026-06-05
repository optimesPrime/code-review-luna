from __future__ import annotations
from typing import Any, Protocol, runtime_checkable

from phases.backend_models import BackendChangedSymbol, BackendGraphEdge, BackendGraphNode


@runtime_checkable
class LanguageAdapter(Protocol):
    """Contract every backend language adapter must satisfy."""

    name: str
    extensions: tuple[str, ...]

    def get_language(self) -> Any:
        """Return the tree-sitter Language object for this language (lazy-loaded)."""
        ...

    def find_enclosing_symbol(
        self,
        root_node: Any,
        source: bytes,
        line: int,
        rel_path: str,
        is_new_file: bool,
    ) -> BackendChangedSymbol | None:
        ...

    def extract_file_nodes(
        self,
        root_node: Any,
        source: bytes,
        rel_path: str,
    ) -> list[BackendGraphNode]:
        ...

    def extract_file_edges(
        self,
        root_node: Any,
        source: bytes,
        rel_path: str,
        method_index: dict[str, str],
    ) -> list[BackendGraphEdge]:
        ...
