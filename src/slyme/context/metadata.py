from enum import Flag, auto

__all__ = [
    "DEP",
    "Dep",
    "HELP",
    "TYPE",
]

# Metadata Key
DEP = "node.dep"
HELP = "node.help"
TYPE = "node.type"


class Dep(Flag):
    """
    Defines the dependency role of a Ref within a Node using standard Read/Write semantics.
    """

    NONE = 0

    REQUIRE = auto()
    PROVIDE = auto()

    # Syntactic Sugar
    REQUIRE_PROVIDE = REQUIRE | PROVIDE
