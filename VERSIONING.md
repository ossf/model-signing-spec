<!-- Copyright 2024 The OpenSSF Authors

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License. -->

# Versioning

This document defines how the OMS specification and its artifacts are
versioned.

## Specification Version

The OMS specification uses a maturity lifecycle:

| Stage | Meaning |
|---|---|
| **Pre-Draft** | Exploratory; no stability guarantees. |
| **Draft** | Under active development; breaking changes possible with notice. |
| **Approved** | Stable; breaking changes require a new major version. |

The current stage is tracked by the version in the spec filename
and the `Version` field at the top of the spec (e.g.,
`spec/v1.0.md`).  Stage transitions (Draft → Approved) are recorded
in the `CHANGELOG.md`.

## Repository Layout

Specifications and schemas are organized by version:

```
spec/
  v1.0.md          ← spec starting at v1.0 (minor versions evolve in place)
  v2.0.md          ← breaking changes start a new major version
schemas/
  v1.0/            ← schemas starting at v1.0
    bundle.schema.json
    envelope.schema.json
    statement.schema.json
    predicate.schema.json
  v2.0/            ← schemas for v2 (when needed)
```

Directories are named after the **initial minor version** of each
major release (e.g., `v1.0`).  Minor versions (v1.0 → v1.1) evolve
in place within the same directory — they are backward-compatible by
definition (only additive optional fields).  The minor version is
tracked in the schema `$id` URIs and the predicate type URI, and each
release is tagged in git.

A new directory is created only for a **major version** bump, which
signals breaking changes.

## Schema Versioning

The JSON Schemas use **semver** in their `$id` URIs
(e.g., `https://model_signing/predicate/v1.0/schema`).  The version
in the `$id` MUST match the schema directory name and the spec version.

| Change type | Version bump | Example |
|---|---|---|
| New optional field | Minor | v1.0 → v1.1 |
| Field removed or type changed | Major | v1.0 → v2.0 |
| Description / comment only | Patch (no URI change) | — |

When a schema's `$id` version changes, all schemas that reference it via
`$ref` MUST be updated accordingly.

## Bundle Media Type

OMS bundles use the Sigstore bundle media type
(`application/vnd.dev.sigstore.bundle.v0.3+json`).  OMS does **not**
define its own media type — versioning of the outer bundle format is
governed by
[sigstore/protobuf-specs RELEASE.md](https://github.com/sigstore/protobuf-specs/blob/main/RELEASE.md).

The OMS predicate type URI
(`https://model_signing/signature/v1.0`) carries its own version
independently of the bundle media type.

## Predicate Type URI

The predicate type URI is the primary OMS version signal:

```
https://model_signing/signature/v<MAJOR>.<MINOR>
```

| Change type | Effect |
|---|---|
| New optional field in predicate | Minor bump (v1.0 → v1.1); existing verifiers MUST still accept |
| Field removed, renamed, or semantics changed | Major bump (v1.0 → v2.0); new URI |
| New required field in predicate | Major bump |

Verifiers SHOULD accept any predicate type matching their supported
major version.  For example, a verifier supporting `v1.*` MUST accept
both `v1.0` and `v1.1`.

## Spec and Schema Evolution

Each major version directory (`spec/vN.0.md` + `schemas/vN.0/`) is a
self-contained unit.

**Minor version changes** (backward-compatible, additive):

1. Update `spec/vN.0.md` and `schemas/vN.0/` in place.
2. Bump the minor version in schema `$id` URIs and predicate type URI.
3. Update test vectors to cover the new fields.
4. Tag the release (e.g., `v1.1.0`).

**Major version changes** (breaking):

1. Create `spec/vN+1.0.md` and `schemas/vN+1.0/` by copying from `vN.0`.
2. Apply breaking changes to the new files only.
3. The previous major version's files remain frozen.
4. Update test vectors and tag the release (e.g., `v2.0.0`).

**Historical bundles** are tested separately in the conformance
suite's `historical/` category, not by the latest schema.

## Backward Compatibility

1. **Producers** MUST generate bundles using the latest approved
   predicate version.
2. **Verifiers** MUST support the current major version and SHOULD
   support the previous major version for a deprecation period of at
   least 6 months after a new major version is approved.
3. **Schema changes** MUST NOT remove or rename required fields within
   the same major version.
4. **Test vectors** in `test-vectors/vN.0/` MUST be updated whenever
   the schema changes.
5. **Verifier relaxations** for older bundles are documented in
   the spec (§11.1) and tested by the conformance suite's `historical/`
   test category.

## Release Process

1. Propose changes via pull request against `main`.
2. All schema changes MUST include updated test vectors and pass CI
   validation.
3. Spec stage transitions (Draft → Approved) require maintainer
   consensus per `CHARTER.md`.
4. Tag releases using `vMAJOR.MINOR.PATCH` (e.g., `v1.0.0`).
