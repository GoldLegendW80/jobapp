# ruff: noqa: E501  tables of addresses, one per line
"""B19, round 1 of its review: three guards around a signed in search of a real job website.

- A text box whose names say it writes to someone (a message, a reply, a comment, a chat, a note
  or a post, in English, French and German) is never a search box: typing and Enter there are
  refused, signed in or anonymous (discovery.composes); a contenteditable that is no searchbox
  or combobox stays refused as before. Generated from the review's word list and the forms a page
  gives a name, next to a corpus of the real websites' search boxes that must still take a saved
  value.
- A page of a person or of the person's own lists never opens by a navigation or a link click
  on the family (discovery.personal), while the search pages, result lists and postings still
  open; a signed in LinkedIn's own redirect of its root to the feed is followed.
- A page's POST whose address sets an alert, favourites, subscribes, likes, reacts or shares never
  leaves (discovery.POST_WORDS), and a search button whose id holds one of them is not clicked.

Round 2 of its review: Algolia's client metadata (`x-algolia-agent` and its two sisters) is no
part of what a POST says, so a page built with React InstantSearch searches (discovery.
CLIENT_KEYS); `like` and `share` are read as whole words and `react` only as `reaction`, so
`react-aria-12` and `likely` pass; LinkedIn's redirect of its start to the feed is followed for
exactly `/feed/` and from the start address only (discovery.start_feed); Indeed's `/write` and
`/writereview` are whole path segments, so `/cmp/Writer*` company pages open.
"""

import asyncio
import unicodedata
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from tests.browser import test_b17_browserd as b17
from tests.browser import test_j18_scope as j18
from tests.browser.test_b19_search import ALGOLIA, INDEED, JUNGLE, LINKEDIN, control, judge, scope
from tests.browser.test_j18_scope import discovery

# --- a composer is never a search box -----------------------------------------------------------

# The review's words (follow up 6), as a page writes them, and their other forms on real pages.
REVIEW_WORDS = [
    "Message", "Messagerie", "Nachricht", "Reply", "Répondre", "Antworten", "Comment",
    "Commentaire", "Kommentar", "Chat", "Note", "Post", "Publier", "Write", "Écrire", "Schreiben",
]  # fmt: skip
OTHER_FORMS = [
    "Messages", "Messaging", "Nachrichten", "Replies", "Réponse", "Antwort", "Comments",
    "Commentaires", "Kommentare", "Chats", "Notes", "Posts", "Beitrag", "Beiträge", "Writing",
    "Écrivez", "Schreibe", "InMail", "Tchat", "Publication", "Publish",
]  # fmt: skip
# A glued id is read for a stem inside it; "post" only as a whole word (`postcode` is a place box:
# declared open point, `postbox` is read as a box).
GLUED_MISSED = {"Post", "Posts", "Posten", "Poster", "Publication"}


def plain(word: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", word) if not unicodedata.combining(ch)
    )


def full_width(word: str) -> str:
    return "".join(chr(ord(ch) + 0xFEE0) if "!" <= ch <= "~" else ch for ch in word)


def forms(word: str) -> list[str]:
    """The names a page gives a box that holds `word`: a label, a placeholder, an aria-label, an
    id or a name attribute."""
    a = plain(word).lower()
    names = [
        word,
        word.upper(),
        word.lower(),
        plain(word),
        full_width(word),
        word[:2] + "​" + word[2:],  # a zero width space inside
        f"Add a {word.lower()}…",
        f"{word} (optional)",
        f"Votre {word.lower()} ici",
        f"Ihre {word}",
        f"{a}-box",
        f"new_{a}_input",
        f"new{a.capitalize()}Field",
        f"{a.upper()}_INPUT",
    ]
    if word not in GLUED_MISSED:
        names.append(f"{a}box")
    return names


COMPOSERS = [(word, name) for word in REVIEW_WORDS + OTHER_FORMS for name in forms(word)]
# Every kind of box a signed in search may type in (discovery.search_box).
KINDS = [
    {"tag": "input", "kind": "text"},
    {"tag": "input", "kind": "search"},
    {"tag": "input", "kind": "text", "role": "combobox"},
    {"tag": "input", "kind": "search", "role": "searchbox"},
    {"tag": "textarea", "kind": "textarea", "rows": 1},
    {"tag": "div", "kind": "", "role": "combobox", "editable": True},
    {"tag": "div", "kind": "", "role": "searchbox", "editable": True},
]


