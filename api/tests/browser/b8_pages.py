"""Snapshots of the browser sources' pages (browser B8), in the format browserd answers
(Playwright MCP's `snapshotForAI`), written line for line after the live pages of 2026-10-20 with
invented postings: never a real Google or Business France page in a test (CLAUDE.md rule 5)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Offer:
    title: str
    company: str
    location: str  # Google: "Paris, France; Munich, Germany"; VIE: "FRANCE - PARIS"
    number: int


def offers(count: int, start: int = 1, **overrides: str) -> list[Offer]:
    return [
        Offer(
            title=overrides.get("title", f"AI Engineer {n}"),
            company=overrides.get("company", "Example Robotics"),
            location=overrides.get("location", "Paris, France"),
            number=1000 + n,
        )
        for n in range(start, start + count)
    ]


def slug(title: str) -> str:
    return "-".join("".join(c if c.isalnum() else " " for c in title.lower()).split())


def google_card(o: Offer, ref: int) -> list[str]:
    places = o.location.split("; ")
    lines = [
        f"                - listitem [ref=e{ref}]:",
        f"                  - generic [ref=e{ref + 1}]:",
        f"                    - generic [ref=e{ref + 2}]:",
        f'                      - heading "{o.title}" [level=3] [ref=e{ref + 3}]',
        f"                      - generic [ref=e{ref + 4}]:",
        f"                        - generic [ref=e{ref + 5}]:",
        f"                          - generic [aria-hidden] [ref=e{ref + 6}]: corporate_fare",
        f"                          - generic [ref=e{ref + 7}]: {o.company}",
        f"                        - generic [ref=e{ref + 8}]:",
        f"                          - generic [aria-hidden] [ref=e{ref + 9}]: place",
        f"                          - generic [ref=e{ref + 10}]: {places[0]}",
    ]
    lines += [
        f"                          - generic [ref=e{ref + 11 + i}]: ; {p}"
        for i, p in enumerate(places[1:])
    ]
    lines += [
        f"                      - generic [ref=e{ref + 20}]:",
        f'                        - heading "Minimum qualifications" [level=4] [ref=e{ref + 21}]',
        f"                        - list [ref=e{ref + 22}]:",
        f"                          - listitem [ref=e{ref + 23}]: Experience with Python.",
        f"                      - generic [ref=e{ref + 24}]:",
        f"                        - generic [aria-hidden] [ref=e{ref + 25}]: Learn more",
        f'                        - link "Learn more about {o.title}" [ref=e{ref + 26}] [cursor=pointer]:',  # noqa: E501
        f"                          - /url: jobs/results/{o.number}-{slug(o.title)}?q=ai&hl=en_US",
        f"                    - generic [ref=e{ref + 27}]:",
        f'                      - button "Share {o.title}" [ref=e{ref + 28}] [cursor=pointer]:',
        f"                        - generic [aria-hidden] [ref=e{ref + 29}]: share",
    ]
    return lines


def google_results(cards: list[Offer], first: int = 1, total: int | None = None) -> str:
    total = len(cards) if total is None else total
    last = first + len(cards) - 1 if cards else 0
    lines = [
        '- region "cookies" [ref=e1]:',
        "  - text: This site uses cookies.",
        "- main [ref=e247]:",
        "  - generic [ref=e248]:",
        '    - heading "Jobs search results" [level=2]',
        "    - generic [ref=e250]:",
        "              - list [ref=e265]:",
    ]
    for index, card in enumerate(cards):
        lines += google_card(card, 1000 + index * 40)
    lines += [
        "              - status [ref=e9670]:",
        f"                - generic [ref=e9671]: Showing {first if cards else 0} to {last} of {total} rows",  # noqa: E501
        f"                - generic [aria-hidden] [ref=e9672]: {first}‑{last} of {total}",
    ]
    return "\n".join(lines)


def google_posting(o: Offer, body: str = "Build agents with Python and LLM evaluation.") -> str:
    return "\n".join(
        [
            "- generic [ref=f1]:",
            '  - heading "Jobs search results" [level=2]',
            '  - heading "Other posting" [level=3] [ref=f2]',
            "- main [ref=f4e257]:",
            "  - generic [ref=f4e264]:",
            "    - generic [ref=f4e265]:",
            f'      - heading "{o.title}" [level=2] [ref=f4e266]',
            "      - generic [ref=f4e268]:",
            f'        - button "Share {o.title}" [ref=f4e272] [cursor=pointer]:',
            "          - generic [aria-hidden] [ref=f4e273]: share",
            "    - generic [ref=f4e280]:",
            "      - generic [ref=f4e281]:",
            "        - generic [aria-hidden] [ref=f4e282]: corporate_fare",
            f"        - generic [ref=f4e283]: {o.company}",
            "    - generic [ref=f4e289]:",
            "      - generic [aria-hidden] [ref=f4e291]: Apply",
            '      - link "Apply" [ref=f4e292] [cursor=pointer]:',
            "        - /url: ./apply?jobId=invented",
            '    - heading "Minimum qualifications:" [level=3] [ref=f4e300]',
            "    - list [ref=f4e301]:",
            "      - listitem [ref=f4e302]: Bachelor's degree or equivalent practical experience.",
            '    - heading "About the job" [level=3] [ref=f4e313]',
            f"    - paragraph [ref=f4e314]: {body}",
            '    - text: "Individual pay: \\"competitive\\"."',
            "    - paragraph [ref=f4e329]:",
            "      - text: Information collected and processed as part of your profile.",
            "    - paragraph [ref=f4e331]: Equal opportunity employer.",
        ]
    )


def vie_card(o: Offer, ref: int) -> list[str]:
    return [
        f"                - figure [ref=f7e{ref}] [cursor=pointer]:",
        f'                  - img "Logo entreprise" [ref=f7e{ref + 1}]',
        f"                  - generic [ref=f7e{ref + 2}]:",
        f'                    - heading "{o.company}" [level=3] [ref=f7e{ref + 3}]',
        f'                    - heading "{o.title}" [level=2] [ref=f7e{ref + 4}]',
        f'                    - heading "{o.location}" [level=2] [ref=f7e{ref + 5}]:',
        f"                      - generic [aria-hidden] [ref=f7e{ref + 6}]: ",
        f"                      - text: {o.location}",
        f"                    - 'heading \"Missions: build agents. Profile: engineer.\" [level=2] [ref=f7e{ref + 7}]'",  # noqa: E501
        f"                    - list [ref=f7e{ref + 8}]:",
        f"                      - listitem [ref=f7e{ref + 9}]:",
        "                        - text: VIE",
        f"                      - listitem [ref=f7e{ref + 10}]:",
        "                        - text: 12 months",
        f'                    - link "Show Offer / Apply" [ref=f7e{ref + 11}]:',
        f"                      - /url: /en/offres/{o.number}",
    ]


def vie_results(cards: list[Offer], total: int | None = None, more: bool = False) -> str:
    total = len(cards) if total is None else total
    lines = [
        "- main [ref=f7e70]:",
        "      - generic [ref=f7e80]:",
        '        - heading "Search results" [level=1] [ref=f7e82]',
        "        - generic [ref=f7e83]:",
        f"          - paragraph [ref=f7e84]: {total} offers found",
        "        - generic [ref=f7e91]:",
        '          - heading "Last offers" [level=1] [ref=f7e92]',
        "          - article [ref=f7e93]:",
        "            - generic [ref=f7e95]:",
        "              - generic:",
    ]
    for index, card in enumerate(cards):
        lines += vie_card(card, 100 + index * 20)
    if more:
        lines.append("          - generic [ref=f7e9184]: Show more offers")
    return "\n".join(lines)


def vie_offer(o: Offer, published: str = "20/10/2026") -> str:
    return "\n".join(
        [
            "    - main [ref=f9e46]:",
            "      - generic [ref=f9e48]:",
            '        - img "Banner images" [ref=f9e49]',
            "        - article [ref=f9e50]:",
            f'          - heading "{o.company}" [level=2] [ref=f9e51] [cursor=pointer]',
            f'          - heading "{o.title}" [level=1] [ref=f9e52]',
            "          - list [ref=f9e53]:",
            "            - listitem [ref=f9e54]: PARIS -",
            "            - listitem [ref=f9e55]: VIE -",
            f"          - paragraph [ref=f9e58]: Published on {published}",
            "          - generic [ref=f9e59]: Apply",
            "      - article [ref=f9e80]:",
            '        - heading "The job" [level=1] [ref=f9e81]',
            '        - heading "POSITION AND MISSION" [level=3] [ref=f9e89]',
            '        - paragraph [ref=f9e90]: "Missions : build agents with Python."',
            "      - article [ref=f9e101]:",
            '        - heading "INTERESTED IN THIS POSITION? APPLY NOW!" [level=1] [ref=f9e103]',
            '        - button "Go" [ref=f9e104] [cursor=pointer]:',
            '      - heading "OFFRES A LA UNE" [level=1] [ref=f9e108]',
            '      - heading "Another offer" [level=2] [ref=f9e119]',
        ]
    )
