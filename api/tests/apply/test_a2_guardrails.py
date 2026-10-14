"""The pure parts of A2: the never apply match, the form address key, the same role keys, the state
table. Accept and refuse tables in both directions (review checklist, "Heuristic code"); the
database side is in test_start.py.
"""

import uuid
from datetime import UTC, datetime
from itertools import product

import pytest

from app.apply import guardrails, states
from app.apply.guardrails import canonical_key, company_key, matching_rule, title_key
from app.apply.models import ENDED, Application, Mode, State
from app.jobs.models import Job
from app.jobs.sweep import NeverApplyRule

OWNER = NeverApplyRule.parse(
    "No cybersecurity roles: cyber, security, pentest, threat (Harald, 2026-10-06)"
)
SECURITY_ONLY = NeverApplyRule.parse("No security roles: Security Engineer")
AT = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)

# --- never apply ---------------------------------------------------------------------------------

REFUSED_BY_OWNER = [
    "Security Engineer",
    "security engineer",
    "SECURITY ENGINEER",
    "Cybersecurity Engineer",
    "Cyber Security Analyst",
    "Cyber-Security Analyst",
    "cyber_security analyst",
    "CyberSecurity Lead",
    "AppSecurity Lead",
    "Product Security Engineer",
    "Security Engineers",
    "Pentester",
    "Pentest Lead",
    "Pentests and Red Team",
    "Threat Intelligence Analyst",
    "Threats Researcher",
    "Ingénieur Cybersécurité",
    "Ingénieur cyber sécurité (H/F)",
    "Ｓｅｃｕｒｉｔｙ Engineer",  # full width letters
    "Head of Security, AI",
    "AI Engineer, Security",
    "AI Engineer (Security team)",
    "Senior Engineer/Security",
    "Engineer.Security",
    "Insider Threat Engineer",
    "CYBER Analyst",
    "Applied AI Engineer, Cyber harness",
    "Sécurité: security operations",
]

KEPT_BY_OWNER = [
    "Applied AI Engineer",
    "Forward Deployed Engineer",
    "GenAI Engineer",
    "Machine Learning Engineer",
    "Software Engineer, Agents",
    "Ingénieur IA générative (H/F)",
    "Securities Trading Engineer",  # securities is not security
    "Secure Systems Engineer",
    "Data Engineer, Payments",
    "LLM Evaluation Engineer",
    "Solutions Architect",
    "",
]


@pytest.mark.parametrize("title", REFUSED_BY_OWNER)
def test_the_owners_rule_refuses_every_form_of_the_role(title: str) -> None:
    assert matching_rule([OWNER], "Example Robotics", title) is OWNER


@pytest.mark.parametrize("title", KEPT_BY_OWNER)
def test_the_owners_rule_keeps_other_roles(title: str) -> None:
    assert matching_rule([OWNER], "Example Robotics", title) is None


def test_the_company_counts_as_the_sweep_counts_it() -> None:
    assert matching_rule([OWNER], "Northwind Security", "Applied AI Engineer") is OWNER


@pytest.mark.parametrize(
    "title",
    ["Security Engineer", "security engineer", "Security Engineers", "Senior Security-Engineer"],
)
def test_a_multi_word_term(title: str) -> None:
    assert matching_rule([SECURITY_ONLY], "Example Robotics", title) is SECURITY_ONLY


@pytest.mark.parametrize("title", ["Security Analyst", "Engineer, Security", "AI Engineer"])
def test_a_multi_word_term_needs_its_words(title: str) -> None:
    assert matching_rule([SECURITY_ONLY], "Example Robotics", title) is None


@pytest.mark.parametrize(
    "text",
    ["No cybersecurity roles.", "No sales roles", "", "never: ", "(only a remark)"],
)
def test_a_rule_without_terms_matches_nothing(text: str) -> None:
    # The sweep's own reading of a rule (declared open point in the PR body).
    rule = NeverApplyRule.parse(text)
    assert matching_rule([rule], "Example Robotics", "Security Engineer") is None


