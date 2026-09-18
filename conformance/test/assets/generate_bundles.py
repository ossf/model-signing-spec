"""Generate regenerable bundle.sig files from test suite configuration.

This is a **config-driven engine**: mutation and handcraft transforms are
declared in ``bundle-transforms.yaml`` and loaded by ``config_loader`` into
``suite.mutations`` / ``suite.handcrafts``.  The Python code here is a
generic interpreter -- no per-mutation or per-handcraft functions exist.

Reads test entries (pre-loaded from YAML by ``config_loader``), finds all
tests with a ``bundle_source`` field, and dispatches to the appropriate
generation strategy:

- **sign**      -- invoke the adapter entrypoint to sign a model.
- **copy**      -- copy a bundle from another test's output.
- **mutate**    -- load a bundle, apply a config-driven JSON transform.
- **handcraft** -- create directly from a config-driven spec.

Tests without ``bundle_source`` (or with ``"type": "committed"``) are skipped
-- their bundles are checked into the repository.

Public API::

    generate_all_bundles(assets_dir, test_cases_dir, entrypoint, suite)

The module can also be run standalone::

    python generate_bundles.py <entrypoint> [--config-dir PATH]
"""

from __future__ import annotations

import base64
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# Low-level signing helper
# ---------------------------------------------------------------------------

def _sign(
    entrypoint: str,
    method: str,
    model_path: Path,
    output_bundle: Path,
    private_key: Path,
    signing_cert: Optional[Path] = None,
    cert_chain: Optional[List[Path]] = None,
    ignore_paths: Optional[List[Path]] = None,
) -> None:
    """Sign a model by invoking the adapter entrypoint.

    Raises ``RuntimeError`` if the adapter exits with a non-zero return code.
    """
    args: list[str] = [
        entrypoint,
        "sign-model",
        "--method", method,
        "--model-path", str(model_path),
        "--output-bundle", str(output_bundle),
        "--private-key", str(private_key),
    ]
    if signing_cert is not None:
        args += ["--signing-cert", str(signing_cert)]
    for cert in (cert_chain or []):
        args += ["--cert-chain", str(cert)]
    for p in (ignore_paths or []):
        args += ["--ignore-paths", str(p)]

    output_bundle.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(args, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        safe_args = []
        skip_next = False
        for a in args:
            if skip_next:
                safe_args.append("[REDACTED]")
                skip_next = False
            elif a == "--identity-token":
                safe_args.append(a)
                skip_next = True
            else:
                safe_args.append(a)
        raise RuntimeError(
            f"Signing failed for {output_bundle.name}:\n"
            f"  command: {' '.join(safe_args)}\n"
            f"  stdout:  {result.stdout.strip()}\n"
            f"  stderr:  {result.stderr.strip()}"
        )
    logger.info("  signed -> %s", output_bundle)


# ---------------------------------------------------------------------------
# Bundle payload helpers
# ---------------------------------------------------------------------------

def _decode_payload(bundle: dict) -> dict:
    """Decode the base64-encoded DSSE payload from a bundle dict."""
    payload_b64 = bundle["dsseEnvelope"]["payload"]
    return json.loads(base64.b64decode(payload_b64))


def _encode_payload(bundle: dict, payload: dict) -> None:
    """Base64-encode *payload* and set it back in the bundle dict."""
    encoded = base64.b64encode(
        json.dumps(payload, indent=2).encode()
    ).decode()
    bundle["dsseEnvelope"]["payload"] = encoded


# ---------------------------------------------------------------------------
# Config-driven transform engine
# ---------------------------------------------------------------------------
# Mutations and handcrafts are declared in bundle-transforms.yaml and loaded
# by config_loader into suite.mutations / suite.handcrafts.  The functions
# below interpret those specs generically — no per-mutation Python code.

def _navigate_path(data: dict, dotted_path: str) -> tuple[dict, str]:
    """Navigate a dotted path and return ``(parent_dict, final_key)``.

    Example::

        >>> d = {"a": {"b": {"c": 1}}}
        >>> _navigate_path(d, "a.b.c")
        ({"c": 1}, "c")
    """
    parts = dotted_path.split(".")
    current = data
    for part in parts[:-1]:
        current = current[part]
    return current, parts[-1]


def _apply_transform(bundle: dict, transform: dict) -> None:
    """Apply a config-driven mutation transform to a bundle dict.

    Supported operations:

    - ``set``: Navigate *path*, assign *value*.
    - ``delete``: Navigate *path*, remove the key.

    If ``target`` is ``"payload"``, the DSSE payload is base64-decoded
    before the operation and re-encoded afterward.
    """
    operation = transform["operation"]
    target = transform.get("target", "envelope")
    path = transform["path"]

    if target == "payload":
        payload = _decode_payload(bundle)
        parent, key = _navigate_path(payload, path)
        if operation == "set":
            parent[key] = transform["value"]
        elif operation == "delete":
            parent.pop(key, None)
        else:
            raise ValueError(f"Unknown mutation operation: {operation!r}")
        _encode_payload(bundle, payload)
    else:
        parent, key = _navigate_path(bundle, path)
        if operation == "set":
            parent[key] = transform["value"]
        elif operation == "delete":
            parent.pop(key, None)
        else:
            raise ValueError(f"Unknown mutation operation: {operation!r}")


def _apply_handcraft(
    dest: Path,
    transform: dict,
    source_bundle: Optional[Path] = None,
) -> None:
    """Create a bundle file from a handcraft transform spec.

    Supported specs:

    - ``content_type: empty`` -- zero-byte file.
    - ``content_type: json`` -- serialize ``content`` dict as JSON.
    - ``content: "..."`` -- write string content directly.
    - ``operation: truncate`` -- take first *bytes* from *source_bundle*.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    content_type = transform.get("content_type")

    if content_type == "empty":
        dest.write_bytes(b"")
    elif transform.get("operation") == "truncate":
        if source_bundle is None:
            raise ValueError("truncate handcraft requires source_bundle")
        n = transform.get("bytes", 100)
        dest.write_bytes(source_bundle.read_bytes()[:n])
    elif content_type == "json":
        dest.write_text(json.dumps(transform["content"]))
    elif "content" in transform:
        dest.write_text(transform["content"])
    else:
        raise ValueError(f"Unknown handcraft spec: {transform}")
    logger.info("  created -> %s (%s)", dest, transform.get("description", "handcraft"))


# ---------------------------------------------------------------------------
# Bundle file operations
# ---------------------------------------------------------------------------

def _copy_bundle(src: Path, dest: Path) -> None:
    """Copy a bundle file, creating parent dirs as needed."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    logger.info("  copied  -> %s", dest)


def _mutate_bundle(src: Path, dest: Path, transform: dict) -> None:
    """Load a bundle from *src*, apply a config-driven *transform*, write to *dest*."""
    bundle = json.loads(src.read_text())
    _apply_transform(bundle, transform)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(bundle))
    logger.info("  mutated -> %s", dest)


