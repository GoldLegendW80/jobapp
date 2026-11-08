"""What one answer of a posting's own address says (app/jobs/liveness.py, SPEC-jobs "Liveness"):
the tables of pages that close a posting by themselves, that only propose it, that say nothing,
and that cannot tell; in English, French, German, Dutch, Spanish and Polish; with the near misses
around every rule (a time or condition clause, a compound word, a badge, another page)."""

import pytest

from app.jobs import liveness
from app.jobs.liveness import CLOSED, MAY_BE_CLOSED, NO_CLOSURE, UNCONFIRMED, judge

POSTING = "https://jobs.example-robotics.example/careers/4012345-robotics-engineer"
# Enough plain copy that a page is never "too little text to tell".
COPY = (
    "Example Robotics builds warehouse robots. You will design motion planning software, "
    "review code with the team and ship to customers across Europe. We offer a hybrid week, "
    "a learning budget and a team that cares about clear writing. "
) * 2

# A clause of the posting's own page that says, whole, that the posting is closed.
CLOSED_PAGES = [
    "This job is no longer available.",
    "Sorry, this job is no longer available. Browse our other openings.",
    "We're sorry, but this position has been filled.",
    "The job you are looking for is no longer open.",
    "This job posting has expired.",
    "This role is closed",
    "THIS POSITION IS NO LONGER ACCEPTING APPLICATIONS",
    "We are no longer accepting applications for this role.",
    "Unfortunately, the position you applied for has been filled.",
    "Job not found",
    "Page not found",
    "The page you are looking for doesn't exist.",
    "Sorry, this job was removed at 08:47 p.m. (UTC) on Wednesday, Oct 14, 2026",
    "This job was closed on 15 December 2026.",
    "You can't view this job because it's not available at this time.",
    "Cette offre n\u2019est plus disponible.",
    "Cette offre n'est plus disponible",
    "D\u00e9sol\u00e9, cette offre a expir\u00e9.",
    "L\u2019offre que vous recherchez n\u2019est plus en ligne.",
    "Cette annonce a \u00e9t\u00e9 pourvue.",
    "Ce poste a d\u00e9j\u00e0 \u00e9t\u00e9 pourvu.",
    "Le recrutement est termin\u00e9 pour ce poste.",
    "Nous n'acceptons plus de candidatures pour cette offre.",
    "Leider ist diese Stelle bereits besetzt.",
    "Diese Stelle ist nicht mehr verf\u00fcgbar.",
    "Die Stellenanzeige ist abgelaufen.",
    "Wir nehmen leider keine Bewerbungen mehr an.",
    "Deze vacature is niet meer gepubliceerd.",
    "Deze vacature is gesloten.",
    "We accepteren geen sollicitaties meer.",
    "Esta oferta ya no est\u00e1 disponible.",
    "La oferta ha expirado.",
    "Ya no se aceptan candidaturas para esta oferta.",
    "Og\u0142oszenie wygas\u0142o",
    "To og\u0142oszenie jest ju\u017c nieaktualne.",
    "Pracodawca zako\u0144czy\u0142 zbieranie zg\u0142osze\u0144 na to stanowisko.",
    # A closure with no subject, scoped to this posting.
    "Applications closed for this role.",
    "Applications for this position are now closed.",
    "We are not accepting applications.",
    "We're no longer accepting candidates for this role.",
    "Not accepting applications for this job.",
    "Les candidatures sont closes pour ce poste.",
    "Ya no se admiten candidaturas para este puesto.",
    "Rekrutacja na to stanowisko zosta\u0142a zako\u0144czona.",
    # A 404 next to its word; a soft 404 by its sentence.
    "Error 404",
    "404 Not Found",
    "404 error",
    "404 page",
    "Error 404: page not found",
    "Page not found",
    # Folded before matching: hidden characters, lookalike spaces, full width letters.
    "Cette\u00a0offre n\u2019est\u200b plus disponible.",
    "\uff34his job is no longer available.",
    "\u201cThis job is no longer available.\u201d",
]

