"""The pure parts of A2: the never apply match, the form address key, the same role keys, the state
table. Accept and refuse tables in both directions (review checklist, "Heuristic code"); the
database side is in test_start.py.
"""

import random
import unicodedata
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
        ("https://www.linkedin.com/jobs/view/1?gh_jid=1", "other"),
        (f"https://fr.linkedin.com/jobs/view/1?ashby_jid={ASHBY}", "other"),
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


def _application(state: State, mode: Mode = Mode.full_auto) -> Application:
    return Application(id=uuid.uuid4(), state=state, mode=mode, submitted_at=None)


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
    # A13a: the Yes to a Full auto rehearsal is the one exit, to the review queue.
    for end in (*ENDED, State.submitted):
        wanted = {State.ready} if end is State.rehearsed else set()
        assert states.TRANSITIONS[end] == wanted


@pytest.mark.parametrize("mode", [Mode.review, Mode.manual])
def test_only_a_full_auto_rehearsal_leaves_rehearsed(mode: Mode) -> None:
    application = _application(State.rehearsed, mode)
    with pytest.raises(states.TransitionError):
        states.move(application, State.ready, AT)
    assert application.state is State.rehearsed


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


# --- issue #296: what a reader does not see, and lookalike letters --------------------------------

# Each row hides or disguises a term of the owner's rule: the rule must still refuse it.
HIDDEN_FROM_THE_RULE = [
    "Secu­rity Engineer",  # soft hyphen (the reviewer's probe)
    "Cyber­security Analyst",
    "Secu​rity Engineer",  # zero width space
    "Pen​test Lead",
    "Secu‌rity Engineer",  # zero width non-joiner
    "Secu‍rity Engineer",  # zero width joiner
    "Thr⁠eat Analyst",  # word joiner
    "﻿Security Engineer",  # BOM in front
    "Secu﻿rity Engineer",  # BOM inside
    "Secu‎rity Engineer",  # left to right mark
    "Secu‮rity Engineer",  # right to left override
    "Secu⁦rity Engineer",  # left to right isolate
    "Secu⁢rity Engineer",  # invisible times
    "Secu᠎rity Engineer",  # Mongolian vowel separator
    "Secu\U000e0041rity Engineer",  # tag letter
    "Secu\x07rity Engineer",  # control
    "Secu\x9frity Engineer",  # C1 control
    "Secu\ud800rity Engineer",  # lone surrogate
    "Security Engineer",  # private use
    "Secu͏rity Engineer",  # combining grapheme joiner (combining class 0)
    "Secu️rity Engineer",  # variation selector
    "Sécürity Engineer",  # combining accents
    "Secu⃝rity Engineer",  # enclosing circle
    "Secuㅤrity Engineer",  # Hangul filler
    "Secuᅟrity Engineer",  # Hangul choseong filler
    "Ѕесurity Engineer",  # Cyrillic S, e, c
    "Securitу Engineer",  # Cyrillic y
    "Securіty Engineer",  # Cyrillic i
    "Сyber Analyst",  # Cyrillic capital C
    "Тhreat Analyst",  # Cyrillic capital T
    "Pеntеst Lead",  # Cyrillic e twice
    "ЅΕСURITY ENGINEER",  # Cyrillic S, Greek E, Cyrillic C
    "ΤΗREΑΤ Analyst",  # Greek T, H, A, T
    "ꜱᴇᴄᴜʀɪᴛʏ Engineer",  # small capitals
    "Securıty Engineer",  # dotless i
    "Ｃｙｂｅｒ Analyst",  # full width
    "\U0001d412\U0001d41e\U0001d41c\U0001d42e\U0001d42b\U0001d422\U0001d42d\U0001d432 Engineer",
    "Ⓢⓔⓒⓤⓡⓘⓣⓨ Engineer",  # circled letters
    "Security\tEngineer",
    "Security\nEngineer",
    "  Security   Engineer  ",
    "Security　Engineer",  # ideographic space
    "SECURITY ENGINEER",  # thin space
    "security engineer",  # line separator
    "(Security) Engineer!!!",
    "«Security» Engineer",
    "#Security",
    "x " * 100_000 + "Secu­rity",  # huge, the term at the end
    "Secu" + "​" * 50_000 + "rity",  # a huge run of invisible characters
]

