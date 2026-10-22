"""The private Easy Apply opener never grants an agent a submit click."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "browser"))

from browserd import detect
from browserd.config import Config
from browserd.leases import LeaseError
from browserd.tools import actions, linkedin
from browserd.tools.routes import linkedin_open

URL = "https://www.linkedin.com/jobs/view/4100000001/"


class Button:
    def __init__(self):
        self.safe = True
        self.clicks = 0
        self.counted = 1

    async def count(self):
        return self.counted

    async def element_handle(self):
        return self

    async def evaluate(self, script, args):
        assert script == linkedin.OPENER_JS
        return self.safe

    async def click(self, **kwargs):
        self.clicks += 1


class Tab:
    def __init__(self, url=URL, purpose="apply_fill"):
        self.request = SimpleNamespace(purpose=purpose, automation=True, submit_selectors=())
        self.button = Button()
        self.url = url
        self.after_pace = None
        self.unsafe_after_pace = False
        self.input_allowed = True

    def require_input(self):
        if not self.input_allowed:
            raise actions.PersonInput

    async def page(self):
        return self

    @property
    def main_frame(self):
        return self

    def get_by_role(self, role, name, exact):
        assert role == "button"
        assert exact is True
        return self.button

    async def approach(self, button):
        if self.after_pace:
            self.url = self.after_pace
        if self.unsafe_after_pace:
            self.button.safe = False

    async def settle(self, result):
        return result


def opened(tab, job="4100000001", base=""):
    return asyncio.run(linkedin.open_modal(tab, job, base))


def test_private_opener_opens_only_the_modal_and_model_click_stays_refused():
    tab = Tab()
    assert opened(tab)["ok"]
    assert tab.button.clicks == 1
    assert detect.says_submit(["Easy Apply"])
    assert "linkedin_open" not in actions.TOOLS


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/jobs/view/4100000001/",
        "https://www.linkedin.com/jobs/view/2/",
        "https://www.linkedin.com/in/4100000001/",
        "http://www.linkedin.com/jobs/view/4100000001/",
        "https://www.linkedin.com.evil.example/jobs/view/4100000001/",
    ],
)
def test_wrong_host_job_or_context_refuses(url):
    tab = Tab(url)
    assert not opened(tab)["ok"]
    assert tab.button.clicks == 0


@pytest.mark.parametrize("purpose", ["sweep", "profile_edit", "signin", "apply_manual"])
def test_wrong_purpose_refuses(purpose):
    tab = Tab(purpose=purpose)
    assert not opened(tab)["ok"]
    assert tab.button.clicks == 0


@pytest.mark.parametrize("change", ["job", "facts", "ambiguous"])
def test_fresh_checks_after_pacing_refuse(change):
    tab = Tab()
    if change == "job":
        tab.after_pace = "https://www.linkedin.com/jobs/view/2/"
    elif change == "facts":
        tab.unsafe_after_pace = True
    else:
        tab.button.counted = 2
    assert not opened(tab)["ok"]
    assert tab.button.clicks == 0


def test_fixture_mapping_is_exact_origin_and_path():
    base = "http://atsforms:8080/linkedin"
    assert opened(Tab(base + "/jobs/view/4100000001/"), base=base)["ok"]
    for url in [
        "http://atsforms:8081/linkedin/jobs/view/4100000001/",
        "http://atsforms:8080/other/jobs/view/4100000001/",
    ]:
        assert not opened(Tab(url), base=base)["ok"]


def test_fixture_configuration_refused_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("BROWSER_LINKEDIN_STUB_BASE_URL", "http://atsforms:8080/linkedin")
    with pytest.raises(ValueError, match="production"):
        Config.from_env()


def test_private_endpoint_checks_the_lease_token_before_reading_arguments():
    class Manager:
        def check_token(self, lease, token):
            raise LeaseError(403, "bad_token")

    request = SimpleNamespace(headers={"x-lease-token": "wrong"}, json=lambda: pytest.fail("read"))
    with pytest.raises(LeaseError):
        asyncio.run(
            linkedin_open(SimpleNamespace(manager=Manager()), request, {"lease_id": "other"})
        )


def test_a_takeover_after_the_final_structural_check_still_prevents_the_opener_click():
    tab = Tab()
    calls = []
    original = tab.button.evaluate

    async def checked(script, args):
        result = await original(script, args)
        calls.append(True)
        if len(calls) == 2:
            tab.input_allowed = False
        return result

    tab.button.evaluate = checked
    with pytest.raises(actions.PersonInput):
        opened(tab)
    assert tab.button.clicks == 0
