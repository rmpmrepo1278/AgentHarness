# Changelog

All notable changes to the agentharness homelab are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Secret rotation alerts (`make secrets-check`)
- Grafana alerting rules (`make grafana-alerts`)
- Automated backup restore drill (`make backup-drill`)
- Container image scanning with Trivy (`make scan-images`)
- Python linting with ruff (`make lint`)
- Type checking with mypy (`make typecheck`)
- CHANGELOG.md tracking

### Changed
- Ollama memory limit: 12GB → 16GB
- All container ports bound to 127.0.0.1 (except traefik, pihole)
- Neo4j recreated with healthcheck
- Pre-push hook now runs 5 eval suites
- Duplicate scheduler daemon killed

## [2026-08-22]

### Added
- TokenRouter as last-resort free LLM provider (qwen + nvidia)
- Doc drift detection system (13 checks)
- 5 infrastructure eval suites (48 tests)
- postgres_backup.sh (monthly pg_dump)
- docker_build_prune.sh (weekly build cache prune)

### Fixed
- Splade port secured (0.0.0.0 → 127.0.0.1)
- Grafana port corrected (3002 → 3001)
- Dead boot_inbox_watcher job removed
- Memory limits on metronix containers