# True titles, other scripts and emoji included: the fold must not turn them into a refusal.
TRUE_TITLES = [
    "Applied AI Engineer",
    "Senior Applied AI Engineer",
    "Machine Learning Engineer, LLM Platform",
    "Forward Deployed Engineer (Paris)",
    "Ingénieur IA générative (H/F)",
    "Développeur Python confirmé",
    "Ingénieur Sûreté de fonctionnement",
    "Développeur·se Full Stack",
    "Securities Operations Analyst",
    "Social Securities Analyst",
    "Secure Payments Engineer",
    "Pentagon Analytics Engineer",
    "Threads Engineer",
    "Treasury Analyst",
    "Site Reliability Engineer",
    "Platform Engineer, Observability",
    "Prompt Engineer​",
    "Data­Engineer",
    "AI Research Engineer \U0001f916",
    "Team Lead \U0001f469‍\U0001f4bb Platform",
    "Инженер по машинному обучению",
    "Разработчик Python",
    "Специалист по данным",
    "Μηχανικός Λογισμικού",
    "Τεχνητή Νοημοσύνη, Μηχανικός",
    "Ingeniero de Datos",
    "Datenwissenschaftler (m/w/d)",
    "AI エンジニア",
    "机器学习工程师",
    "مهندس تعلم الآلة",
    "LLM Ops Engineer",
    "Head of AI",
    "Customer Success Manager",
    "Solutions Engineer (Benelux)",
    "Agent Engineer, Évaluations",
    "",
    "​​",
]

# Declared open points (PR body): still matched by nothing, each toward applying. A fix flips a row
# (the Braille blank and zero width spaces around a term moved to UNSEEN_SHAPES).
STILL_MISSED = [
    "S e c u r i t y Engineer",  # letters spaced out
    "Secu-rity Engineer",  # a visible hyphen inside the term
    "Anticyberfraud Analyst",  # a term inside a word that neither starts nor ends with it
    "Secu\trity Engineer",  # a tab, drawn as a gap like a space
    "Secսrity Engineer",  # Armenian u, a script outside the lookalike table
    "Ꮪecurity Engineer",  # Cherokee S
    "Secυrity Engineer",  # Greek small upsilon, read as its capital: "y"
    "Peηtest Lead",  # Greek small eta, read as its capital: "h"
    "Secμrity Engineer",  # Greek small mu, read as its capital: "m"
    "Securitγ Engineer",  # Greek gamma, not read
    "Seϲurity Engineer",  # lunate sigma, which NFKC makes a sigma
]


@pytest.mark.parametrize("title", HIDDEN_FROM_THE_RULE, ids=range(len(HIDDEN_FROM_THE_RULE)))
def test_the_owners_rule_sees_through_unseen_and_lookalike_characters(title: str) -> None:
    assert matching_rule([OWNER], "Example Robotics", title) is OWNER


@pytest.mark.parametrize("title", TRUE_TITLES, ids=range(len(TRUE_TITLES)))
def test_true_titles_stay_untouched_by_the_fold(title: str) -> None:
    assert matching_rule([OWNER], "Example Robotics", title) is None


@pytest.mark.parametrize("title", STILL_MISSED, ids=range(len(STILL_MISSED)))
def test_the_declared_open_points(title: str) -> None:
    assert matching_rule([OWNER], "Example Robotics", title) is None


def test_a_rule_in_another_script_matches_its_own_script() -> None:
    rule = NeverApplyRule.parse("Pas de cybersécurité: кибербезопасность, ασφάλεια")
    assert matching_rule([rule], "Example Robotics", "Кибербезопасность, аналитик") is rule
    assert matching_rule([rule], "Example Robotics", "Μηχανικός ασφάλειας") is rule
    assert matching_rule([rule], "Example Robotics", "Разработчик Python") is None


@pytest.mark.parametrize(
    ("text", "folded"),
    [
        ("Applied AI Engineer", "applied ai engineer"),
        ("Red-Team / Blue_Team", "red team blue team"),
        ("Sécurité", "securite"),
        ("Secu­rity", "security"),
        ("Security\tEngineer", "security engineer"),
        ("Ѕесurity", "security"),
        ("", ""),
    ],
)
def test_fold(text: str, folded: str) -> None:
    assert guardrails.fold(text) == folded


def test_the_company_key_reads_the_fold_too() -> None:
    assert company_key("Example​ Robotics SAS") == company_key("Example Robotics")
    assert company_key("Exаmple Robotics") == company_key("Example Robotics")


# --- the fold in every case, and every reviewer input across rounds -----------------------------


def test_every_lookalike_reads_one_letter_in_every_case() -> None:
    table = {chr(k): chr(v) for k, v in guardrails._LOOKALIKES.items()}
    # the table's letters and every letter whose case fold is one of them: their capitals
    letters = {c for c in map(chr, range(0x110000)) if c.isalpha() and c.casefold() in table}
    assert set(table) < letters
    for c in sorted(letters):
        forms = (c, c.upper(), c.lower(), c.casefold())
        assert {guardrails.fold(form) for form in forms} == {table[c.casefold()]}, f"U+{ord(c):04X}"


FOREIGN = NeverApplyRule.parse("Pas de sécurité: sécurité, sicherheit")
SHORT_TERMS = NeverApplyRule.parse("Short terms: soc, siem, ctf, iam, grc, red team")


