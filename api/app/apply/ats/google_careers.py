"""Google Careers' actual Apply link, labelled controls and file chooser uploads.

Official flow: https://support.google.com/googlecareers/answer/6095391?hl=en (2026-10-22).
No application address is invented. Unsupported button-only openers and route shapes need the
person. The exact href read on the pinned posting is included in approval metadata and compared
again in the fresh submit lease, including an opaque jobId the site may choose.
"""

import re
from collections.abc import Sequence
from urllib.parse import parse_qsl, urljoin, urlsplit

from app.apply.ats import base, generic
from app.apply.ats.wizard import Wizard, fixture_job, fixture_url, parts_of
from app.browser.sources import parse_snapshot

HOSTS = ("www.google.com", "careers.google.com")
PATH = re.compile(
    r"/(?:about/careers/applications/)?jobs/results/"
    r"(?P<id>[0-9]{1,30})-[a-z0-9]+(?:-[a-z0-9]+)*/?"
)
APPLICATION = re.compile(r"/(?:about/careers/)?applications/(?:jobs/)?apply/?")
OPAQUE = re.compile(r"[a-zA-Z0-9_-]{1,200}")
PROFILE = frozenset(
    {
        "create your careers profile",
        "create a careers profile",
        "complete your careers profile",
        "creez votre profil candidat",
    }
)
TARGET_KEY = "designated:target"


class GoogleCareers(Wizard):
    name = "google_careers"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.target = ""
        self.target_changed = False

    def job_of(self, url: str) -> str:
        if fixture_url(url, self.stub_base, self.name):
            job = fixture_job(url)
            return f"fixture:{job}" if job else ""
        if self.target and url == self.target:
            return self.job_id
        parts = parts_of(url)
        match = PATH.fullmatch(parts.path) if parts and parts.hostname in HOSTS else None
        return match["id"] if match else ""

    def form_url(self, posting_url: str) -> str | None:
        return posting_url if self.job_of(posting_url) else None

    def pending_at(self, root: base.Node) -> str:
        if pending := super().pending_at(root):
            return pending
        return (
            "profile_incomplete"
            if any(
                n.role in ("heading", "alert")
                and base.fold(n.name or n.text).rstrip(".") in PROFILE
                for n in root.walk()
            )
            else ""
        )

    def apply_target(self, snapshot: str, posting_url: str) -> str:
        candidates = set()
        source = urlsplit(posting_url)
        for node in parse_snapshot(snapshot).walk():
            if node.role != "link" or base.fold(node.name) != "apply" or not node.url:
                continue
            candidate = urljoin(posting_url, node.url)
            if fixture_url(candidate, self.stub_base, self.name):
                if (
                    self.job_of(candidate) == self.job_id
                    and dict(parse_qsl(urlsplit(candidate).query)).get("apply") == "manual"
                ):
                    candidates.add(candidate)
                continue
            parts = parts_of(candidate)
            if not parts or (parts.scheme, parts.netloc) != (source.scheme, source.netloc):
                continue
            query = parse_qsl(parts.query)
            if (
                APPLICATION.fullmatch(parts.path)
                and len(query) == 1
                and query[0][0] == "jobId"
                and OPAQUE.fullmatch(query[0][1])
            ):
                candidates.add(candidate)
        return next(iter(candidates)) if len(candidates) == 1 else ""

    def approved(self, rows: Sequence[generic.Row]) -> None:
        super().approved(rows)
        approved = [r.value for r in rows if r.key == TARGET_KEY]
        self.target_changed = approved != [self.target]

    async def discover(self, tools: base.Tools, known: frozenset[str] = frozenset()):
        if self.target_changed:
            return None, "form_changed"
        return await super().discover(tools, known)

    async def open(self, tools: base.Tools, posting_url: str) -> str:
        if self.form_url(posting_url) is None:
            return "form_changed"
        self.job_id = self.job_of(posting_url)
        if code := await self.visit(tools, posting_url):
            return "signin" if code == "host_not_allowed" else code
        shot = await tools.snapshot()
        snapshot = str(shot.get("snapshot") or "")
        if self.pending_step(snapshot):
            return ""
        if self.job_of(str(shot.get("url") or "")) != self.job_id:
            return "form_changed"
        self.target = self.apply_target(snapshot, posting_url)
        if not self.target:
            return "form_changed"
        code = await self.visit(tools, self.target)
        if code == "host_not_allowed":
            recover = getattr(tools, "recover_signin", None)
            if recover is None or await recover() != "":
                return "signin"
            # Re-read the exact posting and its unique Apply target after human SSO.
            expected = self.target
            code = await self.open(tools, posting_url)
            return "form_changed" if self.target != expected else code
        return code

    async def fill_form(self, tools: base.Tools, context):
        report = await super().fill_form(tools, context)
        report.target = self.target
        return report
