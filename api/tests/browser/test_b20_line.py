"""B20: the line of the website search (browser/browserd/tools/line.py), as accept and refuse
tables: a click, Enter (a key) and a script's request each refused on Apply, Easy Apply, Submit
application, Send message, Connect, Pay, Sign out, Delete account (English, French, German),
while Save job, Follow, Accept cookies, Search and Next page pass. Facts are FACTS_JS's shape."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "browser"))

from browserd.tools import discovery, line

REFUSED = [
    "Apply", "Easy Apply", "Submit application", "Send message", "Connect", "Pay", "Sign out",
    "Delete account",
    "Postuler", "Candidature simplifiée", "Envoyer ma candidature", "Envoyer un message",
    "Se connecter", "Payer", "Se déconnecter", "Supprimer le compte",
    "Bewerben", "Einfach bewerben", "Bewerbung absenden", "Nachricht senden", "Vernetzen",
    "Bezahlen", "Abmelden", "Konto löschen",
]  # fmt: skip
PASSED = [
    "Save job", "Follow", "Accept cookies", "Search", "Next page",
    "Enregistrer", "Suivre", "Tout accepter", "Rechercher", "Suivant",
    "Speichern", "Folgen", "Alle akzeptieren", "Suchen", "Weiter",
]  # fmt: skip


def button(name: str, **extra: object) -> dict:
    facts = {"tag": "button", "type": "button", "role": "", "names": [name], "credential": False}
    return {**facts, **extra}


@pytest.mark.parametrize("name", REFUSED)
@pytest.mark.parametrize("action", ["click", "key"])
def test_the_line_refuses_its_controls(name: str, action: str) -> None:
    assert line.control(button(name), action) is not None


@pytest.mark.parametrize("name", PASSED)
@pytest.mark.parametrize("action", ["click", "key"])
def test_a_search_s_controls_pass(name: str, action: str) -> None:
    assert line.control(button(name), action) is None


def test_a_posting_link_s_title_is_no_word_of_the_line() -> None:
    link = {"tag": "a", "type": "", "role": "", "names": ["Post-doc, SharePoint Engineer"]}
    assert line.control({**link, "credential": False}, "click") is None


@pytest.mark.parametrize(
    ("kinds", "method", "allowed"),
    [
        (["input:search", "button:submit"], "get", True),  # a search form
        (["button:submit", "input:checkbox"], "post", True),  # a consent form
        (["input:text", "input:email", "input:file", "button:submit"], "post", False),  # apply
        (["textarea:", "button:submit"], "post", False),  # a message
        (["input:text", "input:password", "button:submit"], "post", False),  # a sign in
    ],
)
def test_a_submit_passes_in_a_search_or_consent_form_only(
    kinds: list[str], method: str, allowed: bool
) -> None:
    # FACTS_JS's shape: the form's method is `discovery.method` (part B: `native` was read).
    facts = button("Go", type="submit", in_form=True, form_kinds=kinds, discovery={"method": method})
    assert (line.control(facts, "click") is None) is allowed


def test_a_submit_word_wins_in_any_form() -> None:
    facts = button(
        "Submit", type="submit", in_form=True, form_kinds=[], discovery={"method": "get"}
    )
    assert line.control(facts, "click") == "submit"


def test_a_credential_is_never_typed() -> None:
    field = {"tag": "input", "type": "password", "role": "", "names": [], "credential": True}
    assert line.control(field, "type") == "credential"


@pytest.mark.parametrize(
    ("method", "url", "kind", "refused"),
    [
        ("GET", "https://jobs.example.org/search?q=apply", "document", False),
        ("GET", "https://ats.example.org/jobs/42", "document", False),
        ("POST", "https://jobs.example.org/search", "document", False),
        ("POST", "https://jobs.example.org/jobs/42/apply", "document", True),
        ("POST", "https://jobs.example.org/account/login", "document", True),
        ("POST", "https://jobs.example.org/form", "document", True),
        ("POST", "https://jobs.example.org/api/messaging/send", "fetch", True),
        ("POST", "https://jobs.example.org/api/sendInvitation", "xhr", True),
        ("POST", "https://jobs.example.org/checkout", "fetch", True),
        ("POST", "https://jobs.example.org/logout", "fetch", True),
        ("POST", "https://jobs.example.org/api/graphql?op=saveJob", "fetch", False),
        ("POST", "https://jobs.example.org/1/indexes/jobs/queries", "xhr", False),
        ("GET", "ftp://jobs.example.org/x", "document", True),
    ],
)
def test_the_request_floor(method: str, url: str, kind: str, refused: bool) -> None:
    assert (line.request_refused(method, url, kind) is not None) is refused


def test_the_scope_holds_the_start_and_the_login_only() -> None:
    assert discovery.Scope.parse({"start": "https://jobs.example.org/"}) is not None
    assert discovery.Scope.parse({"start": "https://jobs.example.org/", "signed_in": True})
    assert discovery.Scope.parse({"start": "https://jobs.example.org/", "titles": []}) is None
    assert discovery.Scope.parse({"start": "javascript:alert(1)"}) is None
    assert discovery.public_url("https://ats.example.net/jobs/1")


def test_every_playwright_cli_tool_is_a_discovery_tool() -> None:
    from browserd.tools import actions

    assert set(actions.TOOLS) >= discovery.TOOLS
    assert "browser_file_upload" not in discovery.TOOLS


def test_a_get_form_of_more_than_40_fields_is_a_search_and_a_post_one_is_not() -> None:
    """The method read where FACTS_JS writes it (`discovery.method`), never `native`."""
    kinds = ["input:checkbox"] * 41 + ["button:submit"]
    get = button("Go", type="submit", in_form=True, form_kinds=kinds, discovery={"method": "get"})
    post = {**get, "discovery": {"method": "post"}}
    assert line.control(get, "click") is None
    assert line.control(post, "click") == "submit"
    assert line.control({**get, "native": {"method": "get"}, "discovery": {}}, "click")


JOBS = "https://jobs.example.test"
ALGOLIA = (
    "https://abc-dsn.algolia.test/1/indexes/*/queries?x-algolia-agent=Algolia%20for%20JavaScript"
    "%20(4.24.0)%3B%20Browser%3B%20react-instantsearch%20(7.13.0)%3B%20e2e-connect-ui%20(1.0.0)"
    "&x-algolia-api-key=3f2a9c1d0b8e7a65&x-algolia-application-id=E2E"  # gitleaks:allow (fake)
)
# A fetch, an XHR or a beacon POST to these never leaves: apply, submit, write to someone, sign
# out, pay or change the account, glued, camel cased, spaced, encoded, in a key or a value.
POST_REFUSED = [
    "/apply", "/jobs/42/apply", "/api/quickapply", "/jobs/42/easyapply", "/api/v1/quickApply",
    "/easy-apply", "/EasyApply", "/APPLY", "/%61pply", "/api/applications", "/candidature",
    "/postuler", "/bewerbung", "/api/submitApplication", "/form/presubmit", "/api/messages",
    "/api/sendmessage", "/api/sendMessage", "/messaging/conversations/7",
    "/voyager/api/voyagerMessagingDashMessengerMessages?action=createMessage",
    "/api/graphql?operationName=SendMessage", "/a?action=apply", "/a?do=submit",
    "/invitations", "/api/sendinvite", "/connect", "/api/connections", "/comments/new",
    "/reply", "/checkout", "/api/pay", "/billing/update", "/logout", "/sign-out", "/signout",
    "/log/out", "/account/delete", "/settings/profile", "/api/removeSavedJob", "/sendmail",
    "/" + "a" * 5000 + "/apply", "/" + "a" * 5000,
]  # fmt: skip
# The search, saving a job, following, consent, analytics: they leave.
POST_PASSED = [
    "/api/graphql?op=saveJob", "/api/saved-jobs", "/api/follows", "/li/track", "/collect",
    "/1/indexes/jobs/queries", "/api/jobs/search", "/rum", "/api/impressions",
    "/cookie-consent", "/api/consents", "/j/collect?v=2", "/api/jobs/42/views",
    "/graphql?operationName=JobSearch", "/api/alerts?q=postdoc",
]  # fmt: skip


@pytest.mark.parametrize("path", POST_REFUSED)
@pytest.mark.parametrize("kind", ["fetch", "xhr", "ping", "document"])
def test_a_post_that_says_a_step_of_the_line_never_leaves(path: str, kind: str) -> None:
    assert line.request_refused("POST", JOBS + path, kind) is not None


@pytest.mark.parametrize("path", POST_PASSED)
@pytest.mark.parametrize("kind", ["fetch", "xhr", "ping"])
def test_a_page_s_own_post_that_is_no_step_of_the_line_leaves(path: str, kind: str) -> None:
    assert line.request_refused("POST", JOBS + path, kind) is None


def test_a_search_service_s_client_metadata_is_never_read() -> None:
    """B19's Welcome to the Jungle: its Algolia client says "connect" and "application" in its
    metadata; the search leaves, and the same words anywhere else in its address do not."""
    assert line.request_refused("POST", ALGOLIA, "fetch") is None
    assert line.request_refused("POST", ALGOLIA + "&op=apply", "fetch") == "line_post"
    assert line.request_refused("POST", ALGOLIA.replace("indexes", "connect"), "xhr")


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        (JOBS + "/search", True),
        (JOBS + "/careersection/jobsearch.ftl", True),
        (JOBS + "/fr/recherche", True),
        (JOBS + "/de/Suche", True),
        (JOBS + "/consent", True),
        (JOBS + "/cookie-consent/accept", True),
        (JOBS + "/gdpr/save", True),
        ("https://consent.example.test/save", True),
        ("https://cookies.example.test/save", True),
        (JOBS + "/example-robotics/jobs/42", False),  # an ATS's application form
        (JOBS + "/jobs", False),
        (JOBS + "/form", False),
        (JOBS + "/careers/results.aspx", False),
        (JOBS + "/search/apply", False),
        (JOBS + "/consent?next=/apply", False),
        ("https://search.example.test/jobs/42", False),  # a host's search word is no search
        (JOBS + "/account/cookie-settings", False),
        (JOBS + "/", False),
    ],
)
def test_a_document_post_leaves_only_as_a_search_or_a_consent_choice(url, allowed) -> None:
    assert (line.request_refused("POST", url, "document") is None) is allowed


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "get"])
@pytest.mark.parametrize("path", ["/jobs/42/apply", "/messaging", "/logout", "/" + "a" * 5000])
def test_a_get_always_leaves(method: str, path: str) -> None:
    assert line.request_refused(method, JOBS + path, "document") is None


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "ws://x.test/", 7])
def test_another_scheme_or_no_address_is_refused(url: object) -> None:
    assert line.request_refused("POST", url, "fetch") is not None