def _posting(title: str, text: str = "") -> Job:
    return Job(company="Example Robotics", title=title, posting_text=text)


def test_never_apply_codes() -> None:
    plain = _posting("Applied AI Engineer")
    assert guardrails.never_apply(plain, [OWNER], swept=False, mode=Mode.review) is None
    assert guardrails.never_apply(plain, [OWNER], swept=True, mode=Mode.review) == (
        "never_apply_role"
    )
    assert guardrails.never_apply(plain, None, swept=False, mode=Mode.review) == (
        "rules_unreadable"
    )
    assert guardrails.never_apply(plain, [], swept=False, mode=Mode.review) is None


def test_full_auto_reads_the_first_1500_characters_of_the_text() -> None:
    near = _posting("Applied AI Engineer", "x " * 700 + "threat models")  # starts at 1400
    far = _posting("Applied AI Engineer", "x " * 760 + "threat models")  # starts at 1520
    for mode in (Mode.manual, Mode.review):
        assert guardrails.never_apply(near, [OWNER], swept=False, mode=mode) is None
    assert guardrails.never_apply(near, [OWNER], swept=False, mode=Mode.full_auto) == (
        "never_apply_role"
    )
    assert guardrails.never_apply(far, [OWNER], swept=False, mode=Mode.full_auto) is None


def test_the_match_is_linear() -> None:
    title = "a" * 200_000 + " security"
    assert matching_rule([OWNER], "", title) is OWNER


# --- the form address ----------------------------------------------------------------------------

ASHBY = "3f1c2a9e-5b7d-4c11-9a2e-7d0b6c5e4f31"
LEVER = "0b7a3c1d-2e4f-4a6b-8c9d-1e2f3a4b5c6d"

SAME_KEY = [
    (f"ashby:{ASHBY}", f"https://jobs.ashbyhq.com/example-robotics/{ASHBY}"),
    (f"ashby:{ASHBY}", f"https://jobs.ashbyhq.com/example-robotics/{ASHBY}/application"),
    (f"ashby:{ASHBY}", f"http://jobs.ashbyhq.com/Example-Robotics/{ASHBY.upper()}/"),
    (f"ashby:{ASHBY}", f"https://jobs.ashbyhq.com/other-org/{ASHBY}?utm_source=x"),
    (f"ashby:{ASHBY}", f"https://example.org/careers?ashby_jid={ASHBY}"),
    (f"ashby:{ASHBY}", f"https://example.org/careers/?ashby_jid={ASHBY.replace('-', '')}#apply"),
    ("greenhouse:4012345", "https://boards.greenhouse.io/examplerobotics/jobs/4012345"),
    ("greenhouse:4012345", "https://job-boards.greenhouse.io/examplerobotics/jobs/4012345"),
    ("greenhouse:4012345", "https://job-boards.eu.greenhouse.io/examplerobotics/jobs/4012345"),
    ("greenhouse:4012345", "https://boards.greenhouse.io/examplerobotics/jobs/4012345?gh_src=a"),
    ("greenhouse:4012345", "https://boards.greenhouse.io/embed/job_app?for=x&token=4012345"),
    ("greenhouse:4012345", "https://example.org/careers/ai?gh_jid=4012345"),
    ("greenhouse:4012345", "https://www.example.org/jobs/?gh_jid=04012345&utm_medium=email"),
    (f"lever:{LEVER}", f"https://jobs.lever.co/example-robotics/{LEVER}"),
    (f"lever:{LEVER}", f"https://jobs.lever.co/example-robotics/{LEVER}/apply"),
    (f"lever:{LEVER}", f"https://jobs.eu.lever.co/example-robotics/{LEVER}?lever-source=x"),
    ("url:http://example.org/careers/42", "https://www.example.org/careers/42/"),
    ("url:http://example.org/careers/42", "http://EXAMPLE.org:80/careers/42#top"),
    ("url:http://example.org/careers/42", "https://example.org:443/careers/42?utm_campaign=a"),
]


