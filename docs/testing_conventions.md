# Testing Conventions

This document describes testing standards and conventions for the idempotency-kit library.

## Test Structure

Every library must have a `tests/` directory:

```
tests/
├── conftest.py          # Shared fixtures
├── unit/                # Unit tests (Mock external deps)
│   └── conftest.py      # Auto-applies @pytest.mark.unit
└── integration/         # Integration tests (Use real deps via Docker)
    └── conftest.py      # Auto-applies @pytest.mark.integration
```

## Markers

### Automatic Markers

Test markers are applied **automatically** via `conftest.py` files:

- **`@pytest.mark.unit`**: Automatically applied to all tests in `tests/unit/` directory
- **`@pytest.mark.integration`**: Automatically applied to all tests in `tests/integration/` directory

**DO NOT** manually add these markers to test functions - they are added automatically based on the directory structure.

## Test Naming

All tests must follow the **BDD naming pattern**:

```
test__subject__condition__expectedresult
```

### Examples

- `test__domain_service__valid_input__creates_record`
- `test__redis_repository__expired_record__removes_from_cache`
- `test__coordinator__cache_miss__executes_action_and_saves`
- `test__decorator__no_key__executes_directly`

### Naming Guidelines

- **Subject**: Component being tested (e.g., `domain_service`, `redis_repository`, `coordinator`)
- **Condition**: Input state or scenario (e.g., `valid_input`, `expired_record`, `cache_miss`)
- **Expected Result**: What should happen (e.g., `creates_record`, `removes_from_cache`, `executes_action`)

## AAA Pattern

All tests should follow the **Arrange-Act-Assert** pattern with explicit comments:

```python
async def test__redis_repository__save_and_get__retrieves_saved_record(redis_client):
    # Arrange
    repo = RedisAsyncIdempotencyRepository(redis_client)
    service = IdempotencyDomainService()
    record = service.create_record(operation="test", idempotency_key="key", result={})

    # Act
    await repo.save(record)
    retrieved = await repo.get("test", "key")

    # Assert
    assert retrieved is not None
    assert retrieved.idempotency_key == "key"
```

### AAA Guidelines

- **Arrange**: Set up test data, create mocks, configure dependencies
- **Act**: Execute the code being tested (usually one line)
- **Assert**: Verify the expected outcome

## One Behavior Per Test

Each test should verify **only one behavior**. Use `@pytest.mark.parametrize` to test multiple similar scenarios:

```python
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("operation", "test:op"),
        ("idempotency_key", "key:123"),
    ],
    ids=["operation_with_colon", "key_with_colon"],
)
def test__idempotency_record_create__colon_in_field__raises_validation_error(
    field: str, value: str
) -> None:
    # Arrange
    params = {"operation": "op", "idempotency_key": "key", "result": {}, "ttl_seconds": 60}
    params[field] = value

    # Act & Assert
    with pytest.raises(ValidationError, match="cannot contain ':'"):
        IdempotencyRecord.create(**params)
```

### Parametrization Guidelines

- Use descriptive `ids` for each parameter set
- Group related test cases together
- Keep parameter sets readable and maintainable

## Test Organization

### Unit Tests

Unit tests should:

- Mock all external dependencies (Redis, databases, etc.)
- Focus on testing business logic in isolation
- Be fast (< 1s per test)
- Use `fake_redis` fixture instead of real Redis

**Example:**

```python
async def test__coordinator__cache_miss__executes_action_and_saves(
    coordinator: AsyncIdempotencyCoordinator, mock_repo: AsyncMock, mock_adapter: MagicMock
) -> None:
    """Test that cache miss executes action and saves result."""
    # Arrange
    operation = "test.op"
    key = "test-key"
    expected_result = {"data": "ok"}
    action = AsyncMock(return_value=expected_result)

    mock_repo.get.return_value = None
    mock_repo.save.return_value = None

    # Act
    result = await coordinator.coordinate(operation, key, 60, mock_adapter, action)

    # Assert
    assert result == expected_result
    action.assert_called_once()
    mock_repo.get.assert_called_once_with(operation, key)
    mock_repo.save.assert_called_once()
```

### Integration Tests

Integration tests should:

- Use real dependencies (Redis via Docker)
- Test end-to-end functionality
- Verify actual storage behavior
- Use `redis_client` fixture for real Redis connection

**Example:**

```python
async def test__redis_repository__save_and_get__retrieves_saved_record(
    redis_client: AsyncRedisClient
) -> None:
    """Test that saved record can be retrieved from Redis."""
    # Arrange
    repo = RedisAsyncIdempotencyRepository(redis_client)
    service = IdempotencyDomainService()
    record = service.create_record(operation="test", idempotency_key="key", result={})

    # Act
    await repo.save(record)
    retrieved = await repo.get("test", "key")

    # Assert
    assert retrieved is not None
    assert retrieved.idempotency_key == "key"
```

