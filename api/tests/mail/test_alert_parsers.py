"""The job alert parsers (mail/alerts): pure, on synthetic mails after the public formats.

Accept and refuse tables first (docs/agents/when/heuristic-code.md): what a job link is, then
whole mails: several jobs, missing fields, tracked links, encodings, quoted-printable, hostile
sizes. Then who may send (the allow list) and whether Gmail authenticated it.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.mail import parse
from app.mail.alerts import check, indeed, linkedin, providers, source
from app.mail.alerts import text as t
from app.profile.schemas import AlertEmails
from tests.mail import alert_support as a

JOB = "0123456789abcdef"  # an invented Indeed job id: 16 hexadecimal characters

# --- Indeed links ----------------------------------------------------------------------------

INDEED_ACCEPTED = [
    (
        f"https://fr.indeed.com/rc/clk/dl?jk={JOB}&from=ja",
        f"https://fr.indeed.com/viewjob?jk={JOB}",
    ),
    (
        f"https://fr.indeed.com/rc/clk?jk={JOB}&from=ja&tk=x",
        f"https://fr.indeed.com/viewjob?jk={JOB}",
    ),
    (
        f"https://fr.indeed.com/pagead/clk/dl?jk={JOB}&ad=x",
        f"https://fr.indeed.com/viewjob?jk={JOB}",
    ),
    (f"https://fr.indeed.com/pagead/clk?ad=x&jk={JOB}", f"https://fr.indeed.com/viewjob?jk={JOB}"),
    (f"https://fr.indeed.com/viewjob?jk={JOB}", f"https://fr.indeed.com/viewjob?jk={JOB}"),
    (
        f"https://fr.indeed.com/viewjob/?jk={JOB}&utm_source=x",
        f"https://fr.indeed.com/viewjob?jk={JOB}",
    ),
    (f"http://fr.indeed.com/rc/clk/dl?jk={JOB}", f"https://fr.indeed.com/viewjob?jk={JOB}"),
    (f"HTTPS://FR.INDEED.COM/rc/clk/dl?jk={JOB}", f"https://fr.indeed.com/viewjob?jk={JOB}"),
    (f"https://ch.indeed.com/rc/clk/dl?jk={JOB}", f"https://ch.indeed.com/viewjob?jk={JOB}"),
    (f"https://www.indeed.com/rc/clk/dl?jk={JOB}", f"https://www.indeed.com/viewjob?jk={JOB}"),
    (f"https://indeed.com/viewjob?jk={JOB}", f"https://www.indeed.com/viewjob?jk={JOB}"),
    (
        f"https://fr.indeed.com/rc/clk/dl?jk={JOB.upper()}",
        f"https://fr.indeed.com/viewjob?jk={JOB}",
    ),
    (
        f"https://fr.indeed.com/rc/clk/dl?from=ja&jk={JOB}#top",
        f"https://fr.indeed.com/viewjob?jk={JOB}",
    ),
]

INDEED_REFUSED = [
    "https://fr.indeed.com/pagead/clk/dl?ad=-6NYlbfkN0Opaque&from=ja",  # sponsored, no key
    f"https://fr.indeed.com/rc/clk/dl?jk={JOB}&jk=fedcba9876543210",  # two keys
    f"https://fr.indeed.com/rc/clk/dl?jk={JOB[:-1]}",  # 15 characters
    f"https://fr.indeed.com/rc/clk/dl?jk={JOB}0",  # 17 characters
    "https://fr.indeed.com/rc/clk/dl?jk=0123456789abcdeg",  # not hexadecimal
    "https://fr.indeed.com/rc/clk/dl?jk=",  # empty
    f"https://fr.indeed.com/rc/clk/dl?JK={JOB}",  # another parameter
    f"https://fr.indeed.com/rc/clk/dl?xjk={JOB}",
    f"https://fr.indeed.com/jobs?q=data&jk={JOB}",  # a search page, not a job path
    f"https://fr.indeed.com/alert/unsubscribe?jk={JOB}",
    f"https://fr.indeed.com/rc/clk/dl/extra?jk={JOB}",
    f"https://cts.indeed.com/v3/opaque?jk={JOB}",  # a tracker host
    f"https://secure.indeed.com/viewjob?jk={JOB}",  # a three letter host
    f"https://jobalert.indeed.com/viewjob?jk={JOB}",
    f"https://fr.indeed.com.evil.example/viewjob?jk={JOB}",  # a lookalike
    f"https://evil.example/fr.indeed.com/viewjob?jk={JOB}",
    f"https://fr.indeed.co/viewjob?jk={JOB}",
    f"https://fr-indeed.com/viewjob?jk={JOB}",
    f"https://user@fr.indeed.com/viewjob?jk={JOB}",  # a user part
    f"https://fr.indeed.com:8443/viewjob?jk={JOB}",  # a port
    f"ftp://fr.indeed.com/viewjob?jk={JOB}",
    f"javascript:fr.indeed.com/viewjob?jk={JOB}",
    f"https://fr.indeed.com\\@evil.example/viewjob?jk={JOB}",
    "",
]


@pytest.mark.parametrize(("url", "expected"), INDEED_ACCEPTED)
def test_an_indeed_job_link_gives_its_canonical_address(url: str, expected: str) -> None:
    assert indeed.canonical(url) == expected


@pytest.mark.parametrize("url", INDEED_REFUSED)
def test_anything_else_names_no_indeed_job(url: str) -> None:
    assert indeed.canonical(url) is None


# --- LinkedIn links --------------------------------------------------------------------------

LINKEDIN_ACCEPTED = [
    "https://www.linkedin.com/comm/jobs/view/4000000001/?trackingId=AbC%3D%3D&trk=eml",
    "https://www.linkedin.com/comm/jobs/view/4000000001?trackingId=x",
    "https://www.linkedin.com/jobs/view/4000000001/",
    "https://www.linkedin.com/jobs/view/4000000001",
    "https://linkedin.com/jobs/view/4000000001/",
    "https://fr.linkedin.com/jobs/view/4000000001/?refId=x",
    "http://www.linkedin.com/comm/jobs/view/4000000001/",
    "https://WWW.LINKEDIN.COM/comm/jobs/view/4000000001/",
]

LINKEDIN_REFUSED = [
    "https://www.linkedin.com/comm/jobs/search?keywords=data",  # the search, not a job
    "https://www.linkedin.com/comm/jobs/alerts?midToken=x",
    "https://www.linkedin.com/jobs/view/abc/",
    "https://www.linkedin.com/jobs/view/12345/",  # too short to be a job number
    "https://www.linkedin.com/jobs/view/4000000001/extra",
    "https://www.linkedin.com/in/someone/",
    "https://www.linkedin.com.evil.example/jobs/view/4000000001/",
    "https://evil.example/www.linkedin.com/jobs/view/4000000001/",
    "https://lnkd.in/jobs/view/4000000001/",
    "https://www.linkedin.com:444/jobs/view/4000000001/",
    "https://user@www.linkedin.com/jobs/view/4000000001/",
    "https://mobile.www.linkedin.com/jobs/view/4000000001/",
    "",
]


@pytest.mark.parametrize("url", LINKEDIN_ACCEPTED)
def test_a_linkedin_job_link_gives_its_canonical_address(url: str) -> None:
    assert linkedin.canonical(url) == "https://www.linkedin.com/jobs/view/4000000001/"


@pytest.mark.parametrize("url", LINKEDIN_REFUSED)
def test_anything_else_names_no_linkedin_job(url: str) -> None:
    assert linkedin.canonical(url) is None


# --- Indeed mails ----------------------------------------------------------------------------


def body(name: str) -> parse.ParsedMessage:
    return parse.parse(a.gmail_json(a.eml(name)), a.ME)


def test_an_indeed_alert_in_quoted_printable_gives_every_job_and_drops_the_sponsored_one() -> None:
    message = body("indeed-fr-plain.eml")
    parsed = indeed.parse_text(message.body_text)
    assert [(job.title, job.company, job.location, job.url) for job in parsed.jobs] == [
        (
            "Ingénieur données confirmé",
            "Exemple Analytique",
            "Lyon (69)",
            "https://fr.indeed.com/viewjob?jk=0a1b2c3d4e5f6a7b",
        ),
        # The LAST " - " splits the company from the place.
        (
            "Data Engineer (H/F)",
            "Société Fictive - Studio",
            "Villeurbanne (69)",
            "https://fr.indeed.com/viewjob?jk=1234567890abcdef",
        ),
        (
            "Analyste BI 🚀",
            "Exemple Données & Cie",
            "Lyon 3e (69)",
            "https://fr.indeed.com/viewjob?jk=fedcba9876543210",
        ),
    ]
    assert parsed.dropped == 1  # the sponsored entry: its link carries no job key
    first = parsed.jobs[0]
    assert first.snippet.startswith("Concevoir et maintenir les flux de données")
    # The tracked link's parameters are gone: only the job's key travels.
    assert all("tk=" not in job.url and "from=" not in job.url for job in parsed.jobs)


def test_an_indeed_alert_declared_latin_1_keeps_its_accents() -> None:
    parsed = indeed.parse_text(body("indeed-latin1.eml").body_text)
    assert [(job.title, job.company) for job in parsed.jobs] == [
        ("Développeur Python sénior", "Exemple Énergie")
    ]


def plain(*paragraphs: str) -> str:
    return "\n\n".join(paragraphs)


LINK = f"https://fr.indeed.com/rc/clk/dl?jk={JOB}&from=ja"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Title, company and place, no snippet.
        (plain(f"Data Engineer\nExemple - Paris\n{LINK}"), [("Data Engineer", "Exemple", "Paris")]),
        # A company line without " - ": the place is unknown, never invented.
        (plain(f"Data Engineer\nExemple SA\n{LINK}"), [("Data Engineer", "Exemple SA", "")]),
        # A label before the link on its line.
        (
            plain(f"Data Engineer\nExemple - Paris\nVoir l'offre : {LINK}"),
            [("Data Engineer", "Exemple", "Paris")],
        ),
        # Windows line ends.
        (
            f"Data Engineer\r\nExemple - Paris\r\n{LINK}\r\n",
            [("Data Engineer", "Exemple", "Paris")],
        ),
        # A title alone above the link is no job.
        (plain(f"Data Engineer\n{LINK}"), []),
        # A link alone is no job.
        (plain(LINK), []),
        # The link must end the paragraph: a link in the middle is a snippet's.
        (plain(f"Data Engineer\nExemple - Paris\n{LINK}\nmore text"), []),
        # The same job twice in one mail is one job.
        (
            plain(
                f"Data Engineer\nExemple - Paris\n{LINK}", f"Data Engineer\nExemple - Paris\n{LINK}"
            ),
            [("Data Engineer", "Exemple", "Paris")],
        ),
        # Header and footer paragraphs are no jobs.
        (
            plain(
                "2 nouveaux emplois", "Se désabonner : https://fr.indeed.com/alert/unsubscribe?t=1"
            ),
            [],
        ),
        # Control characters and direction overrides are not kept.
        (
            plain(f"Data‮ Engineer\x07\nExemple⁦ - Paris\n{LINK}"),
            [("Data Engineer", "Exemple", "Paris")],
        ),
        ("", []),
    ],
)
def test_indeed_text_parts(text: str, expected: list[tuple[str, str, str]]) -> None:
    assert [(j.title, j.company, j.location) for j in indeed.parse_text(text).jobs] == expected


def test_a_block_whose_link_names_no_job_is_counted_as_dropped() -> None:
    parsed = indeed.parse_text(
        plain(f"Data Engineer\n{LINK}", "Data\nExemple - Paris\n" + LINK[:-25])
    )
    assert parsed.jobs == ()
    assert parsed.dropped == 2


def test_hostile_sizes_are_bounded() -> None:
    many = plain(
        *(
            f"Job {n}\nExemple - Paris\nhttps://fr.indeed.com/viewjob?jk={n:016x}"
            for n in range(150)
        )
    )
    parsed = indeed.parse_text(many)
    assert len(parsed.jobs) == t.MAX_JOBS
    assert parsed.dropped == 50
    long_title = "x" * (t.MAX_LINE + 1)
    assert indeed.parse_text(plain(f"{long_title}\nExemple - Paris\n{LINK}")).jobs == ()
    capped = indeed.parse_text(plain(f"{'y' * 1500}\nExemple - Paris\n{LINK}")).jobs[0]
    assert len(capped.title) == t.MAX_TITLE
    # A megabyte of blank lines and of one character reads in linear time and gives nothing.
    assert indeed.parse_text("\n" * 1_000_000).jobs == ()
    assert indeed.parse_text("h" * 1_000_000).jobs == ()
    assert indeed.parse_text("https://" * 100_000).jobs == ()


def test_the_html_text_version_reads_label_and_cells() -> None:
    version = "\n".join(
        [
            "1 nouvel emploi",
            f"Ingénieur MLOps (https://fr.indeed.com/rc/clk/dl?jk={JOB}&from=ja)",
            "Exemple Robotique\tParis (75)",
            "Industrialiser les modèles.",
            "Voir tous les emplois (https://fr.indeed.com/jobs?q=mlops)",
            "Data Analyst (https://fr.indeed.com/viewjob?jk=fedcba9876543210)",
            "Exemple Conseil - Nantes",
            # A link whose label is its address has no title: no job.
            "https://fr.indeed.com/viewjob?jk=00112233445566aa",
            "Sponsorisé (https://fr.indeed.com/pagead/clk?ad=opaque)",
            "Exemple Sponsor\tLyon",
        ]
    )
    parsed = indeed.parse_html_text(version)
    assert [(j.title, j.company, j.location, j.snippet) for j in parsed.jobs] == [
        ("Ingénieur MLOps", "Exemple Robotique", "Paris (75)", "Industrialiser les modèles."),
        ("Data Analyst", "Exemple Conseil", "Nantes", ""),
    ]
    assert parsed.dropped == 1  # the sponsored entry


# --- LinkedIn mails --------------------------------------------------------------------------


def test_a_linkedin_alert_gives_every_job_between_the_dashes() -> None:
    parsed = linkedin.parse_text(body("linkedin-alert.eml").body_text)
    assert [(j.title, j.company, j.location, j.url) for j in parsed.jobs] == [
        (
            "Data Engineer",
            "Example Data Works",
            "Lyon, Auvergne-Rhône-Alpes, France",
            "https://www.linkedin.com/jobs/view/4000000001/",
        ),
        (
            "Senior Analytics Engineer",
            "Exemple Fictif SAS",
            "Villeurbanne, Auvergne-Rhône-Alpes, France (Hybrid)",
            "https://www.linkedin.com/jobs/view/4000000002/",
        ),
    ]
    assert parsed.jobs[0].snippet == "This company is actively hiring Apply with resume & profile"
    assert parsed.dropped == 0


DASH = "-" * 57
VIEW = "View job: https://www.linkedin.com/comm/jobs/view/4000000009/?trackingId=x"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The header lost its blank line: it is never the title.
        (
            "Your job alert for data\nNew jobs match your preferences.\n"
            f"Data Engineer\nExemple\nLyon\n{VIEW}",
            [("Data Engineer", "Exemple", "Lyon")],
        ),
        # The link stands alone below a blank line.
        (f"Data Engineer\nExemple\nLyon\n\n{VIEW}\n{DASH}", [("Data Engineer", "Exemple", "Lyon")]),
        # The link alone, no label.
        (
            "Data Engineer\nExemple\nLyon\nhttps://www.linkedin.com/jobs/view/4000000009/",
            [("Data Engineer", "Exemple", "Lyon")],
        ),
        # Two lines above the link: no job.
        (f"Data Engineer\nExemple\n{VIEW}", []),
        # A header link to similar jobs is no job, nor the footer.
        (
            f"Jobs similar to Data Engineer: https://www.linkedin.com/comm/jobs/search?x=1\n\n{DASH}\n"
            "See all jobs on LinkedIn: https://www.linkedin.com/comm/jobs/search?k=data",
            [],
        ),
        # Nineteen dashes do not separate blocks: the block's first job link is its job.
        (
            f"Data Engineer\nExemple\nLyon\n{VIEW}\n{'-' * 19}\nOther\nAutre\nNantes\n"
            "View job: https://www.linkedin.com/jobs/view/4000000010/",
            [("Data Engineer", "Exemple", "Lyon")],
        ),
        ("", []),
    ],
)
def test_linkedin_text_parts(text: str, expected: list[tuple[str, str, str]]) -> None:
    assert [(j.title, j.company, j.location) for j in linkedin.parse_text(text).jobs] == expected


def test_the_local_check_reads_a_downloaded_alert(
    tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "alert.eml"
    path.write_bytes(a.eml("linkedin-alert.eml"))
    assert check.main(["linkedin", str(path)]) == 0
    out = capsys.readouterr().out
    # No Gmail line in a hand made file: never authenticated.
    assert out.splitlines()[0] == "refused: not_authenticated"
    assert "jobs: 2, dropped blocks: 0" in out
    assert "https://www.linkedin.com/jobs/view/4000000002/" in out
    assert check.main(["monster", str(path)]) == 2


def test_linkedin_hostile_sizes_are_bounded() -> None:
    blocks = [
        f"Job {n}\nExemple\nLyon\nView job: https://www.linkedin.com/jobs/view/{4000000000 + n}/"
        for n in range(130)
    ]
    parsed = linkedin.parse_text(f"\n{DASH}\n".join(blocks))
    assert len(parsed.jobs) == t.MAX_JOBS
    assert parsed.dropped == 30
    assert linkedin.parse_text("-" * 1_000_000).jobs == ()
    assert linkedin.parse_text("View job: " * 100_000).jobs == ()


# --- who may send ----------------------------------------------------------------------------


def test_the_setup_switches_are_the_providers_of_the_allow_list() -> None:
    assert tuple(AlertEmails.model_fields) == providers.KEYS
    for provider in providers.PROVIDERS.values():
        assert provider.senders
        assert all(
            sender == sender.strip().lower() and "@" in sender for sender in provider.senders
        )


INDEED = providers.PROVIDERS["indeed"]
LINKEDIN = providers.PROVIDERS["linkedin"]
RAW = a.eml("indeed-fr-plain.eml")


@pytest.mark.parametrize(
    ("options", "reason"),
    [
        ({}, None),
        ({"from_header": "Indeed <donotreply@jobalert.indeed.com>"}, None),
        ({"from_header": "ALERT@INDEED.COM"}, None),  # addresses are compared in lower case
        ({"auth": "spf-only"}, "not_authenticated"),
        ({"auth": "dmarc-fail"}, "not_authenticated"),
        ({"auth": "other-domain"}, "not_authenticated"),
        ({"auth": "none"}, "not_authenticated"),
        # Another address of the same organisation is not on the list.
        ({"from_header": "Indeed <jobs@indeed.com>"}, "sender_not_allowed"),
        ({"from_header": "Indeed <donotreply@match.indeed.com>"}, "sender_not_allowed"),
        (
            {"from_header": "alert@indeed.com.evil.example", "domain": "evil.example"},
            "sender_not_allowed",
        ),
        (
            {"from_header": "alert@indeed.com <evil@evil.example>", "domain": "evil.example"},
            "sender_not_allowed",
        ),
        ({"from_header": "evil@evil.example", "domain": "evil.example"}, "sender_not_allowed"),
        ({"from_header": "alert@indeed.com, evil@evil.example"}, "sender_not_allowed"),
        ({"from_header": "alert@lndeed.com", "domain": "lndeed.com"}, "sender_not_allowed"),
    ],
)
def test_only_an_allowed_sender_that_gmail_authenticated_is_read(
    options: dict[str, str], reason: str | None
) -> None:
    raw = a.gmail_json(RAW, **options)
    assert source.refused(parse.parse(raw, a.ME), INDEED, raw) == reason


def test_a_provider_reads_only_its_own_senders() -> None:
    message = parse.parse(a.gmail_json(RAW), a.ME)
    assert source.refused(message, LINKEDIN) == "sender_not_allowed"


def test_a_mail_gmail_fetched_from_another_mailbox_is_refused() -> None:
    answer = a.gmail_json(RAW)
    answer["payload"]["headers"].insert(0, {"name": "X-Gmail-Fetch-Info", "value": "pop.example"})
    assert source.refused(parse.parse(answer, a.ME), INDEED, answer) == "not_authenticated"


def test_without_its_authentication_line_a_message_is_vetoed() -> None:
    assert source.dmarc_vetoed({"payload": {"headers": []}})
    assert source.dmarc_vetoed({"payload": {}})
    assert source.dmarc_vetoed("not a message")
    assert not source.dmarc_vetoed(a.gmail_json(RAW))


def test_the_query_names_the_exact_senders_and_never_spam_or_trash() -> None:
    assert source.query(INDEED, 1_793_343_600_999) == (
        "from:(alert@indeed.com OR donotreply@jobalert.indeed.com) after:1793343600 "
        "-in:spam -in:trash"
    )


# --- the person's address and tracking tokens never reach a posting (fail closed) -------------

ADDRESS = "harald@example.org"  # the account's own address, as an alert names its recipient
SECRET = "SECRET123"  # a tracking token of an invented link


def leaks(parsed: t.Parsed) -> list[str]:
    """Every field of every job that names the address, the token or a tracking link."""
    return [
        value
        for job in parsed.jobs
        for value in (job.title, job.company, job.location, job.snippet, job.url)
        if ADDRESS in value or SECRET in value or "unsub" in value or "track" in value
    ]


@pytest.mark.parametrize(
    "line",
    [
        f"Sent to {ADDRESS}",
        f"envoyé à {ADDRESS} voir https://fr.indeed.com/unsub?t={SECRET} ici",  # a link mid line
        f"https://www.linkedin.com/comm/jobs/alerts?midToken={SECRET}",  # a trailing link
        f"Gérer l'alerte : https://fr.indeed.com/alert?email={ADDRESS}",
        f"voir fr.indeed.com/unsub?t={SECRET} ou www.example.org",  # no scheme
        "voir www\u200b.example.org/unsub",  # a zero width space hiding a link
        "harald\x07@example.org",  # a control character inside it
        "Sent to harald\uff20example.org",  # a fullwidth "@"
        "Sent to harald\ufe6bexample.org",  # a small "@"
        "voir \uff57\uff57\uff57\uff0eexample\uff0eorg",  # a fullwidth "www."
        "Sent to harald%40example.org",  # an "@" percent encoded
        "Sent to harald @ example.org",  # an "@" between spaces
        "Sent to harald [at] example.org",
        "Sent to harald(AT)example.org",
    ],
)
def test_a_line_with_a_link_or_an_address_holds_one(line: str) -> None:
    assert t.holds_address(line)


@pytest.mark.parametrize(
    "line",
    [
        "Data Engineer (H/F)",
        "Exemple Données & Cie - Lyon 3e (69)",
        "Node.js, React et AWS. Salaire : 45 000 €",
        "Contact @ l'équipe produit.",  # an "@" without an address
        "Data Scientist @ Exemple",  # an "@" between spaces, no address after it
        "Chef de projet (attaché commercial)",  # "at" inside a word is no "@"
        "a@b.",  # nothing after the dot
        "",
    ],
)
def test_ordinary_text_holds_no_address(line: str) -> None:
    assert not t.holds_address(line)


def test_the_address_check_is_linear_on_a_hostile_line() -> None:
    hostile = ("a@" * (t.MAX_LINE // 2)) + " " + ("@." * (t.MAX_LINE // 2))
    assert t.holds_address(hostile)
    assert not t.holds_address("@" * t.MAX_LINE + " " + "." * t.MAX_LINE)


def test_indeed_drops_the_address_and_links_from_the_snippet() -> None:
    parsed = indeed.parse_text(
        plain(
            "Data Engineer\nExemple - Paris\nConstruire les flux.\n"
            f"envoyé à {ADDRESS} voir https://fr.indeed.com/unsub?t={SECRET} ici\n"
            f"Sent to {ADDRESS}\nil y a 1 jour\n{LINK}"
        )
    )
    assert [(j.title, j.company, j.location, j.snippet) for j in parsed.jobs] == [
        ("Data Engineer", "Exemple", "Paris", "Construire les flux. il y a 1 jour")
    ]
    assert leaks(parsed) == []


@pytest.mark.parametrize(
    "text",
    [
        # The header, holding the address, glued to the first job.
        f"Votre alerte pour {ADDRESS}\nData Engineer\nExemple - Paris\n{LINK}",
        f"3 nouveaux emplois correspondent à votre alerte\nData Engineer\nExemple - Paris\n{LINK}",
        f"New jobs for {ADDRESS}\nData Engineer\nExemple - Paris\n{LINK}",
        # Indeed's English header: "Jobs for <search> in <place>".
        f"Jobs for data engineer in Paris\nData Engineer\nExemple - Paris\n{LINK}",
        f"Jobs for {ADDRESS} in Paris\nData Engineer\nExemple - Paris\n{LINK}",
    ],
)
def test_an_indeed_header_glued_to_the_first_job_is_never_its_title(text: str) -> None:
    parsed = indeed.parse_text(text)
    assert [(j.title, j.company, j.location) for j in parsed.jobs] == [
        ("Data Engineer", "Exemple", "Paris")
    ]
    assert leaks(parsed) == []


@pytest.mark.parametrize(
    "text",
    [
        # An unknown line above the title that holds the address: the job is dropped.
        f"Bonjour {ADDRESS}\nData Engineer\nExemple - Paris\n{LINK}",
        f"Data Engineer\nExemple - Paris {ADDRESS}\n{LINK}",
        f"Data Engineer https://fr.indeed.com/unsub?t={SECRET}\nExemple - Paris\n{LINK}",
        f"Data Engineer\nhttps://fr.indeed.com/unsub?t={SECRET}\n{LINK}",
    ],
)
def test_an_indeed_field_line_with_an_address_drops_the_job(text: str) -> None:
    parsed = indeed.parse_text(text)
    assert parsed.jobs == ()
    assert parsed.dropped == 1


def test_the_indeed_html_text_version_fails_closed_too() -> None:
    version = "\n".join(
        [
            f"Ingénieur MLOps (https://fr.indeed.com/rc/clk/dl?jk={JOB}&from=ja)",
            "Exemple Robotique\tParis (75)",
            f"Envoyé à {ADDRESS}",
            "Industrialiser les modèles.",
            "Data Analyst (https://fr.indeed.com/viewjob?jk=fedcba9876543210)",
            f"Exemple Conseil {ADDRESS}\tNantes",
        ]
    )
    parsed = indeed.parse_html_text(version)
    assert [(j.title, j.snippet) for j in parsed.jobs] == [
        ("Ingénieur MLOps", "Industrialiser les modèles.")
    ]
    assert parsed.dropped == 1
    assert leaks(parsed) == []


def test_linkedin_drops_the_address_and_tracking_links_from_the_snippet() -> None:
    parsed = linkedin.parse_text(
        "Your job alert for data\n\n"
        f"Data Engineer\nExemple\nLyon\nActively recruiting\n"
        f"Sent to {ADDRESS} https://www.linkedin.com/comm/track?email={ADDRESS}\n"
        f"Manage: https://www.linkedin.com/comm/jobs/alerts?midToken={SECRET}\n"
        f"{VIEW}\n{DASH}\n"
        f"See all jobs on LinkedIn: https://www.linkedin.com/comm/jobs/search?k=data\n"
        f"Unsubscribe: https://www.linkedin.com/comm/jobs/alerts?midToken={SECRET}"
    )
    assert [(j.title, j.company, j.location, j.snippet) for j in parsed.jobs] == [
        ("Data Engineer", "Exemple", "Lyon", "Actively recruiting")
    ]
    assert leaks(parsed) == []


@pytest.mark.parametrize(
    "text",
    [
        # A header holding the address glued to the first job, not one LinkedIn's guard knows.
        f"Sent to {ADDRESS}\nData Engineer\nExemple\nLyon\n{VIEW}",
        f"Data Engineer\nExemple {ADDRESS}\nLyon\n{VIEW}",
        f"Data Engineer\nExemple\nLyon https://www.linkedin.com/comm/track?midToken={SECRET}\n{VIEW}",
    ],
)
def test_a_linkedin_field_line_with_an_address_drops_the_job(text: str) -> None:
    parsed = linkedin.parse_text(text)
    assert parsed.jobs == ()
    assert parsed.dropped == 1


def test_a_linkedin_header_with_the_address_glued_to_the_first_job_is_skipped() -> None:
    parsed = linkedin.parse_text(
        f"Your job alert for {ADDRESS}\nData Engineer\nExemple\nLyon\n{VIEW}"
    )
    assert [(j.title, j.company, j.location) for j in parsed.jobs] == [
        ("Data Engineer", "Exemple", "Lyon")
    ]
    assert leaks(parsed) == []
