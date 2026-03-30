# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project follows semantic versioning principles for release notes.

## [Unreleased]

### Added
- Security docs synchronized with actual helper CLI surface (including `qos-*` and `denylist-*` operations).
- New operator document: `docs/rkn_denylist.md` with baseline denylist template and compliance policy notes.
- CI workflow for dependency security audit + SBOM export.
- Security regression tests for helper CLI whitelist and helper policy file hardening checks.
- Rate-limit observability metrics in runtime health report.

### Changed
- Admin callback authorization checks standardized through shared guard helper.
- Denylist domain resolution now has bounded DNS timeout and max resolved entries safety budget.