def native(*names: str) -> dict:
    """An anonymous page's native GET search box (jobs J18), its names given."""
    shape = j18.control("q", kind="search")
    shape["names"] = list(names)
    return shape


@pytest.mark.parametrize(("word", "name"), COMPOSERS)
def test_a_box_whose_names_say_it_writes_to_someone_is_never_a_search_box(word, name):
    for kind in KINDS:
        for shape in (control(name, **kind), control("Search", name, **kind)):
            assert discovery.composes(shape), (word, name, kind)
            assert not discovery.search_box(shape)
            assert judge(shape, "browser_type", text="GenAI Engineer") is not None
            assert judge(shape, "browser_type", text="Paris", key=True) is not None
            assert judge(shape, "browser_type", text="") is not None
    anonymous = native(name)
    assert j18.SCOPE.titles  # a saved title the native box would take, but for its names
    assert (
        discovery.verdict(
            anonymous, "browser_type", j18.START, scope=j18.SCOPE, args={"text": "robot engineer"}
        )
        is not None
    )


# What the review quoted (follow up 6): chat composers are often a one line textarea.
QUOTED = [
    control("Write a message…", tag="textarea", kind="textarea", rows=1),
    control("Type a message", tag="textarea", kind="textarea", rows=1),
    control("Écrire un message", "msg-form__textarea", tag="textarea", kind="textarea", rows=1),
    control("Add a comment…", tag="textarea", kind="textarea", rows=1),
    control("Write a reply", kind="text"),
    control("Nachricht schreiben", role="combobox"),
    control("Ajouter une note", kind="search"),
    control("Start a post", tag="div", kind="", role="combobox", editable=True),
    control("Search", "replyInput", kind="search"),
    control("Search", "chatComposer", role="combobox"),
    control("Search", "msgbox", kind="text"),
    control("Send InMail", kind="text"),
]


@pytest.mark.parametrize("shape", QUOTED)
def test_the_review_s_composers_are_refused(shape):
    assert not discovery.search_box(shape)
    assert judge(shape, "browser_type", text="GenAI Engineer") is not None
    assert judge(shape, "browser_type", text="GenAI Engineer", key=True) is not None


def test_a_contenteditable_that_is_no_searchbox_stays_refused():
    for name in ("Search", "Write a message", "What do you want to talk about?"):
        shape = control(name, tag="div", kind="", role="textbox", editable=True)
        assert not discovery.search_box(shape)
        assert judge(shape, "browser_type", text="GenAI Engineer") is not None
        assert judge(shape, "browser_type", text="GenAI Engineer", key=True) is not None


# The real websites' search boxes (their labels, placeholders and ids, EN, FR, DE, 2026-10) and
# their near misses: each stays a search box and takes its saved value.
TRUE_BOXES = [
    "Search", "Search by title, skill, or company", "Title, skill or company",
    "City, state, or zip code", "Search jobs", "Job title, keywords, or company", "What", "Where",
    "Intitulé de poste, mots-clés ou entreprise", "Ville, département, code postal",
    "Poste, mot-clé ou entreprise", "Métier, mot-clé ou entreprise", "Rechercher un poste",
    "Poste recherché", "Postes", "Code postal", "Postcode", "Postal code", "Postleitzahl",
    "Jobtitel, Stichwort oder Unternehmen", "Ort, Bundesland oder PLZ", "Was", "Wo",
    "Stellenangebote suchen", "Suche", "Search job postings", "Search for a job", "Where?",
    "Où ?", "Keywords", "Entreprise", "Lieu", "jobs-search-box-keyword-id-ember30",
    "jobs-search-box-location-id-ember31", "text-input-what", "text-input-where",
    "search-query", "searchKeywords", "location", "q", "l", "e.g. Technical writer",
    "Content writer, engineer", "Rechercher des offres", "Trouver un emploi", "Notation",
    "Chercher par métier", "Ex : Châteauroux", "Annonces", "postalCode", "zipcode",
]  # fmt: skip


@pytest.mark.parametrize("name", TRUE_BOXES)
def test_the_real_websites_search_boxes_still_take_a_saved_value(name):
    text = "Paris" if discovery.box_kind([name]) == "place" else "GenAI Engineer"
    for kind in KINDS:
        shape = control(name, **kind)
        assert not discovery.composes(shape), (name, kind)
        assert discovery.search_box(shape)
        assert judge(shape, "browser_type", text=text) is None, (name, kind)
        assert judge(shape, "browser_type", text=text, key=True) is None