def _greek(term: str) -> NeverApplyRule:
    return NeverApplyRule.parse(f"Not these roles: {term}")


# French and German titles: whether the owner's English rule refuses them (the foreign one does)
_FRENCH_AND_GERMAN = {
    "Développeur sécurité réseau": False,
    "Ingénieur Cybersécurité (H/F)": True,
    "IT-Sicherheitsingenieur (m/w/d)": False,
    "Ingenieur für Informationssicherheit": False,
}
_WRITTEN = (
    lambda t: unicodedata.normalize("NFC", t),
    lambda t: unicodedata.normalize("NFD", t),
    lambda t: unicodedata.normalize("NFD", t.upper()),
)
_KEPT_BY_BOTH = [
    "Менеджер по продажам",
    "Тестировщик ПО",
    "Аналитик данных",
    "Секретарь",
    "ΤΕΧΝΙΚΟΣ ΥΠΟΣΤΗΡΙΞΗΣ",
    "Οικονομικός Αναλυτής",
    "Сотрудник склада",
    "Социолог",
    "Securities Lawyer",
    "Data Engineer \u2764\ufe0f",
    "データエンジニア",
]

# Every input a review quoted, with its round (rule, title, refused): never delete a row.
REVIEWER_INPUTS = [
    ("1", OWNER, "Secu\u00adrity Engineer", True),
    ("2", _greek("γραμματέας"), "Γραμματέας", True),
    ("2", _greek("μηχανικός ασφαλείας"), "Μηχανικός Ασφαλείας", True),
    ("2", _greek("μηχανικός ασφαλείας"), "ΜΗΧΑΝΙΚΟΣ ΑΣΦΑΛΕΙΑΣ", True),
    ("2", _greek("υποδομές"), "Υποδομές Cloud", True),
    ("2", _greek("ΜΗΧΑΝΙΚΟΣ"), "μηχανικός λογισμικού", True),
    *(
        ("2", rule, written(title), refused)
        for title, by_owner in _FRENCH_AND_GERMAN.items()
        for written in _WRITTEN
        for rule, refused in ((FOREIGN, True), (OWNER, by_owner))
    ),
    ("2", OWNER, "Ｔｈｒｅａｔ Analyst", True),  # full width
    ("2", OWNER, "𝕊𝕖𝕔𝕦𝕣𝕚𝕥𝕪 Engineer", True),  # double struck
    ("2", OWNER, "𝔖𝔢𝔠𝔲𝔯𝔦𝔱𝔶 Engineer", True),  # Fraktur
    ("2", OWNER, "𝘚𝘦𝘤𝘶𝘳𝘪𝘵𝘺 Engineer", True),  # sans serif italic
    ("2", OWNER, "ˢᵉᶜᵘʳⁱᵗʸ Engineer", True),  # modifier letters
    ("2", OWNER, "Cyber Sеcurity", True),  # Cyrillic e
    ("2", OWNER, "Pentеster", True),  # Cyrillic e
    ("2", OWNER, "Threаt Intelligence", True),  # Cyrillic a
    ("2", SHORT_TERMS, "Red\u00a0Team Lead", True),  # no-break space
    ("2", OWNER, "Cyber\u2011Security Analyst", True),  # non-breaking hyphen
    ("2", OWNER, "S̲e̲c̲u̲r̲i̲t̲y̲ Engineer", True),  # combining low line
    ("2", NeverApplyRule.parse("No security: Secu\u00adrity"), "Security Engineer", True),
    *(("2", rule, title, False) for title in _KEPT_BY_BOTH for rule in (OWNER, SHORT_TERMS)),
    ("2", NeverApplyRule.parse("Без безопасности: безопасность"), "БЕЗОПАСНОСТЬ ИНФОРМАЦИИ", True),
]


@pytest.mark.parametrize(
    ("round_", "rule", "title", "refused"), REVIEWER_INPUTS, ids=range(len(REVIEWER_INPUTS))
)
def test_every_reviewer_input_keeps_its_outcome(
    round_: str, rule: NeverApplyRule, title: str, refused: bool
) -> None:
    assert (matching_rule([rule], "Example Robotics", title) is rule) is refused


@pytest.mark.parametrize("title", _FRENCH_AND_GERMAN)
def test_nfc_and_nfd_fold_alike(title: str) -> None:
    nfc, nfd = (unicodedata.normalize(form, title) for form in ("NFC", "NFD"))
    assert guardrails.fold(nfc) == guardrails.fold(nfd)


_CASED_ROWS = [
    *((OWNER, title) for title in REFUSED_BY_OWNER + KEPT_BY_OWNER),
    *((OWNER, title) for title in HIDDEN_FROM_THE_RULE + TRUE_TITLES + STILL_MISSED),
    *((rule, title) for _, rule, title, _ in REVIEWER_INPUTS),
]


