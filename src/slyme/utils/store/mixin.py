from slyme.utils.inspect import resolve_instance_classname
from slyme.utils.mixin import GetattrAdapterMixin
from .store import Key
from .descriptor import KeyField


class KeyFieldMixin(GetattrAdapterMixin):
    """
    Mixin for managing KeyFields using a Local KeyFields Dictionary.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._store_keys: dict[str, Key] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls._local_key_fields: dict[str, KeyField] = {}
        cls.sync_local_key_fields()

    def __getattr__(self, name: str):
        """
        Safeguard against accessing `_store_keys` on an uninitialized instance.

        If `KeyField.__get__` tries to access `instance._store_keys` and fails,
        Python invokes this method. We trap it here to raise a clear `RuntimeError`
        about missing initialization, preventing the error from being misinterpreted
        as the KeyField itself being missing.
        """
        if name == "_store_keys":
            raise RuntimeError(
                f"Attribute `_store_keys` is missing on instance of {resolve_instance_classname(self)}. "
                "This implies you are attempting to assign a value to a KeyField before the instance is initialized. "
                "Please ensure `super().__init__` (or `KeyFieldMixin.__init__`) is called before setting "
                "any KeyField values."
            )
        return super().__getattr__(name)

    @classmethod
    def sync_local_key_fields(cls) -> None:
        """"""
        cls._local_key_fields.clear()
        for name, value in cls.__dict__.items():
            if isinstance(value, KeyField):
                cls._local_key_fields[name] = value

    @classmethod
    def set_key_field(
        cls, name: str, field: KeyField, force_sync: bool = False
    ) -> None:
        """Dynamically set a KeyField."""
        if not isinstance(field, KeyField):
            raise TypeError(f"Value for {name} must be a KeyField")

        setattr(cls, name, field)
        field.__set_name__(cls, name)
        if force_sync:
            cls.sync_local_key_fields()
        else:
            cls._local_key_fields[name] = field

    @classmethod
    def del_key_field(cls, name: str, force_sync: bool = False) -> None:
        """Dynamically delete a KeyField."""
        # NOTE: Should ensure the field exists and is a KeyField instance first.
        field = cls.__dict__[name]
        if not isinstance(field, KeyField):
            raise TypeError(
                f"Class attribute '{name}' should be a KeyField, got {type(field)}."
            )

        delattr(cls, name)
        if force_sync:
            cls.sync_local_key_fields()
        else:
            cls._local_key_fields.pop(name, None)

    @classmethod
    def get_key_fields(cls) -> dict[str, KeyField]:
        """
        Resolve all available KeyFields by traversing the MRO dynamically.
        """
        all_fields: dict[str, KeyField] = {}
        for base in reversed(cls.__mro__):
            local_fields = getattr(base, "_local_key_fields", None)
            if local_fields:
                all_fields.update(local_fields)

        return all_fields

    def get_keys(self, strict: bool = True) -> dict[str, Key]:
        """
        Get runtime Key values for all defined fields.
        """
        store_keys = self._store_keys.copy()
        if not strict:
            return store_keys

        # Strict Mode: Consistency Check
        defined_fields = self.get_key_fields()
        missing_keys = defined_fields.keys() - store_keys.keys()

        if missing_keys:
            raise AttributeError(
                f"Instance of {resolve_instance_classname(self)} is missing values for "
                f"required KeyFields: {', '.join(repr(k) for k in missing_keys)}. "
                "Ensure all fields are initialized before retrieving keys in strict mode."
            )
        return store_keys
