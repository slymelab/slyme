try:
    from ._store_c import Store, Ref
except (ImportError, ModuleNotFoundError):
    from ._store_py import Store, Ref
