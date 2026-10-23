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
function deferred() {
  let resolve = () => {};
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function show(
  run = fixture(),
  refresh = vi.fn(),
  beforeAdopt?: (run: ProfileRun) => Promise<ProfileRun>,
) {
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
        update={async (next) => {
          setValue(beforeAdopt ? await beforeAdopt(next) : next);
        }}
        refresh={refresh}
      />
    );
  }
  render(
    <QueryClientProvider client={client}>
      <Harness />
    </QueryClientProvider>,
  );
  return (value: ProfileRun) => {
    act(() => {
      replaceRun(value);
    });
  };
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
  it.each([false, true])(
    "commits Replace immediately from %s until its response is adopted",
    async (initial) => {
      const run = fixture();
      run.sections[0]!.fields[1]!.replace = initial;
      run.sections[0]!.fields[2]!.current_value = "Site team";
      const acknowledged = editedRun(run, "title", { replace: !initial });
      const response = deferred();
      const adoption = deferred();
      const adopting = vi.fn();
      const changes: unknown[] = [];
      const approvals: unknown[] = [];
      const skips = vi.fn();
      server.use(
        http.put(
          "*/api/site-profiles/run-a/sections/1/fields/title",
          async ({ request }) => {
            changes.push(await request.json());
            await response.promise;
            return HttpResponse.json(acknowledged);
          },
        ),
        http.post(
          "*/api/site-profiles/run-a/sections/1/approve",
          async ({ request }) => {
            approvals.push(await request.json());
            return HttpResponse.json(acknowledged);
          },
        ),
        http.post("*/api/site-profiles/run-a/sections/1/skip", () => {
          skips();
          return HttpResponse.json(run);
        }),
      );
      const replaceRun = show(run, vi.fn(), async (value) => {
        adopting(value);
        await adoption.promise;
        return value;
      });
      const box = screen.getByRole("checkbox", { name: "Replace Job title" });
      const approve = screen.getByRole("button", { name: "Approve section" });
      const skip = screen.getByRole("button", { name: "Skip section" });
      try {
        // Assert at the native event boundary, before observer timers or the PUT.
        fireEvent.click(box);
        expect(box).toHaveProperty("checked", !initial);
        expect(box).toBeDisabled();
        expect(approve).toBeDisabled();
        expect(skip).toBeDisabled();
        expect(
          screen.getByRole("textbox", { name: "Edit Full name" }),
        ).toBeDisabled();
        expect(
          screen.getByText("Waiting for your field changes to be saved."),
        ).toBeVisible();
        fireEvent.click(approve);
        fireEvent.click(skip);
        expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
        expect(skips).not.toHaveBeenCalled();
        expect(
          screen.getByRole("checkbox", { name: "Replace Team size" }),
        ).not.toBeChecked();
        await waitFor(() => {
          expect(changes).toEqual([{ replace: !initial }]);
        });
        const poll = editedRun(run, "team", { value: "Unrelated update" });
        poll.sections[0]!.values_hash = "c".repeat(64);
        replaceRun(poll);
        expect(box).toHaveProperty("checked", !initial);
        expect(approve).toBeDisabled();
        response.resolve();
        await waitFor(() => {
          expect(adopting).toHaveBeenCalledWith(acknowledged);
        });
        expect(box).toHaveProperty("checked", !initial);
        expect(box).toBeDisabled();
        expect(approve).toBeDisabled();
        adoption.resolve();
        await waitFor(() => {
          expect(box).toBeEnabled();
        });
        expect(box).toHaveProperty("checked", !initial);
        const user = userEvent.setup();
        await user.click(approve);
        await user.click(
          screen.getByRole("button", { name: "Approve and save section" }),
        );
        await waitFor(() => {
          expect(approvals).toEqual([{ values_hash: "b".repeat(64) }]);
        });
      } finally {
        response.resolve();
        adoption.resolve();
      }
    },
  );
  it("commits Replace immediately after a different field's Keep edit", async () => {
    const run = fixture();
    const nameSaved = editedRun(run, "name", {
      value: "MORGAN EXAMPLETON",
      source: "person",
    });
    const titleSaved = editedRun(nameSaved, "title", { replace: true });
    titleSaved.sections[0]!.values_hash = "c".repeat(64);
    const nameResponse = deferred();
    const titleResponse = deferred();
    const calls: unknown[] = [];
    server.use(
      http.put(
        "*/api/site-profiles/run-a/sections/1/fields/:field",
        async ({ params, request }) => {
          calls.push({ field: params.field, changes: await request.json() });
          await (params.field === "name"
            ? nameResponse.promise
            : titleResponse.promise);
          return HttpResponse.json(
            params.field === "name" ? nameSaved : titleSaved,
          );
        },
      ),
    );
    show(run);
    const name = screen.getByRole("textbox", { name: "Edit Full name" });
    const box = screen.getByRole("checkbox", { name: "Replace Job title" });
    try {
      fireEvent.change(name, { target: { value: "MORGAN EXAMPLETON" } });
      fireEvent.click(
        within(screen.getByRole("row", { name: /Full name/ })).getByRole(
          "button",
          { name: "Keep edit" },
        ),
      );
      expect(name).toBeDisabled();
      expect(box).toBeDisabled();
      nameResponse.resolve();
      await waitFor(() => {
        expect(box).toBeEnabled();
      });
      expect(name).toHaveValue("MORGAN EXAMPLETON");
      fireEvent.click(box);
      expect(box).toBeChecked();
      expect(box).toBeDisabled();
      expect(
        screen.getByRole("button", { name: "Approve section" }),
      ).toBeDisabled();
      await waitFor(() => {
        expect(calls).toEqual([
          { field: "name", changes: { value: "MORGAN EXAMPLETON" } },
          { field: "title", changes: { replace: true } },
        ]);
      });
      titleResponse.resolve();
      await waitFor(() => {
        expect(box).toBeEnabled();
      });
      expect(box).toBeChecked();
      expect(name).toHaveValue("MORGAN EXAMPLETON");
    } finally {
      nameResponse.resolve();
      titleResponse.resolve();
    }
  });
  it("adopts the acknowledged field even when another field changed the section hash", async () => {
    const run = fixture();
    const acknowledged = editedRun(run, "title", { replace: true });
    const superseding = editedRun(acknowledged, "team", {
      value: "New team value",
    });
    superseding.sections[0]!.values_hash = "c".repeat(64);
    const adoption = deferred();
    const adopting = vi.fn();
    server.use(
      http.put("*/api/site-profiles/run-a/sections/1/fields/title", () =>
        HttpResponse.json(acknowledged),
      ),
    );
    const replaceRun = show(run, vi.fn(), async () => {
      adopting();
      await adoption.promise;
      return superseding;
    });
    const box = screen.getByRole("checkbox", { name: "Replace Job title" });
    try {
      fireEvent.click(box);
      expect(box).toBeChecked();
      await waitFor(() => {
        expect(adopting).toHaveBeenCalledOnce();
      });
      replaceRun(superseding);
      expect(box).toBeDisabled();
      adoption.resolve();
      await waitFor(() => {
        expect(box).toBeEnabled();
      });
      expect(box).toBeChecked();
      expect(
        screen.getByRole("button", { name: "Approve section" }),
      ).toBeEnabled();
    } finally {
      adoption.resolve();
    }
  });
  it.each([
    [
      "newer microsecond",
      "2026-10-22T00:00:01.000002Z",
      "2026-10-22T00:00:01.000001Z",
      true,
    ],
    [
      "older microsecond",
      "2026-10-22T00:00:01.000001Z",
      "2026-10-22T00:00:01.000002Z",
      false,
    ],
    [
      "equal microsecond",
      "2026-10-22T00:00:01.000001Z",
      "2026-10-22T00:00:01.000001Z",
      false,
    ],
    [
      "fraction after omitted fraction",
      "2026-10-22T00:00:01.000001Z",
      "2026-10-22T00:00:01Z",
      true,
    ],
    [
      "omitted fraction before fraction",
      "2026-10-22T00:00:01Z",
      "2026-10-22T00:00:01.000001Z",
      false,
    ],
    [
      "equal omitted fraction",
      "2026-10-22T00:00:01Z",
      "2026-10-22T00:00:01.000000Z",
      false,
    ],
    [
      "unequal fraction widths",
      "2026-10-22T00:00:01.100001Z",
      "2026-10-22T00:00:01.1Z",
      true,
    ],
    [
      "equal fraction widths",
      "2026-10-22T00:00:01.10Z",
      "2026-10-22T00:00:01.100000Z",
      false,
    ],
    [
      "positive offset newer",
      "2026-10-22T02:00:01.000002+02:00",
      "2026-10-22T00:00:01.000001Z",
      true,
    ],
    [
      "positive offset older",
      "2026-10-22T02:00:01.000001+02:00",
      "2026-10-22T00:00:01.000002Z",
      false,
    ],
    [
      "negative offset newer",
      "2026-10-21T22:00:01.000002-02:00",
      "2026-10-22T00:00:01.000001Z",
      true,
    ],
    [
      "equivalent zones",
      "2026-10-22T00:00:01.000001+00:00",
      "2026-10-22T00:00:01.000001Z",
      false,
    ],
    [
      "whole second newer",
      "2026-10-22T00:00:02Z",
      "2026-10-22T00:00:01.999999Z",
      true,
    ],
    ["missing current time", "", "2026-10-22T00:00:01Z", false],
    ["malformed current time", "unknown", "2026-10-22T00:00:01Z", false],
    ["malformed acknowledgement", "2026-10-22T00:00:02Z", "unknown", false],
    ["invalid date", "2026-02-31T00:00:02Z", "2026-02-01T00:00:01Z", false],
    ["invalid hour", "2026-10-22T24:00:02Z", "2026-10-22T00:00:01Z", false],
    ["missing zone", "2026-10-22T00:00:02", "2026-10-22T00:00:01Z", false],
    [
      "unknown precision",
      "2026-10-22T00:00:02.0000001Z",
      "2026-10-22T00:00:01Z",
      false,
    ],
    [
      "invalid zone",
      "2026-10-22T00:00:02+25:00",
      "2026-10-22T00:00:01Z",
      false,
    ],
  ] as const)(
    "handles target supersession with %s conservatively",
    async (_name, currentTime, acknowledgedTime, newer) => {
      const run = fixture();
      const acknowledged = editedRun(run, "title", { replace: true });
      acknowledged.updated_at = acknowledgedTime;
      const superseding = editedRun(acknowledged, "title", {
        replace: false,
        value: "Latest title",
      });
      superseding.sections[0]!.values_hash = "c".repeat(64);
      superseding.updated_at = currentTime;
      const adoption = deferred();
      const adopting = vi.fn();
      server.use(
        http.put("*/api/site-profiles/run-a/sections/1/fields/title", () =>
          HttpResponse.json(acknowledged),
        ),
      );
      const replaceRun = show(run, vi.fn(), async () => {
        adopting();
        await adoption.promise;
        return superseding;
      });
      const box = screen.getByRole("checkbox", { name: "Replace Job title" });
      const approve = screen.getByRole("button", { name: "Approve section" });
      try {
        fireEvent.click(box);
        expect(box).toBeChecked();
        await waitFor(() => {
          expect(adopting).toHaveBeenCalledOnce();
        });
        replaceRun(superseding);
        expect(box).toBeChecked();
        expect(box).toBeDisabled();
        expect(approve).toBeDisabled();
        await act(async () => {
          adoption.resolve();
          await adoption.promise;
        });
        if (newer) {
          await waitFor(() => {
            expect(box).toBeEnabled();
          });
          expect(box).not.toBeChecked();
          expect(approve).toBeEnabled();
        } else {
          expect(box).toBeChecked();
          expect(box).toBeDisabled();
          expect(approve).toBeDisabled();
          replaceRun(acknowledged);
          await waitFor(() => {
            expect(box).toBeEnabled();
          });
          expect(box).toBeChecked();
        }
      } finally {
        adoption.resolve();
      }
    },
  );
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
  it.each([false, true])(
    "restores the acknowledged Replace choice %s after a rejected PUT",
    async (initial) => {
      const run = fixture();
      run.sections[0]!.fields[1]!.replace = initial;
      const response = deferred();
      const refresh = vi.fn();
      server.use(
        http.put(
          "*/api/site-profiles/run-a/sections/1/fields/title",
          async () => {
            await response.promise;
            return HttpResponse.json(
              { detail: "Please review the field." },
              { status: 409 },
            );
          },
        ),
      );
      show(run, refresh);
      const box = screen.getByRole("checkbox", { name: "Replace Job title" });
      try {
        fireEvent.click(box);
        expect(box).toHaveProperty("checked", !initial);
        expect(box).toBeDisabled();
        response.resolve();
        expect(await screen.findByRole("alert")).toHaveTextContent(
          "Please review the field.",
        );
        expect(refresh).toHaveBeenCalledOnce();
        expect(box).toHaveProperty("checked", initial);
        expect(box).toBeEnabled();
        expect(
          screen.getByRole("button", { name: "Approve section" }),
        ).toBeEnabled();
      } finally {
        response.resolve();
      }
    },
  );
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
