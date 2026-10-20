"""The catalogue of freelance and project sources (opportunities O7), as SPEC-opportunities'
table states it on 2026-10-20: the kinds the sweep reads, with the attribution each asks and the
address and date of the terms or notice it was built against; and the platforms it does not read
by itself, with their one line reason and the date of the check. Those platforms are read only
with the person's opt in, by task O7b, never here. GET /api/opportunities/feeds answers it.

No dash is used as a separator in any text here: a person reads every one.
"""

from dataclasses import asdict, dataclass

CHECKED_ON = "2026-10-20"


@dataclass(frozen=True)
class Supported:
    kind: str
    name: str
    description: str
    attribution: str
    terms_url: str
    checked_on: str = CHECKED_ON


@dataclass(frozen=True)
class NotSupported:
    kind: str
    name: str
    reason: str
    terms_url: str
    checked_on: str = CHECKED_ON


SUPPORTED: tuple[Supported, ...] = (
    Supported(
        kind="remoteok",
        name="Remote OK",
        description=(
            "Remote jobs and contract missions from the public Remote OK API. Postings tagged "
            "contract or freelance are freelance missions."
        ),
        attribution="Each posting names Remote OK as its source and links back to it.",
        terms_url="https://remoteok.com/api",
    ),
    Supported(
        kind="weworkremotely",
        name="We Work Remotely",
        description=(
            "Remote jobs from the public We Work Remotely RSS feed. Contract postings are "
            "freelance missions."
        ),
        attribution="Each posting links back to We Work Remotely.",
        terms_url="https://weworkremotely.com/remote-job-rss-feed",
    ),
    Supported(
        kind="hackernews_freelance",
        name="Hacker News: Freelancer? Seeking freelancer?",
        description=(
            "Clients looking for a freelancer in the month's Hacker News thread, read through "
            "the official public API. Every post kept is a freelance mission."
        ),
        attribution="Each posting links back to its comment on Hacker News.",
        terms_url="https://github.com/HackerNews/API",
    ),
    Supported(
        kind="hackernews_hiring",
        name="Hacker News: Who is hiring?",
        description=(
            "Companies hiring in the month's Hacker News thread, read through the official "
            "public API."
        ),
        attribution="Each posting links back to its comment on Hacker News.",
        terms_url="https://github.com/HackerNews/API",
    ),
)

NOT_SUPPORTED: tuple[NotSupported, ...] = (
    NotSupported(
        kind="malt",
        name="Malt",
        reason="No public mission board: clients contact freelancers in its own messaging.",
        terms_url="https://www.malt.fr/legal",
    ),
    NotSupported(
        kind="freework",
        name="Free-Work",
        reason="Its terms allow personal browsing only and forbid extracting its offers.",
        terms_url="https://www.free-work.com/fr/terms",
    ),
    NotSupported(
        kind="codeur",
        name="Codeur.com",
        reason="Its terms forbid automated reading, and its project alerts are paid.",
        terms_url="https://www.codeur.com/pages/termes-et-conditions-d-utilisation",
    ),
    NotSupported(
        kind="upwork",
        name="Upwork",
        reason="Its terms forbid automated reading, and its RSS feeds ended in August 2024.",
        terms_url="https://www.upwork.com/robots.txt",
    ),
    NotSupported(
        kind="freelancer",
        name="Freelancer.com",
        reason="Its terms forbid automated reading and showing its listings elsewhere.",
        terms_url="https://www.freelancer.com/about/terms",
    ),
    NotSupported(
        kind="comet",
        name="Comet",
        reason="Missions are shown only to selected freelancers signed in to its app.",
        terms_url="https://www.comet.co/",
    ),
    NotSupported(
        kind="cremedelacreme",
        name="Crème de la Crème",
        reason="Missions are proposed to selected members, with no public listing.",
        terms_url="https://www.cremedelacreme.io/robots.txt",
    ),
)

SUPPORTED_KINDS: tuple[str, ...] = tuple(source.kind for source in SUPPORTED)


def as_json() -> dict[str, list[dict[str, str]]]:
    return {
        "supported": [asdict(source) for source in SUPPORTED],
        "not_supported": [asdict(platform) for platform in NOT_SUPPORTED],
    }
