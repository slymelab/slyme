from typing import Protocol


class HasExtraRepr(Protocol):
    """
    Protocol for objects that provide an extended string representation.

    This allows objects to expose internal state details for logging,
    debugging, or rendering without overriding the standard __repr__.
    """

    def extra_repr(self) -> str:
        """Return the extra representation string."""
        ...
