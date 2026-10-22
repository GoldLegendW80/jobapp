"""Local model of the counted A16 fixture; ordinary Easy Apply clicks still refuse."""

import json
from typing import Any

from tests.apply.a4_support import Spec
from tests.apply.a8b_support import CustomForm

URL = "https://www.linkedin.com/jobs/view/4100000001/"


class LinkedInForm(CustomForm):
    def __init__(
        self,
        *,
        unknown=False,
        unknown_early=False,
        follow=True,
        changed_summary=False,
        wall="",
        company="",
        yes_no=False,
        french=False,
        changed_boolean=False,
        known_boolean=False,
    ):
        first = [
            Spec("First name", "text", True),
            Spec("Last name", "text", True),
            Spec("Email", "text", True),
        ]
        second = [Spec("Resume", "file", True)]
        if yes_no:
            second.append(
                Spec(
                    "Do you require visa sponsorship?"
                    if known_boolean
                    else "Have you completed lunar flight training?",
                    "yesno",
                    True,
                )
            )
        if unknown_early:
            first.append(Spec("How many moon landings have you completed?", "text", True))
        if unknown:
            second.append(Spec("How many moon landings have you completed?", "text", True))
        final = [Spec("Follow this company", "checkbox", checked=True)] if follow else []
        super().__init__([first, second, final], submit="Submit application")
        self.opened = False
        self.openings = 0
        self.changed_summary, self.wall, self.company = changed_summary, wall, company
        self.french, self.changed_boolean = french, changed_boolean

    async def navigate(self, url: str) -> dict[str, Any]:
        self.opened, self.step = False, 0
        return await super().navigate(url)

    async def linkedin_open(self, job_id: str) -> dict[str, Any]:
        self._log("linkedin_open", job_id=job_id)
        self.opened = True
        self.openings += 1
        return {"ok": True}

    def render(self) -> str:
        if self.wall:
            return f'- heading "{self.wall}" [level=1]' + (
                '\n- textbox "Password" [ref=e8]' if self.wall == "Sign in" else ""
            )
        if self.company:
            return f'- link "Apply on company site" [ref=e9]:\n  - /url: {self.company}'
        if not self.opened:
            return '- heading "Applied AI Engineer"\n- button "Easy Apply" [ref=e9]'
        rendered = super().render()
        if self.french:
            rendered = rendered.replace('- button "Yes"', '- button "Oui"').replace(
                '- button "No"', '- button "Non"'
            )
        if self._other:
            return rendered
        rendered = rendered.replace(
            "  - generic [ref=", '  - dialog "Apply to Example Robotics" [ref=', 1
        )
        if self.step == 1:
            rendered = rendered.replace('- button "Next"', '- button "Review"')
        if self.step == 0 and any(
            s.label.startswith("How many moon") and not s.value for s in self.fields
        ):
            rendered = rendered.replace('- button "Next"', '- button "Next" [disabled]')
        if self.step == 2:
            lines = ['    - region "Application summary":']
            for spec in self.fields[:3]:
                value = "Changed after your approval" if self.changed_summary else spec.value
                lines += [f'      - group "{spec.label}":', f"        - text: {json.dumps(value)}"]
            for spec in (s for s in self.fields if s.kind == "yesno"):
                value = "Yes" if self.changed_boolean else spec.value
                if self.french:
                    value = {"Yes": "Oui", "No": "Non"}.get(value, value)
                lines += [f'      - group "{spec.label}":', f"        - text: {json.dumps(value)}"]
            rendered += "\n" + "\n".join(lines)
        return rendered
