# Copyright 2024 The OpenSSF Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""OMS JSON Schemas and validation for OpenSSF Model Signing bundles.

This package ships the normative JSON Schemas from the OMS specification
and provides ``validate_bundle()`` — the single entry point for
validating an OMS bundle against the spec.

Usage::

    from pathlib import Path
    from oms_schemas import validate_bundle

    validate_bundle(Path("model.sig"), method="key")
"""

from __future__ import annotations

import base64
import json
import warnings
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

PACKAGE_DIR: Path = Path(__file__).parent
"""Root directory of the oms_schemas package."""

DEFAULT_SCHEMA_VERSION = "v1.0"

DEPRECATED_PREDICATE_TYPE = "https://model_signing/Digests/v0.1"

_validator_cache: dict[str, tuple[Draft202012Validator, Draft202012Validator]] = {}


def _init_validators(
    schema_version: str | None = None,
) -> tuple[Draft202012Validator, Draft202012Validator]:
    """Load schemas and compile validators for a given schema version.

    Args:
        schema_version: Schema directory name (e.g., ``"v1.0"``).
            Defaults to :data:`DEFAULT_SCHEMA_VERSION`.

    Returns:
        A (bundle_validator, statement_validator) tuple.

    Raises:
        FileNotFoundError: If the schema version directory does not exist.
    """
    version = schema_version or DEFAULT_SCHEMA_VERSION
    if version in _validator_cache:
        return _validator_cache[version]

    schema_dir = PACKAGE_DIR / version
    if not schema_dir.is_dir():
        raise FileNotFoundError(
            f"Schema version directory not found: {schema_dir}"
        )

    schemas: dict[str, Any] = {}
    for f in schema_dir.glob("*.json"):
        schemas[f.name] = json.loads(f.read_text())

    pairs = [
        (s["$id"], Resource.from_contents(s, default_specification=DRAFT202012))
        for s in schemas.values()
    ]
    registry = Registry().with_resources(pairs)

    bundle_v = Draft202012Validator(
        schemas["bundle.schema.json"], registry=registry
    )
    statement_v = Draft202012Validator(
        schemas["statement.schema.json"], registry=registry
    )
    _validator_cache[version] = (bundle_v, statement_v)
    return bundle_v, statement_v


def decode_payload(bundle: dict[str, Any]) -> dict[str, Any]:
    """Decode the Base64-encoded DSSE payload into an in-toto Statement."""
    raw = bundle["dsseEnvelope"]["payload"]
    padded = raw + "=" * (-len(raw) % 4)
    return json.loads(base64.b64decode(padded))


def validate_bundle(
    bundle_path: Path,
    method: str | None = None,
    schema_version: str | None = None,
) -> None:
    """Validate an OMS bundle file against the spec schemas.

    Two-level validation:
      Level 1: ``bundle.schema.json`` validates the outer Sigstore bundle
      Level 2: ``statement.schema.json`` validates the decoded DSSE payload

    After schema validation, method-specific structural checks are applied
    to verify the ``verificationMaterial`` matches the signing method.

    Args:
        bundle_path: Path to the bundle JSON file.
        method: Signing method (``key``, ``certificate``, ``sigstore``).
            When provided, verifies that ``verificationMaterial`` contains
            the correct fields for that method.
        schema_version: Schema version to validate against (e.g., ``"v1.0"``).
            Defaults to the current version.

    Raises:
        jsonschema.ValidationError: If the bundle fails schema validation.
        json.JSONDecodeError: If the bundle is not valid JSON.
        AssertionError: If method-specific structural checks fail.
    """
    bundle_v, statement_v = _init_validators(schema_version)

    raw = bundle_path.read_text()
    bundle = json.loads(raw)

    bundle_v.validate(bundle)

    statement = decode_payload(bundle)
    if statement.get("predicateType") == DEPRECATED_PREDICATE_TYPE:
        warnings.warn(
            f"Bundle uses deprecated predicateType {DEPRECATED_PREDICATE_TYPE!r}; "
            f"skipping statement-level schema validation (see spec/v1.0.md §11)",
            stacklevel=2,
        )
    else:
        statement_v.validate(statement)

    if method:
        vm = bundle.get("verificationMaterial", {})
        validate_method_fields(vm, method)


def validate_method_fields(vm: dict[str, Any], method: str) -> None:
    """Assert verificationMaterial fields match the declared signing method.

    The bundle schema uses ``anyOf`` to accept all three method variants,
    but cannot enforce that a ``key``-method bundle uses ``publicKey``
    rather than ``x509CertificateChain``.  This function checks that
    the correct branch was used.

    Args:
        vm: The ``verificationMaterial`` object from the bundle.
        method: One of ``key``, ``certificate``, ``sigstore``.

    Raises:
        AssertionError: If the fields do not match the method.
    """
    if method == "key":
        assert "publicKey" in vm, (
            "key-method bundle must contain verificationMaterial.publicKey"
        )
        assert "x509CertificateChain" not in vm, (
            "key-method bundle must not contain x509CertificateChain"
        )
        assert "certificate" not in vm, (
            "key-method bundle must not contain certificate (sigstore field)"
        )

    elif method == "certificate":
        assert "x509CertificateChain" in vm, (
            "certificate-method bundle must contain "
            "verificationMaterial.x509CertificateChain"
        )
        assert "certificate" not in vm, (
            "certificate-method bundle must not contain certificate "
            "(that is the sigstore field)"
        )

    elif method == "sigstore":
        assert "certificate" in vm, (
            "sigstore-method bundle must contain "
            "verificationMaterial.certificate (Fulcio cert)"
        )
        assert "tlogEntries" in vm, (
            "sigstore-method bundle must contain tlogEntries"
        )
        tlog = vm["tlogEntries"]
        assert isinstance(tlog, list) and len(tlog) >= 1, (
            "sigstore-method bundle must have at least one tlogEntry"
        )
