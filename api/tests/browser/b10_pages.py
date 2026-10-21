"""Snapshots of LinkedIn's pages (browser B10), in the format browserd answers (Playwright MCP's
`snapshotForAI`), written after the structure of the pages of 2026-10-20 with invented postings
and companies: never a real LinkedIn page in a test (CLAUDE.md rule 5), no person's data."""

from dataclasses import dataclass
from urllib.parse import quote

from app.browser.sources import linkedin as li


@dataclass(frozen=True)
class Offer:
    title: str
    company: str = "Example Robotics"
    location: str = "Paris, Île-de-France, France"
    number: int = 4100000001
    external: str | None = None  # the company's Apply address; None is Easy Apply


def offers(count: int, start: int = 1) -> list[Offer]:
    return [
        Offer(title=f"AI Engineer {n}", number=4100000000 + n) for n in range(start, start + count)
    ]


def view(o: Offer, base: str = "https://www.linkedin.com") -> str:
    return f"{base}/jobs/view/{o.number}/"


def card(o: Offer, ref: int, base: str = "https://fr.linkedin.com") -> list[str]:
    slug = "-".join(o.title.lower().split())
    return [
        f"        - listitem [ref=e{ref}]:",
        f"          - generic [ref=e{ref + 1}]:",
        f'            - link "{o.title}" [ref=e{ref + 2}] [cursor=pointer]:',
        f"              - /url: {base}/jobs/view/{slug}-at-example-{o.number}"
        "?position=1&pageNum=0&refId=AAA%3D%3D&trackingId=BBB%3D%3D",
        f"            - generic [ref=e{ref + 3}]:",
        f'              - heading "{o.title}" [level=3] [ref=e{ref + 4}]',
        f"              - heading [level=4] [ref=e{ref + 5}]:",
        f'                - link "{o.company}" [ref=e{ref + 6}] [cursor=pointer]:',
        f"                  - /url: {base}/company/example?trk=public_jobs",
        f"              - generic [ref=e{ref + 7}]:",
        f"                - generic [ref=e{ref + 8}]: {o.location}",
        f"                - time [ref=e{ref + 9}]: 2 hours ago",
    ]


def results(
    cards: list[Offer], keywords: str = "AI Engineer", base: str = "https://fr.linkedin.com"
) -> str:
    lines = [
        "- generic [ref=e1]:",
        "  - main [ref=e2]:",
        f'    - heading "{len(cards)} {keywords} Jobs in France" [level=1] [ref=e3]',
        "    - list [ref=e4]:",
    ]
    for index, o in enumerate(cards):
        lines += card(o, 10 + index * 10, base)
    return "\n".join(lines)


def posting(o: Offer, body: str = "Build agents with Python and LLM evaluation.") -> str:
    apply = (
        [
            '      - link "Apply on company website" [ref=e20]:',
            "        - /url: https://www.linkedin.com/safety/go/"
            f"?url={quote(o.external, safe='')}&urlhash=AbCd&mt=x",
            "        - text: Apply",
        ]
        if o.external
        else ['      - button "Easy Apply" [ref=e20]']
    )
    return "\n".join(
        [
            "- main [ref=e1]:",
            '  - region "Main content" [ref=e2]:',
            f'    - link "Company, {o.company}." [ref=e3]:',
            "      - /url: https://www.linkedin.com/company/example/life/",
            f"    - paragraph [ref=e4]: {o.title}",
            "    - paragraph [ref=e5]:",
            f"      - text: {o.location} ·",
            "      - strong: 2 hours ago",
            *apply,
            '    - heading "About the job" [level=2] [ref=e30]',
            "    - paragraph [ref=e31]:",
            f"      - paragraph [ref=e32]: {body}",
            "      - list [ref=e33]:",
            "        - listitem [ref=e34]: Ship retrieval and agents to production.",
            '    - heading "About the company" [level=2] [ref=e40]',
            "    - paragraph [ref=e41]: Example Robotics builds robots.",
        ]
    )


SIGNIN = "\n".join(
    [
        "- main [ref=e1]:",
        '  - heading "Sign in" [level=1] [ref=e2]',
        '  - textbox "Email or phone" [ref=e3]',
        '  - button "Sign in" [ref=e4]',
    ]
)

CHECKPOINT = "\n".join(
    [
        "- main [ref=e1]:",
        '  - heading "Let\'s do a quick security check" [level=1] [ref=e2]',
        "  - paragraph [ref=e3]: We noticed some unusual activity.",
    ]
)


def search_url(page: int = 1, keywords: str = "AI Engineer", location: str | None = None) -> str:
    from app.profile.schemas import BrowserSource

    source = BrowserSource.model_validate(
        {"kind": "linkedin", "query": keywords, "location": location, "enabled": True}
    )
    return li.results_url(source, page)