@pytest.mark.parametrize(("key", "url"), SAME_KEY)
def test_canonical_key(key: str, url: str) -> None:
    assert canonical_key(url) == key


DIFFERENT = [
    ("https://boards.greenhouse.io/a/jobs/4012345", "https://boards.greenhouse.io/a/jobs/4012346"),
    ("https://example.org/careers?gh_jid=1", "https://example.org/careers?gh_jid=2"),
    (f"https://jobs.lever.co/a/{LEVER}", f"https://jobs.ashbyhq.com/a/{LEVER}"),
    ("https://example.org/careers/42", "https://example.org/Careers/42"),
    ("https://example.org/careers/42", "https://example.org/careers/43"),
    ("https://example.org/careers?id=1", "https://example.org/careers?id=2"),
    ("https://example.org/careers/42", "https://example.com/careers/42"),
]


@pytest.mark.parametrize(("one", "two"), DIFFERENT)
def test_different_forms_have_different_keys(one: str, two: str) -> None:
    assert canonical_key(one) != canonical_key(two)


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "   ",
        "mailto:jobs@example.org",
        "javascript:alert(1)",
        "ftp://example.org/a",
        "/a",
    ],
)
def test_no_form_address(url: str | None) -> None:
    assert canonical_key(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://boards.greenhouse.io/a/jobs/notanumber",
        "https://jobs.ashbyhq.com/a/not-a-uuid",
        "https://jobs.lever.co/a",
        "https://example.org/careers?gh_jid=12a",
        "https://example.org/careers?ashby_jid=nope",
    ],
)
def test_an_unrecognised_ats_address_is_compared_as_an_address(url: str) -> None:
    assert (canonical_key(url) or "").startswith("url:")


@pytest.mark.parametrize(
    ("url", "ats"),
    [
        (f"https://jobs.ashbyhq.com/a/{ASHBY}", "ashby"),
        ("https://job-boards.eu.greenhouse.io/a/jobs/1", "greenhouse"),
        (f"https://jobs.lever.co/a/{LEVER}", "lever"),
        ("https://example.org/careers?gh_jid=1", "greenhouse"),
        (f"https://example.org/careers?ashby_jid={ASHBY}", "ashby"),
        ("https://example.org/careers/1", "other"),
        ("https://greenhouse.io.example.org/a/jobs/1", "other"),
        ("https://notlever.co/a", "other"),
        ("mailto:jobs@example.org", "other"),
    ],
)
def test_ats_of(url: str, ats: str) -> None:
    assert guardrails.ats_of(url) == ats


def test_a_stub_address_reads_as_the_ats_it_names() -> None:
    base = "http://127.0.0.1:8123"
    url = f"{base}/ashby/example-robotics/{ASHBY}/application"
    read = guardrails.unstub(url, base)
    assert guardrails.ats_of(read) == "ashby"
    assert canonical_key(read) == f"ashby:{ASHBY}"
    assert guardrails.unstub(url, None) == url
    assert guardrails.unstub(url, "http://127.0.0.1:8124") == url
    assert guardrails.unstub(f"{base}/sites/x/careers/y", base) == f"{base}/sites/x/careers/y"


# --- the same role -------------------------------------------------------------------------------

