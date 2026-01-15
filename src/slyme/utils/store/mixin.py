from typing import Union
from .store import Key


class StoreKeyMixin:
    """
    Mixin for managing KeyFields using a Local KeyFields Dictionary.
    """

    @property
    def store_keys(self) -> dict[str, Key]:
        """
        Retrieve a dictionary of all registered Store Keys on this instance.
        """
        return {
            name: value
            for name in self._attr_registry[Key]
            if (value := getattr(self, name)) is not None
        }

    def register_store_key(self, name: str, value: Union[Key, None]) -> None:
        self._register_attribute(name, value, Key)
