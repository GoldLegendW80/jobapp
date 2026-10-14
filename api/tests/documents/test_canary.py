"""The canary (D2): a planted marker is found, and the pure functions of the module write no
log line at all, so no content can reach one."""

from __future__ import annotations

import copy
import logging

import pytest

from app.documents import checks, corrections
from app.documents import content as c
from app.documents.settings import DocumentSettings
from app.documents.sources import sources_for
from tests.documents import canary
from tests.documents.conftest import base_cv, posting, view_of, written


def test_a_planted_marker_is_found_in_a_log_and_in_a_value() -> None:
    planted = canary.marker()
    record = logging.LogRecord(
        "documents.test", logging.INFO, __file__, 1, "x %s", (planted,), None
    )
    found = canary.find([planted], logs=[record], values=[{"content": [planted]}])
    assert {f.where for f in found} == {"log", "value"}
    assert canary.find([planted], logs=["nothing"], values=[{"a": "b"}]) == []


def test_checking_and_correcting_writes_no_log(caplog: pytest.LogCaptureFixture) -> None:
    planted = canary.marker()
    data = copy.deepcopy(written("cv_typical"))
    data["experience"][0]["bullets"].append(f"Led 14 engineers on {planted} — at Globex Inc.")
    base = base_cv("alex")
    sources = sources_for(view_of("alex", base), base, None, posting("lumen"))
    settings = DocumentSettings(no_dash_separators=True, banned_phrases=(planted,))
    caplog.set_level(logging.DEBUG)
    model = c.parse("cv", data)
    model, _ = corrections.fix_separators("cv", model, settings)
    result = checks.run("cv", model, sources, settings)
    _, records = corrections.remove_remaining(
        model, result, corrections.fallbacks_for(view_of("alex", base), base, posting("lumen"))
    )
    assert any(planted in r.before for r in records)  # the record holds it, by design
    assert canary.find([planted], logs=caplog.records) == []
    assert all(planted not in str(issue) for issue in result.issues)  # issues are offsets only
