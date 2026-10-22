"""Bounded known-site application steps. Credentials and unknown answers belong to the person.

The adapter binds every fresh snapshot to the original job. The replay adapter retains that
binding through Generic.configured, and validates the same ordered step names and final summary.
Only ordinary guarded clicks advance a step; the gate owns the final submission.
"""

import json
import re
from collections.abc import Sequence
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from app.apply import answers, failures
from app.apply.ats import base, generic

FINAL = frozenset({"submit", "submit application", "envoyer ma candidature", "postuler"})
NEXT = generic.NEXT_NAMES | {"review", "review application", "verifier la candidature"}
SIGNIN = frozenset(
    {
        "sign in",
        "sign in to apply",
        "create an account",
        "create account",
        "connectez-vous",
        "se connecter",
        "connexion",
    }
)
FORMS = frozenset({"application", "job application", "candidature", "apply", "postuler"})
SUMMARY = frozenset({"application summary", "recapitulatif de candidature"})


def parts_of(url: str):
    if not isinstance(url, str) or len(url) > 4000 or any(c.isspace() or ord(c) < 32 for c in url):
        return None
    try:
        parts = urlsplit(url)
        if parts.username or parts.password or parts.fragment or parts.port not in (None, 443):
            return None
    except ValueError:
        return None
    return (
        parts
        if parts.scheme == "https" and parts.hostname and parts.netloc == parts.hostname
        else None
    )


def fixture_url(url: str, stub_base: str | None, name: str) -> bool:
    """Only one explicit fixture path under the configured local ATS origin."""
    if not stub_base:
        return False
    try:
        parts, stub = urlsplit(url), urlsplit(stub_base)
        return (
            stub.scheme == "http"
            and stub.hostname in {"atsforms", "localhost", "127.0.0.1", "::1"}
            and (parts.scheme, parts.netloc, parts.path)
            == (stub.scheme, stub.netloc, stub.path.rstrip("/") + f"/forms/a15/{name}.html")
            and not parts.fragment
            and not parts.username
            and not parts.password
            and not any(c.isspace() or ord(c) < 32 for c in url)
            and len(url) <= 4000
        )
    except ValueError:
        return False


def fixture_job(url: str) -> str:
    values = parse_qs(urlsplit(url).query)
    job = values.get("job", ["1001"])
    return job[0] if len(job) == 1 and re.fullmatch(r"[0-9]{1,12}", job[0]) else ""


def fixture_apply(url: str) -> str:
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    query["apply"] = ["manual"]
    return urlunsplit(parts._replace(query=urlencode(query, doseq=True)))


