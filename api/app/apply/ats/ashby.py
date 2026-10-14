"""Ashby (SPEC-apply, "Per ATS strategies"): `jobs.ashbyhq.com/<org>/<id>/application`, and the
same address with `?embed=js` when the hosted page answers not found (boards that hide the
hosted page still serve the embed, `engine/stage.py`'s lesson). Yes or no questions are button
pairs (asserted on the buttons' pressed state, `base._fill_yes_no`); the location is a typeahead
whose options come from the server once something is typed; "Autofill from resume" is never
used (the app fills, `base.AUTOFILL`); an invisible check scores the session at submit, so the
pacing of the tool layer and the gate's dwell matter (apply A6).
"""

import re
from urllib.parse import urlsplit

from app.apply.ats import base

HOSTS = ("jobs.ashbyhq.com",)
_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%-]{0,99}")
EMBED = "?embed=js"


class Ashby(base.Adapter):
    name = "ashby"
    hosts = HOSTS
    submit_names = frozenset({"submit application", "envoyer la candidature"})
    anchors = (
        frozenset({"name", "full name", "nom", "nom complet"}),
        frozenset({"email", "e-mail", "adresse e-mail"}),
        frozenset({"resume", "cv", "resume/cv"}),
    )
    traps = ("yes_no_buttons", "location_typeahead", "autofill_from_resume", "invisible_check")

    def form_url(self, posting_url: str) -> str | None:
        parts = urlsplit(posting_url)
        path = parts.path.rstrip("/")
        if self.stub_base is not None and posting_url.startswith(self.stub_base + "/"):
            if not path.startswith("/ashby/"):
                return None
            origin, path = self.stub_base + "/ashby", path[len("/ashby") :]
        elif (
            parts.scheme == "https"
            and (parts.hostname or "").lower() in HOSTS
            and parts.port is None
            and not parts.username
        ):
            origin = "https://jobs.ashbyhq.com"
        else:
            return None
        segments = path.split("/")[1:]
        if segments[-1:] == ["application"]:
            segments = segments[:-1]
        if len(segments) != 2 or not all(_SLUG.fullmatch(s) for s in segments):
            return None
        return f"{origin}/{segments[0]}/{segments[1]}/application"

    async def open(self, tools: base.Tools, posting_url: str) -> str:
        """The hosted form, else its embed when the hosted page says not found; a page that
        says not found both ways is `posting_closed`."""
        url = self.form_url(posting_url) or posting_url
        code = await self.visit(tools, url)
        if code == "posting_closed" and self.form_url(posting_url) is not None:
            return await self.visit(tools, url + EMBED)
        return code
