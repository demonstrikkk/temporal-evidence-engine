"""The rule version is code, not configuration.

`RULE_VERSION` used to be a `Settings` field, and this machine's `.env` pinned
`tev-1.0.0`. The code said `1.1.0` and every verdict the running product stamped
said `1.0.0`, because an env file silently outranked the constant. A label that
can be changed without changing a rule is not a rule version - it is a comment
that can lie, attached to the one field a reviewer trusts to tell them which
rules produced a verdict.

The constant now lives in `app/core/config.py`; this test fails if anyone turns
it back into a knob, or reintroduces a second place that stamps a verdict.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.core import config
from app.core.config import RULE_VERSION

APP = Path(__file__).resolve().parents[2] / "app"


def test_the_rule_version_cannot_be_overridden_from_the_environment():
    assert "RULE_VERSION" not in config.Settings.model_fields


def test_the_rule_version_is_a_version():
    assert re.fullmatch(r"tev-\d+\.\d+\.\d+", RULE_VERSION), RULE_VERSION


def test_no_module_reads_a_rule_version_from_settings():
    offenders = [path.name for path in APP.rglob("*.py")
                 if "settings.RULE_VERSION" in path.read_text(encoding="utf-8")]
    assert offenders == [], offenders