# Closure words that only propose: a badge, words inside a longer clause, a listing page.
PROPOSED_PAGES = [
    "Job expired",
    "Expired job",
    "Poste pourvu",
    "Offre expir\u00e9e",
    "Offre cl\u00f4tur\u00e9e",
    "Vacature vervuld",
    "Oferta cerrada",
    "This job is closed to applicants outside the EU.",
    "Note that this position has been filled by an internal candidate last week.",
    "Our team: the role is no longer open to external candidates.",
    "Cette annonce n'est plus disponible depuis la refonte de notre site carri\u00e8re.",
    "Die Anzeige ist abgelaufen und wird bald entfernt.",
    # A whole closure clause that names nothing: this posting's badge, or another's in a list.
    "No longer accepting applications",
    "Not accepting applications",
    "Applications are closed.",
    "Sorry, applications are closed.",
    "Applications closed",
    "The application deadline has passed.",
    "The recruitment process is closed.",
    "Les candidatures sont closes.",
    "Candidatures cl\u00f4tur\u00e9es",
    "Le recrutement est termin\u00e9.",
    "Es werden keine Bewerbungen mehr angenommen.",
    "Die Bewerbungsfrist ist abgelaufen.",
    "Sollicitaties gesloten",
    "De sollicitatietermijn is verstreken.",
    "Ya no se aceptan candidaturas.",
    "Oferta cerrada el 15/12/2025",
    "Kandydaci nie s\u0105 ju\u017c akceptowani",
    "Rekrutacja zako\u0144czona",
    "Similar jobs: Robotics Engineer \u00b7 Example Corp \u00b7 Applications closed",
    "12 jobs found",
    "3 offres trouv\u00e9es",
    "Search for jobs page is loaded",
]

# Pages of an open posting, or that say nothing about closing: never closed, never proposed.
OPEN_PAGES = [
    "Applications will be accepted until the position has been filled.",
    "We will contact shortlisted candidates once applications have closed.",
    "Apply before the position is filled.",
    "This role is closed-loop control of the platform.",
    "This role is filled on a rolling basis.",
    "Once the application form has been filled out, we will be in touch.",
    "This job is not closed.",
    "Not found? Contact us.",
    "Our office is closed on Sundays.",
    "The role is open to candidates in France and Spain.",
    "Die Stelle ist ab sofort zu besetzen.",
    "Die Stelle ist in Vollzeit zu besetzen.",
    "Cette offre est disponible jusqu'au 30 novembre.",
    "Postulez avant que le poste soit pourvu.",
    "Si l'offre est pourvue, nous vous recontacterons.",
    "Une fois les candidatures closes, nous vous recontacterons.",
    "La oferta sigue abierta hasta que se cubra la vacante.",
    "Wenn die Stelle besetzt ist, informieren wir Sie.",
    "Zodra de vacature vervuld is, sluiten we de procedure.",
    "Ogloszenie jest aktualne.",
    "Apply now",
    "Postuler",
    "Our closing ceremony is open to all employees.",
    "Closed captions are available for every interview.",
    "We work on closed-source and open-source projects.",
    "The page you are reading describes the role.",
    "404 Robotics Street, Paris",
    # A number that is not an error: an area code, a room, a count.
    "For accommodations during the hiring process call (404) 555-0100.",
    "Tel. +1 (404) 555 0100",
    "(404) 555-0100",
    "Tel: 404",
    "Call us: 404 555 0100",
    "Contact: 404",
    "Room 404",
    "Office: 404",
    "Interviews take place in room 404.",
    "We are not accepting applications from recruitment agencies.",
    "Not accepting applications by e-mail: use the form below.",
    "Jobs not found? Search again.",
    "Diese Stelle ist nicht mehr besetzt.",
    "Job type: full time. Expiry date: 2027-01-31.",
    "Applications reviewed on a rolling basis.",
    "Positions are filled quickly, so apply early.",
]


