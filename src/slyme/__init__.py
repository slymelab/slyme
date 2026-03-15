try:
    from .__version__ import __version__
except Exception:
    print("__version__ load failed.")

__all__ = ["__version__"]
