"""Regressions for bounded invalidation state and refresh ABA protection."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from agentx.core.knowledge import ProvenanceKind, ProvenanceReference
from agentx.world_model import (
    CacheRefreshState,
    DeviceState,
    LazyWorldStateCache,
    ObservationMetadata,
    WorldAvailability,
    WorldEntityId,
    WorldEntityKind,
)

NOW = datetime(2026, 9, 16, tzinfo=UTC)


def identity(number: int) -> WorldEntityId:
    return WorldEntityId("cache-test", WorldEntityKind.DEVICE, str(number))


def observation(entity_id: WorldEntityId) -> DeviceState:
    return DeviceState(
        entity_id=entity_id,
        platform="windows",
        role="local_host",
        availability=WorldAvailability.AVAILABLE,
        capability_health=(),
        last_seen=NOW,
        metadata=ObservationMetadata(
            observation_id=entity_id.value,
            source=ProvenanceReference(kind=ProvenanceKind.SYSTEM, reference="cache-test"),
            observed_at=NOW,
            ttl=timedelta(seconds=30),
            environment_id="cache-test",
        ),
    )


class WorldCacheBookkeepingTests(unittest.TestCase):
    def test_absent_invalidation_and_eviction_do_not_accumulate_tombstones(self) -> None:
        cache = LazyWorldStateCache(max_entries=4)
        for number in range(1000):
            cache.invalidate(identity(number), reason="external invalidation")
            cache.put(observation(identity(number)))
            self.assertLessEqual(len(cache), 4)
            # This is the retained resource being bounded, including absent IDs.
            self.assertLessEqual(len(cache._epochs), 4)
        self.assertEqual(cache.lookup(identity(999), at=NOW).value, observation(identity(999)))

    def test_generation_rotation_cannot_accept_a_pre_invalidation_observation(self) -> None:
        cache = LazyWorldStateCache(max_entries=2)

        class RacingProvider:
            def observe(self, entity_id: WorldEntityId) -> DeviceState:
                cache.invalidate(entity_id, reason="target changed during observation")
                # Evict its tombstone before the old observation returns.
                for number in range(10, 20):
                    cache.invalidate(identity(number), reason="unrelated changes")
                return observation(entity_id)

        cache.register_provider(WorldEntityKind.DEVICE, RacingProvider())
        result = cache.lookup(identity(1), at=NOW)
        self.assertIs(result.state, CacheRefreshState.REFRESH_FAILURE)
        self.assertIsNone(result.value)
        self.assertEqual(len(cache), 0)

    def test_unrelated_invalidation_without_rotation_keeps_targeted_semantics(self) -> None:
        cache = LazyWorldStateCache(max_entries=4)

        class Provider:
            def observe(self, entity_id: WorldEntityId) -> DeviceState:
                cache.invalidate(identity(2), reason="unrelated change")
                return observation(entity_id)

        cache.register_provider(WorldEntityKind.DEVICE, Provider())
        result = cache.lookup(identity(1), at=NOW)
        self.assertIs(result.state, CacheRefreshState.REFRESH_SUCCESS)
        self.assertEqual(result.value, observation(identity(1)))


if __name__ == "__main__":
    unittest.main()
