"""Hostile local wizards with changing refs, ordered steps and no external service."""

import json
import re
from urllib.parse import parse_qs, urlsplit

from tests.apply.a4_support import FakeForm, Spec
from tests.apply.a8b_support import CustomForm

BASE = "http://atsforms:8080"


class WizardForm(CustomForm):
    def __init__(self, *, kind="workday", wall="", variant="", french=False, **kwargs):
        steps = [
            [
                Spec("First name", "text", True),
                Spec("Last name", "text", True),
                Spec("Email", "text", True),
            ],
            [Spec("Resume", "file", True), Spec("Cover letter", "file")],
            [],
        ]
        if kind != "workday":
            steps = [[*steps[0], *steps[1]]]
        if kind == "vie":
            steps[0].append(Spec("Motivation message", "text", True))
        super().__init__(steps, **kwargs)
        self.files = ("cv.pdf", "letter.pdf")
        self.kind, self.wall, self.variant, self.french = kind, wall, variant, french
        self.history = []
        self.parsed = False
        self.expired = False
        self.links = []

    def _ref(self, what, index=-1, part=""):
        return FakeForm._ref(self, what, index, part)

    def render(self):
        if self.wall:
            return '- heading "Sign in" [ref=e1]\n- textbox "Password" [ref=e2]'
        if self.variant == "incomplete":
            text = (
                "Complete your Careers Profile"
                if self.kind == "google_careers"
                else "Complete your candidate profile"
            )
            return f'- heading "{text}" [ref=e1]'
        if self.kind == "google_careers" and not parse_qs(urlsplit(self.url).query).get("apply"):
            target = self.url + ("&" if "?" in self.url else "?") + "apply=manual"
            return (
                '- heading "Applied AI Engineer" [ref=e1]\n- link "Apply" [ref=e2]:\n'
                f"  - /url: {target}"
            )
        body = super().render()
        body = re.sub(
            r"(?m)^  - generic \[ref=([^]]+)\]:$",
            r'  - form "Application" [ref=\1]:',
            body,
            count=1,
        )
        if self.step == 0 and any(
            s.label == "Your project codename" and not s.value for s in self.fields
        ):
            body = body.replace('- button "Next" [ref=', '- button "Next" [disabled] [ref=')
        if self.kind == "workday" and self.step == 2 and self._other is None:
            lines = ['    - region "Application summary":']
            for spec in self.fields:
                if not spec.value:
                    continue
                value = "Changed answer" if self.variant == "summary" else spec.value
                if self.french and spec.kind == "yesno":
                    value = {"No": "Non", "Yes": "Oui"}.get(value, value)
                lines += [
                    f"      - group {json.dumps(spec.label)}:",
                    f"        - generic: {json.dumps(value)}",
                ]
            body = body.replace(
                '    - button "Submit application"',
                "\n".join(lines) + '\n    - button "Submit application"',
            )
        if self.french:
            body = body.replace('button "Yes"', 'button "Oui"').replace(
                'button "No"', 'button "Non"'
            )
        return body

    async def navigate(self, url):
        self.step = 0
        self.history.append(url)
        return await super().navigate(url)

    async def click(self, ref, element=""):
        target = self._target(ref)
        result = await super().click(ref, element)
        if target and target[0] == "next" and self.variant == "expire" and not self.expired:
            self.wall, self.expired = "signin", True
        if self.variant == "payment" and self.step == 1:
            self.fields[3].label = "Credit card number"
            self.fields[3].kind = "text"
        return result

    async def upload(self, ref, names, element=""):
        result = await super().upload(ref, names, element)
        if self.kind == "google_careers" and element == "Resume" and not self.parsed:
            self.spec("First name").value = "Parser changed this"
            self.parsed = True
        return result


def url(kind="workday", job="1001", base=BASE):
    return f"{base}/forms/a15/{kind}.html?job={job}"
