class GetattrAdapterMixin:

    def __getattr__(self, name: str):
        try:
            super_getattr = super().__getattr__
        except AttributeError:
            # End of chain (e.g., next is `object`).
            # Raise a fresh AttributeError (using `from None`) to hide the internal
            # implementation detail that we were looking for a super method.
            raise AttributeError(
                f"'{type(self).__name__}' object has no attribute '{name}'"
            ) from None

        # Call super().__getattr__ if exists.
        return super_getattr(name)
