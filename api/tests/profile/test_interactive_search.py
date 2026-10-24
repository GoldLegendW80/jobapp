"""Saved interactive intent is bounded, canonical and independent of browser consent."""

import copy
import socket
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.browser import linkedin_guard
from app.profile import schemas
from tests.identity.api_helpers import Api
from tests.jobs.test_api import sign_in


def site(url: str = "https://jobs.example.org/", **changes: Any) -> dict[str, Any]:
    return {"platform": "custom", "url": url, "enabled": True, "mode": "llm_browser", **changes}


@pytest.mark.parametrize(
    ("url", "canonical"),
    [
        ("HTTPS://Jobs.Example.org:443#top", "https://jobs.example.org/"),
        ("http://jobs.example.org:80/", "http://jobs.example.org/"),
        ("https://jobs.example.org./", "https://jobs.example.org/"),
        ("https://jobs.example.org/été", "https://jobs.example.org/%C3%A9t%C3%A9"),
        ("https://jobs.example.org/?q=ai engineer", "https://jobs.example.org/?q=ai%20engineer"),
        ("https://jobs.example.org:8443/roles", "https://jobs.example.org:8443/roles"),
        ("https://xn--bcher-kva.example.org/", "https://xn--bcher-kva.example.org/"),
    ],
)
def test_source_urls_use_existing_finder_canonicalization(url: str, canonical: str) -> None:
    saved = schemas.SearchSection.model_validate({"interactive_sites": [site(url)]})
    assert saved.interactive_sites[0].url == canonical


@pytest.mark.parametrize(
    "url",
    [
        "https://user:password@jobs.example.org/",
        "https://user@jobs.example.org/",
        "file:///tmp/jobs",
        "javascript:alert(1)",
        "ftp://jobs.example.org/",
        "https://127.0.0.1/",
        "https://8.8.8.8/",
        "https://[::1]/",
        "https://[2001:4860:4860::8888]/",
        "http://2130706433/",
        "http://127.1/",
        "http://0x7f000001/",
        "http://localhost/",
        "http://sub.localhost/",
        "http://jobs.local/",
        "http://jobs.internal/",
        "http://jobs.lan/",
        "http://metadata.google.internal/",
        "https://jobs/",
        "https://example..org/",
        "https://jobs.example.org:99999/",
        "https://jobs.example.org\\@localhost/",
        "https://jobs.example.org/\x00",
        "https://jobs.example.org/\ud800",
        "https://jobs.example.org/" + "x" * 2000,
    ],
)
def test_invalid_or_private_source_addresses_are_refused_without_dns(url: str) -> None:
    with pytest.raises(ValidationError):
        schemas.SearchSection.model_validate({"interactive_sites": [site(url)]})


@pytest.mark.parametrize(
    ("platform", "host"),
    [
        ("linkedin", "www.linkedin.com"),
        ("welcome_to_the_jungle", "www.welcometothejungle.com"),
        ("indeed", "fr.indeed.com"),
        ("freework", "www.free-work.com"),
    ],
)
def test_custom_alias_keeps_code_owned_platform_classification(platform: str, host: str) -> None:
    selected = schemas.SearchSection.model_validate(
        {"interactive_sites": [site(f"https://{host}/", platform="custom")]}
    ).interactive_sites[0]
    assert selected.effective_platform == platform
    assert (
        schemas.SearchSection.model_validate(
            {"interactive_sites": [site(f"https://{host}/", platform=platform)]}
        )
        .interactive_sites[0]
        .effective_platform
        == platform
    )
    with pytest.raises(ValidationError):
        schemas.SearchSection.model_validate(
            {"interactive_sites": [site("https://linkedin.com.example.org/", platform=platform)]}
        )


@pytest.mark.parametrize(("root", "platform"), schemas.INTERACTIVE_HOSTS.items())
def test_protected_host_boundary_neighbors(root: str, platform: str) -> None:
    accepted = [root, "www." + root, "jobs." + root, root.upper(), root + ".", "a.b." + root]
    refused = [
        "not" + root,
        root + ".example.org",
        root.replace(".", "-") + ".org",
        "example.org",
        "jobs." + root + ".example.org",
    ]
    for host in accepted:
        picked = schemas.InteractiveSite.model_validate(site(f"https://{host}/"))
        assert picked.effective_platform == platform
    for host in refused:
        picked = schemas.InteractiveSite.model_validate(site(f"https://{host}/"))
        assert picked.effective_platform == "custom"
        with pytest.raises(ValidationError):
            schemas.InteractiveSite.model_validate(site(f"https://{host}/", platform=platform))


