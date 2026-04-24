"""WAFI architecture package.

Production-oriented reference implementation of the WAFI-AGENT blueprint
with strict separation between cognition and execution.
"""

from .invariants import ArchitectureInvariant, InvariantRegistry, default_invariants

__all__ = [
    "ArchitectureInvariant",
    "InvariantRegistry",
    "default_invariants",
]
