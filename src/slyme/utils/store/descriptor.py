from slyme.utils.typing import (
    Generic,
    TypeVar,
    overload,
    Union,
    Self,
    Protocol,
    TYPE_CHECKING,
)
from slyme.utils.mixin import GetattrAdapterMixin
from .store import Key

if TYPE_CHECKING:
    from .mixin import KeyFieldMixin

_KeyT = TypeVar("_KeyT", bound=Key)


class _PathFactory(Protocol[_KeyT]):
    def __call__(self, path: str, *args, **kwargs) -> _KeyT: ...


class KeyField(GetattrAdapterMixin, Generic[_KeyT]):
    """
    Descriptor declaring a Key dependency/production.
    Strictly forbids default creation logic on access.
    Supports explicit deletion via 'del'.
    """

    def __init__(
        self, path_factory: Union[_PathFactory[_KeyT], None] = None, doc: str = ""
    ):
        self.path_factory = path_factory
        self.doc = doc

    def __set_name__(self, owner: type["KeyFieldMixin"], name: str):
        self.name = name

    def __getattr__(self, name: str):
        """
        Safeguard against accessing metadata (like `name`) on an improperly initialized descriptor.

        This explicitly traps missing `name` attributes to prevent confusing AttributeErrors
        inside `__get__` when `__set_name__` has not been triggered (e.g., dynamically created
        descriptors not assigned to a class).
        """
        if name == "name":
            raise RuntimeError(
                f"KeyField attribute '{name}' is missing. "
                "This usually happens when the descriptor is not assigned to a class attribute "
                "correctly (missing __set_name__ call), or instantiated dynamically without manual setup."
            )
        return super().__getattr__(name)

    @overload
    def __get__(
        self, instance: None, owner: Union[type["KeyFieldMixin"], None] = None
    ) -> Self: ...
    @overload
    def __get__(
        self,
        instance: "KeyFieldMixin",
        owner: Union[type["KeyFieldMixin"], None] = None,
    ) -> _KeyT: ...
    def __get__(
        self,
        instance: Union["KeyFieldMixin", None],
        owner: Union[type["KeyFieldMixin"], None] = None,
    ) -> Union[Self, _KeyT]:
        """
        Retrieve the Key instance from the owner's `_store_keys`.

        This method relies on two critical preconditions:
        1. `self.name` must be set (guaranteed by `KeyField.__getattr__` check).
        2. `instance._store_keys` must exist (guaranteed by `KeyFieldMixin.__getattr__` check).

        Failure in either precondition triggers a specific `RuntimeError` rather than
        a generic `AttributeError`, aiding in debugging initialization issues.
        """
        if instance is None:
            return self

        try:
            return instance._store_keys[self.name]
        except KeyError:
            raise AttributeError(
                f"KeyField `{self.name}` has not been assigned a value yet. "
                f"You should initialize it first."
            ) from None

    def __set__(self, instance: "KeyFieldMixin", value: Union[str, _KeyT]):
        if isinstance(value, str):
            if self.path_factory is None:
                raise TypeError(
                    f"KeyField `{self.name}` does not allow string assignment "
                    f"(no `path_factory` provided). Expected `{Key}` instance."
                )
            value = self.path_factory(value)

        if not isinstance(value, Key):
            raise TypeError(
                f"Expected a `{Key}` instance for field `{self.name}`, got {type(value)}."
            )

        instance._store_keys[self.name] = value

    def __delete__(self, instance: "KeyFieldMixin"):
        """"""
        try:
            del instance._store_keys[self.name]
        except KeyError:
            raise AttributeError(
                f"KeyField `{self.name}` is not set, and cannot delete."
            ) from None
