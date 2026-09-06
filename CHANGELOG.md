# Changelog

## [0.2.0](https://github.com/bedrock-python/idempotency-kit/compare/idempotency-kit-v0.1.1...idempotency-kit-v0.2.0) (2026-09-06)


### ⚠ BREAKING CHANGES

* DEFAULT_TTL_MINUTES is 60 (was 30) and MAX_TTL_SECONDS is 2592000 (was 86400), so IdempotencyDomainService() built without arguments now keeps records for an hour and accepts a TTL of up to 30 days. Pass default_ttl_minutes=30 and max_ttl_seconds=86400 to keep the old values.

### Bug Fixes

* the defects the agents page turned up ([#22](https://github.com/bedrock-python/idempotency-kit/issues/22)) ([1573db4](https://github.com/bedrock-python/idempotency-kit/commit/1573db4774191598cad38b02e653b2656d3bb763))


### Documentation

* an upgrade note for the unified TTL defaults ([#25](https://github.com/bedrock-python/idempotency-kit/issues/25)) ([bb90e84](https://github.com/bedrock-python/idempotency-kit/commit/bb90e8453e0ac74856b354b34f01dfe495e9393c))

## [0.1.1](https://github.com/bedrock-python/idempotency-kit/compare/idempotency-kit-v0.1.0...idempotency-kit-v0.1.1) (2026-08-28)


### Bug Fixes

* **core:** store any JSON result and decide hit/miss on the record, not on None ([#8](https://github.com/bedrock-python/idempotency-kit/issues/8)) ([ddb537e](https://github.com/bedrock-python/idempotency-kit/commit/ddb537e8c39e5becb1a9b1ff7549bc11f40735d7)), closes [#6](https://github.com/bedrock-python/idempotency-kit/issues/6)
* **dishka:** provide the metrics collector from settings so the shipped providers build a container ([#9](https://github.com/bedrock-python/idempotency-kit/issues/9)) ([2a06416](https://github.com/bedrock-python/idempotency-kit/commit/2a064169a16332762d3ab62926c03d2f203482bd)), closes [#7](https://github.com/bedrock-python/idempotency-kit/issues/7)
* **release:** update version to 0.1.0 and fix release-please config ([016c6c4](https://github.com/bedrock-python/idempotency-kit/commit/016c6c42ba189291be56250192f9e6db7020f853))
* update publish workflow, release-please version search, gitignore ([#3](https://github.com/bedrock-python/idempotency-kit/issues/3)) ([9915f0e](https://github.com/bedrock-python/idempotency-kit/commit/9915f0e6e784b6d59375541e6244efaca694a0bb))

## 0.1.0 (2026-05-13)


### Bug Fixes

* **ci:** remove coverage threshold from integration tests ([b00f037](https://github.com/bedrock-python/idempotency-kit/commit/b00f03742925922e261790b0ee649055c31fdaae))
* **ci:** set integration test coverage threshold to 50% ([ed4e790](https://github.com/bedrock-python/idempotency-kit/commit/ed4e7907ff977916b0be11a6597a090b9ae11cab))


### Documentation

* rewrite README to match library style with badges ([cebded3](https://github.com/bedrock-python/idempotency-kit/commit/cebded360b4c0c37d7604efd230574b4464f47c8))

## Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
