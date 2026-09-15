"""Unit tests for the Prometheus collector and its cached getter."""

from prometheus_client import CollectorRegistry

from idempotency_kit.infra.metrics.prometheus import PrometheusIdempotencyMetrics, get_idempotency_metrics


def test__get_idempotency_metrics__same_prefix_twice__returns_the_cached_instance() -> None:
    """A second container asking for the same prefix gets the instance the first one registered."""
    # Act
    first = get_idempotency_metrics(prefix="cache_test")
    second = get_idempotency_metrics(prefix="cache_test")

    # Assert
    assert first is second


def test__get_idempotency_metrics__different_prefix__returns_a_distinct_instance() -> None:
    """Two prefixes are two series, so two instances."""
    # Act
    first = get_idempotency_metrics(prefix="prefix_a_test")
    second = get_idempotency_metrics(prefix="prefix_b_test")

    # Assert
    assert first is not second


def test__get_idempotency_metrics__empty_and_none_prefix__are_the_same_instance() -> None:
    """An empty prefix names the same series as no prefix, so it must not register them twice."""
    # Act
    unprefixed = get_idempotency_metrics()
    empty = get_idempotency_metrics(prefix="")

    # Assert
    assert unprefixed is empty


def test__prometheus_metrics__own_registry__records_there_and_can_be_built_twice() -> None:
    """A private registry isolates a test: the series land in it, and a second instance is no conflict."""
    # Arrange
    registry = CollectorRegistry()
    metrics = PrometheusIdempotencyMetrics(registry=registry)

    # Act
    metrics.record_hit("op.hit")
    PrometheusIdempotencyMetrics(registry=CollectorRegistry())

    # Assert
    assert registry.get_sample_value("idempotency_operations_total", {"operation": "op.hit", "status": "hit"}) == 1.0
