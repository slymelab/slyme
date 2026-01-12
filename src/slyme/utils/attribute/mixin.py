from slyme.utils.typing import Any, Union
from slyme.utils.collection import OrderedSet
from slyme.utils.registry import TypeRegistry
from .descriptor import cached_property


class AttributeRegistryMixin:
    """
    Mixin that maintains a registry of instance attributes categorized by their types.

    This mixin intercepts attribute assignment to automatically register attributes into
    orthogonal categories defined in the internal ``_attr_registry``. It ensures type
    consistency and exclusivity for registered attributes.

    Key Features:

    1. **Automatic Registration**:
       When an attribute is set, its value's type is checked against the registry.
       If recognized, the attribute name is added to the corresponding category.

    2. **Registry Exclusivity**:
       Once an attribute is registered as a specific category (e.g., ``CategoryA``),
       it **cannot** be reassigned to a value belonging to a different category
       (e.g., ``CategoryB``). This prevents implicit "type drift".
       Attempting to do so will raise a :class:`TypeError`.

    3. **None Support**:
       Assigning ``None`` to an already registered attribute is permitted.
       This operation is treated as "keeping the current state", meaning the attribute
       remains registered under its original category.

    4. **Category Reset**:
       To change an attribute's registered category, the attribute must be explicitly
       deleted first (using ``del instance.attr``). Deletion removes the attribute
       from the registry, allowing it to be re-registered as a new type subsequently.
    """

    @cached_property
    def _attr_registry(self) -> TypeRegistry[Any, OrderedSet[str]]:
        registry = TypeRegistry(str(self), strict=True, orthogonal=True)
        self._init_attr_registry(registry)
        return registry

    def _init_attr_registry(self, registry: TypeRegistry[Any, OrderedSet[str]]) -> None:
        """
        Hook method to initialize the attribute registry configuration.

        This method is called once during the first access to ``_attr_registry``.
        Subclasses should override this method to register the specific types they
        intend to track.

        Args:
            registry: The initialized ``TypeRegistry`` instance ready for configuration.
        """
        pass

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_attr_registry":
            super().__setattr__(name, value)
            return
        self._register_attribute(name, value=value, value_cls=None)

    def _register_type(self, type_cls: type) -> None:
        self._attr_registry(OrderedSet(), key=type_cls, strict=True)

    def _register_attribute(
        self,
        name: str,
        value: Any,
        value_cls: Union[type, None],
    ) -> None:
        """
        Set an attribute with strict registry consistency and exclusivity checks.

        Args:
            name: The name of the attribute.
            value: The value to be assigned.
            value_cls: The expected type of the attribute. If None, it will be inferred from ``value``.
        """
        # Basic type check if both `value` and `value_cls` are present.
        if value_cls is not None and value is not None:
            if not isinstance(value, value_cls):
                raise TypeError(
                    f"Value for '{name}' must be {value_cls}, got {type(value)}."
                )

        registry = self._attr_registry
        # 1. Get the current registered category of the attribute.
        current_category: Union[type, None] = None
        for category_cls, category_set in registry.items():
            if name in category_set:
                current_category = category_cls
                break

        # 2. Resolve the target category to be registered.
        # Logic: If input has no type info (value is None AND no explicit type), target = current.
        raw_type = value_cls
        if raw_type is None and value is not None:
            raw_type = type(value)

        target_category: Union[type, None] = None
        if raw_type is None:
            # NOTE: `raw_type` is None means both `value` and `value_cls` are None,
            # and this will not change the register state (i.e., target = current).
            target_category = current_category
        else:
            target_category = registry.lookup_cls(raw_type)

        if value_cls is not None and target_category is None:
            # Explicit `value_cls` is specified but not found.
            raise ValueError(
                f"Explicit value_cls '{value_cls.__name__}' matches no registered category "
                f"in '{registry.namespace}'."
            )

        # 3. Compatibility Check.
        if current_category is not None:
            # Exclusivity rule: Once registered, target category must match current category.
            if target_category is not current_category:
                raise TypeError(
                    f"Attribute '{name}' is exclusively registered as '{current_category}'. "
                    f"Cannot assign type '{raw_type}' (resolves to: {target_category}). "
                    f"Delete attribute first to change category."
                )

        # 4. Action & Register.
        super().__setattr__(name, value)
        if target_category is not None:
            registry[target_category].add(name)

    def __delattr__(self, name: str) -> None:
        super().__delattr__(name)
        for category_set in self._attr_registry.values():
            category_set.discard(name)
