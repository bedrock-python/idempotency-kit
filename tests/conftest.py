"""Shared test fixtures."""

from collections.abc import AsyncGenerator, Generator
from contextlib import suppress
from typing import cast

import pytest
import pytest_asyncio

try:
    import docker
    from docker.errors import DockerException
except ImportError:
    docker = None  # type: ignore[assignment]
    DockerException = Exception  # type: ignore[assignment, misc]

from fakeredis.aioredis import FakeRedis
from redis.asyncio import Redis as AsyncRedisClient
from testcontainers.redis import RedisContainer


def is_docker_available() -> bool:
    """Check if docker is available to run integration tests."""
    if docker is None:
        return False
    try:
        client = docker.from_env()
        client.version()
    except (DockerException, Exception):
        return False
    else:
        return True


@pytest.fixture(scope="session")
def redis_container() -> Generator[RedisContainer, None, None]:
    """Start a Redis container for the test session."""
    if not is_docker_available():
        pytest.skip("Docker is not available, skipping integration tests that require it.")

    with RedisContainer("redis:7-alpine") as redis:
        yield redis


@pytest_asyncio.fixture
async def redis_client(redis_container: RedisContainer) -> AsyncGenerator[AsyncRedisClient, None]:
    """Create a real Redis client for testing."""
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)

    client = AsyncRedisClient(host=host, port=int(port), decode_responses=False)
    yield client
    # Flush DB to ensure isolation between tests
    with suppress(Exception):
        await client.flushdb()  # type: ignore[attr-defined]
    await client.aclose()


@pytest_asyncio.fixture
async def fake_redis() -> AsyncGenerator[AsyncRedisClient, None]:
    """Create a fake Redis client for unit testing."""
    redis = FakeRedis()
    # We yield it as AsyncRedisClient protocol
    yield cast(AsyncRedisClient, redis)
    await redis.aclose()
