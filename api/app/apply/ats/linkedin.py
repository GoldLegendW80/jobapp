"""Opted-in Easy Apply, bounded modal steps and deterministic replay of approved answers.

The opener is private browserd code. Next and Review use ordinary guarded clicks, and only
the application gate clicks Submit application. Unknown questions remain left for the person.
"""

import re
from collections.abc import Sequence
from urllib.parse import urljoin, urlsplit

from app.apply import answers
from app.apply.ats import base, generic
from app.browser.sources import parse_snapshot as links_snapshot
from app.browser.sources.linkedin import external_url, public_host

NEXT = generic.NEXT_NAMES | {"review", "review application", "verifier", "verifier la candidature"}
FINAL = frozenset(
    {"submit application", "submit", "envoyer la candidature", "envoyer ma candidature"}
)
COMPANY = frozenset(
    {
        "apply on company site",
        "apply on company website",
        "apply",
        "postuler",
        "postuler sur le site de l'entreprise",
    }
)
FOLLOW = re.compile(r"^(?:follow (?:this company|.+)|suivre (?:cette entreprise|.+))$")
JOB = re.compile(r"/jobs/view/([0-9]{1,24})/?")
CHECKPOINT = (
    "security verification",
    "unusual activity",
    "security check",
    "verify your identity",
    "verification de securite",
    "activite inhabituelle",
    "checkpoint",
)


class Report(generic.DeterministicReport):
    """Marker mixed into the existing FormReport, never supplied by a model or API body."""


def company_site(snapshot: str, url: str, stub_base: str | None) -> str | None:
    candidates = set()
    for node in links_snapshot(snapshot).walk():
        if node.role != "link" or base.fold(node.name) not in COMPANY or not node.url:
            continue
        candidate = external_url(node.url, url) or urljoin(url, node.url)
        parts = urlsplit(candidate)
        fixture = bool(
            stub_base
            and candidate.startswith(stub_base.rstrip("/") + "/")
            and not candidate.startswith(stub_base.rstrip("/") + "/linkedin/")
        )
        try:
            port_ok = parts.port in (None, 80 if parts.scheme == "http" else 443)
        except ValueError:
            continue
        host = public_host(parts.hostname)
        if (
            parts.scheme in ("http", "https")
            and not parts.username
            and not parts.password
            and not parts.fragment
            and not any(c.isspace() or ord(c) < 32 for c in candidate)
            and (
                fixture
                or (
                    port_ok
                    and host
                    and host != "linkedin.com"
                    and not host.endswith(".linkedin.com")
                )
            )
        ):
            candidates.add(candidate)
    return next(iter(candidates)) if len(candidates) == 1 else None


