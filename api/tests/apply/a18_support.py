"""Invented candidate portal, including guarded Save and an empty-only autosave variant."""

import base64
import io
import json
from types import SimpleNamespace

from PIL import Image

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
        return {"ok": True}


class Leases:
    def __init__(self):
        self.calls = []

    async def acquire(self, user, purpose, automation, *, hosts):
        self.calls.append((user, purpose, automation))
        return SimpleNamespace(
            lease=SimpleNamespace(id="test-profile-lease"),
            token="not-a-real-token",
            capability="test-profile-capability",
        )

    async def release(self, user, lease_id):
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
