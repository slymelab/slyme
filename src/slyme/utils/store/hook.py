"""Store hooks."""

from dataclasses import dataclass
from slyme.utils.typing import Union, Any, TYPE_CHECKING, TypeVar
from slyme.utils.constant import Missing
from slyme.utils.collection import MutableSequenceProxy

if TYPE_CHECKING:
    from . import Store, Key
_StoreHookT = TypeVar("_StoreHookT", bound="StoreHook")


class StoreHook:
    def getitem(self, instance: "Store", key: "Key", value: Any, /) -> None:
        pass

    def setitem(
        self,
        instance: "Store",
        key: "Key",
        old_value: Union[Missing, Any],
        new_value: Any,
        /,
    ) -> None:
        pass

    def delitem(
        self, instance: "Store", key: "Key", old_value: Union[Missing, Any], /
    ) -> None:
        pass


class StoreHookContainer(StoreHook, MutableSequenceProxy[_StoreHookT]):
    def getitem(self, instance: "Store", key: "Key", value: Any, /) -> None:
        for hook in self:
            hook.getitem(instance, key, value)

    def setitem(
        self,
        instance: "Store",
        key: "Key",
        old_value: Union[Missing, Any],
        new_value: Any,
        /,
    ) -> None:
        for hook in self:
            hook.setitem(instance, key, old_value, new_value)

    def delitem(
        self, instance: "Store", key: "Key", old_value: Union[Missing, Any], /
    ) -> None:
        for hook in self:
            hook.delitem(instance, key, old_value)


# Record hook
@dataclass(frozen=True)
class GetitemRecord:
    __slots__ = ("instance", "key", "value")
    instance: "Store"
    key: "Key"
    value: Any


@dataclass(frozen=True)
class SetitemRecord:
    __slots__ = ("instance", "key", "old_value", "new_value")
    instance: "Store"
    key: "Key"
    old_value: Any
    new_value: Any


@dataclass(frozen=True)
class DelitemRecord:
    __slots__ = ("instance", "key", "old_value")
    instance: "Store"
    key: "Key"
    old_value: Any


class RecordHook(StoreHook):
    def __init__(self):
        super().__init__()
        self.records: list[Union[GetitemRecord, SetitemRecord, DelitemRecord]] = []

    def getitem(self, instance: "Store", key: "Key", value: Any, /) -> None:
        self.records.append(GetitemRecord(instance, key, value))

    def setitem(
        self,
        instance: "Store",
        key: "Key",
        old_value: Union[Missing, Any],
        new_value: Any,
        /,
    ) -> None:
        self.records.append(SetitemRecord(instance, key, old_value, new_value))

    def delitem(
        self, instance: "Store", key: "Key", old_value: Union[Missing, Any], /
    ) -> None:
        self.records.append(DelitemRecord(instance, key, old_value))
