try:
    from ._store_c import Store, StoreKey
except (ImportError, ModuleNotFoundError):
    from ._store_py import Store, Key