def page(text: str) -> str:
    return f"{COPY}\n{text}\n{COPY}"


@pytest.mark.parametrize("text", CLOSED_PAGES)
def test_a_closure_on_the_postings_own_page_closes_it(text: str) -> None:
    found = judge(200, page(text), POSTING, POSTING)
    assert found.verdict == CLOSED, found
    assert found.reason == "page_says"
    assert found.sentence  # the page's own words, quoted


@pytest.mark.parametrize("text", CLOSED_PAGES)
def test_the_same_closure_on_a_page_the_address_led_to_is_only_proposed(text: str) -> None:
    elsewhere = "https://jobs.example-robotics.example/careers"
    found = judge(200, page(text), POSTING, elsewhere)
    assert found.verdict == MAY_BE_CLOSED, found


@pytest.mark.parametrize("text", PROPOSED_PAGES)
def test_closure_words_that_do_not_say_it_whole_are_a_proposal(text: str) -> None:
    found = judge(200, page(text), POSTING, POSTING)
    assert found.verdict == MAY_BE_CLOSED, found
    assert found.reason == "page_hints"


@pytest.mark.parametrize("text", OPEN_PAGES)
def test_a_page_that_says_no_closure_is_left_alone(text: str) -> None:
    found = judge(200, page(text), POSTING, POSTING)
    assert found == liveness.Finding(NO_CLOSURE, "none"), text


def test_the_quote_is_the_sentence_as_the_page_writes_it() -> None:
    text = page("Cette offre n\u2019est plus disponible. Voir nos autres offres.")
    found = judge(200, text, POSTING, POSTING)
    assert found.sentence == "Cette offre n\u2019est plus disponible."


def test_a_long_sentence_is_quoted_within_its_bound() -> None:
    long = "This job is no longer available " + "x" * 2000
    found = judge(200, page(f"Sorry, this job is no longer available, {long}"), POSTING, POSTING)
    assert found.verdict == CLOSED
    assert found.sentence is not None
    assert len(found.sentence) == liveness.QUOTE_MAX


@pytest.mark.parametrize(
    ("status", "verdict", "reason"), [(404, CLOSED, "http_404"), (410, CLOSED, "http_410")]
)
def test_the_own_address_gone_closes_it(status: int, verdict: str, reason: str) -> None:
    assert judge(status, "", POSTING, POSTING) == liveness.Finding(verdict, reason)


def test_a_404_after_a_redirect_elsewhere_is_only_proposed() -> None:
    found = judge(404, "", POSTING, "https://jobs.example-robotics.example/careers")
    assert found == liveness.Finding(MAY_BE_CLOSED, "redirected")


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (401, "refused"),
        (403, "refused"),
        (407, "refused"),
        (451, "refused"),
        (429, "rate_limited"),
        (500, "server_error"),
        (502, "server_error"),
        (503, "server_error"),
        (204, "unreadable"),
        (304, "unexpected_answer"),
        (418, "unexpected_answer"),
    ],
)
def test_an_answer_that_refuses_or_fails_is_unconfirmed(status: int, reason: str) -> None:
    text = "" if status == 204 else page("This job is no longer available.")
    found = judge(status, text, POSTING, POSTING)
    if status == 204:
        assert found.verdict == UNCONFIRMED
        assert found.reason == reason
    else:
        assert found == liveness.Finding(UNCONFIRMED, reason)


@pytest.mark.parametrize(
    "text",
    [
        "Just a moment...",
        "Checking your browser before accessing the site.",
        "Attention Required! | Cloudflare",
        "Please complete the security check to access the site. Ray ID: 7d9f",
        "Verify you are human by completing the action below.",
        "V\u00e9rifiez que vous \u00eates humain",
        "Access denied",
    ],
)
def test_a_bot_check_is_unconfirmed_never_closed(text: str) -> None:
    assert judge(200, text, POSTING, POSTING) == liveness.Finding(UNCONFIRMED, "bot_check")


