"""This module defines special constants in ``slyme``."""

from enum import Enum, auto
from typing import Literal


# Flag constants.
class FlagConstant(Enum):
    MISSING = auto()
    STOP = auto()


Missing = Literal[FlagConstant.MISSING]
MISSING: Missing = FlagConstant.MISSING
Stop = Literal[FlagConstant.STOP]
STOP: Stop = FlagConstant.STOP
