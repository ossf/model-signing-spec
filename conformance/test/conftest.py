"""Pytest configuration and shared fixtures for model-signing conformance tests."""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
from pathlib import Path

import pytest

from .client import CaseConfig, ModelSigningClient
from .config_loader import ConformanceSuite, load_suite

logger = logging.getLogger(__name__)

ASSETS = Path(__file__).parent / "assets"
TEST_CASES = Path(__file__).parent / "test-cases"
CONFIG_DIR = Path(__file__).parent.parent / "config"

_GENERATOR_SCRIPTS = [
    ASSETS / "generate_keys.py",
    ASSETS / "generate_models.py",
    ASSETS / "generate_bundles.py",
]
_YAML_CONFIG_FILES = [
    CONFIG_DIR / "index.yaml",
    CONFIG_DIR / "models.yaml",
    CONFIG_DIR / "keys.yaml",
    CONFIG_DIR / "fixtures.yaml",
    CONFIG_DIR / "bundle-transforms.yaml",
    TEST_CASES / "roundtrip.yaml",
    TEST_CASES / "historical.yaml",
    TEST_CASES / "policy-positive.yaml",
    TEST_CASES / "policy-negative.yaml",
]
_STAMP_FILE = ASSETS / ".generated"


def _compute_generators_fingerprint() -> str:
    """Hash generator scripts and YAML config files for cache invalidation."""
    h = hashlib.sha256()
    for path in sorted(_GENERATOR_SCRIPTS + _YAML_CONFIG_FILES):
        if path.exists():
            h.update(path.read_bytes())
    return h.hexdigest()


def _validate_assets(assets_dir: Path) -> None:
    required = [
        assets_dir / "keys" / "certificate" / "ca-cert.pem",
        assets_dir / "keys" / "p256" / "signing-key.pem",
        assets_dir / "models" / "simple" / "signme-1",
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise FileNotFoundError(
            "Asset generation incomplete -- missing critical files:\n"
            + "\n".join(f"  - {m}" for m in missing)
        )


from .config_loader import _CATEGORY_TO_VERIFY_DIR as _VERIFY_DIR_MAP


def _load_suite_cached() -> ConformanceSuite:
    """Load the suite once per process (module-level cache)."""
    global _CACHED_SUITE
    if _CACHED_SUITE is None:
        _CACHED_SUITE = load_suite(CONFIG_DIR, TEST_CASES)
    return _CACHED_SUITE


_CACHED_SUITE: ConformanceSuite | None = None


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--entrypoint",
        required=True,
        help="Path to the conformance adapter binary/script",
    )
    parser.addoption(
        "--skip-signing",
        action="store_true",
        default=False,
        help="Skip sign+verify roundtrip tests (verify-only tests still run)",
    )
    parser.addoption(
        "--skip-sigstore",
        action="store_true",
        default=False,
        help="Skip all sigstore method tests",
    )
    parser.addoption(
        "--xfail",
        default="",
        help="Newline- or comma-separated list of test IDs to mark as xfail",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "signing: test requires signing capability"
    )


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Skip signing tests when --skip-signing is passed."""
    if item.get_closest_marker("signing"):
        if item.config.getoption("--skip-signing"):
            pytest.skip("Skipping sign+verify test (--skip-signing)")

    xfail_list_raw = item.config.getoption("--xfail")
    if xfail_list_raw:
        xfail_ids = [x.strip() for x in re.split(r'[\n,]', xfail_list_raw) if x.strip()]
        for xfail_id in xfail_ids:
            if xfail_id in item.nodeid or item.name.startswith(xfail_id):
                item.add_marker(
                    pytest.mark.xfail(reason=f"Known xfail: {xfail_id}", strict=False)
                )
                break


@pytest.fixture(scope="session", autouse=True)
def generate_test_assets(request: pytest.FixtureRequest) -> None:
    """Regenerate test assets when config or generators change."""
    suite = _load_suite_cached()

    fingerprint = _compute_generators_fingerprint()
    if _STAMP_FILE.exists() and _STAMP_FILE.read_text().strip() == fingerprint:
        logger.info("Asset generators unchanged -- skipping regeneration")
        _validate_assets(ASSETS)
        return

    logger.info("Regenerating test assets (generators changed or first run)...")

    from .assets.generate_keys import generate_all_keys
    generate_all_keys(ASSETS, keys_manifest=suite.keys)

    from .assets.generate_models import generate_all_models
    generate_all_models(ASSETS, suite.models)

    from .assets.download_fixtures import download_fixtures
    download_fixtures(TEST_CASES, suite.fixtures)

    entrypoint: str | None = request.config.getoption("--entrypoint")
    test_cases_verify = TEST_CASES / "verify"
    positive_present = (
        test_cases_verify / "positive" / "key-simple" / "bundle.sig"
    ).exists()
    negative_present = (
        test_cases_verify / "negative" / "key-simple-wrong-key_fail" / "bundle.sig"
    ).exists()
    bundles_missing = not (positive_present and negative_present)

    generation_ok = True
    if bundles_missing and entrypoint is not None:
        ep_path = Path(entrypoint)
        ep_found = ep_path.exists() or shutil.which(entrypoint) is not None
        if ep_found:
            try:
                from .assets.generate_bundles import generate_all_bundles
                generate_all_bundles(
                    ASSETS, test_cases_verify, entrypoint,
                    suite=suite,
                )
            except Exception:
                generation_ok = False
                logger.warning(
                    "Bundle generation failed -- verify tests may fail",
                    exc_info=True,
                )
        else:
            logger.warning("Entrypoint %r not found -- skipping bundle generation", entrypoint)
    elif bundles_missing:
        logger.warning("No entrypoint provided -- skipping bundle generation")
    else:
        logger.info("Bundles already present -- skipping bundle generation")

    if generation_ok:
        _STAMP_FILE.write_text(fingerprint + "\n")
    _validate_assets(ASSETS)


@pytest.fixture
def client(request: pytest.FixtureRequest) -> ModelSigningClient:
    entrypoint = request.config.getoption("--entrypoint")
    assert isinstance(entrypoint, str), "--entrypoint is required"
    return ModelSigningClient(entrypoint=entrypoint)


def _resolve_entry(
    suite: ConformanceSuite, category_name: str, entry: dict,
) -> CaseConfig:
    """Resolve a single test entry from a YAML category into a CaseConfig."""
    cat_defaults = suite.defaults(category_name)
    method = entry["method"]
    method_defs = suite.method_defaults(category_name, method)
    return CaseConfig.from_suite_entry(entry, cat_defaults, method_defs)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrize verify_cfg and roundtrip_cfg from YAML config files."""
    suite = _load_suite_cached()

    if "roundtrip_cfg" in metafunc.fixturenames:
        cfgs: list[CaseConfig] = []
        ids: list[str] = []
        for entry in suite.tests("roundtrip"):
            cfg = _resolve_entry(suite, "roundtrip", entry)
            cfgs.append(cfg)
            ids.append(f"{cfg.id} | {cfg.description}")
        metafunc.parametrize("roundtrip_cfg", cfgs, ids=ids)

    if "verify_cfg" in metafunc.fixturenames:
        items: list[CaseConfig] = []
        ids_v: list[str] = []
        for suite_cat, dir_name in _VERIFY_DIR_MAP.items():
            try:
                tests = suite.tests(suite_cat)
            except KeyError:
                continue
            for entry in tests:
                cfg = _resolve_entry(suite, suite_cat, entry)
                items.append(cfg)
                ids_v.append(f"{dir_name}/{cfg.id} | {cfg.description}")
        metafunc.parametrize("verify_cfg", items, ids=ids_v)
