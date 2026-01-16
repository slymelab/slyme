try:
    from ._store_c import Store, Key
except (ImportError, ModuleNotFoundError):
    from ._store_py import Store, Key