# ---------------------------------------------------------------------------
# Sign-type dispatch
# ---------------------------------------------------------------------------

def _generate_sign_bundle(
    dest: Path,
    entry: dict,
    source: dict,
    assets_dir: Path,
    roundtrip_method_defaults: dict,
    entrypoint: str,
    key_roles: dict | None = None,
) -> None:
    """Generate a bundle by signing a model via the adapter entrypoint."""
    method = entry["method"]

    # Resolve signing parameters:
    # 1. bundle_source.key_group -> resolve from key_roles lookup
    # 2. bundle_source.sign -> explicit override (legacy)
    # 3. roundtrip method_defaults -> fallback
    sign_params = source.get("sign")
    if sign_params is None:
        bs_key_group = source.get("key_group")
        if bs_key_group and key_roles:
            roles = key_roles.get(bs_key_group, {})
            sign_params = {}
            if "sign_private_key" in roles:
                sign_params["private_key"] = roles["sign_private_key"]
            if "sign_cert" in roles:
                sign_params["signing_cert"] = roles["sign_cert"]
            if "sign_cert_chain" in roles:
                sign_params["cert_chain"] = roles["sign_cert_chain"]
        if not sign_params:
            sign_params = roundtrip_method_defaults.get(method, {}).get("sign", {})

    model_rel = entry["model"]
    model_path = assets_dir / "models" / model_rel

    verify_block = entry.get("verify", {})
    ignore_paths_rel = verify_block.get("ignore_paths", [])

    # If expected_signed_files is set, copy only those files to a temp dir
    # (used when signing a subset, e.g., expired cert signs without ignore-me)
    signed_files = entry.get("expected_signed_files")
    if signed_files and source.get("key_group"):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            for filename in signed_files:
                src_file = model_path / filename
                dst_file = tmp / filename
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst_file)
            _do_sign(entrypoint, method, tmp, dest, sign_params, assets_dir, [])
    else:
        ignore_abs = [model_path / p for p in ignore_paths_rel]
        _do_sign(
            entrypoint, method, model_path, dest,
            sign_params, assets_dir, ignore_abs,
        )


