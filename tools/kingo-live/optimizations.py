#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility shim — the real implementation now lives in the Windows client.

Single source of truth:
    clients/windows/source/core/selection_optim.py

This module used to hold a second, drifting copy of ClockSync / Prewarmer /
ReleaseMonitor / FailureClassifier / FailoverPlanner. It now re-exports the
client implementation so the research CLI and the shipped client cannot
diverge in behaviour.

Historical note kept from the original file:

    optimizations.py - the high-value layer of the real course-selection client.
    Everything in this module runs on REAL HTTP responses produced by
    live_client.Transport. There is no simulation, no synthetic clock, no fake
    seat ledger.

Python 3.10+ / standard library only.
"""

from __future__ import annotations

import sys
from pathlib import Path

_CLIENT_SOURCE = Path(__file__).resolve().parents[2] / "clients" / "windows" / "source"
if str(_CLIENT_SOURCE) not in sys.path:
    sys.path.insert(0, str(_CLIENT_SOURCE))

from core.selection_optim import *          # noqa: E402,F401,F403
from core.selection_optim import __all__    # noqa: E402,F401
