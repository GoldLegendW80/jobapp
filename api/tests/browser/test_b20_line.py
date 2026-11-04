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


def link(name: str, **extra: object) -> dict:
    facts = {"tag": "a", "type": "", "role": "", "names": [name], "credential": False}
    return {**facts, **extra}


# Signing out and changing the account are links too (review of B20, round 1): LinkedIn's "Sign
# Out" in its Me menu (`<a href="/m/logout/">`), Indeed's "Sign out", in English, French and
# German; and LinkedIn's Connect link ("Invite Jane Roe to connect").
LEAVING_LINKS = [
    "Sign Out", "Sign out", "Log out", "Logout", "SignOut", "Close account", "Delete account",
    "Edit profile", "Change your password", "Account settings", "Connect",
    "Invite Jane Roe to connect",
    "Se déconnecter", "Déconnexion", "Supprimer le compte", "Modifier mon profil",
    "Paramètres du compte",
    "Abmelden", "Ausloggen", "Konto löschen", "Profil bearbeiten", "Kontoeinstellungen",
]  # fmt: skip
# A posting link's title says none of them, even with their words' starts side by side.
POSTING_LINKS = [
    "Logistics Engineer", "Logistics Outbound Coordinator", "Blog Outreach Manager",
    "Sign Language Interpreter, Outreach", "Post-doc, SharePoint Engineer",
    "Connected Vehicle Engineer", "Account Executive", "Profile Data Analyst", "Change Manager",
    "Techniker Löschanlagen", "Se connecter",
]  # fmt: skip


@pytest.mark.parametrize("name", LEAVING_LINKS)
@pytest.mark.parametrize("action", ["click", "key"])
def test_a_sign_out_or_account_link_is_the_line(name: str, action: str) -> None:
    # "Delete account" and "Supprimer le compte" are detect's danger already.
    assert line.control(link(name), action) in ("line", "danger")
    assert line.control(link(name, role="menuitem"), action) in ("line", "danger")


# Log off and its spellings (review of B20, round 2), on a link and on a button: "Log off",
# ASP.NET's "LogOff", Windows' French "Fermer la session" (German says "Abmelden", above).
LOG_OFF = [
    "Log off", "Log Off", "LOG OFF", "Log-off", "Logoff", "LogOff", "Log off of Example",
    "Log\u200boff", "Ｌｏｇ ｏｆｆ", "Fermer la session", "Fermer ma session",
]  # fmt: skip
# "Sign off" is a way out on a button or a menu item that is no link; never read on a link,
# whose name is a posting's title ("Sign-off Engineer"): a link's address is read instead.
SIGN_OFF = ["Sign off", "Sign Off", "SIGN OFF", "Sign-off", "Sign off now"]
# Posting titles beside these words: never read as a way out on a link.
OFF_TITLES = [
    "Sign-off Engineer", "Physical Design Sign Off Lead", "STA Signoff Engineer",
    "Timing Sign-off Engineer, Offshore Wind", "Backlog Officer", "Logistics Officer",
    "Catalog Offer Manager", "Change Log Office Assistant", "Log Analyst, Office Team",
    "Responsable de session de formation", "Sitzungsleiter Offshore",
]  # fmt: skip


@pytest.mark.parametrize("name", LOG_OFF)
@pytest.mark.parametrize("action", ["click", "key"])
def test_log_off_is_the_line_on_a_link_and_a_button(name: str, action: str) -> None:
    assert line.control(link(name), action) == "line"
    assert line.control(link(name, role="menuitem"), action) == "line"
    assert line.control(button(name), action) == "line"


@pytest.mark.parametrize("name", SIGN_OFF)
@pytest.mark.parametrize("action", ["click", "key"])
def test_sign_off_is_the_line_on_a_button_and_a_menu_item_never_on_a_link(
    name: str, action: str
) -> None:
    assert line.control(button(name), action) == "line"
    menu = {**button(name), "tag": "div", "role": "menuitem"}
    assert line.control(menu, action) == "line"
    assert line.control(link(name), action) is None


@pytest.mark.parametrize("name", OFF_TITLES)
@pytest.mark.parametrize("action", ["click", "key"])
def test_a_posting_title_beside_log_or_sign_off_opens(name: str, action: str) -> None:
    assert line.control(link(name), action) is None


@pytest.mark.parametrize("name", POSTING_LINKS)
@pytest.mark.parametrize("action", ["click", "key"])
def test_a_posting_link_s_title_is_no_word_of_the_line(name: str, action: str) -> None:
    assert line.control(link(name), action) is None


