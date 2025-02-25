from abc import (
    ABCMeta,
)
from collections import (
    defaultdict,
)
from itertools import (
    chain,
)
from typing import (
    Mapping,
    Self,
)

import attrs

from azul.attrs import (
    SerializableAttrs,
    serializable,
)
from azul.collections import (
    aset,
    none_safe_apply,
)
from azul.indexer import (
    Bundle,
    SourcedBundleFQID,
)
from azul.indexer.document import (
    EntityReference,
    EntityType,
)
from azul.types import (
    MutableJSON,
)

# AnVIL snapshots do not use UUIDs for primary/foreign keys. This type alias
# helps us distinguish these keys from the document UUIDs, which are drawn from
# the `datarepo_row_id` column. Note that entities from different tables may
# have the same key, so `KeyReference` should be used when mixing keys from
# different entity types.
Key = str


@attrs.frozen(kw_only=True)
class KeyReference(SerializableAttrs):
    key: Key
    entity_type: EntityType


def ref_set_field():
    return serializable(attrs.field(),
                        from_json=lambda x: frozenset(map(EntityReference.parse, x)),
                        to_json=lambda x: sorted(map(str, x)))


@attrs.frozen(kw_only=True, order=False)
class Link[REF: EntityReference | KeyReference](SerializableAttrs):
    inputs: frozenset[REF] = ref_set_field()
    activity: REF | None = None
    outputs: frozenset[REF] = ref_set_field()

    @property
    def all_entities(self) -> frozenset[REF]:
        return self.inputs | self.outputs | aset(self.activity)

    @classmethod
    def group_by_activity(cls, links: set[Self]):
        """
        Merge links that share the same (non-null) activity.
        """
        groups_by_activity: Mapping[KeyReference, set[Self]] = defaultdict(set)
        for link in links:
            if link.activity is not None:
                groups_by_activity[link.activity].add(link)
        for activity, group in groups_by_activity.items():
            if len(group) > 1:
                links -= group
                merged_link = cls(inputs=frozenset.union(*[link.inputs for link in group]),
                                  activity=activity,
                                  outputs=frozenset.union(*[link.outputs for link in group]))
                links.add(merged_link)

    def __lt__(self, other: Self) -> bool:
        return min(self.inputs) < min(other.inputs)


class EntityLink(Link[EntityReference]):
    pass


class KeyLink(Link[KeyReference]):

    def to_entity_link(self,
                       entities_by_key: Mapping[KeyReference, EntityReference]
                       ) -> EntityLink:
        lookup = entities_by_key.__getitem__
        return EntityLink(inputs=frozenset(map(lookup, self.inputs)),
                          activity=none_safe_apply(lookup, self.activity),
                          outputs=frozenset(map(lookup, self.outputs)))


@attrs.define(kw_only=True)
class AnvilBundle[BUNDLE_FQID: SourcedBundleFQID](Bundle[BUNDLE_FQID],
                                                  metaclass=ABCMeta):
    # The `entity_type` attribute of these keys contains the entities' BigQuery
    # table name (e.g. `anvil_sequencingactivity`), not the entity type used for
    # the contributions (e.g. `activities`). The metadata plugin converts from
    # the former to the latter during transformation.
    entities: dict[EntityReference, MutableJSON] = attrs.field(factory=dict)
    links: set[EntityLink] = serializable(
        attrs.field(factory=set),
        from_json=lambda x: set(EntityLink.from_json(v) for v in x),
        to_json=lambda x: [v.to_json() for v in sorted(x)]
    )
    orphans: dict[EntityReference, MutableJSON] = attrs.field(factory=dict)

    def reject_joiner(self):
        # We can skip the `links` attribute because the only strings it contains
        # are UUIDs and table names
        self._reject_joiner(chain(self.entities.values(), self.orphans.values()))
