"""Packaged-app verification; no network connections, GUI, or personal data.

This checks that PyInstaller included the required import graph and template,
using an isolated temporary directory instead of the user's settings directory.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile

from core.desktop_settings import load_config, save_config_atomic
from core.demo import run_demo
from core.preflight import inspect_readiness
from main import validate


def run_selftest(template: Path, demo_dir: Path | None = None) -> None:
    config = load_config(template)
    validate(config, check_only=True)
    if config.get('protocol', {}).get('verified') is not False:
        raise AssertionError('The distributed template must not claim verified endpoints')
    if config.get('strategy', {}).get('dry_run') is not True:
        raise AssertionError('The distributed template must remain read-only')
    with tempfile.TemporaryDirectory(prefix='kingo-selftest-') as directory:
        destination = Path(directory) / 'config.yaml'
        test_config = deepcopy(config)
        test_config.setdefault('auth', {})['cookie'] = 'DO_NOT_PERSIST_TEST_COOKIE'
        save_config_atomic(destination, test_config)
        saved = load_config(destination)
        if saved.get('auth', {}).get('cookie'):
            raise AssertionError('Local configuration saved a credential')
        if not inspect_readiness(saved, env={}):
            raise AssertionError('Offline preflight not available')

    # A PyInstaller one-file build must bundle its complete fictional demo.
    if demo_dir is not None:
        demo = run_demo(demo_dir)
        if len(demo.steps) != 3 or demo.steps[1].newly_available != ("target-B",):
            raise AssertionError("Packaged fictional demo is missing or invalid")