@pytest.mark.parametrize(("rule", "title"), _CASED_ROWS, ids=range(len(_CASED_ROWS)))
def test_the_match_does_not_depend_on_the_case_of_the_title(
    rule: NeverApplyRule, title: str
) -> None:
    cases = (title, title.upper(), title.lower(), title.casefold())
    assert len({matching_rule([rule], "Example Robotics", case) is rule for case in cases}) == 1


OTHER_SCRIPT_TERMS = [
    "γραμματέας",
    "μηχανικός ασφαλείας",
    "υποδομές",
    "νοσηλευτής",
    "ζωολόγος",
    "γεωλόγος",
    "безопасность",
    "инженер-тестировщик",
]
_RULE_CASES = {"lower": str.lower, "upper": str.upper, "title": str.title}
_TITLE_CASES = {
    **_RULE_CASES,
    "casefold": str.casefold,
    "upper NFD": lambda t: unicodedata.normalize("NFD", t.upper()),
}


@pytest.mark.parametrize(
    ("term", "rule_case", "title_case"),
    list(product(OTHER_SCRIPT_TERMS, _RULE_CASES, _TITLE_CASES)),
)
def test_a_rule_in_another_script_matches_in_every_case(
    term: str, rule_case: str, title_case: str
) -> None:
    rule = _greek(_RULE_CASES[rule_case](term))
    title = f"{_TITLE_CASES[title_case](term)}, Example Robotics"
    assert matching_rule([rule], "Example Robotics", title) is rule


@pytest.mark.parametrize(
    ("written", "other"),
    [
        ("ΜΗΧΑΝΙΚΗ ΑΕ", "Μηχανική ΑΕ"),
        ("ΜΗΧΑΝΙΚΗ ΑΕ", "μηχανική αε"),
        ("ΜΗΧΑΝΙΚΗ ΑΕ", unicodedata.normalize("NFD", "Μηχανική ΑΕ")),
        ("ΥΠΟΔΟΜΕΣ ΝΕΦΟΥΣ", "Υποδομές Νέφους"),
        ("ООО ПРИМЕР", "ООО Пример"),
    ],
)
def test_the_company_key_does_not_depend_on_case_in_any_script(written: str, other: str) -> None:
    assert company_key(written) == company_key(other)


# --- issue #296: the Greenhouse and Ashby addresses, both directions ------------------------------

OTHER_ASHBY = "9e8d7c6b-5a49-4382-9170-6f5e4d3c2b1a"

KEYED_AS_THE_ATS = [
    ("https://boards.greenhouse.io/Org/JOBS/4012345", "greenhouse:4012345"),
    ("https://boards.greenhouse.io/org/Jobs/4012345/", "greenhouse:4012345"),
    ("https://job-boards.greenhouse.io/org/JoBs/4012345?gh_src=x", "greenhouse:4012345"),
    ("https://boards-api.greenhouse.io/v1/boards/org/jobs/4012345", "greenhouse:4012345"),
    (
        "https://boards-api.greenhouse.io/v1/boards/org/jobs/4012345?questions=true",
        "greenhouse:4012345",
    ),
    ("https://BOARDS-API.greenhouse.io/V1/Boards/org/JOBS/4012345/", "greenhouse:4012345"),
    ("https://api.greenhouse.io/v1/boards/org/jobs/4012345", "greenhouse:4012345"),
    ("https://boards.greenhouse.io/Embed/Job_App?for=x&token=4012345", "greenhouse:4012345"),
    # another host's path is not read as Greenhouse's: only its gh_jid counts
    ("https://notgreenhouse.io/org/jobs/999?gh_jid=4012345", "greenhouse:4012345"),
    (f"https://jobs.ashbyhq.com/org/{ASHBY}", f"ashby:{ASHBY}"),
    (f"https://ashbyhq.com/org/{ASHBY}", f"ashby:{ASHBY}"),
    (f"https://evilashbyhq.com/org/{OTHER_ASHBY}?ashby_jid={ASHBY}", f"ashby:{ASHBY}"),
]

KEYED_AS_AN_ADDRESS = [
    "https://boards-api.greenhouse.io/v1/boards/org/jobs",
    "https://boards-api.greenhouse.io/v1/boards/org/departments/4012345",
    "https://boards-api.greenhouse.io/v1/boards/org/offices/4012345",
    "https://boards-api.greenhouse.io/v2/boards/org/jobs/4012345",
    "https://boards.greenhouse.io/org/jobsx/4012345",
    "https://boards.greenhouse.io/org/departments/4012345",
    "https://boards.greenhouse.io/jobs/4012345",
    "https://boards.greenhouse.io/org/jobs/4012345abc",
    "https://notgreenhouse.io/org/jobs/4012345",
    "https://greenhouse.io.example.org/org/jobs/4012345",
    f"https://notashbyhq.com/org/{ASHBY}",
    f"https://jobs.ashbyhq.com.example.org/org/{ASHBY}",
]


