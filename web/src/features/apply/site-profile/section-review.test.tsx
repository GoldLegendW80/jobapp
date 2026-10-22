import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
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
  function Harness() {
    const [value, setValue] = useState(run);
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
  return refresh;
}

describe("site profile section review", () => {
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
    let finish = () => {};
    const pending = new Promise<void>((resolve) => {
      finish = resolve;
    });
    server.use(
      http.put(
        "*/api/site-profiles/run-a/sections/1/fields/title",
        async () => {
          await pending;
          const updated = structuredClone(run);
          updated.sections[0]!.fields[1]!.replace = true;
          return HttpResponse.json(updated);
        },
      ),
    );
    show(run);
    const box = screen.getByRole("checkbox", { name: "Replace Job title" });
    await userEvent.setup().click(box);
    expect(box).toBeChecked();
    expect(box).toBeDisabled();
    finish();
    await waitFor(() => {
      expect(box).toBeEnabled();
    });
    expect(box).toBeChecked();
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
          return HttpResponse.json(run);
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
