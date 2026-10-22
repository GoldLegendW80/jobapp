import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { server } from "@/test/server";
import { type ProfileRun } from "./api";
import { SectionReview } from "./section-review";

export function fixture(): ProfileRun {
  const field = {
    field_key: "name",
    label: "Full name",
    kind: "text",
    value: "Alex Example",
    current_value: null,
    source: "identity",
    source_path: "identity.full_name",
    required: true,
    refusal: null,
    options: [],
    replace: false,
    approved: false,
  };
  return {
    id: "run-a",
    url: "https://portal.example/profile",
    host: "portal.example",
    site_kind: "generic",
    state: "ready",
    autosave: false,
    autosave_confirmed: false,
    needs: null,
    error_code: null,
    created_at: "2026-10-22T00:00:00Z",
    updated_at: "2026-10-22T00:00:00Z",
    cost: {},
    sections: [
      {
        section: "1",
        label: "Experience",
        position: 1,
        state: "ready",
        error_code: null,
        values_hash: "a".repeat(64),
        fields: [
          field,
          {
            ...field,
            field_key: "title",
            label: "Job title",
            current_value: "Site title",
            value: "Platform engineer",
            source: "experience",
            source_path: "experience.employers.0.title",
          },
          {
            ...field,
            field_key: "team",
            label: "Team size",
            value: null,
            source: "left",
            source_path: "",
            refusal: "no_source",
          },
        ],
        screenshots: [],
        events: [],
      },
    ],
  };
}
function show(run = fixture(), refresh = vi.fn()) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  let replaceRun: (value: ProfileRun) => void = () => {};
  function Harness() {
    const [value, setValue] = useState(run);
    replaceRun = setValue;
    return (
      <SectionReview
        run={value}
        section={value.sections[0]!}
        update={setValue}
        refresh={refresh}
      />
    );
  }
  render(
    <QueryClientProvider client={client}>
      <Harness />
    </QueryClientProvider>,
  );
  return (value: ProfileRun) => act(() => replaceRun(value));
}

function editedRun(run: ProfileRun, key: string, changes: object) {
  const updated = structuredClone(run);
  updated.sections[0]!.values_hash = "b".repeat(64);
  Object.assign(
    updated.sections[0]!.fields.find((field) => field.field_key === key)!,
    changes,
  );
  return updated;
}

