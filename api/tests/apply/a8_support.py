"""Fakes for apply A8: the fake Lever form of tests/ats_forms/a8/ modelled over A4's FakeForm and
A6's SubmitForm, for tests with no browser, no model and no company. Once the CV is uploaded,
Lever's resume parse writes values that are not the person's into the name, e-mail, phone and
current company (after `parse_after` more snapshots); its questions are native selects; its
"Additional information" box is named by its placeholder only; `hcaptcha` shows hCaptcha's
checkbox in its frame. Every call is recorded, the gate's click included."""

from __future__ import annotations

from typing import Any

from tests.apply.a4_support import Spec
from tests.apply.a6_support import SubmitForm

LEVER_PATH = "/forms/a8/lever/example-robotics/applied-ai-engineer/apply.html"
CAPTCHA_PATH = "/forms/a8/lever/example-robotics/applied-ai-engineer--captcha/apply.html"
COMMENTS = "Add a cover letter or anything else you want to share."
SPONSORSHIP = "Will you now or in the future require sponsorship?"
HEARD = "How did you hear about this job?"
PARSED = {
    "Full name": "Alex Exampel",
    "Email": "alex.exampel@parsed.example",
    "Phone": "+33 1 00 00 00 00",
    "Current company": "Northwind Labs",
}
HCAPTCHA = (
    "hCaptcha checkbox. Select in order to trigger the challenge, or to bypass it if you have "
    "an accessibility cookie."
)


class LeverForm(SubmitForm):
    def __init__(
        self,
        *args: Any,
        parse_after: int = 0,
        hcaptcha: bool = False,
        parse: dict[str, str] | None = None,
        **kw: Any,
    ) -> None:
        super().__init__(*args, **kw)
        self.parse_after = parse_after
        self.hcaptcha = hcaptcha
        self.parse = dict(PARSED if parse is None else parse)
        self._parse_in: int | None = None
        self.parses = 0

    async def upload(self, ref: str, names: list[str], element: str = "") -> dict[str, Any]:
        target = self._target(ref)
        result = await super().upload(ref, names, element)
        resume = target is not None and target[1] >= 0 and target[0] == "file"
        if result.get("ok") and resume and self.fields[target[1]].label == "Resume/CV":
            self._parse_in = self.parse_after
        return result

    async def snapshot(self) -> dict[str, Any]:
        if self._parse_in is not None:
            if self._parse_in <= 0:
                for spec in self.fields:
                    if spec.label in self.parse:
                        spec.value = self.parse[spec.label]
                self._parse_in, self.parses = None, self.parses + 1
            else:
                self._parse_in -= 1
        return await super().snapshot()

    def render(self) -> str:
        page = super().render()
        if not self.hcaptcha or self._other is not None:
            return page
        frame, box = self._ref("captcha"), self._ref("captcha")
        return page + f'\n  - iframe [ref={frame}]:\n    - checkbox "{HCAPTCHA}" [ref={box}]'


def lever_form(**rest: Any) -> LeverForm:
    """The fake Lever form of tests/ats_forms/a8/lever.js, field for field."""
    return LeverForm(
        [
            Spec("Resume/CV", "file", True),
            Spec("Full name", "text", True),
            Spec("Email", "text", True),
            Spec("Phone", "text"),
            Spec("Current location", "text"),
            Spec("Current company", "text"),
            Spec("LinkedIn URL", "text"),
            Spec("GitHub URL", "text"),
            Spec("Portfolio URL", "text"),
            Spec(SPONSORSHIP, "select", True, ("Yes", "No")),
            Spec(HEARD, "select", False, ("LinkedIn", "Company website", "Referral", "Other")),
            Spec(COMMENTS, "text", aria_only=True),
            Spec("Gender", "select", False, ("Male", "Female", "Decline to self-identify")),
        ],
        submit="Submit application",
        title="Applied AI Engineer",
        **rest,
    )
