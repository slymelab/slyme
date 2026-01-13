from slyme.utils.typing import Union
from slyme.utils.attribute import AttributeRegistryMixin, AttributeTypeRegistry
from .store import StoreKey


class StoreKeyMixin(AttributeRegistryMixin):
    """
    Mixin for managing KeyFields using a Local KeyFields Dictionary.
    """

    def _init_attr_registry(self, registry: AttributeTypeRegistry) -> None:
        super()._init_attr_registry(registry)
        registry.register_type(StoreKey)

    @property
    def store_keys(self) -> dict[str, StoreKey]:
        """
        Retrieve a dictionary of all registered Store Keys on this instance.
        """
        return {
            name: value
            for name in self._attr_registry[StoreKey]
            if (value := getattr(self, name)) is not None
        }

    def register_store_key(self, name: str, value: Union[StoreKey, None]) -> None:
        self._register_attribute(name, value, StoreKey)