class LinkedIn(generic.Deterministic):
    name = "linkedin"
    hosts = ("linkedin.com", "www.linkedin.com")

    def __init__(
        self,
        stub_base: str | None = None,
        *,
        framed: bool = False,
        submit: str = "",
        steps: Sequence[str] = (),
    ) -> None:
        super().__init__(stub_base, submit=submit, steps=steps)
        self.company_url: str | None = None
        self.expected: list[tuple[str, object]] = []
        self.job_id = ""

    @property
    def submit_names(self) -> frozenset[str]:
        return super().submit_names if self.submit else FINAL | NEXT

    def form_url(self, posting_url: str) -> str | None:
        raw = posting_url
        if self.stub_base and raw.startswith(self.stub_base + "/linkedin/"):
            raw = "https://www.linkedin.com/" + raw[len(self.stub_base + "/linkedin/") :]
        parts = urlsplit(raw)
        if (
            parts.scheme == "https"
            and parts.netloc in self.hosts
            and JOB.fullmatch(parts.path)
            and not parts.fragment
        ):
            return posting_url
        return None

    def recognises(self, snapshot: str) -> bool:
        return False  # a lookalike page never opts itself into the known-site adapter

    def job_of(self, url: str) -> str:
        if self.form_url(url) is None:
            return ""
        return urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]

    async def read(self, tools: base.Tools) -> tuple[base.Discovery | None, str]:
        adapter = self

        class Bound:
            def __getattr__(self, name: str):
                return getattr(tools, name)

            async def snapshot(self):
                shot = await tools.snapshot()
                if adapter.pending_step(str(shot.get("snapshot") or "")):
                    return shot
                if adapter.job_id and adapter.job_of(str(shot.get("url") or "")) != adapter.job_id:
                    return {"ok": False, "error": "form_changed"}
                return shot

        return await super().read(Bound())

    def scope(self, root: base.Node) -> base.Node | None:
        dialogs = [
            n
            for n in root.walk()
            if n.role == "dialog"
            and base.fold(n.name).startswith(("apply to ", "easy apply", "candidature"))
        ]
        return dialogs[0] if len(dialogs) == 1 else None

    def pending_at(self, root: base.Node) -> str:
        text = base.fold(
            " ".join(" ".join(n.words()) for n in root.walk() if n.role in ("heading", "alert"))
        )
        if any(word in text for word in CHECKPOINT):
            return "linkedin_paused"
        return super().pending_at(root)

    def anchor_problem(self, found: base.Discovery) -> str:
        if found.root is None:
            return "anchor_missing"
        if len(found.submit_refs) != 1:
            return "submit_missing"
        return ""

    def check_designation(self, root: base.Node, ref: str) -> str:
        targets = [
            n for n in root.walk() if n.role == "button" and generic.button_name(n.name) in FINAL
        ]
        return (
            ""
            if (
                root.role == "dialog"
                and len(targets) == 1
                and targets[0].ref == ref
                and "disabled" not in targets[0].attrs
            )
            else "form_changed"
        )

    def approved(self, rows: Sequence[generic.Row]) -> None:
        super().approved(rows)
        self.expected = [
            (r.label, r.value) for r in rows if not r.key.startswith(("designated:", "skipped:"))
        ]

    def summary_problem(self, root: base.Node) -> bool:
        summaries = [
            n
            for n in root.walk()
            if n.role in ("region", "group")
            and base.fold(n.name) in ("application summary", "recapitulatif de candidature")
        ]
        for summary in summaries:
            rows = [n for n in summary.children if n.role == "group" and n.name]
            if not rows:
                return True
            for row in rows:
                values = [
                    v for label, v in self.expected if base.fold(label) == base.fold(row.name)
                ]
                shown = base.fold(" ".join(row.words(skip=None)[1:]))
                if not values or not all(
                    isinstance(v, str) and base.fold(v) == shown for v in values
                ):
                    return True
        return False

    async def discover(
        self, tools: base.Tools, known: frozenset[str] = frozenset()
    ) -> tuple[base.Discovery | None, str]:
        found, code = await super().discover(tools, known)
        if (
            not code
            and found is not None
            and found.root is not None
            and self.summary_problem(found.root)
        ):
            return found, "form_changed"
        return found, code

    async def open(self, tools: base.Tools, posting_url: str) -> str:
        if self.form_url(posting_url) is None:
            return "form_changed"
        self.job_id = self.job_of(posting_url)
        if code := await self.visit(tools, posting_url):
            return code
        shot = await tools.snapshot()
        snapshot = str(shot.get("snapshot") or "")
        if self.pending_step(snapshot):
            return ""  # the owned sign-in wrapper and normal pending-step path handle the wall
        self.company_url = company_site(snapshot, posting_url, self.stub_base)
        if self.company_url:
            return "company_site"
        path = urlsplit(posting_url).path
        if self.stub_base:
            path = path.removeprefix(urlsplit(self.stub_base).path + "/linkedin")
        match = JOB.fullmatch(path)
        if match is None or not hasattr(tools, "linkedin_open"):
            return "form_changed"
        result = await tools.linkedin_open(match[1])
        if result.get("ok"):
            return ""
        code = str(result.get("error") or "form_changed")
        return "form_changed" if code == "linkedin_opener_refused" else code

    @staticmethod
    def optional_follow(item: base.Field) -> bool:
        return (
            item.kind == "checkbox"
            and not item.required
            and bool(FOLLOW.fullmatch(base.fold(item.label)))
        )

    async def fill(
        self, tools: base.Tools, item: base.Field, answer: answers.Answer | answers.Left
    ) -> base.Filled:
        if (
            self.optional_follow(item)
            and isinstance(answer, answers.Answer)
            and answer.value is False
        ):
            found, _ = await self.read(tools)
            now = found.find(item.key) if found is not None else None
            if now is None or found.pending:
                return base.Filled("failed", (found.pending if found else "") or "form_changed")
            if not (await tools.check(now.ref, False, now.label)).get("ok"):
                return base.Filled("failed", "form_changed")
            fresh, _ = await self.read(tools)
            field = fresh.find(item.key) if fresh is not None else None
            return (
                base.Filled("filled")
                if field is not None and not field.checked
                else base.Filled("failed", "form_changed")
            )
        return await super().fill(tools, item, answer)

    async def fill_form(self, tools: base.Tools, context: answers.Context) -> base.Report:
        from app.apply.form_agent import FormReport

        class ModalReport(FormReport, Report):
            pass

        report = ModalReport()
        for _ in range(generic.STEPS_MAX + 1):
            found, code = await self.discover(tools)
            if found is None or code:
                report.stopped, report.needs_person = code, found.pending if found else ""
                return report
            done = set()
            for _ in range(2):
                for item in [f for f in found.fields if f.key not in done]:
                    done.add(item.key)
                    answer = (
                        answers.Answer(False, "identity.linkedin_follow_declined")
                        if self.optional_follow(item)
                        else self.resolve(item, context)
                    )
                    filled = await self.fill(tools, item, answer)
                    report.outcomes.append(base.Outcome(item, answer, filled))
                    if filled.status == "failed":
                        report.stopped = (
                            "needs_person"
                            if filled.code in (*base.PENDING_STEPS, "linkedin_paused")
                            else filled.code
                        )
                        report.needs_person = filled.code
                        return report
                found, code = await self.discover(tools, frozenset(done))
                if found is None or code:
                    report.stopped, report.needs_person = code, found.pending if found else ""
                    return report
                if all(f.key in done for f in found.fields):
                    break
            report.skipped += found.skipped
            button = next(n for n in found.root.walk() if n.ref == found.submit_refs[0])
            if generic.button_name(button.name) in FINAL:
                if self.check_designation(found.root, button.ref) or not await generic.marked(
                    tools, button.ref, button.name
                ):
                    report.stopped = "form_changed"
                else:
                    report.designated, report.designated_ref = button.name, button.ref
                return report
            if generic.button_name(button.name) not in NEXT or self.step >= generic.STEPS_MAX:
                report.stopped = "form_changed"
                return report
            # A left required answer must not be advanced through a site's validation.
            if any(o.field.required and o.filled.status != "filled" for o in report.outcomes):
                report.stopped, report.needs_person = "needs_person", "required_left"
                return report
            if not (await tools.click(button.ref, button.name)).get("ok"):
                report.stopped = "form_changed"
                return report
            report.steps.append(button.name)
            self.expected = [
                (o.field.label, o.answer.value)
                for o in report.outcomes
                if isinstance(o.answer, answers.Answer)
            ]
            self.step += 1
        report.stopped = "form_changed"
        return report