describe("site profile section review", () => {
  it.each([null, "", "Empty"])(
    "treats an unchanged value %s as acknowledged",
    (value) => {
      const run = fixture();
      run.sections[0]!.fields[1]!.value = value;
      show(run);
      const row = screen.getByRole("row", { name: /Job title/ });
      expect(within(row).getByRole("textbox")).toHaveValue(value ?? "");
      expect(
        within(row).getByRole("button", { name: "Keep edit" }),
      ).toBeDisabled();
      expect(
        screen.getByRole("button", { name: "Approve section" }),
      ).toBeEnabled();
    },
  );
  it("names the site and section in the explicit confirmation, with left required fields allowed", async () => {
    const run = fixture();
    const calls: unknown[] = [];
    server.use(
      http.post(
        "*/api/site-profiles/run-a/sections/1/approve",
        async ({ request }) => {
          calls.push(await request.json());
          return HttpResponse.json(run);
        },
      ),
    );
    show(run);
    const user = userEvent.setup();
    expect(screen.getByText("Team size")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Approve section" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("portal.example");
    expect(screen.getByRole("alertdialog")).toHaveTextContent("Experience");
    expect(calls).toEqual([]);
    await user.click(
      screen.getByRole("button", { name: "Approve and save section" }),
    );
    await waitFor(() => {
      expect(calls).toEqual([{ values_hash: "a".repeat(64) }]);
    });
  });
  it("sends Replace only for the selected field", async () => {
    const run = fixture();
    const calls: unknown[] = [];
    server.use(
      http.put(
        "*/api/site-profiles/run-a/sections/1/fields/title",
        async ({ request }) => {
          calls.push(await request.json());
          return HttpResponse.json({
            ...run,
            sections: [
              {
                ...run.sections[0]!,
                values_hash: "b".repeat(64),
                fields: run.sections[0]!.fields.map((f) => ({
                  ...f,
                  replace: f.field_key === "title",
                })),
              },
            ],
          });
        },
      ),
    );
    show(run);
    const user = userEvent.setup();
    await user.click(
      screen.getByRole("checkbox", { name: "Replace Job title" }),
    );
    await waitFor(() => {
      expect(calls).toEqual([{ replace: true }]);
    });
    await waitFor(() => {
      expect(
        screen.getByRole("checkbox", { name: "Replace Job title" }),
      ).toBeChecked();
    });
  });
  it("holds the person's Replace selection while the save is pending", async () => {
    const run = fixture();
    const approvals: unknown[] = [];
    let finish = () => {};
    const pending = new Promise<void>((resolve) => {
      finish = resolve;
    });
    server.use(
      http.put(
        "*/api/site-profiles/run-a/sections/1/fields/title",
        async () => {
          await pending;
          return HttpResponse.json(editedRun(run, "title", { replace: true }));
        },
      ),
      http.post(
        "*/api/site-profiles/run-a/sections/1/approve",
        async ({ request }) => {
          approvals.push(await request.json());
          return HttpResponse.json(run);
        },
      ),
    );
    show(run);
    const box = screen.getByRole("checkbox", { name: "Replace Job title" });
    await userEvent.setup().click(box);
    expect(box).toBeChecked();
    expect(box).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Approve section" }),
    ).toBeDisabled();
    finish();
    await waitFor(() => {
      expect(box).toBeEnabled();
    });
    expect(box).toBeChecked();
    expect(
      screen.getByRole("button", { name: "Approve section" }),
    ).toBeEnabled();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Approve section" }));
    await user.click(
      screen.getByRole("button", { name: "Approve and save section" }),
    );
    await waitFor(() => {
      expect(approvals).toEqual([{ values_hash: "b".repeat(64) }]);
    });
  });
  it.each(["Job title", "Team size"])(
    "blocks approval of an unsaved %s and allows an explicit undo",
    async (label) => {
      show();
      const user = userEvent.setup();
      const row = screen.getByRole("row", { name: new RegExp(label) });
      expect(
        within(row).getByRole("button", { name: "Keep edit" }),
      ).toBeDisabled();
      await user.type(
        screen.getByRole("textbox", { name: `Edit ${label}` }),
        " changed",
      );
      const approve = screen.getByRole("button", { name: "Approve section" });
      expect(approve).toBeDisabled();
      await user.click(approve);
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
      expect(screen.getByText(/Keep or undo your edits/)).toBeVisible();
      await user.click(within(row).getByRole("button", { name: "Undo" }));
      expect(approve).toBeEnabled();
      expect(
        screen.getByRole("textbox", { name: `Edit ${label}` }),
      ).toHaveValue(label === "Team size" ? "" : "Platform engineer");
    },
  );
  it("accepts a manually reverted edit without sending a field PUT", async () => {
    show();
    const user = userEvent.setup();
    const text = screen.getByRole("textbox", { name: "Edit Job title" });
    await user.type(text, " changed");
    expect(
      screen.getByRole("button", { name: "Approve section" }),
    ).toBeDisabled();
    await user.clear(text);
    await user.type(text, "Platform engineer");
    expect(
      screen.getByRole("button", { name: "Approve section" }),
    ).toBeEnabled();
  });
  it("waits for Keep edit and approves only the acknowledged hash", async () => {
    const run = fixture();
    let finish = () => {};
    const pending = new Promise<void>((resolve) => {
      finish = resolve;
    });
    const calls: unknown[] = [];
    server.use(
      http.put(
        "*/api/site-profiles/run-a/sections/1/fields/title",
        async () => {
          await pending;
          return HttpResponse.json(
            editedRun(run, "title", { value: "Corrected title" }),
          );
        },
      ),
      http.post(
        "*/api/site-profiles/run-a/sections/1/approve",
        async ({ request }) => {
          calls.push(await request.json());
          return HttpResponse.json(run);
        },
      ),
    );
    show(run);
    const user = userEvent.setup();
    const text = screen.getByRole("textbox", { name: "Edit Job title" });
    await user.clear(text);
    await user.type(text, "Corrected title");
    await user.click(
      within(screen.getByRole("row", { name: /Job title/ })).getByRole(
        "button",
        { name: "Keep edit" },
      ),
    );
    const approve = screen.getByRole("button", { name: "Approve section" });
    expect(approve).toBeDisabled();
    expect(text).toBeDisabled();
    expect(calls).toEqual([]);
    finish();
    await waitFor(() => {
      expect(approve).toBeEnabled();
    });
    expect(text).toHaveValue("Corrected title");
    await user.click(approve);
    await user.click(
      screen.getByRole("button", { name: "Approve and save section" }),
    );
    await waitFor(() => {
      expect(calls).toEqual([{ values_hash: "b".repeat(64) }]);
    });
  });
  it("keeps a failed text edit blocked until it is saved or undone", async () => {
    const refresh = vi.fn();
    server.use(
      http.put("*/api/site-profiles/run-a/sections/1/fields/title", () =>
        HttpResponse.json(
          { detail: "Please review the field." },
          { status: 409 },
        ),
      ),
    );
    show(fixture(), refresh);
    const user = userEvent.setup();
    const text = screen.getByRole("textbox", { name: "Edit Job title" });
    await user.type(text, " changed");
    const row = screen.getByRole("row", { name: /Job title/ });
    await user.click(within(row).getByRole("button", { name: "Keep edit" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Please review the field.",
    );
    expect(refresh).toHaveBeenCalled();
    expect(text).toHaveValue("Platform engineer changed");
    expect(
      screen.getByRole("button", { name: "Approve section" }),
    ).toBeDisabled();
    await user.click(within(row).getByRole("button", { name: "Undo" }));
    expect(
      screen.getByRole("button", { name: "Approve section" }),
    ).toBeEnabled();
  });
  it("restores the acknowledged Replace choice after a failed PUT", async () => {
    server.use(
      http.put("*/api/site-profiles/run-a/sections/1/fields/title", () =>
        HttpResponse.json(
          { detail: "Please review the field." },
          { status: 409 },
        ),
      ),
    );
    show();
    const box = screen.getByRole("checkbox", { name: "Replace Job title" });
    await userEvent.setup().click(box);
    await screen.findByRole("alert");
    expect(box).not.toBeChecked();
    expect(box).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "Approve section" }),
    ).toBeEnabled();
  });
  it("preserves another field's unsaved edit when a field save returns the section", async () => {
    const run = fixture();
    server.use(
      http.put("*/api/site-profiles/run-a/sections/1/fields/title", () =>
        HttpResponse.json(editedRun(run, "title", { replace: true })),
      ),
    );
    show(run);
    const user = userEvent.setup();
    const name = screen.getByRole("textbox", { name: "Edit Full name" });
    await user.type(name, " changed");
    await user.click(
      screen.getByRole("checkbox", { name: "Replace Job title" }),
    );
    await waitFor(() => {
      expect(name).toBeEnabled();
    });
    expect(name).toHaveValue("Alex Example changed");
    expect(
      screen.getByRole("button", { name: "Approve section" }),
    ).toBeDisabled();
  });
  it("closes a stale confirmation when the server changes the reviewed hash", async () => {
    const run = fixture();
    const replaceRun = show(run);
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Approve section" }));
    expect(screen.getByRole("alertdialog")).toBeVisible();
    replaceRun(editedRun(run, "title", { value: "Server correction" }));
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    });
    expect(screen.getByRole("textbox", { name: "Edit Job title" })).toHaveValue(
      "Server correction",
    );
    replaceRun(run);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });
  it("closes an open confirmation when the displayed draft changes", async () => {
    show();
    const text = screen.getByRole("textbox", { name: "Edit Job title" });
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Approve section" }));
    expect(screen.getByRole("alertdialog")).toBeVisible();
    fireEvent.change(text, { target: { value: "Corrected title" } });
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    });
    expect(
      screen.getByRole("button", { name: "Approve section" }),
    ).toBeDisabled();
  });
  it("does not resurrect a superseded draft if a server value changes back", async () => {
    const run = fixture();
    const replaceRun = show(run);
    await userEvent
      .setup()
      .type(
        screen.getByRole("textbox", { name: "Edit Job title" }),
        " changed",
      );
    replaceRun(editedRun(run, "title", { value: "Server correction" }));
    replaceRun(run);
    expect(screen.getByRole("textbox", { name: "Edit Job title" })).toHaveValue(
      "Platform engineer",
    );
    expect(
      screen.getByRole("button", { name: "Approve section" }),
    ).toBeEnabled();
  });
  it("resets drafts and confirmation when a different run replaces the section", async () => {
    const run = fixture();
    const replaceRun = show(run);
    const user = userEvent.setup();
    await user.type(
      screen.getByRole("textbox", { name: "Edit Full name" }),
      " changed",
    );
    replaceRun({ ...run, id: "run-b" });
    expect(screen.getByRole("textbox", { name: "Edit Full name" })).toHaveValue(
      "Alex Example",
    );
    expect(
      screen.getByRole("button", { name: "Approve section" }),
    ).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Approve section" }));
    replaceRun(run);
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    });
  });
  it("keeps an inline edit and refreshes after a stale approval", async () => {
    const run = fixture();
    const edits: unknown[] = [];
    const refresh = vi.fn();
    server.use(
      http.put(
        "*/api/site-profiles/run-a/sections/1/fields/title",
        async ({ request }) => {
          edits.push(await request.json());
          return HttpResponse.json(
            editedRun(run, "title", { value: "Platform engineer with Python" }),
          );
        },
      ),
      http.post("*/api/site-profiles/run-a/sections/1/approve", () =>
        HttpResponse.json(
          { code: "stale", detail: "The section changed." },
          { status: 409 },
        ),
      ),
    );
    show(run, refresh);
    const user = userEvent.setup();
    const text = screen.getByRole("textbox", { name: "Edit Job title" });
    await user.clear(text);
    await user.type(text, "Platform engineer");
    await user.type(text, " with Python");
    const row = screen.getByRole("row", { name: /Job title/ });
    await user.click(within(row).getByRole("button", { name: "Keep edit" }));
    await waitFor(() => {
      expect(edits).toEqual([{ value: "Platform engineer with Python" }]);
    });
    await user.click(screen.getByRole("button", { name: "Approve section" }));
    await user.click(
      screen.getByRole("button", { name: "Approve and save section" }),
    );
    await waitFor(() => {
      expect(refresh).toHaveBeenCalled();
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The section changed.",
    );
  });
});
