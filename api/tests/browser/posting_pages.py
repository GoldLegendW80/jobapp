"""A posting's page as a website serves it (review of PR 864, round 2), for every test that reads
one: a header with a logo link, a navigation of several links, a sign in link and button, the
posting, a block of similar postings with their own links, and a footer of links. Fifteen links
with words, so J31's list rule (LIST_CARDS_MIN cards) counts such a page as a list, as it counts a
real one (browser B32). Invented."""

HEADER = "\n".join(
    [
        "- banner:",
        '  - link "Example Robotics careers" [ref=h0]:',
        "    - /url: /",
        "  - navigation:",
    ]
    + [
        f'    - link "{word}" [ref=h{i}]:\n      - /url: /{word.lower()}'
        for i, word in enumerate(("Jobs", "Companies", "Salaries", "Blog"), 1)
    ]
    + ['  - link "Sign in" [ref=h5]:', "    - /url: /login", '  - button "Sign in" [ref=h6]']
)
SIMILAR_TITLES = (
    "Robot Painter",
    "Robot Arm Engineer",
    "Robot Tester",
    "Robot Pilot",
    "Robot Fitter",
)
SIMILAR = "\n".join(
    ['  - region "Similar jobs":', '    - heading "Similar jobs" [level=2]', "    - list:"]
    + [
        f'      - listitem:\n        - link "{t}" [ref=s{i}]:\n          - /url: /jobs/{60 + i}\n'
        "        - paragraph: Example Robotics, Lyon"
        for i, t in enumerate(SIMILAR_TITLES)
    ]
)
FOOTER = "\n".join(
    ["- contentinfo:"]
    + [
        f'  - link "{word}" [ref=f{i}]:\n    - /url: /{word.lower()}'
        for i, word in enumerate(("About", "Privacy", "Terms", "Cookies", "Contact"))
    ]
)


def dressed(snapshot: str) -> str:
    """A posting's snapshot (its heading and text) inside a real page's `main`, with the header,
    the similar postings and the footer around it."""
    inside = "\n".join(f"  {line}" for line in snapshot.split("\n"))
    return f"{HEADER}\n- main:\n{inside}\n{SIMILAR}\n{FOOTER}"


def posting(title: str, body: str) -> str:
    return dressed(f'- heading "{title}" [level=1]\n{body}')
