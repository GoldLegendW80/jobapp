"""A17: scan all application modules, with hostile alias examples guarding the scanner."""

from pathlib import Path

import pytest

from tests.apply.privileged_boundaries import permitted, scan


@pytest.mark.parametrize(
    "source",
    [
        "from app.browser.tools import gate_click; gate_click(t, r, c)",
        "from app.browser.tools import gate_click as click; click(t, r, c)",
        "from app.browser import tools as t; t.gate_click(a, b, c)",
        "import app.browser.tools; app.browser.tools.gate_click(a, b, c)",
        "import app.browser.tools as t; t.gate_click(a, b, c)",
        "from ..browser.tools import gate_click as click; click(a, b, c)",
        "from app.browser.tools import gate_click as a; b = a; c = b; c(t, r, k)",
        "click = client.gate_click; click(lease, token, ref, capability)",
        "click: object = client.gate_click; click(a, b, c)",
        "a, b = client.gate_click, other; a(t, r, c)",
        "def run(client):\n    click = client.gate_click\n    click(a, b, c)",
        "class Runner:\n    async def run(self):\n        await self.client.gate_click(a, b, c)",
        "client.gate_click(a, b, c)",
        "send = mcp.call; send(account, 'send_message', args)",
        "send = mcp.call_tool; send(name='send_message', arguments=args)",
        "tool = 'send_message'; mcp.call(account, tool, args)",
        "a = 'send_message'; b = a; invoke = mcp.call; invoke(account, b, args)",
        "from app.mail.mcp_client import GmailMcp as Client; "
        "c = Client(); c.call(a, 'send_message', {})",
        "provider.send_message(raw)",
        "send = provider.send_message; send(raw)",
        "client._send('POST', f'{base}/messages/send', body=raw)",
        "post = client.post; endpoint = '/messages/send'; post(endpoint, data=raw)",
        "click = getattr(client, 'gate_click'); click(a, b, c)",
        "from functools import partial as p; click = p(client.gate_click, a); click(b, c)",
        "def run():\n    click(a, b, c)\nfrom app.browser.tools import gate_click as click",
        "from app.gmailmcp.server import Service as Provider; "
        "client = Provider(); send = client.send; send(key, raw)",
        "from app.gmailmcp.server import Service as Provider\n"
        "def run(client: Provider):\n    send = client.send\n    send(key, raw)",
    ],
)
def test_forbidden_sample_callers_are_detected(source):
    calls = scan(source, "app.apply.rogue")
    assert calls
    assert all(not permitted(call) for call in calls)


@pytest.mark.parametrize(
    "source",
    [
        "def gate_click(tools, ref, capability):\n    pass",
        "async def send_message(raw):\n    return raw",
        "from app.browser.tools import gate_click as click",
        "text = 'gate_click(t, r, c)'",
        "# client.gate_click(a, b, c)\npass",
        "from app.agents import operator; operator.send_message(db, user, text)",
        "from app.agents.operator import send_message as send; send(db, user, text)",
        "from app.documents.chat_router import send_message as send; send(text)",
        "mcp.call(account, 'get_message', args)",
        "client.get('/messages', params={'q': 'send_message'})",
        "from app.browser.tools import gate_click as click\ndef run(click):\n    click()",
        "from app.browser.tools import gate_click as click\n"
        "def run():\n    click = harmless\n    click()",
    ],
)
def test_declarations_read_calls_and_shadowed_names_are_permitted(source):
    assert not scan(source, "app.apply.ordinary")


def test_browser_wrapper_exception_is_scoped_to_its_declaration():
    wrapper = "async def gate_click(t, r, c):\n    return await t.client.gate_click(r, c)"
    assert all(permitted(call) for call in scan(wrapper, "app.browser.tools"))
    rogue = "async def rogue(t, r, c):\n    return await t.client.gate_click(r, c)"
    assert all(not permitted(call) for call in scan(rogue, "app.browser.tools"))


def test_all_application_privileged_calls_stay_inside_their_gates():
    root = Path(__file__).resolve().parents[2] / "app"
    calls = []
    for path in sorted(root.rglob("*.py")):
        module = "app." + ".".join(path.relative_to(root).with_suffix("").parts)
        calls.extend(scan(path.read_text(), module))
    assert not [call for call in calls if not permitted(call)]
    assert {call.module for call in calls if call.kind == "click"} == {
        "app.apply.gate",
        "app.apply.profile_gate",
        "app.browser.tools",
    }
    assert {call.module for call in calls if call.kind == "mail"} == {"app.mail.gate"}
    assert {call.module for call in calls if call.kind == "provider"} == {"app.gmailmcp.server"}
