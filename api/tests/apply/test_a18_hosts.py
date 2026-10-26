"""One spelling per host: every way of writing a LinkedIn or Workday address that Chrome opens on
that site is classified as that site, refused by its guard and counted as the same host."""

import pytest
import sqlalchemy as sa

from app.apply import profile_sites, site_profile
from app.apply.models import SiteKind, SiteProfileRun
from tests.apply.conftest import AT

LINKEDIN = [
    "https://www.linkedin.com/in/example/",
    "https://www.linkedin.com./in/example/",
    "https://www.linkedin%2Ecom/in/example/",
    "https://www.linkedin%2ecom./in/example/",
    "https://www。linkedin。com/in/example/",
    "https://www.linkedin．com/in/example/",
    "https://www.linkedin｡com/in/example/",
    "https://LinkedIn.com./",
    "https://WWW.LINKEDIN.COM/in/example/",
    "https://www.linkedin.com:443/in/example/",
    "http://www.linkedin.com:8080/in/example/",
    "https://www.ｌｉｎｋｅｄｉｎ.com/in/example/",
    "https://fr.linkedin.com./in/example/",
]
LINKEDIN_KINDS = [
    *LINKEDIN,
    "https://user@www.linkedin.com/in/example/",
    "https://user:secret@www.linkedin.com./in/example/",
]
NOT_LINKEDIN = [
    "https://linkedin.com.evil.example/",
    "https://notlinkedin.com/",
    "https://www.linkedin.co/",
    "https://www.linked-in.com/",
    "https://www.linkedin.com@evil.example/",
    "https://www.linkedin.com%2F@evil.example/",
    "https://www.lіnkedin.com/",  # a Cyrillic i: another host, shown as punycode by Chrome
    "https://portal.example/linkedin.com/",
]


@pytest.mark.parametrize("url", LINKEDIN_KINDS)
def test_every_linkedin_spelling_is_linkedin(url):
    assert profile_sites.kind(url) == "linkedin"


@pytest.mark.parametrize("url", NOT_LINKEDIN)
def test_neighbours_are_not_linkedin(url):
    assert profile_sites.kind(url) != "linkedin"


@pytest.mark.parametrize(
    "url",
    [
        "https://acme.wd3.myworkdayjobs.com./en-US/careers",
        "https://ACME.wd3.MyWorkdayJobs.com/en-US/careers",
        "https://acme.wd3.myworkdayjobs%2Ecom/en-US/careers",
        "https://acme.wd3。myworkdayjobs。com/en-US/careers",
        "https://wd5.myworkdaysite.com./recruiting/acme",
    ],
)
def test_every_workday_spelling_is_workday(url):
    assert profile_sites.kind(url) == "workday"


@pytest.mark.parametrize("url", LINKEDIN)
def test_linkedin_spelling_refused_without_the_acceptance(db, person_a, url):
    with pytest.raises(site_profile.RefusedError) as refused:
        site_profile.start(db, person_a.user_id, url, now=AT)
    assert refused.value.code == "linkedin_off"
    assert db.scalar(sa.select(sa.func.count()).select_from(SiteProfileRun)) == 0


@pytest.mark.parametrize("url", LINKEDIN)
def test_linkedin_spelling_refused_during_the_checkpoint_pause(db, person_a, monkeypatch, url):
    monkeypatch.setattr(site_profile.linkedin_guard, "refusal", lambda *args: "linkedin_paused")
    with pytest.raises(site_profile.RefusedError) as refused:
        site_profile.start(db, person_a.user_id, url, now=AT)
    assert refused.value.code == "linkedin_paused"


@pytest.mark.parametrize(
    "url",
    [
        "https://user@www.linkedin.com/in/example/",
        "https://www.linkedin.com@evil.example/",
        "https://" + "a" * 64 + ".example/",  # a label IDNA cannot encode: fail closed
        "https://www.linkedin.com%2F.example/",
        "https://portal%40evil.example/",
    ],
)
def test_userinfo_and_unencodable_hosts_refused(db, person_a, url):
    with pytest.raises(site_profile.RefusedError) as refused:
        site_profile.start(db, person_a.user_id, url, now=AT)
    assert refused.value.code == "bad_url"
    assert db.scalar(sa.select(sa.func.count()).select_from(SiteProfileRun)) == 0


@pytest.mark.parametrize("url", LINKEDIN)
def test_linkedin_spellings_share_the_one_live_run(db, person_a, monkeypatch, url):
    monkeypatch.setattr(site_profile.linkedin_guard, "refusal", lambda *args: None)
    first = site_profile.start(
        db, person_a.user_id, "https://www.linkedin.com./in/example/", now=AT
    )
    db.commit()
    assert (first.host, first.site_kind) == ("www.linkedin.com", SiteKind.linkedin)
    with pytest.raises(site_profile.RefusedError) as refused:
        site_profile.start(db, person_a.user_id, url, now=AT)
    assert refused.value.code == "already_running"


@pytest.mark.parametrize(
    "url",
    [
        "https://PORTAL.example./profile",
        "https://portal%2Eexample/profile",
        "https://portal。example/profile",
        "https://portal.example:8443/profile",
    ],
)
def test_generic_spellings_share_the_one_live_run(db, person_a, url):
    first = site_profile.start(db, person_a.user_id, "https://portal.example/profile", now=AT)
    db.commit()
    assert first.host == "portal.example"
    with pytest.raises(site_profile.RefusedError) as refused:
        site_profile.start(db, person_a.user_id, url, now=AT)
    assert refused.value.code == "already_running"


def test_idn_host_is_stored_as_its_ascii_spelling(db, person_a):
    run = site_profile.start(db, person_a.user_id, "https://Bücher.example./profil", now=AT)
    assert (run.host, run.site_kind) == ("xn--bcher-kva.example", SiteKind.generic)
