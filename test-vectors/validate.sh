#!/usr/bin/env bash
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

# Validate test vectors against the OMS schemas using the oms-validate CLI.
#
# Prerequisites: pip install -e .  (or uv pip install --system -e .)
#
# Directory layout:
#   test-vectors/
#     v1.0/
#       valid/            — MUST pass validation
#       invalid/          — MUST be rejected (bundle-level schema failure)
#       invalid-payload/  — MUST be rejected (statement-level schema failure)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
passed=0
failed=0

for version_dir in "$SCRIPT_DIR"/v*/; do
  version="$(basename "$version_dir")"
  echo "=== $version ==="

  if [ -d "$version_dir/valid" ]; then
    echo "valid/"
    for f in "$version_dir"/valid/*.json; do
      [ -f "$f" ] || continue
      name="$(basename "$f")"
      if python -m oms_schemas "$f" --schema-version "$version" > /dev/null 2>&1; then
        echo "  PASS  $name"
        passed=$((passed + 1))
      else
        echo "  FAIL  $name: expected valid bundle to pass"
        failed=$((failed + 1))
      fi
    done
  fi

  if [ -d "$version_dir/invalid" ]; then
    echo "invalid/"
    for f in "$version_dir"/invalid/*.json; do
      [ -f "$f" ] || continue
      name="$(basename "$f")"
      if python -m oms_schemas "$f" --schema-version "$version" > /dev/null 2>&1; then
        echo "  FAIL  $name: expected rejection but validation passed"
        failed=$((failed + 1))
      else
        echo "  PASS  $name (correctly rejected)"
        passed=$((passed + 1))
      fi
    done
  fi

  if [ -d "$version_dir/invalid-payload" ]; then
    echo "invalid-payload/"
    for f in "$version_dir"/invalid-payload/*.json; do
      [ -f "$f" ] || continue
      name="$(basename "$f")"
      if python -m oms_schemas "$f" --schema-version "$version" > /dev/null 2>&1; then
        echo "  FAIL  $name: expected rejection but validation passed"
        failed=$((failed + 1))
      else
        echo "  PASS  $name (correctly rejected)"
        passed=$((passed + 1))
      fi
    done
  fi

  echo ""
done

echo "$passed passed, $failed failed"

if [ "$failed" -gt 0 ]; then
  exit 1
fi
