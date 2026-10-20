"""browser_request_signin(site, reason) (browser B12a): not a Playwright MCP tool. The agent, or an
apply adapter, reached a login or sign up wall: the person is asked to sign in or sign up himself
in the live view. `site` is the site's name as the person reads it, `reason` one sentence of why;
nothing else is accepted, so no credential can be passed. What follows is browserd/handoff.py's:
`nobody_watching` at once on a `sweep` or `full_auto` lease or when no live view is open, else
`waiting_person` while the person signs in on a Chromium without a debugging port.
"""

from typing import Any

from browserd import handoff


async def run(tab: Any, args: dict[str, Any]) -> dict[str, Any]:
    return await handoff.request(tab, args)