@pytest.mark.parametrize(("url", "key"), KEYED_AS_THE_ATS)
def test_the_ats_neighbours_keep_their_key(url: str, key: str) -> None:
    assert canonical_key(url) == key


@pytest.mark.parametrize("url", KEYED_AS_AN_ADDRESS)
def test_the_ats_neighbours_are_ordinary_addresses(url: str) -> None:
    assert (canonical_key(url) or "").startswith("url:")


# --- issue #296: the state table as properties ----------------------------------------------------

CLICK_OUTCOMES = {State.submitted, State.submit_unknown, State.refused_by_site}


def test_once_the_click_may_have_happened_only_its_outcomes_follow() -> None:
    assert states.TRANSITIONS[State.submitting] == CLICK_OUTCOMES
    # none of them frees the unique indexes, and the two clicked ones record when
    assert not CLICK_OUTCOMES & set(ENDED)
    for end in (*ENDED, State.ready, State.approved):
        with pytest.raises(states.TransitionError):
            states.move(_application(State.submitting), end, AT)
    for clicked in states.CLICKED:
        application = _application(State.submitting)
        states.move(application, clicked, AT)
        assert application.submitted_at == AT


def test_no_path_reaches_submitting_around_a_decision() -> None:
    # Cut the two states only the gate enters: nothing else leads to submitting.
    for start in set(State) - states.DECIDED - {State.submitting}:
        seen, todo = {start}, [start]
        while todo:
            for to in states.TRANSITIONS[todo.pop()] - states.DECIDED:
                if to not in seen:
                    seen.add(to)
                    todo.append(to)
        assert State.submitting not in seen, start


def _walk(rng: random.Random, start: State, *, gate: bool, dry_run: bool) -> list[State]:
    """A random walk through `states.move`, the gate's flag passed only when `gate`."""
    # Full auto, so the walk may take the one exit of `rehearsed` (A13a) too.
    application = Application(
        id=uuid.uuid4(), state=start, mode=Mode.full_auto, dry_run=dry_run, submitted_at=None
    )
    path, clicked = [start], False
    for _ in range(40):
        targets = sorted(states.TRANSITIONS[State(application.state)], key=lambda s: s.value)
        if not targets:
            break
        to = rng.choice(targets)
        try:
            states.move(application, to, AT, by_gate=gate and to in states.DECIDED)
        except states.TransitionError:
            continue
        path.append(to)
        # the click time is set on the first clicked state and kept after it
        assert (application.submitted_at is not None) is (clicked or to in states.CLICKED), path
        clicked = application.submitted_at is not None
    return path


@pytest.mark.parametrize("start", [State.queued, State.ready, State.submit_unknown])
def test_random_walks_never_submit_without_the_gate_or_in_a_rehearsal(start: State) -> None:
    rng = random.Random(296)  # noqa: S311  # a fixed seed, so the walks repeat
    clicked = 0
    for _ in range(3000):
        assert State.submitting not in _walk(rng, start, gate=False, dry_run=False)
        assert State.submitting not in _walk(rng, start, gate=True, dry_run=True)
        path = _walk(rng, start, gate=True, dry_run=False)
        for i in (i for i, at in enumerate(path) if at is State.submitting):
            clicked += 1
            assert path[i - 1] in states.DECIDED, path
            if i + 1 < len(path):
                assert path[i + 1] in CLICK_OUTCOMES, path
    assert clicked > 0  # the walks with the gate do reach the click


# --- issue #335: what a reader does not see, polytonic Greek, hosts, the same role keys ----------


def _unseen_characters() -> list[str]:
    """Drawn as nothing or as a blank, from Unicode's categories rather than the module's: every
    control but tab, line feed and carriage return (drawn as a gap), every format character, the
    Hangul fillers, the Braille blank, and the ends of the surrogate, private use and unassigned
    ranges."""
    found = [
        c
        for c in map(chr, range(0x110000))
        if unicodedata.category(c) in ("Cc", "Cf") and c not in "\t\n\r"
    ]
    found += ["\u115f", "\u1160", "\u3164", "\uffa0", "\u2800"]
    found += ["\ud800", "\udfff", "\ue000", "\uf8ff", "\U000f0000", "\U0010fffd", "\u0378"]
    return found


UNSEEN_CHARACTERS = _unseen_characters()