## Fixtures

**IMPORTANT**: All fixtures must be defined in `conftest.py` files, NOT in test files. Test files should contain only tests.

### Fixture Organization

```
tests/
├── conftest.py                    # Global fixtures (redis_container, redis_client, fake_redis)
├── unit/
│   ├── conftest.py               # Unit-specific fixtures (auto-applies @pytest.mark.unit)
│   └── core/
│       ├── conftest.py           # Core unit fixtures (mock_repo, mock_adapter, coordinator)
│       ├── test_coordinator.py   # ONLY tests, NO fixtures
│       └── test_services.py      # ONLY tests, NO fixtures
└── integration/
    ├── conftest.py               # Integration fixtures (auto-applies @pytest.mark.integration)
    └── test_redis_integration.py # ONLY tests, NO fixtures
```

### Global Fixtures (tests/conftest.py)

- `redis_container`: Session-scoped Redis container for integration tests
- `redis_client`: Real Redis client for integration tests
- `fake_redis`: Fake Redis client for unit tests

### Module-Specific Fixtures

Define module-specific fixtures in the nearest `conftest.py`:

```python
# tests/unit/core/conftest.py
from unittest.mock import AsyncMock
import pytest

@pytest.fixture
def mock_repo() -> AsyncMock:
    """Create mock repository."""
    return AsyncMock()


@pytest.fixture
def mock_adapter() -> MagicMock:
    """Create mock result adapter."""
    adapter = MagicMock(spec=ResultAdapter)
    adapter.encode.side_effect = lambda x: x
    adapter.decode.side_effect = lambda x: x
    return adapter
```

## Running Tests

### Run All Tests

```bash
pytest tests
```

### Run Only Unit Tests

```bash
pytest tests -m unit
```

### Run Only Integration Tests

```bash
pytest tests -m integration
```

### Run With Coverage

```bash
pytest tests --cov=idempotency_kit --cov-report=term-missing
```

## Coverage Requirements

- **Minimum coverage**: 85%
- All public APIs must be covered
- Edge cases and error paths must be tested
- Optional modules (`dishka/`, `settings.py`, `infra/metrics/`) are excluded from coverage

## Best Practices

### ✅ DO

- Follow BDD naming: `test__subject__condition__expectedresult`
- Use AAA comments: `# Arrange`, `# Act`, `# Assert`
- **Put ALL fixtures in `conftest.py` files** - test files should contain ONLY tests
- Test one behavior per test function
- Use parametrization for similar test cases
- Mock external dependencies in unit tests
- Use real dependencies in integration tests
- Write descriptive docstrings
- Keep tests isolated and independent

### ❌ DON'T

- **Don't define fixtures in test files** - use `conftest.py` instead
- Don't manually add `@pytest.mark.unit` or `@pytest.mark.integration` - they're auto-applied
- Don't test multiple behaviors in one test
- Don't use real Redis in unit tests
- Don't skip AAA structure
- Don't use vague test names like `test_save` or `test_error`
- Don't duplicate test logic - use parametrization

## Example Test File

### tests/unit/core/conftest.py (Fixtures)

```python
"""Fixtures for core unit tests."""

import pytest
from idempotency_kit import IdempotencyDomainService

@pytest.fixture
def service() -> IdempotencyDomainService:
    """Create domain service instance."""
    return IdempotencyDomainService()
```

### tests/unit/core/test_services.py (Tests Only)

```python
"""Unit tests for domain service."""

import pytest
from idempotency_kit import IdempotencyDomainService, IdempotencyValidationError


def test__domain_service__valid_input__creates_record(service: IdempotencyDomainService) -> None:
    """Test that valid input creates record successfully."""
    # Arrange
    operation = "user.create"
    key = "test-key-123"
    result = {"user_id": "123"}

    # Act
    record = service.create_record(operation=operation, idempotency_key=key, result=result)

    # Assert
    assert record.operation == operation
    assert record.idempotency_key == key
    assert not record.is_expired


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("operation", "  "),
        ("idempotency_key", "  "),
    ],
    ids=["empty_operation", "empty_key"],
)
def test__domain_service__empty_field__raises_validation_error(
    service: IdempotencyDomainService, field: str, value: str
) -> None:
    """Test that empty operation or key raises ValidationError."""
    # Arrange
    params = {"operation": "test", "idempotency_key": "key", "result": {}}
    params[field] = value

    # Act & Assert
    with pytest.raises(IdempotencyValidationError, match=field):
        service.create_record(**params)
```

**Note**: The `service` fixture is automatically available from `conftest.py`.
