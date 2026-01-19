from enum import Flag, auto

__all__ = [
    "DEP",
    "Dep",
]

# Metadata Key
DEP = "node.dep"


class Dep(Flag):
    """
    Defines the dependency role of a Ref within a Node using standard Read/Write semantics.
    """

    NONE = 0

    READ = auto()
    WRITE = auto()

    # Syntactic Sugar
    READ_WRITE = READ | WRITE