# Where an unseen character `{u}` stands, its rule must still refuse the title: read as nothing
# inside a term, as a gap beside it.
UNSEEN_SHAPES = [
    (OWNER, "Secu{u}rity Engineer"),
    (OWNER, "Pen{u}{u}{u}test Lead"),
    (OWNER, "Cyber{u}security Analyst"),
    (OWNER, "AI{u}Security{u}Engineer"),
    (OWNER, "Applied AI Engineer, Cyber{u}harness"),
    (OWNER, "Anti{u}cyber{u}fraud Analyst"),
    (OWNER, "AppThreat{u}s Lead"),
    (OWNER, "{u}Security{u}"),
    (SECURITY_ONLY, "Security{u}Engineer"),
    (SECURITY_ONLY, "Senior{u}Security{u}{u}Engineers"),
    (SHORT_TERMS, "Red{u}Team Lead"),
]


@pytest.mark.parametrize(("rule", "shape"), UNSEEN_SHAPES, ids=range(len(UNSEEN_SHAPES)))
def test_an_unseen_character_never_hides_a_term(rule: NeverApplyRule, shape: str) -> None:
    missed = [
        f"U+{ord(u):04X}"
        for u in UNSEEN_CHARACTERS
        if matching_rule([rule], "Example Robotics", shape.format(u=u)) is not rule
    ]
    assert not missed, missed[:20]


def test_an_unseen_character_in_a_true_title_refuses_nothing() -> None:
    titles = [title for title in TRUE_TITLES + KEPT_BY_OWNER if title.strip("\u200b")]
    every_place = [
        title[:i] + u + title[i:]
        for title in titles
        for u in ("\u200b", "\x1f", "\x85", "\u00ad", "\ufeff", "\u2800", "\u3164")
        for i in range(len(title) + 1)
    ]
    every_character = [title.replace(" ", u, 1) for title in titles for u in UNSEEN_CHARACTERS]
    refused = [
        ascii(title)
        for title in every_place + every_character
        if matching_rule([OWNER], "Example Robotics", title) is not None
    ]
    assert not refused, refused[:10]


def test_random_disguises_of_a_term_are_refused() -> None:
    # An independent generator: case, lookalike letters, unseen characters and glued neighbours.
    rng = random.Random(335)  # noqa: S311  # a fixed seed, so the titles repeat
    lookalikes: dict[str, list[str]] = {}
    for key, value in guardrails._LOOKALIKES.items():
        lookalikes.setdefault(chr(value), []).append(chr(key))
    missed = []
    for _ in range(400):
        term = rng.choice(OWNER.terms)
        letters = [rng.choice(lookalikes.get(c, [c])) if rng.random() < 0.3 else c for c in term]
        letters = [c.upper() if rng.random() < 0.5 else c for c in letters]
        written = ""
        for c in letters:
            written += c + "".join(rng.choices(UNSEEN_CHARACTERS, k=rng.choice((0, 0, 1, 2))))
        before = rng.choice(("", "Senior ", "App", "AI\u200b", "Data\x1f"))
        after = rng.choice(("", " Engineer", "\u200bEngineer", "\u2060Lead", "s"))
        title = before + written + after
        if matching_rule([OWNER], "Example Robotics", title) is not OWNER:
            missed.append(ascii(title))
    assert not missed, missed[:10]


def test_every_character_with_a_case_folds_alike_in_every_case() -> None:
    cased = [
        c
        for c in map(chr, range(0x110000))
        if len({c, c.upper(), c.lower(), c.casefold(), c.title()}) > 1
    ]
    split = [
        f"U+{ord(c):04X}"
        for c in cased
        if len({guardrails.fold(f) for f in (c, c.upper(), c.lower(), c.casefold(), c.title())}) > 1
    ]
    assert len(cased) > 2_000
    assert not split, split[:20]


# The polytonic letters with an iota subscript or adscript, which the case fold writes as an iota.
IOTA_SUBSCRIPTS = [
    c
    for c in map(chr, range(0x1F00, 0x2000))
    if unicodedata.category(c) in ("Ll", "Lt")
    and " WITH " in unicodedata.name(c, "")
    and unicodedata.name(c).endswith(("YPOGEGRAMMENI", "PROSGEGRAMMENI"))
]


@pytest.mark.parametrize("letter", IOTA_SUBSCRIPTS, ids=lambda c: f"U+{ord(c):04X}")
def test_a_greek_rule_with_an_iota_subscript_matches_in_every_case(letter: str) -> None:
    term = f"{letter}\u03b4\u03b7"
    written = (
        str.lower,
        str.upper,
        str.title,
        str.casefold,
        lambda t: unicodedata.normalize("NFD", t),
        lambda t: unicodedata.normalize("NFD", t.upper()),
    )
    for rule_form, title_form in product(written, written):
        rule = _greek(rule_form(term))
        assert matching_rule([rule], "Example Robotics", f"{title_form(term)} Lead") is rule