# A sign out address, any method, any kind: a navigation, a link, `location.href`, a popup, a
# fetch, an image. LinkedIn's Sign Out and Indeed's, on `.test` stand ins of their hosts.
SIGN_OUT_ADDRESSES = [
    "https://www.linkedin.test/m/logout/",
    "https://secure.indeed.test/auth/logout?continue=x",
    "https://jobs.example.test/sign-out",
    "https://jobs.example.test/log/out",
    "https://jobs.example.test/api/signOut",
    "https://jobs.example.test/uas/logoutUser",
    "https://jobs.example.test/auth?continue=%2Flogout",
    "https://jobs.example.test/r?to=https%253A%252F%252Fjobs.example.test%252Fm%252Flogout",
    "https://emplois.example.test/deconnexion",
    "https://stellen.example.test/abmelden",
]


# Log off and a way out glued to another word (review of B20, round 2): read at a word's end too.
LOG_OFF_ADDRESSES = [
    "https://jobs.example.test/logoff",
    "https://jobs.example.test/Account/LogOff",
    "https://jobs.example.test/account/logoff?returnUrl=%2Fjobs",
    "https://jobs.example.test/LogOff.aspx",
    "https://jobs.example.test/log-off",
    "https://jobs.example.test/log_off",
    "https://jobs.example.test/user?action=logoff",
    "https://jobs.example.test/dologout",
    "https://jobs.example.test/userlogout",
    "https://jobs.example.test/dologout.php?x=1",
    "https://jobs.example.test/sso/ssologout",
    "https://jobs.example.test/user/dologoff",
    "https://jobs.example.test/applogoff.aspx",
    "https://jobs.example.test/api/usersignout",
    "https://jobs.example.test/r?to=%252Fdologout",
    "https://jobs.example.test/ＬＯＧＯＦＦ",
]
# Postings and pages beside these words: they open.
OFF_ADDRESSES = [
    "https://jobs.example.test/jobs/sign-off-engineer-42",
    "https://jobs.example.test/jobs/sta-signoff-engineer-42",
    "https://jobs.example.test/jobs/physical-design-signoff-lead",
    "https://jobs.example.test/jobs/backlog-officer-7",
    "https://jobs.example.test/jobs/logistics-officer",
    "https://jobs.example.test/jobs/catalog-offer-manager",
    "https://jobs.example.test/jobs/blogoutreach-lead",
    "https://jobs.example.test/catalogoutput",
    "https://jobs.example.test/blog/office-tour",
    "https://jobs.example.test/jobs?q=log+offload",
]


@pytest.mark.parametrize("url", LOG_OFF_ADDRESSES)
@pytest.mark.parametrize(
    ("method", "kind"), [("GET", "document"), ("GET", "fetch"), ("POST", "fetch")]
)
def test_a_log_off_address_never_leaves(url: str, method: str, kind: str) -> None:
    assert line.request_refused(method, url, kind) == "sign_out"


@pytest.mark.parametrize("url", OFF_ADDRESSES)
def test_a_posting_beside_log_or_sign_off_is_no_sign_out(url: str) -> None:
    assert not line.signs_out(url)
    assert line.request_refused("GET", url, "document") is None


@pytest.mark.parametrize("url", SIGN_OUT_ADDRESSES)
@pytest.mark.parametrize(
    ("method", "kind"),
    [("GET", "document"), ("GET", "fetch"), ("GET", "image"), ("POST", "fetch"), ("HEAD", "xhr")],
)
def test_a_sign_out_address_never_leaves(url: str, method: str, kind: str) -> None:
    assert line.request_refused(method, url, kind) == "sign_out"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.test/jobs/view/logistics-outbound-coordinator-4012345/",
        "https://jobs.example.test/jobs/blog-outreach-manager-42",
        "https://jobs.example.test/catalog/outbound",
        "https://jobs.example.test/search?q=Logistics%20Engineer&trk=nav_signin",
        "https://jobs.example.test/sign-in?continue=%2Fjobs",
    ],
)
def test_a_posting_or_a_search_address_is_no_sign_out(url: str) -> None:
    assert line.request_refused("GET", url, "document") is None


