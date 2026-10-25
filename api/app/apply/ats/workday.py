"""Workday tenant jobs, opened through their canonical Apply manually route."""

import re
from urllib.parse import urlsplit, urlunsplit

from app.apply.ats.wizard import Wizard, fixture_apply, fixture_job, fixture_url, parts_of

HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.wd[1-9][0-9]?)?\.myworkdayjobs\.com")
PATH = re.compile(
    r"(?P<job>/(?:[a-z]{2}-[A-Z]{2}/)?[A-Za-z0-9_-]{1,100}/job/"
    r"(?:[A-Za-z0-9_.~-]{1,200}/){0,3}[A-Za-z0-9_.~-]{1,200}_[A-Za-z0-9_-]{1,80})(?:/apply(?:/applyManually)?)?/?"
)


class Workday(Wizard):
    name = "workday"
    require_summary = True

    def job_of(self, url: str) -> str:
        if fixture_url(url, self.stub_base, self.name):
            job = fixture_job(url)
            return f"fixture:{job}" if job else ""
        parts = parts_of(url)
        match = PATH.fullmatch(parts.path) if parts and HOST.fullmatch(parts.hostname) else None
        return f"{parts.hostname}{match['job']}" if match else ""

    def form_url(self, posting_url: str) -> str | None:
        if not self.job_of(posting_url):
            return None
        if fixture_url(posting_url, self.stub_base, self.name):
            return fixture_apply(posting_url)
        parts = urlsplit(posting_url)
        match = PATH.fullmatch(parts.path)
        return urlunsplit(parts._replace(path=match["job"] + "/apply/applyManually"))