def test_names_that_cannot_be_read_compose():
    assert discovery.composes({"names": None})
    assert discovery.composes({"names": ["Search", 3]})
    assert discovery.composes({"names": ["Search"] * 61})
    assert not discovery.composes({"names": []})


# --- a person's page and the person's own lists never open ----------------------------------------

PERSONAL = [
    (LINKEDIN, "https://www.linkedin.com/in/someone-e2e/"),
    (LINKEDIN, "https://www.linkedin.com/in/someone-e2e"),
    (LINKEDIN, "https://www.linkedin.com/in/someone-e2e/details/experience/"),
    (LINKEDIN, "https://fr.linkedin.com/in/someone-e2e/"),
    (LINKEDIN, "https://linkedin.com/in/someone-e2e/"),
    (LINKEDIN, "https://www.linkedin.com/IN/someone-e2e/"),
    (LINKEDIN, "https://www.linkedin.com/in/"),
    (LINKEDIN, "https://www.linkedin.com/jobs/../in/someone-e2e/"),
    (LINKEDIN, "https://www.linkedin.com/pub/someone-e2e/1/2/3"),
    (LINKEDIN, "https://www.linkedin.com/pub/dir/Ada/Example"),
    (LINKEDIN, "https://www.linkedin.com/company/example-robotics/people/"),
    (LINKEDIN, "https://www.linkedin.com/company/example-robotics/people/?keywords=genai%20engineer"),
    (LINKEDIN, "https://www.linkedin.com/school/example-school/people/"),
    (LINKEDIN, "https://www.linkedin.com/search/results/people/?keywords=genai%20engineer"),
    (LINKEDIN, "https://www.linkedin.com/mynetwork/"),
    (LINKEDIN, "https://www.linkedin.com/mynetwork/invite-connect/connections/"),
    (LINKEDIN, "https://www.linkedin.com/notifications/"),
    (LINKEDIN, "https://www.linkedin.com/feed/"),
    (LINKEDIN, "https://www.linkedin.com/feed"),
    (LINKEDIN, "https://www.linkedin.com/Feed/"),
    (LINKEDIN, "https://www.linkedin.com/feed/update/urn:li:activity:7000000000000000000/"),
    (LINKEDIN, "https://www.linkedin.com/messaging/"),
    (LINKEDIN, "https://www.linkedin.com/messaging/thread/42/"),
    (LINKEDIN, "https://www.linkedin.com/me/profile-views/"),
    (LINKEDIN, "https://www.linkedin.com/me/"),
    (LINKEDIN, "https://www.linkedin.com/my-items/saved-jobs/"),
    (LINKEDIN, "https://www.linkedin.com/My-Items/"),
    (INDEED, "https://www.indeed.com/myjobs"),
    (INDEED, "https://fr.indeed.com/myjobs?from=gnav"),
    (INDEED, "https://fr.indeed.com/MyJobs"),
    (INDEED, "https://fr.indeed.com/my-jobs"),
    (INDEED, "https://fr.indeed.com/profile"),
    (INDEED, "https://fr.indeed.com/profile/preferences"),
    (INDEED, "https://fr.indeed.com/cmp/Example-Robotics/reviews/write"),
    (INDEED, "https://fr.indeed.com/cmp/Example-Robotics/write-review"),
    (INDEED, "https://fr.indeed.com/cmp/Example-Robotics/reviews/WriteReview"),
    (INDEED, "https://fr.indeed.com/cmp/Example-Robotics/reviews/Write/"),
    (INDEED, "https://fr.indeed.com/cmp/Example-Robotics/Write-Review/"),
    (INDEED, "https://fr.indeed.com/cmp/Example-Robotics/write"),
    (INDEED, "https://my.indeed.com/resume"),
    (INDEED, "https://my.indeed.com/"),
    (INDEED, "https://MY.indeed.com/"),
    (JUNGLE, "https://www.welcometothejungle.com/fr/me"),
    (JUNGLE, "https://www.welcometothejungle.com/fr/me/"),
    (JUNGLE, "https://www.welcometothejungle.com/en/me/settings"),
    (JUNGLE, "https://www.welcometothejungle.com/en-GB/me"),
    (JUNGLE, "https://www.welcometothejungle.com/fr/Me"),
    (JUNGLE, "https://www.welcometothejungle.com/fr/account"),
    (JUNGLE, "https://www.welcometothejungle.com/en/account/edit"),
    (JUNGLE, "https://www.welcometothejungle.com/me"),
    (JUNGLE, "https://welcometothejungle.com/fr/me"),
    ("http://www.indeed.test:8080/", "http://fr.indeed.test:8080/myjobs"),
    ("http://www.indeed.test:8080/", "http://my.indeed.test:8080/"),
    ("http://www.welcometothejungle.test:8080/fr/jobs", "http://www.welcometothejungle.test:8080/fr/me"),
]  # fmt: skip
# Search pages, result lists and postings, their near misses in the same words included.
STILL_OPEN = [
    (LINKEDIN, "https://www.linkedin.com/"),
    (LINKEDIN, "https://www.linkedin.com/jobs/"),
    (LINKEDIN, "https://www.linkedin.com/jobs/search/?keywords=GenAI%20Engineer"),
    (LINKEDIN, "https://www.linkedin.com/jobs/search-results/?keywords=genai%20engineer"),
    (LINKEDIN, "https://www.linkedin.com/jobs/collections/recommended/"),
    (LINKEDIN, "https://www.linkedin.com/jobs/view/4012345678/"),
    (LINKEDIN, "https://www.linkedin.com/jobs/view/engineer-in-paris-at-example-4012345678"),
    (LINKEDIN, "https://www.linkedin.com/jobs/view/people-operations-manager-at-example-4012345678/"),
    (LINKEDIN, "https://www.linkedin.com/jobs/view/community-manager-feed-4012345678/"),
    (LINKEDIN, "https://www.linkedin.com/jobs/view/4012345678/?trk=public_jobs_people"),
    (LINKEDIN, "https://www.linkedin.com/company/example-robotics/"),
    (INDEED, "https://www.indeed.com/"),
    (INDEED, "https://fr.indeed.com/jobs?q=GenAI+Engineer&l=Paris"),
    (INDEED, "https://fr.indeed.com/viewjob?jk=3f2a9c1d0b8e7a65&from=myjobs"),
    (INDEED, "https://fr.indeed.com/rc/clk?jk=3f2a9c1d0b8e7a65&from=profile"),
    (INDEED, "https://fr.indeed.com/cmp/Example-Robotics"),
    (INDEED, "https://fr.indeed.com/cmp/Example-Robotics/reviews"),
    (INDEED, "https://fr.indeed.com/cmp/Writer"),  # a company, not the review form (round 2)
    (INDEED, "https://fr.indeed.com/cmp/Writer/reviews"),
    (INDEED, "https://fr.indeed.com/cmp/Writers-Guild-Example"),
    (INDEED, "https://fr.indeed.com/cmp/Writer-Example/salaries"),
    (INDEED, "https://fr.indeed.com/cmp/Example-Robotics/reviews/Writing"),
    (JUNGLE, "https://www.welcometothejungle.com/en/jobs"),
    (JUNGLE, "https://www.welcometothejungle.com/fr/jobs?query=GenAI%20Engineer"),
    (JUNGLE, "https://www.welcometothejungle.com/fr/companies/example/jobs/genai-engineer_paris"),
    (JUNGLE, "https://www.welcometothejungle.com/fr/companies/me/jobs/genai-engineer"),
    (JUNGLE, "https://www.welcometothejungle.com/fr/companies/me-and-you/jobs/account-manager"),
    (JUNGLE, "https://www.welcometothejungle.com/en-GB/companies/example/jobs/account-manager"),
]  # fmt: skip