def test_the_iota_subscript_letters_are_the_63_of_the_issue() -> None:
    assert len(IOTA_SUBSCRIPTS) == 63
    assert guardrails.fold("\u1fb3") == guardrails.fold("\u0391\u0399") == "ai"


# --- the same role keys: the never apply reading, then glued -------------------------------------

SAME_ROLE_SHAPES = [
    "Applied AI Engi{u}neer",
    "Applied AI{u}Engineer",
    "{u}Applied AI Engineer{u}",
    "Applied{u}{u} AI Engineer",
    "Applied AI Engineer (H{u}/F)",
    "Applied AI Engineer{u}(m/w/d)",
]
SAME_COMPANY_SHAPES = [
    "Example{u}Robotics",
    "Exam{u}ple Robotics",
    "Example Robotics{u}Inc",
    "{u}Example Robotics, Inc.{u}",
]


def test_the_same_role_reads_through_unseen_characters() -> None:
    title, company = title_key("Applied AI Engineer"), company_key("Example Robotics")
    missed = [
        (shape, f"U+{ord(u):04X}")
        for u in UNSEEN_CHARACTERS
        for shape, key, of in (
            *((shape, title, title_key) for shape in SAME_ROLE_SHAPES),
            *((shape, company, company_key) for shape in SAME_COMPANY_SHAPES),
        )
        if of(shape.format(u=u)) != key
    ]
    assert not missed, missed[:20]


def test_the_same_role_reads_lookalike_letters_as_latin() -> None:
    table = {chr(k): chr(v) for k, v in guardrails._LOOKALIKES.items()}
    missed = []
    for text, of in (("Applied AI Engineer", title_key), ("Example Robotics", company_key)):
        for i, c in enumerate(text):
            for lookalike in (k for k, v in table.items() if v == c.lower()):
                for form in {lookalike, lookalike.upper()}:
                    written = text[:i] + form + text[i + 1 :]
                    if of(written) != of(text):
                        missed.append(ascii(written))
    assert not missed, missed[:10]


@pytest.mark.parametrize(
    ("one", "two", "same"),
    [
        ("Front End Engineer", "Frontend Engineer", True),
        ("Front End Engineer", "Front-End Engineer", True),
        ("Applied AI Engineer", "APPLIED AI ENGINEER (F/H)", True),
        ("Applied AI Engineer", "Applied AI Engineer II", False),
        ("Applied AI Engineer", "Applied AI Engineer (Paris)", False),
        ("Applied AI Engineer", "Applied AI Engineers Lead", False),
    ],
)
def test_title_key_glues_its_words(one: str, two: str, same: bool) -> None:
    assert (title_key(one) == title_key(two)) is same


@pytest.mark.parametrize(
    ("one", "two", "same"),
    [
        ("Example Robotics", "ExampleRobotics", True),
        ("Example Robotics", "EXAMPLE-ROBOTICS SAS", True),
        ("Example Robotics", "Example Robotics\u200bGmbH", True),
        ("Example Robotics", "Example Robotics Europe", False),
        ("Northwind Labs", "Northwind", False),
    ],
)
def test_company_key_glues_its_words(one: str, two: str, same: bool) -> None:
    assert (company_key(one) == company_key(two)) is same


# --- the host of a form address, as a browser reads it -------------------------------------------

HOSTS = [
    ("boards.greenhouse.io", "/examplerobotics/jobs/4012345", "greenhouse:4012345"),
    ("job-boards.eu.greenhouse.io", "/examplerobotics/jobs/4012345", "greenhouse:4012345"),
    ("boards-api.greenhouse.io", "/v1/boards/examplerobotics/jobs/4012345", "greenhouse:4012345"),
    ("jobs.ashbyhq.com", f"/example-robotics/{ASHBY}", f"ashby:{ASHBY}"),
    ("jobs.eu.lever.co", f"/example-robotics/{LEVER}/apply", f"lever:{LEVER}"),
    ("careers.example.org", "/jobs?gh_jid=4012345", "greenhouse:4012345"),
    ("www.example.org", "/careers/42", "url:http://example.org/careers/42"),
]


def _writings(host: str) -> list[str]:
    """`host` written the ways a browser still reads as it: the other dots IDNA knows, escaped
    dots, capitals, full width letters, a soft hyphen or a zero width space inside a label, a
    trailing dot."""
    first, _, rest = host.partition(".")
    return [
        *(host.replace(".", dot) for dot in ("\u3002", "\uff0e", "\uff61", "%2E", "%2e")),
        host.upper(),
        "".join(chr(ord(c) + 0xFEE0) for c in host),
        f"{first[:2]}\u00ad{first[2:]}.{rest}",
        f"{first[:2]}\u200b{first[2:]}.{rest}",
        f"{host}.",
        host.replace(".", "\uff0e") + "\uff0e",
    ]


