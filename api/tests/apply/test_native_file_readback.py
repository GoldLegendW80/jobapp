"""Native selection is read independently of upload acknowledgements and page echoes."""

import asyncio
from dataclasses import fields
from typing import Any

import pytest

from app.apply import answers
from app.apply.ats import base
from app.apply.ats.ashby import Ashby
from app.apply.ats.generic import Generic
from app.apply.ats.greenhouse import Greenhouse
from app.apply.ats.lever import Lever
from tests.apply.a4_support import FakeForm, ashby_form, greenhouse_form
from tests.apply.a8_support import lever_form
from tests.apply.a8b_support import workable


class NativeForm(FakeForm):
    selected: Any = None
    echo = ""
    rejected = False

    async def snapshot(self) -> dict[str, Any]:
        for item in self.fields:
            if item.kind == "file":
                item.value = self.echo
                item.error = "This file type is not supported." if self.rejected else ""
        shot = await super().snapshot()
        shot["native_files"] = {
            ref: self.selected for ref, (kind, _, _) in self._refs.items() if kind == "file"
        }
        return shot


@pytest.mark.parametrize(
    ("adapter_type", "factory"),
    [(Generic, workable), (Greenhouse, greenhouse_form), (Ashby, ashby_form), (Lever, lever_form)],
)
@pytest.mark.parametrize(
    ("selected", "echo", "rejected", "status", "reason"),
    [
        (["cv.pdf"], "", False, "filled", ""),
        ([], "cv.pdf", False, "unverified", "no_file"),
        (["other.pdf"], "cv.pdf", False, "unverified", "no_file"),
        (["cv.pdf", "other.pdf"], "cv.pdf", False, "unverified", "no_file"),
        (["cv.pdf"], "cv.pdf", True, "failed", "upload_rejected"),
        (None, "cv.pdf", False, "unverified", "no_file"),
        ("cv.pdf", "cv.pdf", False, "unverified", "no_file"),
        ([3], "cv.pdf", False, "unverified", "no_file"),
    ],
)
def test_current_native_selection_controls_document_readback(
    adapter_type, factory, selected, echo, rejected, status, reason
) -> None:
    original = factory()
    form = NativeForm(**{f.name: getattr(original, f.name) for f in fields(FakeForm)})
    form.files = ("cv.pdf", "letter.pdf")
    form.selected, form.echo, form.rejected = selected, echo, rejected
    adapter = adapter_type()

    async def go() -> None:
        found, code = await adapter.read(form)
        assert found is not None
        assert not code
        item = next(f for f in found.fields if f.kind == "file")
        outcome = await base._fill_file(
            adapter, form, item, answers.Answer("cv.pdf", "documents.cv")
        )
        assert (outcome.status, outcome.code) == (status, reason)
        assert form.submit_clicks == 0

    asyncio.run(go())
