"""mail/auth.py: A of the binding rule, one test per case the plan names (mail M3).

Every header here is hand made, in the shape assumption A4 describes: Gmail's own lines on top
(`Delivered-To`, its internal hop, `Return-Path`, the `Received ... by mx.google.com` hop,
`Received-SPF`, `Authentication-Results: mx.google.com; ...`), then what the sender wrote.
"""

import pytest

from app.mail import auth

ME = "e2e-reader@example.org"
STAMP = "Mon, 12 Oct 2026 09:00:00 +0000"
SPF = (
    "spf=pass (google.com: domain of bounce@example.com designates 203.0.113.7 as permitted "
    "sender) smtp.mailfrom=bounce@example.com"
)
DKIM = "dkim=pass header.i=@example.com header.s=s1 header.b=FaKe0000"
DMARC = "dmarc=pass (p=REJECT sp=REJECT dis=NONE) header.from=example.com"
HOP = (
    "from mail.example.com (mail.example.com. [203.0.113.7]) by mx.google.com with ESMTPS "
    f"id fake-1 for <{ME}>; {STAMP}"
)

Headers = list[tuple[str, str]]


def gmail(*results: str, server: str = "mx.google.com", hop: str | None = HOP) -> Headers:
    """The lines Gmail writes on top of a mail it took by SMTP."""
    lines = [
        ("Delivered-To", ME),
        ("Received", f"by 2002:a05:6402:fake with SMTP id fake1; {STAMP}"),
        ("Return-Path", "<bounce@example.com>"),
    ]
    if hop is not None:
        lines.append(("Received", hop))
    lines.append(
        ("Received-SPF", "pass (google.com: domain of bounce@example.com) client-ip=203.0.113.7;")
    )
    lines.append(("Authentication-Results", "; ".join([server, *results])))
    return lines


def sender(*extra: tuple[str, str]) -> Headers:
    """What the sender wrote, below Gmail's lines."""
    return [
        ("Received", f"from app.example.com by mail.example.com with ESMTP id s1; {STAMP}"),
        *extra,
        ("From", "Ada Example <ada@example.com>"),
        ("To", ME),
        ("Subject", "Your application"),
    ]


def verdict(headers: Headers, from_addr: str | None = "ada@example.com", suspicious: bool = False):
    return auth.authenticate(headers, from_addr, suspicious=suspicious)


def test_dmarc_pass_for_the_visible_sender_is_trusted() -> None:
    assert verdict(gmail(DKIM, SPF, DMARC) + sender()) == auth.Verdict(True, "example.com")


def test_dkim_pass_alone_for_the_sender_s_domain_is_trusted() -> None:
    assert verdict(gmail(DKIM, "spf=fail smtp.mailfrom=x@evil.example") + sender()).trusted


def test_a_forged_header_below_gmail_s_own_is_never_read() -> None:
    forged = ("Authentication-Results", "mx.google.com; " + DKIM + "; " + DMARC)
    headers = gmail(
        "dkim=fail header.i=@example.com",
        "spf=fail smtp.mailfrom=x@evil.example",
        "dmarc=fail header.from=example.com",
    ) + sender(forged)
    assert verdict(headers) == auth.Verdict(False, "example.com")


def test_arc_results_are_the_mail_s_own_claim() -> None:
    arc = ("ARC-Authentication-Results", "i=1; mx.google.com; " + DKIM + "; " + DMARC)
    headers = gmail("dmarc=fail header.from=example.com")
    headers.insert(2, arc)
    assert not verdict(headers + sender(arc)).trusted
    # And with no header of Gmail's at all, an ARC line is not one.
    assert not verdict([("Received", HOP), arc, *sender()]).trusted


def test_a_header_that_names_another_server_is_not_gmail_s() -> None:
    for server in (
        "mx.evil.example",
        "mx.google.com.evil.example",
        "MX.GOOGLE.COM",
        "google.com",
        "mx.google.com 2",
        "mx.google.com x y",
        "",
    ):
        assert not verdict(gmail(DKIM, SPF, DMARC, server=server) + sender()).trusted, server
    # The version of the header, as RFC 8601 allows it, changes nothing.
    assert verdict(gmail(DKIM, SPF, DMARC, server="mx.google.com 1") + sender()).trusted


def test_no_header_at_all_is_not_trusted() -> None:
    assert verdict(sender()) == auth.Verdict(False, "example.com")
    assert verdict([]) == auth.Verdict(False, "example.com")