@pytest.mark.parametrize(("start", "url"), PERSONAL)
def test_a_person_s_page_or_the_person_s_own_list_never_opens(start, url):
    signed = scope(start)
    assert not discovery.signed_request(url, "GET", True, signed)  # a navigation, a frame
    assert not discovery.page_url(url, signed)
    assert not discovery.read_url(url, start, scope=scope(start, signed_in=False))
    link = control("Ada Example", tag="a", kind="", href=url)
    assert judge(link, "browser_click", start=start) is not None  # a link click


@pytest.mark.parametrize(("start", "url"), STILL_OPEN)
def test_search_pages_result_lists_and_postings_still_open(start, url):
    assert discovery.signed_request(url, "GET", True, scope(start))
    link = control("GenAI Engineer", tag="a", kind="", href=url)
    assert judge(link, "browser_click", start=start) is None


FEED = "https://www.linkedin.com/feed/"
# Where a hop to a person's page may come from, and what it may reach (round 2): the saved start
# to exactly `/feed/`, nothing else.
NOT_THE_FEED = [
    "https://www.linkedin.com/feed/update/urn:li:activity:7000000000000000000/",
    "https://www.linkedin.com/feed/update/",
    "https://www.linkedin.com/feed",
    "https://www.linkedin.com/feed//",
    "https://www.linkedin.com/Feed/",
    "https://www.linkedin.com/feed/?trk=x",
    "https://www.linkedin.com/feed/#x",
    "https://www.linkedin.com/feed/hashtag/genai/",
    "https://www.linkedin.com/jobs/feed/",
    "https://www.linkedin.com/in/someone-e2e/",
    "https://www.linkedin.com/messaging/",
    "https://www.linkedin.com/mynetwork/",
    "https://www.linkedin.com/notifications/",
    "https://www.linkedin.com/me/",
    "https://www.linkedin.com/company/example-robotics/people/",
    "https://fr.linkedin.com/feed/",  # another host than the start's own
    "http://www.linkedin.com/feed/",
]
NOT_FROM_THE_START = [
    None,
    "",
    "https://www.linkedin.com/jobs/",
    "https://www.linkedin.com/feed/",
    "https://www.linkedin.com/?trk=x",
    "https://fr.linkedin.com/",
    "https://linkedin.com/",
    "https://www.linkedin.com/jobs/search/?keywords=GenAI%20Engineer",
]