SAME_TITLE = [
    ("Applied AI Engineer", "applied ai engineer"),
    ("Applied AI Engineer", "Applied AI Engineer (H/F)"),
    ("Applied AI Engineer", "Applied AI Engineer H/F"),
    ("Applied AI Engineer", "Applied AI Engineer (F/H)"),
    ("Applied AI Engineer", "Applied AI Engineer (m/w/d)"),
    ("Applied AI Engineer", "Applied AI Engineer - f/m/d"),
    ("Applied AI Engineer", "Applied AI Engineer (all genders)"),
    ("Applied AI Engineer", "Applied  AI  Engineer"),
    ("Applied AI Engineer", "Applied AI-Engineer"),
    ("Ingénieur IA", "Ingenieur IA (H/F)"),
    ("Ingénieur IA", "INGÉNIEUR IA"),
    ("Applied AI Engineer", "Ａｐｐｌｉｅｄ AI Engineer"),
]
OTHER_TITLE = [
    ("Applied AI Engineer", "Senior Applied AI Engineer"),
    ("Applied AI Engineer", "Applied AI Engineer II"),
    ("Applied AI Engineer", "Applied AI Engineers Lead"),
    ("Applied AI Engineer", "Applied AI Engineer (Paris)"),
    ("Applied AI Engineer", "Applied ML Engineer"),
    ("Engineer H", "Engineer"),  # one letter is not a gender marker
]


@pytest.mark.parametrize(("one", "two"), SAME_TITLE)
def test_same_title(one: str, two: str) -> None:
    assert title_key(one) == title_key(two)


@pytest.mark.parametrize(("one", "two"), OTHER_TITLE)
def test_other_title(one: str, two: str) -> None:
    assert title_key(one) != title_key(two)


@pytest.mark.parametrize(
    ("one", "two", "same"),
    [
        ("Example Robotics", "example robotics", True),
        ("Example Robotics", "Example Robotics, Inc.", True),
        ("Example Robotics", "Example Robotics SAS", True),
        ("Example Robotics", "Example Robotics GmbH", True),
        ("Example Robotics", "Example Robotics Ltd", True),
        ("Example Robotics", "Example-Robotics", True),
        ("Écoles Exemple", "Ecoles Exemple SA", True),
        ("SA", "sa", True),  # a name made of a legal form keeps it
        ("Example Robotics", "Example Robotics Europe", False),
        ("Example Robotics", "Example", False),
        ("Northwind Labs", "Northwind", False),
    ],
)
def test_company_key(one: str, two: str, same: bool) -> None:
    assert (company_key(one) == company_key(two)) is same


# --- states --------------------------------------------------------------------------------------


def _application(state: State) -> Application:
    return Application(id=uuid.uuid4(), state=state, submitted_at=None)


def test_every_state_has_a_row() -> None:
    assert set(states.TRANSITIONS) == set(State)


@pytest.mark.parametrize(("current", "to"), list(product(State, State)))
def test_move_follows_the_table(current: State, to: State) -> None:
    application = _application(current)
    if to in states.TRANSITIONS[current]:
        states.move(application, to, AT, by_gate=to in states.DECIDED)
        assert application.state is to
        assert application.updated_at == AT
    else:
        with pytest.raises(states.TransitionError):
            states.move(application, to, AT)
        assert application.state is current


def test_the_ends_have_no_way_out() -> None:
    for end in (*ENDED, State.submitted):
        assert states.TRANSITIONS[end] == frozenset()


def test_submit_only_after_a_decision() -> None:
    sources = {s for s, targets in states.TRANSITIONS.items() if State.submitting in targets}
    assert sources == {State.approved, State.held}


def test_submit_unknown_is_never_submitted_again_by_the_app() -> None:
    assert State.submitting not in states.TRANSITIONS[State.submit_unknown]
    assert states.TRANSITIONS[State.submit_unknown] == {State.submitted, State.ready}


def test_the_click_time_is_set_once_and_kept() -> None:
    application = _application(State.submitting)
    states.move(application, State.submit_unknown, AT)
    assert application.submitted_at == AT
    later = datetime(2026, 10, 15, tzinfo=UTC)
    states.move(application, State.ready, later)
    assert application.submitted_at == AT
    states.move(application, State.approved, later, by_gate=True)
    states.move(application, State.submitting, later)
    states.move(application, State.submitted, later)
    assert application.submitted_at == AT


def test_the_error_names_the_states_only() -> None:
    with pytest.raises(states.TransitionError, match=r"^an application cannot move from "):
        states.move(_application(State.queued), State.submitted, AT)
