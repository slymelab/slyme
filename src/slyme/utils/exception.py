import sys
from typing import Union
from contextlib import contextmanager
from collections.abc import Generator


@contextmanager
def enrich_exception(
    info: str,
    exc_types: Union[type[Exception], tuple[type[Exception], ...]] = Exception,
) -> Generator[None, None, None]:
    """
    Context manager to enrich exceptions with context info.
    """
    try:
        yield
    except exc_types as e:
        # Strategy 1: Modern Python (Preferred)
        if sys.version_info >= (3, 11):
            e.add_note(info)
            raise

        # Strategy 2: Legacy / Compatibility
        # Construct the new message
        if len(e.args) > 0 and isinstance(e.args[0], str):
            e.args = (f"{e.args[0]} ({info})", *e.args[1:])
        else:
            e.args = (*e.args, f"({info})")
        raise