def test_the_hop_above_must_be_gmail_s() -> None:
    for hop in (
        None,  # the nearest Received above is Gmail's internal line, not the SMTP hop
        "from mail.example.com by mx.evil.example with ESMTPS id 1; " + STAMP,
        "from mx.google.com by mail.evil.example with ESMTPS id 1; " + STAMP,
        "from mail.evil.example (by mx.google.com with ESMTPS) by mail.evil.example with ESMTP "
        "id 1; " + STAMP,
        "from mail.evil.example by mx.google.com.evil.example with ESMTPS id 1; " + STAMP,
        "from mail.evil.example with ESMTP id by mx.google.com; " + STAMP,
        "from mail.evil.example by mail.evil.example; by mx.google.com",
        "from mail.evil.example (unclosed by mx.google.com with ESMTP id 1; " + STAMP,
        "from mail.evil.example\r\nReceived: by mx.google.com with ESMTP id 1; " + STAMP,
        "",
    ):
        assert not verdict(gmail(DKIM, SPF, DMARC, hop=hop) + sender()).trusted, hop
    # A Received line under the header proves nothing about it.
    below = [("Authentication-Results", "mx.google.com; " + DMARC), ("Received", HOP), *sender()]
    assert not verdict(below).trusted


def test_a_mail_fetched_by_pop_is_not_trusted_whatever_it_carries() -> None:
    """The first Authentication-Results is forged, with a forged `Received: by mx.google.com`
    above it: the sender wrote both, and Gmail's mark of the fetch says so."""
    forged = [
        ("Received", HOP),
        ("Authentication-Results", "mx.google.com; " + DKIM + "; " + SPF + "; " + DMARC),
        *sender(),
    ]
    assert verdict(forged).trusted  # which is why the mark below is a condition of the rule
    for mark in ("X-Gmail-Fetch-Info", "x-gmail-fetch-info", "X-GMAIL-FETCH-INFO"):
        fetched = [
            ("Delivered-To", ME),
            (mark, "other@example.net 1 pop.example.net 995 other"),
            *forged,
        ]
        assert verdict(fetched) == auth.Verdict(False, "example.com"), mark
        assert not verdict([*forged, (mark, "")]).trusted


def test_an_imported_mail_has_no_header_of_gmail_s() -> None:
    imported = [
        ("Received", "from old.example.net by imap.example.net with IMAP id 9; " + STAMP),
        ("Authentication-Results", "mx.example.net; " + DKIM + "; " + DMARC),
        ("From", "Ada Example <ada@example.com>"),
    ]
    assert not verdict(imported).trusted


def test_dkim_pass_for_a_parent_domain_of_from_is_a_pass() -> None:
    headers = (
        gmail(
            "dkim=pass header.i=@example.com header.s=s1 header.b=FaKe",
            "dmarc=none header.from=jobs.example.com",
        )
        + sender()
    )
    assert verdict(headers, "ada@jobs.example.com") == auth.Verdict(True, "example.com")
    # And for a sibling or a child: the same registrable domain.
    by_d = gmail("dkim=pass header.d=mail.example.com header.s=s1") + sender()
    assert verdict(by_d, "ada@jobs.example.com").trusted
    assert verdict(gmail("dkim=pass header.i=bot@bounce.mail.example.com") + sender()).trusted


def test_dkim_pass_for_another_domain_with_dmarc_fail_is_no_pass() -> None:
    headers = (
        gmail(
            "dkim=pass header.i=@evil.example header.s=s1 header.b=FaKe",
            SPF,
            "dmarc=fail (p=NONE sp=NONE dis=NONE) header.from=example.com",
        )
        + sender()
    )
    assert verdict(headers) == auth.Verdict(False, "example.com")
    # A signing domain that only looks like the sender's.
    for signer in (
        "@example.com.evil.example",
        "@evil-example.com",
        "@xample.com",
        "example.com@evil.example",
        "@",
        "",
    ):
        assert not verdict(gmail(f"dkim=pass header.i={signer}") + sender()).trusted, signer
    # header.i and header.d must agree when both are there.
    assert not verdict(
        gmail("dkim=pass header.i=@example.com header.d=evil.example") + sender()
    ).trusted
    assert not verdict(gmail("dkim=pass header.s=s1 header.b=FaKe") + sender()).trusted


def test_a_dkim_pass_for_the_sender_counts_as_the_rule_says_even_beside_a_dmarc_fail() -> None:
    """The letter of the rule: `dmarc=pass ... or a dkim=pass whose signing domain has that
    same registrable domain`. (An open question for the owner in the pull request: should an
    explicit dmarc=fail veto it?)"""
    headers = (
        gmail(DKIM, "dmarc=fail (p=REJECT sp=REJECT dis=NONE) header.from=example.com") + sender()
    )
    assert verdict(headers).trusted


def test_spf_pass_alone_is_no_pass() -> None:
    assert verdict(gmail(SPF) + sender()) == auth.Verdict(False, "example.com")
    assert not verdict(
        gmail(
            "spf=pass smtp.mailfrom=ada@example.com",
            "dkim=none",
            "dmarc=none header.from=example.com",
        )
        + sender()
    ).trusted
    assert not verdict(gmail("none") + sender()).trusted
    assert not verdict(gmail() + sender()).trusted


