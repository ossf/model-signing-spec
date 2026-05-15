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

# Algorithm Registry

This file defines the cryptographic algorithms supported by OMS.

Changes to this file MUST be reflected in the conformance test suite
and in the `algorithm` / `hash_type` fields of the OMS predicate schema
([`schemas/v1.0/predicate.schema.json`](./schemas/v1.0/predicate.schema.json)).

OMS relies on the [Sigstore Algorithm Registry](https://github.com/sigstore/architecture-docs/blob/main/algorithm-registry.md)
for the signature algorithms used in the DSSE envelope.  This document
covers only the algorithms OMS uses for **file content hashing** (the
`digest` field in resource descriptors) and **key types** for the `key`
and `certificate` signing methods.

## File Hashing Algorithms

These algorithms are used to compute per-file digests in
`predicate.resources[].digest` and recorded in
`predicate.serialization.hash_type`.

| Identifier | Algorithm | Output size | Status |
|---|---|---|---|
| `sha256` | SHA-256 ([FIPS 180-4](https://csrc.nist.gov/pubs/fips/180-4/upd1/final)) | 256 bits | REQUIRED (default) |
| `blake2b` | BLAKE2b ([RFC 7693](https://www.rfc-editor.org/rfc/rfc7693)) | 512 bits | OPTIONAL |
| `blake3` | BLAKE3 ([blake3.io](https://blake3.io), [spec](https://github.com/BLAKE3-team/BLAKE3-specs/blob/master/blake3.pdf)) | 256 bits | OPTIONAL |

Implementations MUST support `sha256`.  Implementations MAY support
`blake2b` and `blake3`.

## Signing Key Types

For the `key` and `certificate` signing methods, the following elliptic
curve key types MUST be supported:

| Curve | OID | Reference |
|---|---|---|
| P-256 (secp256r1) | 1.2.840.10045.3.1.7 | [FIPS 186-5](https://csrc.nist.gov/pubs/fips/186-5/final) |
| P-384 (secp384r1) | 1.3.132.0.34 | [FIPS 186-5](https://csrc.nist.gov/pubs/fips/186-5/final) |
| P-521 (secp521r1) | 1.3.132.0.35 | [FIPS 186-5](https://csrc.nist.gov/pubs/fips/186-5/final) |

For the `sigstore` signing method, the key type is determined by the
Fulcio instance and follows the
[Sigstore Algorithm Registry](https://github.com/sigstore/architecture-docs/blob/main/algorithm-registry.md).

## History

This registry was extracted from `spec/v1.0.md` Section 4.2 and Section 7
to maintain algorithm choices in a separate, independently versioned
document.
