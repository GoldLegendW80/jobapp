"""Default browser typing is unchanged; empty-only profile writes never clear a value."""

from tests.browser.test_b5_actions import Element, Page, acted, actions, call, facts, tab_of


def run(value):
    page = Page()

    class EmptyElement(Element):
        async def evaluate(self, js, arg=None):
            if js == actions.EMPTY_ONLY_JS:
                if value:
                    return {"ok": False, "error": "not_empty"}
                page.calls.append(("atomic_empty_write", arg))
                return {"ok": True}
            return await super().evaluate(js, arg)

    page.elements["aria-ref=e2"] = EmptyElement(page, facts("Full name"))
    tab = tab_of(page)
    tab.request.purpose = "profile_edit"
    return call(
        page, "browser_type", {"ref": "e2", "text": "Alex Example", "empty_only": True}, tab
    ), page


def test_empty_only_write_uses_atomic_setter_without_clear_or_keys():
    result, page = run("")
    assert result["ok"] is True
    assert acted(page) == [("atomic_empty_write", "Alex Example")]


def test_site_value_populated_before_atomic_task_is_kept():
    result, page = run("Site added this value")
    assert result["error"] == "not_empty"
    assert acted(page) == []