def test_linkedin_s_own_redirect_of_its_start_to_the_feed_is_followed():
    """A signed in LinkedIn answers its root, the saved start, with `/feed/`: that redirect hop
    is followed, a navigation or a link there is not."""
    signed = scope(LINKEDIN)
    assert discovery.signed_request(FEED, "GET", True, signed, hop=True, source=LINKEDIN)
    assert discovery.page_url(FEED, signed, hop=True, source=LINKEDIN)
    assert discovery.read_url(FEED, LINKEDIN, scope=signed, hop=True, source=LINKEDIN)
    assert discovery.redirect_target(LINKEDIN, "/feed/", signed) == FEED
    assert discovery.start_feed(FEED, LINKEDIN, LINKEDIN)
    assert not discovery.signed_request(FEED, "GET", True, signed)  # a navigation
    assert not discovery.signed_request(FEED, "GET", True, signed, source=LINKEDIN)  # no hop
    link = control("Ada Example", tag="a", kind="", href=FEED)
    assert judge(link, "browser_click") is not None  # a link click


@pytest.mark.parametrize("url", NOT_THE_FEED)
def test_no_other_page_is_a_hop_from_the_start(url):
    signed = scope(LINKEDIN)
    assert not discovery.signed_request(url, "GET", True, signed, hop=True, source=LINKEDIN)
    assert not discovery.page_url(url, signed, hop=True, source=LINKEDIN)
    assert not discovery.start_feed(url, LINKEDIN, LINKEDIN)
    assert discovery.redirect_target(LINKEDIN, url, signed) is None


@pytest.mark.parametrize("source", NOT_FROM_THE_START)
def test_the_feed_is_followed_from_the_start_address_only(source):
    signed = scope(LINKEDIN)
    assert not discovery.signed_request(FEED, "GET", True, signed, hop=True, source=source)
    assert not discovery.page_url(FEED, signed, hop=True, source=source)
    assert not discovery.start_feed(FEED, source, LINKEDIN)
    assert not discovery.read_url(FEED, LINKEDIN, scope=signed, hop=True, source=source)


def test_a_hop_to_an_indeed_or_jungle_person_page_is_never_followed():
    for start, url in (
        (INDEED, "https://fr.indeed.com/myjobs"),
        (INDEED, "https://my.indeed.com/"),
        (JUNGLE, "https://www.welcometothejungle.com/fr/me"),
    ):
        assert not discovery.signed_request(
            url, "GET", True, scope(start), hop=True, source=start
        ), url


def hop_of(url: str, source: object = LINKEDIN) -> list:
    """The frames browserd's `_hop` stops for a redirect from `source` to `url` of a signed in
    LinkedIn discovery."""
    context = b17.Context([b17.Page()])
    found = b17.tab_on(context)
    found.request = SimpleNamespace(hosts=("linkedin.com", "*.linkedin.com"), purpose="sweep")
    asyncio.run(found.begin_discovery(scope(LINKEDIN), "browser_navigate", {"url": LINKEDIN}))
    stopped: list = []

    async def stop(frame):
        stopped.append(frame)

    request = SimpleNamespace(
        url=url,
        method="GET",
        redirected_from=SimpleNamespace(url=source) if source is not None else object(),
        is_navigation_request=lambda: True,
        frame=SimpleNamespace(parent_frame=None),
    )

    async def go():
        original = b17.actions.stop_loading
        b17.actions.stop_loading = stop
        try:
            found._hop(request)
            await asyncio.sleep(0)
        finally:
            b17.actions.stop_loading = original

    asyncio.run(go())
    return stopped


