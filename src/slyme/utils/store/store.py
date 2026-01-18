try:
    from ._store_c import Store, Field
except (ImportError, ModuleNotFoundError):
    from ._store_py import Store, Field
