"""Invented candidate portal, including guarded Save and an empty-only autosave variant."""

import base64
import io
import json
from types import SimpleNamespace

from PIL import Image

from app.browser.leases import Acquired, Lease
from tests.apply.conftest import AT


class Portal:
    def __init__(self, *, autosave=False, changed=False):
        self.autosave, self.changed = autosave, changed
        self.saved = {
            "Full name": "",
            "Phone": "+33 1 00 00 00 00",
            "Email": "e2e-site@example.org",
            "Job title": "",
            "Team size": "",
        }
        self.values = dict(self.saved)
        self.clicks = 0
        self.typed = []
        image = io.BytesIO()
        Image.new("RGB", (640, 480), "white").save(image, "JPEG")
        self.jpeg = image.getvalue()

    async def navigate(self, url):
        self.values = dict(self.saved)
        return {"ok": True}

    async def snapshot(self):
        out = [
            '- heading "Example Robotics candidate profile" [level=1]',
            '- form "Experience 1" [ref=e1]:',
        ]
        for i, (label, value) in enumerate(self.values.items(), 2):
            out.append(f"  - textbox {json.dumps(label)} [ref=e{i}]: {json.dumps(value)}")
        if self.autosave:
            out.append("- paragraph: Changes are saved automatically")
        else:
            out.append('  - button "Save" [ref=e9]')
        return {"ok": True, "snapshot": "\n".join(out)}

    async def type(self, ref, text, element="", submit=False):
        label = list(self.values)[int(ref[1:]) - 2]
        self.values[label] = text
        self.typed.append(label)
        if self.autosave:
            self.saved[label] = text
        return {"ok": True}

    async def select_option(self, ref, values, element=""):
        return {"ok": False, "error": "submit" if ref == "e9" else "failed"}

    async def click(self, ref, element=""):
        return {"ok": False, "error": "submit"}

    async def screenshot(self, *, full_page=False, kind="png"):
        return {"ok": True, "type": "jpeg", "data": base64.b64encode(self.jpeg).decode()}

    async def box(self, ref, element=""):
        return {"ok": True, "x": 10, "y": 350, "width": 100, "height": 40, "scale": 1}

    async def put_file(self, name, data):
        return {"ok": True}

    async def save(self, tools, ref, capability, element=""):
        assert ref == "e9"
        assert capability == "test-profile-capability"
        self.clicks += 1
        self.saved = dict(self.values)
        if self.changed:
            self.values["Full name"] = "Site changed this value"
            self.saved["Full name"] = "Site changed this value"
        return {"ok": True}


class Leases:
    def __init__(self):
        self.calls = []

    async def acquire(self, user, purpose, automation, *, hosts):
        self.calls.append((user, purpose, automation))
        return Acquired(
            lease=Lease(
                lease_id="test-profile-lease",
                user_id=user,
                profile_id="fixture",
                purpose=purpose,
                automation=automation,
                max_s=900,
                idle_s=300,
                started_at=AT.timestamp(),
                active_since=AT.timestamp(),
                last_input=AT.timestamp(),
            ),
            token="not-a-real-token",
            capability="test-profile-capability",
        )

    async def release(self, user, lease_id=None, reason="released", purposes=None):
        return True


async def enabled():
    return True, False


def services(factory, portal):
    return SimpleNamespace(
        sessions=factory,
        clock=lambda: AT,
        leases=Leases(),
        tools_for=lambda *args: portal,
        profile_switches=enabled,
    )


class DialogPortal(Portal):
    """The known sites' profile initially contains Edit links and no editable fields."""

    def __init__(self, kind):
        super().__init__()
        self.kind, self.opened = kind, None
        self.records = {
            "experience": {"Full name": "", "Job title": ""},
            "education": {"School": "", "Degree": ""},
        }
        self.names = {
            "experience": "Edit experience" if kind == "linkedin" else "My Experience",
            "education": "Edit education" if kind == "linkedin" else "Education",
        }
        self.control_clicks = []

    async def navigate(self, url):
        self.opened = None
        return {"ok": True}

    async def snapshot(self):
        if self.opened is None:
            role = "button" if self.kind == "linkedin" else "link"
            text = "\n".join(
                f"- {role} {json.dumps(name)} [ref=e{index}]"
                for index, name in enumerate(self.names.values(), 10)
            )
            return {"ok": True, "snapshot": '- heading "Candidate profile"\n' + text}
        out = [f"- dialog {json.dumps(self.names[self.opened])} [ref=e1]:"]
        for index, (label, value) in enumerate(self.values.items(), 2):
            out.append(f"  - textbox {json.dumps(label)} [ref=e{index}]: {json.dumps(value)}")
        out.extend(['  - button "Save" [ref=e9]', '  - button "Close" [ref=e8]'])
        return {"ok": True, "snapshot": "\n".join(out)}

    async def click(self, ref, element=""):
        if ref in ("e10", "e11") and self.opened is None:
            self.opened = list(self.names)[int(ref[1:]) - 10]
            self.values = dict(self.records[self.opened])
            self.control_clicks.append(element)
            return {"ok": True}
        if ref == "e8" and self.opened:
            self.opened = None
            self.control_clicks.append(element)
            return {"ok": True}
        return await super().click(ref, element)

    async def save(self, tools, ref, capability, element=""):
        assert self.opened is not None
        self.records[self.opened] = dict(self.values)
        self.clicks += 1
        self.opened = None
        return {"ok": True}
