# Changelog

Release history reconstructed from the original versioned source archives. Original Git commit dates were unavailable; import dates are not historical release dates.

## 1.3.0
- Add container-restart and single-replica container-recreate recovery modes, including manual container selection and escalation.
- Add HTTP, Docker Healthcheck, both and auto check methods; configurable Docker-health failure threshold and private targets without host ports.
- Track independent container counters, schedules, cooldowns and recovery escalation; coordinate one project recovery at a configurable multi-target failure threshold (default 2) using a shared project lock and rate budget.
- Add container status, effective options, recovery history, metrics and webhook target fields; preserve existing v1.2.x configuration and recovery history.
- Add a static local-only Compose label configurator with presets, validation, YAML copy/download, and light/dark themes; link from the main documentation page.
- Add native noarch RPM spec/build script, signed YUM/DNF repository staging, CI and release packaging for DEB/RPM, and updated man page and installation guide.
- Expand unit, JavaScript and isolated Docker integration tests.

## Repository publication preparation
- Platform guide, static documentation site, contribution and security policies, CI/release automation.

## 1.2.2
- Detailed English `man docker-watchdog` page installed by the DEB.

## 1.2.1
- Human-readable status and history, JSON/raw formats, effective settings, timestamps and expanded observability.

## 1.2.0
- Maintenance pause/resume, manual recovery, history and reset-limit commands.
- Config comments, unlimited restart option, startup grace, escalation, Docker events, metrics, hooks and webhooks.
- Improved scheduling and diagnostics; pre-recovery discovery check.

## 1.1.0
- Persistent state, configuration, CLI, DEB packaging and three recovery modes, pending up recovery.

## 1.0.0
- Label-based discovery, published port resolution, HTTP monitoring and whole-project restart with cooldown and restart limiting.
