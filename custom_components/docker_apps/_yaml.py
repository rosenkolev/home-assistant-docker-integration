"""YAML loading that refuses a document too big once its aliases are written out.

Anchors and aliases are fine (`x-common: &default`, `<<: *default`), but ten lines
of nested aliases ("billion laughs") stand for billions of values. They load small,
as the copies are shared, and blow up later when the data is walked or saved.
"""

from __future__ import annotations

import yaml

# values of a document with every alias written out; real compose files have a few
# hundred
MAX_VALUES = 100_000


class YamlTooBig(yaml.YAMLError):
    """The document stands for too many values (nested aliases)."""


def _count(node: yaml.Node, memo: dict[int, int], open_: set[int]) -> int:
    key = id(node)
    if key in memo:
        return memo[key]
    if key in open_:
        raise YamlTooBig("an alias refers to itself")
    open_.add(key)
    total = 1
    if isinstance(node, yaml.SequenceNode):
        total += sum(_count(child, memo, open_) for child in node.value)
    elif isinstance(node, yaml.MappingNode):
        total += sum(
            _count(k, memo, open_) + _count(v, memo, open_) for k, v in node.value
        )
    open_.discard(key)
    if total > MAX_VALUES:
        raise YamlTooBig(f"too many values (YAML aliases): more than {MAX_VALUES}")
    memo[key] = total
    return total


def check_size(node: yaml.Node | None) -> None:
    if node is None:
        return
    try:
        _count(node, {}, set())
    except RecursionError as err:
        raise YamlTooBig("nested too deep") from err


class GuardedLoader(yaml.SafeLoader):
    """A SafeLoader that checks the size before building anything."""

    def get_single_node(self):
        node = super().get_single_node()
        check_size(node)
        return node


def safe_load(text: str | bytes | None):
    """yaml.safe_load, guarded."""
    return yaml.load(text or "", Loader=GuardedLoader)  # noqa: S506 (a SafeLoader)