@pytest.mark.parametrize(("host", "path", "key"), HOSTS)
def test_a_host_is_read_as_a_browser_reads_it(host: str, path: str, key: str) -> None:
    for scheme, written in product(("https", "http"), _writings(host)):
        assert canonical_key(f"{scheme}://{written}{path}") == key, ascii(written)
        assert guardrails.ats_of(f"{scheme}://{written}{path}") == guardrails.ats_of(
            f"https://{host}{path}"
        )


def test_an_international_host_has_one_key_however_it_is_written() -> None:
    key = "url:http://xn--bcher-kva.example/jobs/1"
    for written in (
        "b\u00fccher.example",
        "B\u00dcCHER.example",
        "bu\u0308cher.example",
        "xn--bcher-kva.example",
        "XN--BCHER-KVA.EXAMPLE",
        "b\u00fccher\uff0eexample",
        "b%C3%BCcher.example",
    ):
        assert canonical_key(f"https://{written}/jobs/1") == key, ascii(written)
    assert canonical_key("https://bucher.example/jobs/1") != key
    assert canonical_key("https://example\uff0eorg:8443/careers/42") == (
        "url:http://example.org:8443/careers/42"
    )
    assert canonical_key("https://example\uff0eorg:443/careers/42") == (
        "url:http://example.org/careers/42"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://example\uff0eorg\uff0fcareers/1",  # a full width slash in the host
        "https://example.org\uff1a443/careers/1",  # a full width colon
        "https://exa[mple.org/careers/1",
        "http://[::1/careers/1",
    ],
)
def test_an_address_no_browser_opens_shows_no_form(url: str) -> None:
    assert canonical_key(url) is None
    assert guardrails.ats_of(url) == "other"


def test_the_declared_cost_of_idna_2003_merges_two_hosts() -> None:
    # toward refusing: a second application to the other host is refused as the same form
    assert canonical_key("https://stra\u00dfe.example/jobs/1") == canonical_key(
        "https://strasse.example/jobs/1"
    )


def test_a_host_idna_refuses_is_keyed_as_written() -> None:
    url = "https://exa\u200emple.org/careers/42"  # a bidi mark, which IDNA refuses
    assert canonical_key(url) == "url:http://exa\u200emple.org/careers/42"


# What A2 stored for these addresses before hosts were read through IDNA, and their key now.
STORED_UNDER_OLDER_RULES = [
    (
        "url:http://boards\uff0egreenhouse\uff0eio/examplerobotics/jobs/4012345",
        "greenhouse:4012345",
    ),
    (
        "url:http://boards\u3002greenhouse\u3002io/examplerobotics/jobs/4012345",
        "greenhouse:4012345",
    ),
    (f"url:http://jobs\uff0eashbyhq\uff0ecom/example-robotics/{ASHBY}", f"ashby:{ASHBY}"),
    (f"url:http://jobs\uff61lever\uff61co/example-robotics/{LEVER}", f"lever:{LEVER}"),
    ("url:http://boards%2egreenhouse%2eio/examplerobotics/jobs/4012345", "greenhouse:4012345"),
    ("url:http://example\uff0eorg/careers/42", "url:http://example.org/careers/42"),
    ("url:http://example.org./careers/42", "url:http://example.org/careers/42"),
    ("url:http://b\u00fccher.example/jobs/1", "url:http://xn--bcher-kva.example/jobs/1"),
    ("url:http://www.example.org/careers/42", "url:http://example.org/careers/42"),
    # keyed as an address before the boards API was read as Greenhouse's
    (
        "url:http://boards-api.greenhouse.io/v1/boards/examplerobotics/jobs/4012345",
        "greenhouse:4012345",
    ),
]


@pytest.mark.parametrize(("stored", "key"), STORED_UNDER_OLDER_RULES)
def test_a_key_stored_under_older_rules_answers_to_its_key_now(stored: str, key: str) -> None:
    assert guardrails.current_key(stored) == key


def test_every_key_made_today_is_its_own_current_key() -> None:
    urls = [url for _, url in SAME_KEY] + [url for url, _ in KEYED_AS_THE_ATS]
    urls += KEYED_AS_AN_ADDRESS + [one for pair in DIFFERENT for one in pair]
    urls += [f"https://{w}{path}" for host, path, _ in HOSTS for w in _writings(host)]
    urls += ["https://www.www.example.org/careers/42", "https://WWW.example.org:8080/a?b=&c=d+e"]
    for url in urls:
        key = canonical_key(url)
        assert key is not None, ascii(url)
        assert guardrails.current_key(key) == key, ascii(url)
    for key in ("url:a11/0", "url:", "greenhouse:1", "url:mailto:jobs@example.org"):
        assert guardrails.current_key(key) == key