def test_bot_check_words_in_a_long_posting_are_its_copy() -> None:
    text = page("Just a moment to tell you about our team.") + COPY * 10
    assert judge(200, text, POSTING, POSTING).verdict == NO_CLOSURE


@pytest.mark.parametrize(
    "final",
    [
        "https://www.linkedin.com/authwall?trk=job",
        "https://jobs.example-robotics.example/login?next=/careers/4012345",
        "https://jobs.example-robotics.example/users/sign-in",
        "https://login.example-robotics.example/",
        "https://accounts.example-robotics.example/connexion",
    ],
)
def test_a_sign_in_wall_is_unconfirmed(final: str) -> None:
    found = judge(200, page("This job is no longer available."), POSTING, final)
    assert found == liveness.Finding(UNCONFIRMED, "sign_in")


@pytest.mark.parametrize(
    "text", ["", "Loading\u2026", "<noscript>Enable JavaScript</noscript>", "Apply"]
)
def test_too_little_text_is_unconfirmed(text: str) -> None:
    assert judge(200, text, POSTING, POSTING) == liveness.Finding(UNCONFIRMED, "unreadable")


def test_a_short_page_that_says_it_is_closed_is_closed() -> None:
    found = judge(200, "Ce poste n'est plus \u00e0 pourvoir.", POSTING, POSTING)
    assert found.verdict == CLOSED


def test_greenhouses_error_redirect_with_nothing_said_is_a_proposal() -> None:
    final = "https://jobs.example-robotics.example/careers?error=true"
    found = judge(200, page("Open positions"), POSTING, final)
    assert found == liveness.Finding(MAY_BE_CLOSED, "redirected")


@pytest.mark.parametrize(
    ("requested", "final", "own"),
    [
        (POSTING, POSTING, True),
        (POSTING, "", True),
        (POSTING, POSTING.replace("https://", "http://"), True),
        (
            POSTING,
            "https://www.jobs.example-robotics.example/careers/4012345-robotics-engineer",
            True,
        ),
        (POSTING, "https://jobs.example-robotics.example/en/careers/4012345", True),
        (POSTING, "https://jobs.example-robotics.example/careers", False),
        (POSTING, "https://jobs.example-robotics.example/careers?error=true", False),
        (POSTING, "https://other.example/careers/4012345", False),
        (
            "https://jobs.lever.example/acme/0b6e1b9e-3c1d-4f6a-9a77-5b2f0c3d4e5f",
            "https://jobs.lever.example/acme/0B6E1B9E-3C1D-4F6A-9A77-5B2F0C3D4E5F/",
            True,
        ),
        ("https://acme.example/jobs/engineer", "https://acme.example/jobs/engineer/", True),
        ("https://acme.example/jobs/engineer", "https://acme.example/jobs", False),
        ("https://acme.example/jobs/engineer", "not an address", False),
    ],
)
def test_own_page(requested: str, final: str, own: bool) -> None:
    assert liveness.own_page(requested, final) is own


def test_clauses_are_cut_at_sentences_commas_and_spaced_dashes_never_at_question_marks() -> None:
    found = liveness.sentences("Sorry - this job is closed. Not found? Ask us, we reply")
    assert found == [
        ("Sorry - this job is closed.", ["sorry", "this job is closed"]),
        ("Not found? Ask us, we reply", ["not found ask us", "we reply"]),
    ]


def test_fold_reads_accents_quotes_and_letters_as_plain_text() -> None:
    assert liveness.fold("  L\u2019Offre  \u00c9TAIT\u00a0Og\u0141oszenie\u200b ") == (
        "l'offre etait ogloszenie"
    )


def test_a_page_of_repeated_guarded_closure_words_is_read_whole() -> None:
    clause = "until the position has been filled and " * 20_000
    found = judge(200, page(clause), POSTING, POSTING)
    assert found.verdict == NO_CLOSURE
    stated = " ".join(["job expired"] * 20_000)
    assert judge(200, page(stated), POSTING, POSTING).verdict == MAY_BE_CLOSED
