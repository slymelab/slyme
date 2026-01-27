try:
    from ._store_c import Store, Ref, StoreConfig
except (ImportError, ModuleNotFoundError):
    from ._store_py import Store, Ref, StoreConfig

from ._store_py import Missing, MISSING

__all__ = [
    "Store",
    "Ref",
    "StoreConfig",
    "Missing",
    "MISSING",
]