def test_browserd_follows_the_start_s_redirect_to_the_feed_and_stops_the_rest():
    assert not hop_of(FEED)
    assert not hop_of("https://www.linkedin.com/jobs/search/?keywords=GenAI%20Engineer")
    assert len(hop_of("https://www.linkedin.com/feed/update/urn:li:activity:7000000000000000000/")) == 1
    assert len(hop_of("https://www.linkedin.com/in/someone-e2e/")) == 1
    assert len(hop_of(FEED, source="https://www.linkedin.com/jobs/")) == 1
    assert len(hop_of(FEED, source=None)) == 1  # a source that cannot be read follows nothing


def test_a_website_on_no_family_has_no_personal_pages():
    custom = scope("https://jobs.example.test/")
    assert discovery.signed_request("https://jobs.example.test/in/someone", "GET", True, custom)


# --- a page's POST that alerts, favourites, subscribes, likes, reacts or shares --------------------

NOT_POSTS = [
    (JUNGLE, "https://www.welcometothejungle.com/api/v1/me/alerts", "fetch"),
    (JUNGLE, "https://www.welcometothejungle.com/api/v1/likes", "fetch"),
    (JUNGLE, "https://www.welcometothejungle.com/api/v1/favorites", "fetch"),
    (JUNGLE, "https://www.welcometothejungle.com/api/v1/favoris", "xhr"),
    (JUNGLE, "https://www.welcometothejungle.com/api/v1/me/favourites", "fetch"),
    (JUNGLE, "https://abc-dsn.algolia.net/1/indexes/jobs/reactions", "fetch"),
    (LINKEDIN, "https://www.linkedin.com/voyager/api/voyagerJobsDashJobAlerts?action=create", "fetch"),
    (LINKEDIN, "https://www.linkedin.com/voyager/api/voyagerSocialDashReactions?threadUrn=x", "fetch"),
    (LINKEDIN, "https://www.linkedin.com/voyager/api/contentcreation/normShares", "fetch"),
    (LINKEDIN, "https://www.linkedin.com/voyager/api/feed/likes", "xhr"),
    (LINKEDIN, "https://www.linkedin.com/voyager/api/graphql?action=execute&queryId=voyagerSocialDashReactions.abc", "fetch"),
    (LINKEDIN, "https://www.linkedin.com/voyager/api/newsletters/subscribe", "fetch"),
    (LINKEDIN, "https://www.linkedin.com/voyager/api/voyagerFeedDashSubscriptions", "fetch"),
    (INDEED, "https://fr.indeed.com/rpc/jobalert/create", "fetch"),
    (INDEED, "https://fr.indeed.com/rpc/job-alert", "fetch"),
    (INDEED, "https://fr.indeed.com/rpc/ALERT", "fetch"),
    (INDEED, "https://fr.indeed.com/rpc/jobalert?action=subscribe", "fetch"),
    (INDEED, "https://fr.indeed.com/m/rpc/share", "xhr"),
    (INDEED, "https://fr.indeed.com/rpc/favorite?jk=3f2a", "fetch"),
    (INDEED, "https://de.indeed.com/rpc/Favoriten", "fetch"),
    (INDEED, "https://fr.indeed.com/rpc/x?do=like", "fetch"),
    (INDEED, "https://fr.indeed.com/rpc/x?op=reactionToJob", "fetch"),
    # Round 2: `like` and `share` as whole words, in a path segment, a key or a value; `react` only
    # as `reaction`.
    (INDEED, "https://fr.indeed.com/rpc/x?do=reaction", "fetch"),
    (INDEED, "https://fr.indeed.com/rpc/x?action=like", "fetch"),
    (INDEED, "https://fr.indeed.com/rpc/x?action=Share", "xhr"),
    (INDEED, "https://fr.indeed.com/rpc/x?like=1", "fetch"),
    (INDEED, "https://fr.indeed.com/rpc/x?shared=1", "fetch"),
    (INDEED, "https://fr.indeed.com/rpc/Likes", "fetch"),
    (INDEED, "https://fr.indeed.com/rpc/job/unlike", "fetch"),
    (INDEED, "https://fr.indeed.com/rpc/job/dislike", "fetch"),
    (INDEED, "https://fr.indeed.com/rpc/job-share", "fetch"),
    (INDEED, "https://fr.indeed.com/rpc/job_sharing", "fetch"),
    (INDEED, "https://fr.indeed.com/rpc/jobShared", "fetch"),
    (INDEED, "https://fr.indeed.com/rpc/x?op=postLike", "fetch"),
    (LINKEDIN, "https://www.linkedin.com/voyager/api/feed/postLikes", "fetch"),
    (LINKEDIN, "https://www.linkedin.com/voyager/api/feed/LIKES", "fetch"),
    # Another key's value on the search service is read as before.
    (JUNGLE, "https://csekhvms53-dsn.algolia.net/1/indexes/*/queries?x-algolia-application-id=E2E&do=like", "fetch"),
    (JUNGLE, "https://csekhvms53-dsn.algolia.net/1/indexes/*/queries?do=reaction&x-algolia-agent=Algolia", "fetch"),
    (JUNGLE, "https://csekhvms53-dsn.algolia.net/1/indexes/*/queries?x-algolia-agent=Algolia&follow=1", "fetch"),
    (JUNGLE, "https://csekhvms53-dsn.algolia.net/1/indexes/*/queries?x-algolia-agent=Algolia&action=save", "fetch"),
    (JUNGLE, "https://csekhvms53-dsn.algolia.net/1/indexes/*/queries?x-algolia-agent=Algolia&x-algolia-save=1", "fetch"),  # a key is read, even beside the three
    (JUNGLE, "https://csekhvms53-dsn.algolia.net/1/indexes/*/queries?x-algolia-agent=Algolia&x-algolia-agent-extra=connect", "fetch"),
    (JUNGLE, "https://csekhvms53-dsn.algolia.net/1/indexes/*/queries?X-Algolia-Agent=connect", "fetch"),  # the keys are Algolia's own, in lower case
    (JUNGLE, "https://csekhvms53-dsn.algolia.net/1/indexes/*/follow?x-algolia-agent=Algolia", "fetch"),  # its path is read
    # The skip is for the search service only: the same query on the website's own host is read.
    (JUNGLE, "https://www.welcometothejungle.com/api/v1/search?x-algolia-agent=connect-ui%20(1.0.0)", "fetch"),
]  # fmt: skip
# The agent strings of three Algolia clients, as their libraries write them (the client's own list,
# `; ` between the segments), and a search service's whole query.
AGENT_INSTANTSEARCH = "Algolia for JavaScript (4.24.0); Browser; instantsearch.js (4.75.3); JS Helper (3.22.5)"
AGENT_REACT = "Algolia for JavaScript (4.24.0); Browser; JS Helper (3.22.5); react (18.2.0); react-instantsearch (7.0.0); react-instantsearch-core (7.0.0)"
AGENT_V5_NEXT = "Algolia for JavaScript (5.2.1); Search (5.2.1); Browser; instantsearch.js (4.75.3); react (18.3.1); react-instantsearch (7.13.0); react-instantsearch-core (7.13.0); next.js (14.2.3)"
SERVICE = "https://csekhvms53-dsn.algolia.net/1/indexes/*/queries"
KEY = "e2e0000000000000000000000000000f"


