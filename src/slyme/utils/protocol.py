from typing import Protocol, runtime_checkable


@runtime_checkable
class HasExtraRepr(Protocol):
    """
    Protocol for objects that provide an extended string representation.

    This allows objects to expose internal state details for logging,
    debugging, or rendering without overriding the standard __repr__.
    """

    def extra_repr(self) -> str:
        """Return the extra representation string."""
        ...


@runtime_checkable
class HasTypeRepr(Protocol):
    """
    Protocol for objects that provide a custom type name for rendering.

    This allows objects (like functional wrappers) to masquerade as their
    underlying logic (e.g. function name) instead of their implementation class.
    """

    def type_repr(self) -> str:
        """Return the custom type name string."""
        ...
