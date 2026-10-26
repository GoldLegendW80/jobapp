"""Business France VIE's native application panel or a separate guarded company-site lease."""

import re
from urllib.parse import urlsplit

from pydantic import ValidationError

from app.apply import answers
from app.apply.ats import base
from app.apply.ats.linkedin import company_site
from app.apply.ats.wizard import Wizard, fixture_job, fixture_url, parts_of
from app.profile.schemas import AnswersSection

PATH = re.compile(r"/(?:en/|fr/)?offres/([0-9]{1,12})/?")
INCOMPLETE = frozenset(
    {
        "complete your candidate profile",
        "your candidate profile is incomplete",
        "completez votre profil candidat",
        "votre profil candidat est incomplet",
    }
)


class Vie(Wizard):
    name = "vie"
    hosts = ("mon-vie-via.businessfrance.fr",)

    def job_of(self, url: str) -> str:
        if fixture_url(url, self.stub_base, self.name):
            job = fixture_job(url)
            return f"fixture:{job}" if job else ""
        parts = parts_of(url)
        match = PATH.fullmatch(parts.path) if parts and parts.hostname in self.hosts else None
        return match[1] if match else ""

    def form_url(self, posting_url: str) -> str | None:
        return posting_url if self.job_of(posting_url) else None

    def pending_at(self, root: base.Node) -> str:
        if pending := super().pending_at(root):
            return pending
        return (
            "profile_incomplete"
            if any(
                n.role in ("heading", "alert", "paragraph")
                and base.fold(n.name or n.text).rstrip(".") in INCOMPLETE
                for n in root.walk()
            )
            else ""
        )

    def resolve(self, item: base.Field, context: answers.Context):
        if item.key in self.person_answers:
            return super().resolve(item, context)
        if item.kind == "text" and base.anchor_key(item.label) in {
            "motivation message",
            "message de motivation",
            "message",
        }:
            try:
                own = AnswersSection.model_validate(context.sections.get("answers") or {})
            except ValidationError:
                return answers.Left("no_motivation", required=item.required)
            return (
                answers.Answer(own.motivation, "answers.motivation")
                if own.motivation
                else answers.Left("no_motivation", required=item.required)
            )
        return super().resolve(item, context)

    async def open(self, tools: base.Tools, posting_url: str) -> str:
        if code := await super().open(tools, posting_url):
            return code
        shot = await tools.snapshot()
        if not shot.get("ok"):
            return str(shot.get("error") or "form_changed")
        snapshot = str(shot.get("snapshot") or "")
        if self.pending_step(snapshot):
            return ""
        candidate = company_site(snapshot, posting_url, self.stub_base)
        same_host = candidate and urlsplit(candidate).hostname == urlsplit(posting_url).hostname
        fixture_external = bool(
            self.stub_base
            and candidate
            and not candidate.startswith(self.stub_base + "/forms/a15/vie.html")
        )
        self.company_url = candidate if not same_host or fixture_external else None
        return "company_site" if self.company_url else ""
