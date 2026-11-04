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
    facts = button("Go", type="submit", in_form=True, form_kinds=kinds, native={"method": method})
    assert (line.control(facts, "click") is None) is allowed


def test_a_submit_word_wins_in_any_form() -> None:
    facts = button("Submit", type="submit", in_form=True, form_kinds=[], native={"method": "get"})
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
