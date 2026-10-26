"""The Review first ATS addresses are closed-world; every wizard step stays in the approval."""

import pytest

from app.apply.ats.google_careers import GoogleCareers
from app.apply.ats.vie import Vie
from app.apply.ats.workday import Workday


@pytest.mark.parametrize(
    ("adapter", "url", "accepted"),
    [
        (Workday, "https://example.wd1.myworkdayjobs.com/en-US/Jobs/job/Paris/Engineer_R123", True),
        (
            Workday,
            "https://example.wd5.myworkdayjobs.com/Jobs/job/Engineer_R123/apply/applyManually",
            True,
        ),
        (
            GoogleCareers,
            "https://www.google.com/about/careers/applications/jobs/results/12345-engineer",
            True,
        ),
        (GoogleCareers, "https://careers.google.com/jobs/results/12345-engineer", True),
        (Vie, "https://mon-vie-via.businessfrance.fr/en/offres/12345", True),
        (Vie, "https://mon-vie-via.businessfrance.fr/offres/12345", True),
        *[
            (adapter, url, False)
            for adapter, host, path in (
                (Workday, "example.wd1.myworkdayjobs.com", "/en-US/Jobs/job/Paris/Engineer_R123"),
                (
                    GoogleCareers,
                    "www.google.com",
                    "/about/careers/applications/jobs/results/12345-engineer",
                ),
                (Vie, "mon-vie-via.businessfrance.fr", "/en/offres/12345"),
            )
            for url in (
                "http://" + host + path,
                "https://" + host + ".evil.example.org" + path,
                "https://evil.example.org/" + host + path,
                "https://user@" + host + path,
                "https://" + host + ":8443" + path,
                "https://" + host + "/unrelated",
                "https://" + host + "/",
                "javascript:alert(1)",
                "",
                "https://[invalid",
            )
        ],
    ],
)
def test_supported_addresses_and_neighbours(adapter, url, accepted):
    assert (adapter().form_url(url) is not None) is accepted


@pytest.mark.parametrize(
    ("adapter", "url"),
    [
        *[
            (Workday, f"https://example.wd{n}.myworkdayjobs.com/en-US/Jobs/job/Paris/Engineer_R{n}")
            for n in range(1, 11)
        ],
        *[
            (
                GoogleCareers,
                f"https://www.google.com/about/careers/applications/jobs/results/{n}-engineer",
            )
            for n in range(1001, 1009)
        ],
        *[
            (Vie, f"https://mon-vie-via.businessfrance.fr/{lang}offres/{n}")
            for lang in ("", "en/", "fr/")
            for n in (1001, 1002)
        ],
    ],
)
def test_accepted_address_table_keeps_real_job_identity(adapter, url):
    one = adapter()
    assert one.form_url(url)
    assert one.job_of(url)


@pytest.mark.parametrize("kind", ["workday", "google_careers", "vie"])
def test_fixture_mapping_is_only_the_explicit_local_origin_and_path(kind):
    from app.apply.ats import adapter_for
    from tests.apply.a15_support import BASE, url

    assert adapter_for(url(kind), stub_base=BASE).name == kind
    for candidate in (
        url(kind).replace("atsforms", "other"),
        url(kind).replace("a15/", "a15-neighbour/"),
        url(kind).replace(".html", ".html/extra"),
        url(kind) + "#fragment",
    ):
        assert adapter_for(candidate, stub_base=BASE) is None
    assert adapter_for(url(kind)) is None
    assert (
        adapter_for(
            url(kind, base="https://public.example.org"), stub_base="https://public.example.org"
        )
        is None
    )


def test_canonical_keys_join_workday_posting_and_manual_form():
    from app.apply import guardrails

    original = "https://example.wd1.myworkdayjobs.com/en-US/Jobs/job/Paris/Engineer_R123"
    assert guardrails.ats_of(original) == "workday"
    assert guardrails.canonical_key(original) == guardrails.canonical_key(
        original + "/apply/applyManually"
    )
