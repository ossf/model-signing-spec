# Copyright 2026 The OpenSSF Authors
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

"""Check the closure predicate test vectors (spec/closure/v0.1.md).

Statements in valid/ and invalid/ are checked against the closure schemas and
the rules in Section 3. manifest-digest.json, binding.json and
fetch-check.json are recomputed with the reference functions below
(Sections 4, 6.2 and 6.3).

Usage:
    python test-vectors/closure/v0.1/check.py
"""

from __future__ import annotations

import hashlib
import json
import struct
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

HERE = Path(__file__).resolve().parent
SCHEMAS = HERE.parents[2] / "schemas"

MANIFEST_PREFIX = b"oms-manifest/v1\x00"


def manifest_digest(resources: list[dict[str, Any]]) -> str:
    """Manifest digest of OMS resource descriptors (Section 4)."""
    names = [r["name"] for r in resources]
    if len(set(names)) != len(names):
        raise ValueError("duplicate resource name")
    data = bytearray(MANIFEST_PREFIX)
    # Python orders str by Unicode code point, as Section 4 requires.
    for r in sorted(resources, key=lambda r: r["name"]):
        for field in (
            r["name"].encode("utf-8"),
            r["algorithm"].encode("utf-8"),
            bytes.fromhex(r["digest"]),
        ):
            data += struct.pack(">Q", len(field)) + field
    return hashlib.sha256(data).hexdigest()


def oms_root_digest(resources: list[dict[str, Any]]) -> str:
    """OMS root digest (spec/v1.0.md Section 6.5.1), for comparison only."""
    ordered = sorted(resources, key=lambda r: r["name"])
    return hashlib.sha256(
        b"".join(bytes.fromhex(r["digest"]) for r in ordered)
    ).hexdigest()


def check_binding(model_statement: dict[str, Any], closure: dict[str, Any]) -> str:
    """Model binding (Section 6.2) for an already verified OMS statement."""
    expected = closure["subject"][0]["digest"]["omsManifest"]
    actual = manifest_digest(model_statement["predicate"]["resources"])
    return "established" if actual == expected else "not established"


def check_fetch(closure: dict[str, Any], uri: str, path: str, data: bytes) -> str:
    """Fetch check (Section 6.3) for one fetched file."""
    for dependency in closure["predicate"]["dependencies"]:
        if dependency["uri"] != uri:
            continue
        for resource in dependency["resources"]:
            if resource["name"] == path:
                digest = hashlib.new(resource["algorithm"], data).hexdigest()
                return "match" if digest == resource["digest"] else "mismatch"
        return "undeclared"
    return "undeclared"


def statement_errors(statement: Any, validator: Draft202012Validator) -> list[str]:
    """Schema errors plus the Section 3 rules a schema cannot express."""
    errors = [e.message for e in validator.iter_errors(statement)]
    if errors:
        return errors
    dependencies = statement["predicate"]["dependencies"]
    uris = [d["uri"] for d in dependencies]
    if len(set(uris)) != len(uris):
        errors.append("duplicate dependency uri")
    for dependency in dependencies:
        names = [r["name"] for r in dependency["resources"]]
        if len(set(names)) != len(names):
            errors.append(f"duplicate resource name in {dependency['uri']}")
    return errors


def statement_validator() -> Draft202012Validator:
    resources = []
    for path in [*SCHEMAS.glob("v1.0/*.json"), *SCHEMAS.glob("closure/v0.1/*.json")]:
        schema = json.loads(path.read_text())
        resources.append(
            (schema["$id"], Resource.from_contents(schema, default_specification=DRAFT202012))
        )
    registry = Registry().with_resources(resources)
    root = json.loads((SCHEMAS / "closure/v0.1/statement.schema.json").read_text())
    return Draft202012Validator(root, registry=registry)


def load(name: str) -> Any:
    return json.loads((HERE / name).read_text())


def main() -> int:
    results: list[tuple[bool, str]] = []
    validator = statement_validator()

    for folder, want_valid in (("valid", True), ("invalid", False)):
        for path in sorted((HERE / folder).glob("*.json")):
            errors = statement_errors(json.loads(path.read_text()), validator)
            ok = not errors if want_valid else bool(errors)
            detail = "" if ok else (errors[0] if errors else "accepted")
            results.append((ok, f"{folder}/{path.name} {detail}".rstrip()))

    for case in load("manifest-digest.json")["cases"]:
        try:
            got = manifest_digest(case["resources"])
        except ValueError as e:
            got = f"error: {e}"
        want = case.get("omsManifest") or f"error: {case['error']}"
        ok = got == want
        if ok and "oms_root_sha256" in case:
            ok = oms_root_digest(case["resources"]) == case["oms_root_sha256"]
        results.append((ok, f"manifest-digest {case['id']}"))

    for case in load("binding.json")["cases"]:
        got = check_binding(case["model_statement"], load(case["closure_statement"]))
        results.append((got == case["expected"], f"binding {case['id']}: {got}"))

    for case in load("fetch-check.json")["cases"]:
        got = check_fetch(
            load(case["closure_statement"]),
            case["uri"],
            case["path"],
            case["content"].encode("utf-8"),
        )
        results.append((got == case["expected"], f"fetch-check {case['id']}: {got}"))

    for ok, label in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    failed = sum(not ok for ok, _ in results)
    print(f"{len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