def test_source_save_does_not_resolve_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("saving search intent must not resolve a remote host")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    assert schemas.SearchSection.model_validate({"interactive_sites": [site()]}).interactive_sites


@pytest.mark.parametrize(
    "changes",
    [
        {"enabled": "true"},
        {"mode": "api"},
        {"platform": "free_work"},
        {"accepted_at": "2026-01-01"},
        {"checkpoint": None},
        {"unknown": 1},
    ],
)
def test_site_contract_is_strict_and_cannot_write_consent(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        schemas.SearchSection.model_validate({"interactive_sites": [site(**changes)]})


def test_selection_and_instruction_limits_and_duplicate_canonical_urls() -> None:
    sources = [site(f"https://jobs.example.org/{i}") for i in range(10)]
    saved = schemas.SearchSection.model_validate(
        {"interactive_sites": sources, "interactive_instructions": "  café\r\nremote\t"}
    )
    assert saved.interactive_instructions == "café\nremote"
    assert len(saved.interactive_sites) == 10
    for changes in (
        {"interactive_sites": [*sources, site()]},
        {"interactive_sites": [site(), site("HTTPS://JOBS.EXAMPLE.ORG:443/#x")]},
        {"interactive_instructions": "x" * 2001},
        {"interactive_instructions": 12},
        {"interactive_instructions": "x\x00"},
    ):
        with pytest.raises(ValidationError):
            schemas.SearchSection.model_validate(changes)
    assert (
        schemas.SearchSection.model_validate(
            {"interactive_instructions": " "}
        ).interactive_instructions
        is None
    )
    assert (
        len(
            schemas.SearchSection.model_validate(
                {"interactive_instructions": "x" * 2000}
            ).interactive_instructions
        )
        == 2000
    )


def test_older_writes_distinguish_omission_from_explicit_clear() -> None:
    assert "interactive_sites" not in schemas.validate_section("search", {})
    assert schemas.validate_section("search", {"interactive_sites": []})["interactive_sites"] == []
    assert (
        schemas.validate_section("search", {"interactive_instructions": None})[
            "interactive_instructions"
        ]
        is None
    )
    raw = {name: {} for name in schemas.SECTIONS}
    document = schemas.ImportIn.model_validate(
        {
            "format": "jobapp-profile",
            "version": 1,
            "exported_at": "2026-10-24T09:00:00Z",
            "sections": copy.deepcopy(raw),
        }
    )
    assert "interactive_sites" not in document.stored_sections()["search"]


def test_profile_save_import_isolation_and_consent_checkpoint_preservation(
    api: Api,
    factory: sessionmaker[Session],
) -> None:
    alice = sign_in(factory, api, "e2e-j629-profile-alice@example.org")
    bob = sign_in(factory, api, "e2e-j629-profile-bob@example.org")

    def profile(who: Any) -> Any:
        return api.get("/api/profile", token=who.token).json()

    def save(data: Any, revision: int | None = None) -> Any:
        return api.request(
            "PUT",
            "/api/profile/search",
            token=alice.token,
            json={
                "data": data,
                "base_revision": profile(alice)["revision"] if revision is None else revision,
            },
        )

    chosen = [site("https://www.linkedin.com/jobs/", platform="custom")]
    assert (
        save(
            {"interactive_sites": chosen, "interactive_instructions": "scope-only-71b3"}
        ).status_code
        == 200
    )
    with factory() as db:
        assert not linkedin_guard.allowed(db, alice.id)
        linkedin_guard.record_checkpoint(db, alice.id, datetime(2026, 10, 24, tzinfo=UTC))
        db.commit()
    assert profile(bob)["sections"]["search"] is None
    assert save({"days": 2}, revision=0).status_code == 409
    assert save({"days": 2}).status_code == 200
    saved = profile(alice)["sections"]["search"]
    assert saved["interactive_sites"] == chosen
    assert saved["interactive_instructions"] == "scope-only-71b3"
    exported = api.get("/api/profile/export", token=alice.token).json()
    exported["sections"]["search"].pop("interactive_sites")
    exported["sections"]["search"].pop("interactive_instructions")
    imported = api.request("POST", "/api/profile/import", token=alice.token, json=exported)
    assert imported.status_code == 200, imported.text
    assert profile(alice)["sections"]["search"]["interactive_sites"] == chosen
    with factory() as db:
        assert not linkedin_guard.allowed(db, alice.id)
        assert (
            linkedin_guard.paused_until(db, alice.id, datetime(2026, 10, 25, tzinfo=UTC))
            is not None
        )
    assert save({"interactive_sites": [], "interactive_instructions": None}).status_code == 200
    assert profile(alice)["sections"]["search"]["interactive_sites"] == []
