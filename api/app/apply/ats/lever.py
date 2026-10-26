"""Lever (SPEC-apply, "Per ATS strategies"): `jobs.lever.co/<org>/<id>/apply`, and the same on
`jobs.eu.lever.co`. What it does beside the shared fill of `base`:

- the resume parse: once the CV is attached, Lever reads it and writes the name, e-mail, phone
  and current company it found into the form. A value of the application comes from the profile,
  never from that parse: right after the upload, the form is read until two consecutive reads
  agree (a parse may write over more than one render), every text field the parse changed is
  cleared (`_undo_parse`), then the fill types the profile's answers in page order (the CV comes
  first on Lever's form), so a field the resolver leaves shows nothing rather than the parse's
  guess, and the submit lease's refill does the same before its read back;
- its questions are native selects (`base._fill_select`);
- the letter: when no letter is uploaded (Lever's form has no letter input), the "Additional
  information" box carries the letter's own text, typed by code (`letter_box`), never a
  model's text;
- hCaptcha: its checkbox, whose accessible name starts with "hCaptcha", is a step for the
  person (`needs_person: captcha`), seen on every read before anything is typed.

Addresses it takes (anything else is not Lever's, fail closed):
    https://jobs.lever.co/<org>/<uuid>[/apply]
    https://jobs.eu.lever.co/<org>/<uuid>[/apply]
    <stub>/forms/a8/lever/<org>/<job>[/apply.html]     the fake form of tests/ats_forms/a8/

Declared open points: a parse that writes again after two consecutive reads agreed, or after
`PARSE_WAITS` reads, writes over values typed later; the read back before the gate's click sees
it and ends `form_changed`. A form that asks for the CV after other fields (Lever's own form
puts it first): a field typed before the upload and rewritten by the parse is cleared and stays
empty while reported `filled`, so the read back ends `form_changed` there too. An hCaptcha that
shows only after the click is the site's refusal of the submit (apply A10). A letter box named
in another wording than `LETTER_BOX` is left to the answer agent's letter rule (apply A5).
"""

import logging
import re
from urllib.parse import urlsplit

from app.apply import answers
from app.apply.ats import base

logger = logging.getLogger(__name__)

HOSTS = ("jobs.lever.co", "jobs.eu.lever.co")
STUB_PREFIX = "/forms/a8/lever"
_ORG = re.compile(r"[a-z0-9][a-z0-9._-]{0,99}", re.I)
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_STUB_JOB = re.compile(r"[a-z0-9][a-z0-9-]{0,80}")
PARSE_WAITS = 6  # snapshots taken while the resume parse runs, 0.5 s apart
PARSE_WAIT_S = 0.5
# The "Additional information" box, by its label or by the placeholder Lever names it with
# (`letter_key`'s form: folded, marks and the final stop off). Closed world.
LETTER_BOX = frozenset(
    {
        "additional information",
        "add a cover letter or anything else you want to share",
        "informations complementaires",
        "informations supplementaires",
    }
)
_HCAPTCHA = re.compile(r"hcaptcha\b")
_CAPTCHA_ROLES = ("checkbox", "button", "iframe")


def letter_key(label: str) -> str:
    return base.anchor_key(label).rstrip(" .:")


def letter_box(label: str) -> bool:
    return letter_key(label) in LETTER_BOX


def hcaptcha(root: base.Node) -> bool:
    """hCaptcha's own control: a checkbox, button or frame whose name starts with "hCaptcha"."""
    return any(n.role in _CAPTCHA_ROLES and _HCAPTCHA.match(base.fold(n.name)) for n in root.walk())


def _texts(found: base.Discovery) -> dict[str, str]:
    return {f.key: f.value for f in found.fields if f.kind == "text"}


