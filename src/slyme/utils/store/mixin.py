from . import Key
from .descriptor import KeyField


class KeyFieldMixin:
    """
    Mixin for managing KeyFields using a Local KeyFields Dictionary.
    """

    _local_key_fields: dict[str, KeyField]

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls._local_key_fields = {}
        cls.sync_local_key_fields()

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

    def get_keys(self) -> dict[str, Key]:
        """
        Get runtime Key values for all defined fields.
        """
        fields = self.get_key_fields()
        keys = {}
        for name in fields:
            keys[name] = getattr(self, name)
        return keys
