"""Greenhouse (SPEC-apply, "Per ATS strategies"): the hosted board page, or the `embed/job_app`
page a company site frames. Selects are react-select comboboxes (type, then click the option:
`base._fill_combobox`); a voluntary EEO block closes the form (the resolver applies the person's
EEO policy, `decline` by default); some boards ask for a code sent by e-mail after the submit,
reported as `needs_person: email_code` (apply A10 hands it to the person).

Addresses it takes (anything else is not Greenhouse's, fail closed):
    https://{job-boards,boards}[.eu].greenhouse.io/<org>/jobs/<digits>       the hosted form
    https://{job-boards,boards}[.eu].greenhouse.io/embed/job_app?for=<org>&token=<digits>
A company page carrying `gh_jid` frames the embed page: it is recognised by the page (the submit
and the form's anchors inside one iframe) and filled in that frame only (`base.Adapter.framed`).
"""

import re
from urllib.parse import parse_qs, urlsplit

from app.apply.ats import base

HOSTS = (
    "job-boards.greenhouse.io",
    "boards.greenhouse.io",
    "job-boards.eu.greenhouse.io",
    "boards.eu.greenhouse.io",
)
_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,99}", re.I)
_REAL_JOB = re.compile(r"\d{1,20}")
_STUB_JOB = re.compile(r"\d{1,20}(?:--[a-z]{1,20})?")
_CODE_LABELS = frozenset(
    {"security code", "verification code", "code", "code de securite", "code de verification"}
)


def email_code(root: base.Node, submit_names: frozenset[str]) -> bool:
    found = base.discover_fields(root, submit_names)
    return any(f.kind == "text" and base.fold(f.label) in _CODE_LABELS for f in found.fields)


class Greenhouse(base.Adapter):
    name = "greenhouse"
    hosts = HOSTS
    submit_names = base.SUBMIT_NAMES
    anchors = (base.FIRST_NAME, base.LAST_NAME, base.EMAIL, base.RESUME)
    traps = ("react_select", "eeo_block", "email_code")

    def form_url(self, posting_url: str) -> str | None:
        parts = urlsplit(posting_url)
        if parts.scheme != "https" and not self._stub(posting_url):
            return None
        path, host = parts.path.rstrip("/"), (parts.hostname or "").lower()
        if self.stub_base is not None and self._stub(posting_url):
            prefix, job_id, origin = "/greenhouse", _STUB_JOB, self.stub_base
            if not path.startswith(prefix + "/"):
                return None
            path = path[len(prefix) :]
        elif host in HOSTS and parts.port is None and not parts.username:
            prefix, job_id, origin = "", _REAL_JOB, f"https://{host}"
        else:
            return None
        segments = path.split("/")[1:]
        if segments == ["embed", "job_app"]:
            query = parse_qs(parts.query)
            org, token = query.get("for", [""])[0], query.get("token", [""])[0]
            if _SLUG.fullmatch(org) and job_id.fullmatch(token):
                return f"{origin}{prefix}/embed/job_app?for={org}&token={token}"
            return None
        if len(segments) == 3 and segments[1] == "jobs":
            org, token = segments[0], segments[2]
            if org != "embed" and _SLUG.fullmatch(org) and job_id.fullmatch(token):
                return f"{origin}{prefix}/{org}/jobs/{token}"
        return None

    def _stub(self, url: str) -> bool:
        return self.stub_base is not None and url.startswith(self.stub_base + "/")

    def pending_at(self, root: base.Node) -> str:
        """Also, after the submit: a code sent by e-mail is asked for (a text field labelled as
        a security or verification code, shown with a button to send it). The person types it
        on the page (apply A10a): when its boxes are the page's only credential boxes, it is
        that step and not a sign in wall."""
        boxes = base.credentials(root)
        if boxes and all(base.fold(base.entry_label(b)) in _CODE_LABELS for b in boxes):
            return "email_code"
        if step := super().pending_at(root):
            return step
        if email_code(root, self.submit_names):
            return "email_code"
        return ""