class Lever(base.Adapter):
    name = "lever"
    hosts = HOSTS
    submit_names = base.SUBMIT_NAMES
    anchors = (base.FULL_NAME, base.EMAIL, base.RESUME)
    traps = ("resume_parse", "native_selects", "letter_in_additional_information", "hcaptcha")

    def form_url(self, posting_url: str) -> str | None:
        parts = urlsplit(posting_url)
        if self.stub_base is not None and posting_url.startswith(self.stub_base + "/"):
            path = parts.path.rstrip("/")
            if not path.startswith(STUB_PREFIX + "/"):
                return None
            segments = path[len(STUB_PREFIX) :].split("/")[1:]
            if segments[-1:] == ["apply.html"]:
                segments = segments[:-1]
            if len(segments) != 2 or not (
                _ORG.fullmatch(segments[0]) and _STUB_JOB.fullmatch(segments[1])
            ):
                return None
            return f"{self.stub_base}{STUB_PREFIX}/{segments[0]}/{segments[1]}/apply.html"
        try:
            port = parts.port
        except ValueError:
            return None
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or host not in HOSTS or port is not None or parts.username:
            return None
        segments = parts.path.rstrip("/").split("/")[1:]
        if segments[-1:] == ["apply"]:
            segments = segments[:-1]
        if len(segments) != 2 or not (_ORG.fullmatch(segments[0]) and _UUID.fullmatch(segments[1])):
            return None
        return f"https://{host}/{segments[0]}/{segments[1].lower()}/apply"

    def pending_at(self, root: base.Node) -> str:
        if step := super().pending_at(root):
            return step
        return "captcha" if hcaptcha(root) else ""

    async def fill(
        self, tools: base.Tools, item: base.Field, answer: answers.Answer | answers.Left
    ) -> base.Filled:
        """The shared fill; the CV's upload is followed by the undoing of Lever's parse."""
        if item.kind != "file" or base.anchor_key(item.label) not in base.RESUME:
            return await super().fill(tools, item, answer)
        before, code = await self.read(tools)
        if before is None:
            return base.Filled("failed", code)
        if before.pending:
            return base.Filled("failed", before.pending)
        filled = await super().fill(tools, item, answer)
        if filled.status not in ("filled", "unverified"):
            return filled
        return await self._undo_parse(tools, before) or filled

    async def _undo_parse(self, tools: base.Tools, before: base.Discovery) -> base.Filled | None:
        """Clears every text field whose value the parse changed, as the last read shows it
        once a change is seen and two consecutive reads agree; None when all are cleared (or
        no parse showed within `PARSE_WAITS` reads), else the failure."""
        shown = _texts(before)
        parsed: list[base.Field] = []
        previous: dict[str, str] | None = None
        for attempt in range(PARSE_WAITS):
            now, code = await self.read(tools)
            if now is None:
                return base.Filled("failed", code)
            if now.pending:
                return base.Filled("failed", now.pending)
            parsed = [
                f
                for f in now.fields
                if f.kind == "text" and f.value and f.value != shown.get(f.key, "")
            ]
            values = _texts(now)
            if parsed and values == previous:
                break
            previous = values
            if attempt < PARSE_WAITS - 1:
                await tools.wait_for(time=PARSE_WAIT_S)
        for item in parsed:
            found, code = await self.read(tools)
            if found is not None and found.pending:
                return base.Filled("failed", found.pending)
            now_item = found.find(item.key) if found is not None else None
            if now_item is None:
                return base.Filled("failed", code or "form_changed")
            if code := base._ok(await tools.type(now_item.ref, "", now_item.label)):
                return base.Filled("failed", code)
            found, code = await self.read(tools)
            if found is not None and found.pending:
                return base.Filled("failed", found.pending)
            now_item = found.find(item.key) if found is not None else None
            if now_item is None:
                return base.Filled("failed", code or "form_changed")
            if now_item.value:
                return base.Filled("unverified", "parse_not_cleared")
        if parsed:
            logger.info("lever: resume parse cleared (%d fields)", len(parsed))
        return None

    async def fill_form(self, tools: base.Tools, context: answers.Context) -> base.Report:
        """The shared fill, then the letter's text in "Additional information" when no letter
        was uploaded and the resolver left that box."""
        report = await super().fill_form(tools, context)
        letter = context.documents.letter_text if context.documents is not None else None
        if report.stopped or not letter or not letter.strip():
            return report
        uploaded = any(
            isinstance(o.answer, answers.Answer)
            and o.answer.source == "documents.letter"
            and o.filled.status in ("filled", "unverified")
            for o in report.outcomes
        )
        if uploaded:
            return report
        for index, outcome in enumerate(report.outcomes):
            item = outcome.field
            if item.kind != "text" or not isinstance(outcome.answer, answers.Left):
                continue
            if not letter_box(item.label):
                continue
            answer = answers.Answer(letter, "documents.letter_text")
            filled = await self.fill(tools, item, answer)
            report.outcomes[index] = base.Outcome(item, answer, filled)
            if step := base.step_of(filled):
                report.stopped, report.needs_person = "needs_person", step
            elif filled.status == "failed" and filled.code in base.STOPPING:
                report.stopped = filled.code
            break
        return report