@pytest.mark.parametrize(
    ("url", "refused"),
    [
        ("https://jobs.example.test/api/v1/sessions", True),
        ("https://jobs.example.test/api/v1/sessions/current", True),
        ("https://jobs.example.test/oauth/token", True),
        ("https://jobs.example.test/logout", True),
        ("https://jobs.example.test/api/saved-jobs/42", False),
    ],
)
@pytest.mark.parametrize("method", ["DELETE", "PUT", "PATCH"])
def test_a_delete_of_a_session_or_a_token_never_leaves(url: str, refused: bool, method) -> None:
    """Welcome to the Jungle's way out, `DELETE /api/v1/sessions`, a script may trigger."""
    assert (line.request_refused(method, url, "fetch") is not None) is refused


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
    facts = button(
        "Go", type="submit", in_form=True, form_kinds=kinds, discovery={"method": method}
    )
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
        # A search or consent word in a query value is a tracking parameter of the posting's
        # own address, never a search (review of B20, round 1): its path says.
        ("https://boards.greenhouse.test/examplerobotics/jobs/4012345?gh_src=jobsearch", False),
        (JOBS + "/jobs/42?ref=search", False),
        (JOBS + "/jobs/42?source=LinkedInJobSearch", False),
        (JOBS + "/jobs/42?search=1", False),
        (JOBS + "/jobs/42?cookie_consent=all", False),
        (JOBS + "/jobs/42?from=consent.example.test", False),
        (JOBS + "/search?ref=jobs-42", True),
        (JOBS + "/careersection/jobsearch.ftl?lang=en", True),
    ],
)
def test_a_document_post_leaves_only_as_a_search_or_a_consent_choice(url, allowed) -> None:
    assert (line.request_refused("POST", url, "document") is None) is allowed


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "get"])
@pytest.mark.parametrize("path", ["/jobs/42/apply", "/messaging", "/" + "a" * 5000])
def test_a_get_always_leaves(method: str, path: str) -> None:
    """Any GET but a sign out (test_a_sign_out_address_never_leaves)."""
    assert line.request_refused(method, JOBS + path, "document") is None


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "ws://x.test/", 7])
def test_another_scheme_or_no_address_is_refused(url: object) -> None:
    assert line.request_refused("POST", url, "fetch") is not None


@pytest.mark.parametrize(
    ("name", "method", "allowed"),
    [
        ("Search application", "post", False),
        ("Search applications", "", False),  # no method read: not a GET, refused
        ("Candidature", "post", False),
        ("Bewerbung suchen", "post", False),
        ("Postulación", "post", False),
        ("SearchApplication", "post", False),
        ("Search", "post", True),
        ("Find", "post", True),
        ("Search application", "get", True),  # a GET form is a search
        ("Rechercher une candidature", "get", True),
    ],
)
def test_an_application_s_noun_on_a_button_of_a_post_form_refuses(name, method, allowed) -> None:
    """Part A of B20: a POST form holding a search field only, whose button names an
    application, is no search form (fail closed); a GET one stays a search."""
    facts = button(
        name,
        type="submit",
        in_form=True,
        form_kinds=["input:search", "button:submit"],
        discovery={"method": method},
    )
    for action in ("click", "key"):
        assert (line.control(facts, action) is None) is allowed, (name, method, action)


class Route:
    def __init__(self) -> None:
        self.done: list[str] = []

    async def fallback(self) -> None:
        self.done.append("fallback")

    async def abort(self, code: str = "") -> None:
        self.done.append("abort")

    async def fulfill(self, **_: object) -> None:
        self.done.append("fulfill")


class Unreadable:
    """A request whose every property raises, as a request of a closed target may."""

    def __getattr__(self, name: str) -> object:
        raise RuntimeError("target closed")

    def is_navigation_request(self) -> bool:
        raise RuntimeError("target closed")


class NoKind:
    """A fetch POST to a message address whose resource type cannot be read."""

    method, url, frame = "POST", "https://jobs.example.test/api/messages", None

    def is_navigation_request(self) -> bool:
        return False

    @property
    def resource_type(self) -> str:
        raise RuntimeError("target closed")


@pytest.mark.parametrize("request_", [Unreadable(), NoKind()], ids=["unreadable", "no_kind"])
def test_a_request_the_route_cannot_read_is_refused_never_left_to_raise(request_) -> None:
    import asyncio
    from types import SimpleNamespace

    from browserd.tools import actions

    tab = actions.Tab(SimpleNamespace(hosts=()), actions.State(), None, None, lease_tag="e2e")
    route = Route()
    asyncio.run(tab._line_route(route, request_))
    assert route.done == ["abort"]