def _do_sign(
    entrypoint: str,
    method: str,
    model_path: Path,
    output_bundle: Path,
    sign_params: dict,
    assets_dir: Path,
    ignore_paths: list[Path],
) -> None:
    """Resolve sign_params paths and invoke _sign."""
    private_key = assets_dir / sign_params["private_key"]
    signing_cert = (
        assets_dir / sign_params["signing_cert"]
        if "signing_cert" in sign_params
        else None
    )
    cert_chain = [
        assets_dir / c for c in sign_params.get("cert_chain", [])
    ] or None

    _sign(
        entrypoint=entrypoint,
        method=method,
        model_path=model_path,
        output_bundle=output_bundle,
        private_key=private_key,
        signing_cert=signing_cert,
        cert_chain=cert_chain,
        ignore_paths=ignore_paths or None,
    )


# ---------------------------------------------------------------------------
# Top-level generation
# ---------------------------------------------------------------------------

# Processing order: sign first (others may reference them), then copy,
# mutate, handcraft.
_TYPE_ORDER: dict[str, int] = {
    "sign": 0,
    "copy": 1,
    "mutate": 2,
    "handcraft": 3,
}


def _resolve_source_bundle(
    source: dict,
    test_dir_lookup: dict[str, str],
    test_cases_dir: Path,
) -> Path:
    """Resolve a source_test reference to a bundle.sig path."""
    source_test = source.get("source_test")
    if source_test:
        source_dir = test_dir_lookup.get(source_test)
        if source_dir is None:
            raise ValueError(
                f"source_test {source_test!r} not found in test_dir_lookup"
            )
        return test_cases_dir / source_dir / "bundle.sig"
    source_dir_raw = source.get("source_test_dir")
    if source_dir_raw:
        return test_cases_dir / source_dir_raw / "bundle.sig"
    raise ValueError("bundle_source has neither source_test nor source_test_dir")