def algolia(agent: str, key: str = KEY, app: str = "CSEKHVMS53") -> str:
    return f"{SERVICE}?x-algolia-agent={quote(agent)}&x-algolia-api-key={key}&x-algolia-application-id={app}"


STILL_POSTS = [
    (JUNGLE, "https://csekhvms53-dsn.algolia.net/1/indexes/*/queries?x-algolia-application-id=CSEKHVMS53", "fetch"),
    (JUNGLE, algolia(AGENT_INSTANTSEARCH), "fetch"),
    (JUNGLE, algolia(AGENT_REACT), "fetch"),
    (JUNGLE, algolia(AGENT_REACT), "xhr"),
    (JUNGLE, algolia(AGENT_V5_NEXT), "fetch"),
    (JUNGLE, "https://csekhvms53-dsn.algolia.net/1/indexes/*/queries?x-algolia-agent=Algolia%20for%20JavaScript%20(4.24.0)%3B%20Browser%3B%20react%20(18.2.0)%3B%20react-instantsearch%20(7.0.0)", "fetch"),  # a browser's own encoding
    (JUNGLE, "https://www.welcometothejungle.com/api/v1/search", "fetch"),
    (LINKEDIN, "https://www.linkedin.com/voyager/api/graphql?queryId=voyagerJobsDashJobCards.1", "xhr"),
    (LINKEDIN, "https://www.linkedin.com/li/track", "fetch"),
    (INDEED, "https://fr.indeed.com/m/rpc/jobsearch", "fetch"),
    # `react`, `like` and `share` inside other words are no action (round 2).
    (JUNGLE, "https://www.welcometothejungle.com/api/v1/search?ui=react-aria-12&mode=likely&partner=sharepoint", "fetch"),
    (JUNGLE, "https://www.welcometothejungle.com/api/v1/likelihood", "fetch"),
    (JUNGLE, "https://www.welcometothejungle.com/api/v1/react-aria-12", "fetch"),
    (JUNGLE, "https://www.welcometothejungle.com/api/v1/search?client=react&reactVersion=18", "fetch"),
    (LINKEDIN, "https://www.linkedin.com/voyager/api/sharepoint", "fetch"),
    (LINKEDIN, "https://www.linkedin.com/voyager/api/graphql?variables=unlikely&q=shareholder", "xhr"),
    (INDEED, "https://fr.indeed.com/rpc/alike?preact=1&likeness=1", "fetch"),
]  # fmt: skip
# The search service's client metadata holds words a POST never has: red when CLIENT_KEYS is not
# skipped. A site may add a segment of its own to the agent (`addAlgoliaAgent`), and a secured API
# key or an application id is a random string.
CLIENT_METADATA = [
    algolia(AGENT_REACT + "; e2e-connect-ui (1.0.0)"),
    algolia(AGENT_REACT + "; share-widgets (2.1.0)"),
    algolia(AGENT_REACT + "; like-button (1.0.0)"),
    algolia(AGENT_INSTANTSEARCH + "; saved-searches (3.0.0)"),
    algolia(AGENT_V5_NEXT, key="YjA5ZjFkNDRSaVeNzQxZTc4ZDc5YzAzOWY4YzMyMDZm"),  # base64 holding "SaVe"
    algolia(AGENT_REACT, key="FollowKeyE2e0000"),
    algolia(AGENT_REACT, app="A1SAVE7XQ2"),
    algolia(AGENT_REACT, app="MESSAGE0E2"),
]


