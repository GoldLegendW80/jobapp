"""mail/hosts.py: the lists of the binding rule and the one table of the systems (mail M3).
The sending domains are assumption A12: these tests pin what the file says, not what the
systems really send."""

from itertools import combinations

import pytest

from app.mail import domains, hosts
from app.mail.types import BASES, Binding


def _lists() -> dict[str, set[str]]:
    return {
        hosts.ATS: {
            name
            for system in hosts.ATS_SYSTEMS
            for name in (*system.posting_hosts, *system.sending_domains)
        },
        hosts.JOB_BOARD: set(hosts.JOB_BOARDS),
        hosts.SHARED: set(hosts.SHARED_HOSTING),
        hosts.PUBLIC_MAILBOX: set(hosts.PUBLIC_MAILBOXES),
    }


def test_a_few_rows_are_pinned() -> None:
    by_name = {system.name: system for system in hosts.ATS_SYSTEMS}
    assert by_name["greenhouse"].posting_hosts == ("greenhouse.io",)
    assert by_name["greenhouse"].sending_domains == ("greenhouse.io", "greenhouse-mail.io")
    assert by_name["lever"] == hosts.AtsSystem("lever", ("lever.co",), ("lever.co",))
    assert by_name["ashby"] == hosts.AtsSystem("ashby", ("ashbyhq.com",), ("ashbyhq.com",))
    assert by_name["workable"].sending_domains == ("workable.com", "workablemail.com")
    assert {"linkedin.com", "indeed.com", "welcometothejungle.com"} <= hosts.JOB_BOARDS
    assert {"notion.site", "github.io"} <= hosts.SHARED_HOSTING
    assert {"gmail.com", "outlook.com", "orange.fr"} <= hosts.PUBLIC_MAILBOXES


def test_no_host_is_in_two_lists() -> None:
    lists = _lists()
    for (one, names), (other, others) in combinations(lists.items(), 2):
        assert not names & others, (one, other)
        # Nor under an entry of another list: `list_of` would then answer by the order of a loop.
        for name in names:
            for parent in others:
                assert not name.endswith("." + parent), (name, parent)
                assert not parent.endswith("." + name), (name, parent)


def test_no_host_belongs_to_two_systems() -> None:
    for one, other in combinations(hosts.ATS_SYSTEMS, 2):
        assert one.name != other.name
        mine = {*one.posting_hosts, *one.sending_domains}
        theirs = {*other.posting_hosts, *other.sending_domains}
        assert not mine & theirs, (one.name, other.name)
    for system in hosts.ATS_SYSTEMS:
        assert system.posting_hosts
        assert system.sending_domains


def test_every_entry_is_a_plain_lower_case_host_name() -> None:
    for names in _lists().values():
        for name in names:
            assert domains.normalise(name) == name


def test_an_entry_covers_the_names_under_it_and_nothing_beside_it() -> None:
    assert hosts.list_of("job-boards.eu.greenhouse.io") == hosts.ATS
    assert hosts.list_of("Jobs.Lever.co.") == hosts.ATS
    assert hosts.list_of("fr.linkedin.com") == hosts.JOB_BOARD
    assert hosts.list_of("acme.notion.site") == hosts.SHARED
    assert hosts.list_of("gmail.com") == hosts.PUBLIC_MAILBOX
    # A name that only ends with the letters of an entry is another name.
    assert hosts.list_of("notgreenhouse.io") is None
    assert hosts.list_of("greenhouse.io.evil.example") is None
    assert hosts.list_of("careers.example.org") is None


def test_the_company_s_own_host_is_in_no_list() -> None:
    assert hosts.is_company_own("careers.example.org")
    assert not hosts.is_company_own("boards.greenhouse.io")
    assert not hosts.is_company_own("alice.github.io")
    assert not hosts.is_company_own("mail.gmail.com")
    for nothing in ("", "localhost", "127.0.0.1", "exa mple.org", None):
        assert not hosts.is_company_own(nothing)
        assert hosts.list_of(nothing) is None


def test_a_system_is_found_by_its_posting_host_and_by_its_sending_domain() -> None:
    greenhouse = hosts.ats_of_posting_host("job-boards.greenhouse.io")
    assert greenhouse is not None
    assert greenhouse.name == "greenhouse"
    assert hosts.ats_of_sending_domain("us.greenhouse-mail.io") is greenhouse
    # A sending domain is not a posting host, and the other way round.
    assert hosts.ats_of_posting_host("greenhouse-mail.io") is None
    assert hosts.ats_of_sending_domain("myworkdayjobs.com") is None
    lever = hosts.ats_of_sending_domain("hire.lever.co")
    assert lever is not None
    assert lever is not greenhouse
    assert hosts.ats_of_sending_domain("example.org") is None
    assert hosts.ats_of_posting_host("gmail.com") is None


def test_a_public_mailbox() -> None:
    assert hosts.is_public_mailbox("gmail.com")
    assert hosts.is_public_mailbox("GoogleMail.com")
    assert not hosts.is_public_mailbox("example.org")
    assert not hosts.is_public_mailbox("greenhouse.io")


def test_a_binding_states_its_basis_or_its_reason() -> None:
    assert BASES == (
        "posting_host",
        "user_wrote",
        "user_confirmed",
        "ats_reference",
        "ats_posting",
    )
    bound = Binding(True, basis="posting_host", domain="example.org")
    assert bound.bound
    assert not Binding(False, reason="not_trusted").bound
    for wrong in (
        {"bound": True},
        {"bound": True, "basis": "because", "domain": "example.org"},
        {"bound": True, "basis": "posting_host"},
        {"bound": True, "basis": "posting_host", "domain": "example.org", "reason": "x"},
        {"bound": False},
        {"bound": False, "basis": "posting_host", "reason": "x"},
    ):
        with pytest.raises(ValueError, match="Binding"):
            Binding(**wrong)