def generate_all_bundles(
    assets_dir: Path,
    test_cases_dir: Path,
    entrypoint: str,
    suite: Any,
) -> None:
    """Generate all regenerable bundle.sig files.

    Args:
        assets_dir: Absolute path to ``conformance/test/assets/``.
        test_cases_dir: Absolute path to ``conformance/test/test-cases/verify/``.
        entrypoint: Path (or command name) for the conformance adapter.
        suite: A ConformanceSuite object from config_loader.
    """
    assets_dir = assets_dir.resolve()
    test_cases_dir = test_cases_dir.resolve()

    # Verify the entrypoint is usable.
    ep = Path(entrypoint)
    if not ep.is_absolute():
        ep = Path.cwd() / ep
    if not ep.exists() and not shutil.which(entrypoint):
        logger.warning(
            "Entrypoint not found: %s -- skipping bundle generation",
            entrypoint,
        )
        return

    # Get roundtrip method defaults from suite
    roundtrip_method_defaults = suite.categories.get(  # type: ignore[union-attr]
        "roundtrip", {},
    ).get("method_defaults", {})

    # Collect all tests with bundle_source from verify categories
    entries: list[dict] = []
    for cat_name in ("policy-positive", "policy-negative"):
        try:
            tests = suite.tests(cat_name)  # type: ignore[union-attr]
        except KeyError:
            continue
        for entry in tests:
            source = entry.get("bundle_source")
            if source is None:
                continue
            if source.get("type") == "committed":
                continue
            entries.append(entry)

    # Build lookup: test ID -> test_dir (for source_test resolution)
    test_dir_lookup: dict[str, str] = {}
    for cat_name in ("policy-positive", "policy-negative"):
        try:
            tests = suite.tests(cat_name)  # type: ignore[union-attr]
        except KeyError:
            continue
        for t in tests:
            if "test_dir" in t:
                test_dir_lookup[t["id"]] = t["test_dir"]

    key_roles = getattr(suite, "key_roles", {})

    # Load config-driven transform specs from the suite
    mutations = getattr(suite, "mutations", {})
    handcrafts = getattr(suite, "handcrafts", {})

    # Sort by type priority so sign-based bundles are created first
    entries.sort(key=lambda e: _TYPE_ORDER.get(
        e["bundle_source"]["type"], 99,
    ))

    # Process each entry — catch per-entry errors so one failure (e.g. a
    # client that correctly rejects expired certs) doesn't prevent generation
    # of independent bundles like copy/mutate/handcraft.
    failed: list[str] = []
    for entry in entries:
        source = entry["bundle_source"]
        test_dir = entry["test_dir"]
        dest = test_cases_dir / test_dir / "bundle.sig"
        source_type = source["type"]

        try:
            if source_type == "sign":
                logger.info("Signing bundle for %s ...", test_dir)
                _generate_sign_bundle(
                    dest, entry, source,
                    assets_dir, roundtrip_method_defaults, entrypoint,
                    key_roles=key_roles,
                )

            elif source_type == "copy":
                src_bundle = _resolve_source_bundle(source, test_dir_lookup, test_cases_dir)
                _copy_bundle(src_bundle, dest)

            elif source_type == "mutate":
                src_bundle = _resolve_source_bundle(source, test_dir_lookup, test_cases_dir)
                mutation_name = source["mutation"]
                if mutation_name not in mutations:
                    raise ValueError(
                        f"Unknown mutation {mutation_name!r} for {test_dir}. "
                        f"Available: {sorted(mutations.keys())}"
                    )
                _mutate_bundle(src_bundle, dest, mutations[mutation_name])

            elif source_type == "handcraft":
                variant = source["variant"]
                if variant not in handcrafts:
                    raise ValueError(
                        f"Unknown handcraft variant {variant!r} for {test_dir}. "
                        f"Available: {sorted(handcrafts.keys())}"
                    )
                source_bundle = None
                source_test = source.get("source_test")
                if source_test:
                    source_bundle = _resolve_source_bundle(
                        source, test_dir_lookup, test_cases_dir,
                    )
                # Also check source_test_dir for backward compatibility
                if source_bundle is None and source.get("source_test_dir"):
                    source_bundle = _resolve_source_bundle(
                        source, test_dir_lookup, test_cases_dir,
                    )
                _apply_handcraft(dest, handcrafts[variant], source_bundle=source_bundle)

            else:
                logger.warning(
                    "Unknown bundle_source type %r for %s -- skipping",
                    source_type, test_dir,
                )
        except Exception:
            failed.append(test_dir)
            logger.warning("Failed to generate bundle for %s", test_dir, exc_info=True)

    if failed:
        logger.warning(
            "Bundle generation failed for %d entries: %s",
            len(failed), ", ".join(failed),
        )

    logger.info("Bundle generation complete.")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    here = Path(__file__).resolve().parent
    assets = here
    test_cases = here.parent / "test-cases" / "verify"

    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <entrypoint> [--config-dir PATH]", file=sys.stderr)
        print(
            "  <entrypoint>  Path to the conformance adapter "
            "(e.g., ./selftest-client)",
            file=sys.stderr,
        )
        sys.exit(1)

    ep = sys.argv[1]

    config_dir = None
    for i, arg in enumerate(sys.argv):
        if arg == "--config-dir" and i + 1 < len(sys.argv):
            config_dir = Path(sys.argv[i + 1])

    if config_dir is None:
        config_dir = here.parent.parent.parent / "config"

    from conformance.test.config_loader import load_suite
    test_cases_parent = here.parent / "test-cases"
    suite = load_suite(config_dir, test_cases_parent)

    generate_all_bundles(assets, test_cases, entrypoint=ep, suite=suite)