@pytest.mark.parametrize(("start", "url", "kind"), NOT_POSTS)
def test_a_post_that_alerts_favourites_subscribes_likes_reacts_or_shares_never_leaves(
    start, url, kind
):
    assert not discovery.signed_request(url, "POST", False, scope(start), kind=kind)


@pytest.mark.parametrize(("start", "url", "kind"), STILL_POSTS)
def test_a_search_request_still_posts(start, url, kind):
    assert discovery.signed_request(url, "POST", False, scope(start), kind=kind)


@pytest.mark.parametrize("url", CLIENT_METADATA)
def test_the_search_service_s_client_metadata_is_not_read_as_an_action(url):
    assert discovery.signed_request(url, "POST", False, scope(JUNGLE), kind="fetch")
    assert discovery.signed_request(url, "POST", False, scope(JUNGLE), kind="xhr")


def test_the_page_s_algolia_query_of_the_search_tests_posts():
    assert discovery.signed_request(ALGOLIA, "POST", False, scope(JUNGLE), kind="fetch")


@pytest.mark.parametrize(
    ("names", "clicked"),
    [
        (["Search", "favorite-search"], False),
        (["Search", "subscribe-btn"], False),  # the review's probe, clicked before
        (["Search", "btn-like"], False),
        (["Search", "reactionSearch"], False),
        (["Search", "favouritesSearch"], False),
        (["Search", "jobAlertButton"], False),
        (["Search", "global-nav-search"], True),
        (["Search", "search-button"], True),
        (["Search", "react-aria-12"], True),  # a React site's generated id (round 2)
        (["Search", "react-search-button"], True),
        (["Search", "preact-btn"], True),
        (["Search", "reactSearch"], True),
        (["Rechercher", "btn-search-jobs"], True),
    ],
)
def test_a_search_button_whose_id_holds_one_of_them_is_not_clicked(names, clicked):
    shape = control(*names, tag="button", kind="button")
    assert (judge(shape, "browser_click") is None) is clicked