def test_dmarc_pass_must_be_for_the_visible_from() -> None:
    assert not verdict(gmail("dmarc=pass header.from=evil.example") + sender()).trusted
    assert not verdict(gmail("dmarc=pass") + sender()).trusted
    assert not verdict(gmail("dmarc=bestguesspass header.from=example.com") + sender()).trusted
    # Two dmarc results are a shape Gmail does not write.
    assert not verdict(gmail("dmarc=fail header.from=example.com", DMARC) + sender()).trusted


def test_under_a_shared_suffix_the_domain_is_the_customer_s() -> None:
    passing = gmail("dmarc=pass (p=NONE sp=NONE dis=NONE) header.from=alice.github.io") + sender()
    assert verdict(passing, "hr@alice.github.io") == auth.Verdict(True, "alice.github.io")
    assert verdict(passing, "hr@www.alice.github.io") == auth.Verdict(True, "alice.github.io")
    # Another customer of the same suffix is another organisation.
    assert verdict(passing, "hr@mallory.github.io") == auth.Verdict(False, "mallory.github.io")
    by_dkim = gmail("dkim=pass header.i=@alice.github.io") + sender()
    assert not verdict(by_dkim, "hr@mallory.github.io").trusted
    # The suffix itself is nobody's.
    suffix = gmail("dmarc=pass header.from=github.io", "dkim=pass header.d=github.io") + sender()
    assert verdict(suffix, "hr@github.io") == auth.Verdict(False, None)
    assert not verdict(suffix, "hr@alice.github.io").trusted


@pytest.mark.parametrize(
    "value",
    [
        "mx.google.com; dmarc=pass header.from=example.com\r\nX-Injected: 1",
        "mx.google.com; dmarc=pass header.from=example.com\nAuthentication-Results: mx.google.com",
        "mx.google.com;\rdmarc=pass header.from=example.com",
        "mx.evil.example; none\r\n"
        "Authentication-Results: mx.google.com; dmarc=pass header.from=example.com",
        "mx.google.com; dmarc=pass header.from=example.com\x00",
        "mx.google.com; dmarc=pass header.from=example.com\x0b",
    ],
)
def test_a_line_break_inside_the_header_is_not_a_pass(value: str) -> None:
    headers = gmail(DKIM, SPF, DMARC)
    headers[-1] = ("Authentication-Results", value)
    assert verdict(headers + sender()) == auth.Verdict(False, "example.com")


def test_a_folded_header_is_one_header() -> None:
    folded = (
        "mx.google.com;\r\n       dkim=pass header.i=@example.com;\n\tdmarc=pass (p=REJECT)"
        "\r\n header.from=example.com"
    )
    headers = gmail()
    headers[-1] = ("Authentication-Results", folded)
    headers[3] = ("Received", HOP.replace(" by ", "\r\n        by "))
    assert verdict(headers + sender()).trusted


@pytest.mark.parametrize(
    "results",
    [
        'dmarc=pass header.from="example.com"',  # a quoted string
        "dmarc=pass (p=REJECT header.from=example.com",  # a comment left open
        "dmarc=pass p=REJECT) header.from=example.com",
        "dmarc=pass header.from=example.com header.from=evil.example",
        "dmarc=pass header.from example.com",
        "dmarc pass header.from=example.com",
        "dmarc=pass header.from=example.com; what is this",
        "dmarc=pass header.from=" + "a" * 20_000 + ".example.com",
    ],
)
def test_a_shape_that_is_not_recognised_is_not_a_pass(results: str) -> None:
    assert not verdict(gmail(results) + sender()).trusted


def test_what_a_comment_says_is_never_read() -> None:
    hidden = (
        "spf=fail (dmarc=pass header.from=example.com; dkim=pass header.i=@example.com) "
        "smtp.mailfrom=x@evil.example"
    )
    assert not verdict(gmail(hidden) + sender()).trusted
    nested = "dmarc=fail (a (b \\) dmarc=pass) c) header.from=example.com"
    assert not verdict(gmail(nested) + sender()).trusted


def test_a_suspicious_message_or_one_without_one_sender_is_never_trusted() -> None:
    passing = gmail(DKIM, SPF, DMARC) + sender()
    assert verdict(passing, suspicious=True) == auth.Verdict(False, "example.com")
    assert verdict(passing, None) == auth.Verdict(False, None)
    assert verdict(passing, "") == auth.Verdict(False, None)
    assert verdict(passing, "ada") == auth.Verdict(False, None)
    assert verdict(passing, "ada@localhost") == auth.Verdict(False, None)
    assert verdict(passing, "ada@[203.0.113.7]") == auth.Verdict(False, None)
    assert verdict(passing, "ada@example.com\r\n") == auth.Verdict(False, None)