class Wizard(generic.Deterministic):
    review_only = True
    require_summary = False

    def __init__(
        self,
        stub_base: str | None = None,
        *,
        framed: bool = False,
        submit: str = "",
        steps: Sequence[str] = (),
    ) -> None:
        super().__init__(stub_base, submit=submit, steps=steps)
        self.job_id = ""
        self.expected: list[tuple[str, object]] = []
        self.company_url: str | None = None

    @property
    def submit_names(self) -> frozenset[str]:
        return super().submit_names if self.submit else FINAL | NEXT

    def recognises(self, snapshot: str) -> bool:
        return False

    def job_of(self, url: str) -> str:
        raise NotImplementedError

    def scope(self, root: base.Node) -> base.Node | None:
        forms = [
            n for n in root.walk() if n.role in ("form", "dialog") and base.fold(n.name) in FORMS
        ]
        return forms[0] if len(forms) == 1 else None

    def pending_at(self, root: base.Node) -> str:
        if pending := super().pending_at(root):
            return pending
        if failures.payment_page(
            "\n".join(f"- {n.role} {json.dumps(n.name)}" for n in root.walk())
        ):
            return failures.PAYMENT
        return (
            "signin"
            if any(
                n.role in ("heading", "button", "link") and base.fold(n.name) in SIGNIN
                for n in root.walk()
            )
            else ""
        )

    def approved(self, rows: Sequence[generic.Row]) -> None:
        self.expected = [
            (r.label, r.value)
            for r in rows
            if r.value is not None and not r.key.startswith(("designated:", "skipped:"))
        ]

    def anchor_problem(self, found: base.Discovery) -> str:
        return "" if found.root is not None and len(found.submit_refs) == 1 else "form_changed"

    def summary_problem(self, root: base.Node) -> bool:
        summaries = [
            n for n in root.walk() if n.role in ("region", "group") and base.fold(n.name) in SUMMARY
        ]
        if not summaries:
            return self.require_summary
        if len(summaries) != 1:
            return True
        shown = [
            (base.fold(n.name), base.fold(" ".join(n.words()[1:])))
            for n in summaries[0].children
            if n.role == "group" and n.name
        ]
        expected = [(base.fold(k), base.fold(str(v))) for k, v in self.expected]
        return sorted(shown) != sorted(expected)

    def check_designation(self, root: base.Node, ref: str) -> str:
        final = [
            n for n in root.walk() if n.role == "button" and generic.button_name(n.name) in FINAL
        ]
        if (
            base.fold(root.name) not in FORMS
            or len(final) != 1
            or final[0].ref != ref
            or "disabled" in final[0].attrs
            or self.summary_problem(root)
        ):
            return "form_changed"
        return ""

    async def read(self, tools: base.Tools) -> tuple[base.Discovery | None, str]:
        adapter = self

        class Bound:
            def __getattr__(self, name: str):
                return getattr(tools, name)

            async def snapshot(self):
                shot = await tools.snapshot()
                if shot.get("ok"):
                    # Sign-in and payment must survive even when the wall uses another path.
                    pending = adapter.pending_step(str(shot.get("snapshot") or ""))
                    if not pending and adapter.job_of(str(shot.get("url") or "")) != adapter.job_id:
                        return {"ok": False, "error": "form_changed"}
                return shot

        return await super().read(Bound())

    async def open(self, tools: base.Tools, posting_url: str) -> str:
        target = self.form_url(posting_url)
        if target is None:
            return "form_changed"
        self.job_id = self.job_of(posting_url)
        # Establish a safe original posting before a form can redirect to external SSO.
        if code := await self.visit(tools, posting_url):
            return "signin" if code == "host_not_allowed" else code
        code = await self.visit(tools, target) if target != posting_url else ""
        if code == "host_not_allowed":
            recover = getattr(tools, "recover_signin", None)
            if recover is None or await recover() != "":
                return "signin"
            code = await self.visit(tools, target)
            return "signin" if code == "host_not_allowed" else code
        return code

    def resolve(self, item: base.Field, context: answers.Context):
        return answers.resolve(item.form_field(), context)

    async def fill_form(self, tools: base.Tools, context: answers.Context) -> base.Report:
        from app.apply.form_agent import FormReport

        class WizardReport(FormReport, generic.DeterministicReport):
            pass

        report = WizardReport()
        for _ in range(generic.STEPS_MAX + 1):
            found, code = await self.discover(tools)
            if found is None or code:
                report.stopped, report.needs_person = code, found.pending if found else ""
                return report
            done: set[str] = set()
            for _ in range(2):
                for item in sorted(
                    [f for f in found.fields if f.key not in done],
                    key=lambda field: field.kind != "file",
                ):
                    done.add(item.key)
                    answer = self.resolve(item, context)
                    filled = await self.fill(tools, item, answer)
                    report.outcomes.append(base.Outcome(item, answer, filled))
                    if filled.status == "failed":
                        report.stopped = (
                            "needs_person"
                            if filled.code in (*base.PENDING_STEPS, failures.PAYMENT)
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
            # A later upload/parser must not silently replace an earlier answer.
            current = {f.key: f for f in found.fields}
            for outcome in report.outcomes:
                if outcome.field.key not in done or not isinstance(outcome.answer, answers.Answer):
                    continue
                item = current.get(outcome.field.key)
                wanted = outcome.answer.option or str(outcome.answer.value)
                same = item is not None and (
                    (bool(item.checked) is outcome.answer.value)
                    if item.kind == "checkbox"
                    else base.fold(item.value) == base.fold(wanted)
                )
                if not same:
                    report.stopped = "form_changed"
                    return report
            report.skipped += found.skipped
            self.expected = [
                (o.field.label, o.answer.value)
                for o in report.outcomes
                if isinstance(o.answer, answers.Answer)
            ]
            button = next(n for n in found.root.walk() if n.ref == found.submit_refs[0])
            if generic.button_name(button.name) in FINAL:
                if self.check_designation(found.root, button.ref) or not await generic.marked(
                    tools, button.ref, button.name
                ):
                    report.stopped = "form_changed"
                else:
                    report.designated, report.designated_ref = button.name, button.ref
                return report
            if (
                generic.button_name(button.name) not in NEXT
                or self.step >= generic.STEPS_MAX
                or "disabled" in button.attrs
            ):
                report.stopped = "form_changed"
                return report
            if any(o.field.required and o.filled.status != "filled" for o in report.outcomes):
                report.stopped, report.needs_person = "needs_person", "required_left"
                return report
            clicked = await tools.click(button.ref, button.name)
            if not clicked.get("ok"):
                code = str(clicked.get("error") or "form_changed")
                report.stopped = "needs_person" if code == "host_not_allowed" else code
                report.needs_person = "signin" if code == "host_not_allowed" else ""
                return report
            report.steps.append(button.name)
            self.step += 1
        report.stopped = "form_changed"
        return report
