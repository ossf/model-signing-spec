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

"""CLI entry point for OMS bundle schema validation.

Usage:
    oms-validate bundle.sig
    oms-validate bundle.sig --method key
    oms-validate bundle.sig --schema-version v1.0
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jsonschema import ValidationError

from oms_schemas import validate_bundle


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="oms-validate",
        description="Validate an OMS bundle against the specification schemas.",
    )
    parser.add_argument(
        "bundle",
        type=Path,
        help="Path to the OMS bundle JSON file.",
    )
    parser.add_argument(
        "--method",
        choices=["key", "certificate", "sigstore"],
        default=None,
        help="Signing method to verify structural constraints for.",
    )
    parser.add_argument(
        "--schema-version",
        default=None,
        help="Schema version to validate against (e.g., 'v1.0'). Defaults to the current version.",
    )
    args = parser.parse_args()

    if not args.bundle.exists():
        print(f"Error: {args.bundle} not found", file=sys.stderr)
        return 1

    try:
        validate_bundle(args.bundle, method=args.method, schema_version=args.schema_version)
    except (ValidationError, json.JSONDecodeError) as e:
        msg = e.message if hasattr(e, "message") else str(e)
        print(f"FAIL: {msg}", file=sys.stderr)
        return 1
    except AssertionError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1

    print(f"OK: {args.bundle} is valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
