import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { waitForOnFakeClock } from "@/test/clock";
import { fakeEditableJob, makeDetail } from "@/test/jobs";
import { renderApp } from "@/test/render";

import { NOTES_DEBOUNCE_MS, STALE_MESSAGE } from "./use-job-editor";

const JOB_ID = "00000000-0000-4000-9000-000000000001";
const PATH = `/jobs/${JOB_ID}`;

beforeEach(() => {
  fakeSession(admin);
});

afterEach(() => {
  vi.useRealTimers();
});

async function openDetail() {
  const app = renderApp(PATH);
  await screen.findByRole("heading", { level: 1 });
  return app;
}

const statusSelect = () => screen.getByLabelText("Status");
const history = () =>
  within(screen.getByRole("list", { name: "History, newest first" }))
    .getAllByRole("listitem")
    .map((li) => li.firstElementChild?.nextSibling?.textContent);
const badge = () => document.querySelector("header [data-status]");

describe("status", () => {
  it("changes at once, sends the version it read, and the history follows", async () => {
    const api = fakeEditableJob(makeDetail({ status: "found" }), {
      latencyMs: 150,
    });
    const user = userEvent.setup();
    await openDetail();
    await user.selectOptions(statusSelect(), "shortlisted");
    // Optimistic: the badge and the select change before the api answers.
    expect(statusSelect()).toHaveValue("shortlisted");
    expect(badge()).toHaveTextContent("Shortlisted");
    await screen.findByText("Status set to Shortlisted.");
    expect(api.patches).toEqual([
      { status: "shortlisted", updated_at: "2026-10-09T08:00:00Z" },
    ]);
    await waitFor(() => {
      expect(history()[0]).toBe("Status changed from Found to Shortlisted");
    });
    // The next change carries the version the api returned.
    await user.selectOptions(statusSelect(), "staged");
    await waitFor(() => {
      expect(api.patches[1]).toEqual({
        status: "staged",
        updated_at: "2026-10-09T09:00:01Z",
      });
    });
  });

  it("lists every status in pipeline order", async () => {
    fakeEditableJob(makeDetail());
    await openDetail();
    expect(
      within(statusSelect())
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual([
      "Found",
      "Shortlisted",
      "CV ready",
      "Staged",
      "Applied",
      "Interview",
      "Rejected",
      "Closed",
      "Skipped",
    ]);
  });

  it("asks before marking as applied, and Cancel sends nothing", async () => {
    const api = fakeEditableJob(makeDetail({ status: "staged" }));
    const user = userEvent.setup();
    await openDetail();
    await user.selectOptions(statusSelect(), "applied");
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("Mark this posting as applied?");
    expect(dialog).toHaveTextContent("Today becomes its applied date.");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(statusSelect()).toHaveValue("staged");
    expect(api.patches).toEqual([]);

    await user.selectOptions(statusSelect(), "applied");
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "Mark as applied",
      }),
    );
    await screen.findByText("Status set to Applied.");
    expect(api.patches[0]).toMatchObject({ status: "applied" });
    expect(
      await screen.findByText("Applied", { selector: "dt" }),
    ).toBeVisible();
  });

  it("rolls back when the api refuses", async () => {
    const api = fakeEditableJob(makeDetail({ status: "found" }), {
      latencyMs: 50,
      fail: () =>
        HttpResponse.json({ detail: "internal server error" }, { status: 500 }),
    });
    const user = userEvent.setup();
    await openDetail();
    await user.selectOptions(statusSelect(), "skipped");
    expect(badge()).toHaveTextContent("Skipped");
    await screen.findByText(/Not saved\. The API answered 500/);
    expect(statusSelect()).toHaveValue("found");
    expect(badge()).toHaveTextContent("Found");
    expect(api.patches).toHaveLength(1);
  });

  it("reloads the job when another tab changed it (409)", async () => {
    const api = fakeEditableJob(makeDetail({ status: "found" }));
    const user = userEvent.setup();
    await openDetail();
    // Another tab changes the job after this page loaded it.
    api.job = {
      ...api.job,
      status: "interview",
      updated_at: "2026-10-09T10:00:00Z",
    };
    await user.selectOptions(statusSelect(), "skipped");
    await screen.findByText(STALE_MESSAGE);
    expect(api.patches[0]).toMatchObject({
      updated_at: "2026-10-09T08:00:00Z",
    });
    await waitFor(() => {
      expect(statusSelect()).toHaveValue("interview");
    });
  });
});

describe("notes", () => {
  it("autosaves once after a pause in typing, with a saved indicator", async () => {
    const api = fakeEditableJob(makeDetail({ notes: "" }));
    await openDetail();
    const notes = screen.getByRole("textbox", { name: "Notes" });
    // From here the clock of the pause is the test's alone (issue #104: with shouldAdvanceTime
    // the 100 ms before the end of the pause were real ones, and a loaded machine spent them
    // while the keys were typed). The text arrives in two changes, which need no timer.
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    fireEvent.change(notes, { target: { value: "Called" } });
    expect(screen.getByTestId("notes-status")).toHaveTextContent(
      "Unsaved changes",
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NOTES_DEBOUNCE_MS - 100);
    });
    expect(api.patches).toEqual([]);
    // Typing again starts the pause again: nothing is saved where the first one would end.
    fireEvent.change(notes, { target: { value: "Called the recruiter" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NOTES_DEBOUNCE_MS - 100);
    });
    expect(api.patches).toEqual([]);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });
    await waitForOnFakeClock(() => {
      expect(screen.getByTestId("notes-status")).toHaveTextContent("Saved");
    });
    expect(api.patches).toEqual([
      { notes: "Called the recruiter", updated_at: "2026-10-09T08:00:00Z" },
    ]);
    expect(history()[0]).toBe("Notes edited");
  });

  it("saves at once on blur, and never twice for the same text", async () => {
    const api = fakeEditableJob(makeDetail({ notes: "old" }));
    const user = userEvent.setup();
    await openDetail();
    const notes = screen.getByRole("textbox", { name: "Notes" });
    expect(notes).toHaveValue("old");
    await user.clear(notes);
    await user.type(notes, "new ✓ émoji");
    await user.tab();
    await waitFor(() => {
      expect(api.patches).toHaveLength(1);
    });
    expect(api.patches[0]).toMatchObject({ notes: "new ✓ émoji" });
    await new Promise((resolve) =>
      setTimeout(resolve, NOTES_DEBOUNCE_MS + 200),
    );
    expect(api.patches).toHaveLength(1);
    expect(screen.getByTestId("notes-status")).toHaveTextContent("Saved");
  });

  it("keeps the text and offers Retry when saving fails", async () => {
    let failing = true;
    const api = fakeEditableJob(makeDetail({ notes: "" }), {
      fail: () =>
        failing
          ? HttpResponse.json({ detail: "boom" }, { status: 503 })
          : undefined,
    });
    const user = userEvent.setup();
    await openDetail();
    const notes = screen.getByRole("textbox", { name: "Notes" });
    await user.type(notes, "keep me");
    await user.tab();
    expect(
      await screen.findByText(/Not saved\. The API answered 503/),
    ).toBeVisible();
    expect(notes).toHaveValue("keep me");
    expect(notes).toHaveAttribute("aria-invalid", "true");
    failing = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => {
      expect(screen.getByTestId("notes-status")).toHaveTextContent("Saved");
    });
    expect(api.job.notes).toBe("keep me");
  });

  it("warns past the length limit and does not save", async () => {
    const api = fakeEditableJob(makeDetail({ notes: "" }));
    await openDetail();
    const notes = screen.getByRole("textbox", { name: "Notes" });
    const user = userEvent.setup();
    await user.click(notes);
    await user.paste("x".repeat(100_001));
    expect(screen.getByText(/Too long: 100,001 characters/)).toBeVisible();
    await user.tab();
    await new Promise((resolve) =>
      setTimeout(resolve, NOTES_DEBOUNCE_MS + 200),
    );
    expect(api.patches).toEqual([]);
  });
});

describe("fit set by hand", () => {
  it("sets the fit, shows it as manual, and goes back to the computed one", async () => {
    const api = fakeEditableJob(makeDetail({ fit: 70, fit_overridden: false }));
    const user = userEvent.setup();
    await openDetail();
    expect(screen.queryByRole("button", { name: "Use computed" })).toBeNull();
    const input = screen.getByLabelText("Fit");
    await user.clear(input);
    await user.type(input, "42");
    await user.click(screen.getByRole("button", { name: "Set by hand" }));
    expect(
      screen.getByLabelText("Fit 42 out of 100, set by hand"),
    ).toHaveTextContent("42manual");
    await screen.findByText("Fit set by hand to 42.");
    expect(api.patches[0]).toMatchObject({ fit: 42 });
    expect(screen.getByText("Set by hand: a rescore keeps it.")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Use computed" }));
    await screen.findByText("The fit is computed again: 61.");
    expect(api.patches[1]).toMatchObject({ fit: null });
    expect(screen.getByLabelText("Fit 61 out of 100")).toBeInTheDocument();
    expect(input).toHaveValue(61);
    await waitFor(() => {
      expect(history()[0]).toBe(
        "Fit back to the computed 61, was 42 set by hand",
      );
    });
  });

  it("refuses values outside 0 to 100 and decimals", async () => {
    fakeEditableJob(makeDetail({ fit: 70 }));
    const user = userEvent.setup();
    await openDetail();
    const input = screen.getByLabelText("Fit");
    const set = screen.getByRole("button", { name: "Set by hand" });
    for (const bad of ["101", "-1", "4.5"]) {
      await user.clear(input);
      await user.type(input, bad);
      expect(set).toBeDisabled();
      expect(input).toHaveAttribute("aria-invalid", "true");
    }
  });

  it("says why the computed fit is not available", async () => {
    fakeEditableJob(makeDetail({ fit: 70, fit_overridden: true }), {
      fail: () =>
        HttpResponse.json(
          {
            detail:
              "fit: The fit cannot be computed: this job has no posting text.",
          },
          { status: 422 },
        ),
    });
    const user = userEvent.setup();
    await openDetail();
    await user.click(screen.getByRole("button", { name: "Use computed" }));
    await screen.findByText(
      "Not saved. The fit cannot be computed: this job has no posting text.",
    );
    expect(
      screen.getByLabelText("Fit 70 out of 100, set by hand"),
    ).toBeVisible();
  });
});

describe("edit details", () => {
  it("sends only what changed and closes", async () => {
    const api = fakeEditableJob(makeDetail({ company: "Acme", url: null }));
    const user = userEvent.setup();
    await openDetail();
    await user.click(screen.getByRole("button", { name: "Edit details" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit details" });
    const company = within(dialog).getByLabelText("Company");
    await user.clear(company);
    await user.type(company, "Mistral AI");
    await user.selectOptions(
      within(dialog).getByLabelText("Contract type"),
      "VIE",
    );
    await user.type(
      within(dialog).getByLabelText("Link to the posting"),
      "https://jobs.example.org/42",
    );
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    await screen.findByText("Details saved.");
    expect(api.patches[0]).toEqual({
      company: "Mistral AI",
      contract_type: "VIE",
      url: "https://jobs.example.org/42",
      updated_at: "2026-10-09T08:00:00Z",
    });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText("Mistral AI")).toBeVisible();
    await waitFor(() => {
      expect(history()[0]).toBe("Edited the company, contract type, link");
    });
  });

  it("sets the work mode and the pay, and removes them", async () => {
    const api = fakeEditableJob(makeDetail());
    const user = userEvent.setup();
    await openDetail();
    await user.click(screen.getByRole("button", { name: "Edit details" }));
    let dialog = await screen.findByRole("dialog", { name: "Edit details" });
    await user.selectOptions(
      within(dialog).getByLabelText("Work mode"),
      "Hybrid",
    );
    await user.type(within(dialog).getByLabelText("Lowest pay"), "550");
    await user.type(within(dialog).getByLabelText("Highest pay"), "650");
    await user.selectOptions(within(dialog).getByLabelText("Per"), "a day");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(api.patches[0]).toEqual({
      work_mode: "hybrid",
      pay: { min: 550, max: 650, currency: "EUR", period: "day" },
      updated_at: "2026-10-09T08:00:00Z",
    });

    await waitFor(() => {
      expect(history()[0]).toBe("Edited the work mode, pay");
    });
    expect(screen.getByText("550 to 650 EUR a day")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Edit details" }));
    dialog = await screen.findByRole("dialog", { name: "Edit details" });
    expect(within(dialog).getByLabelText("Lowest pay")).toHaveValue("550");
    await user.selectOptions(
      within(dialog).getByLabelText("Work mode"),
      "Not stated",
    );
    await user.clear(within(dialog).getByLabelText("Lowest pay"));
    await user.clear(within(dialog).getByLabelText("Highest pay"));
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(api.patches[1]).toMatchObject({ work_mode: null, pay: null });
    });
  });

  it("refuses a pay that is not whole numbers, lowest first", async () => {
    const api = fakeEditableJob(makeDetail());
    const user = userEvent.setup();
    await openDetail();
    await user.click(screen.getByRole("button", { name: "Edit details" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit details" });
    await user.type(within(dialog).getByLabelText("Lowest pay"), "700");
    await user.type(within(dialog).getByLabelText("Highest pay"), "600");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(
      await within(dialog).findByText(/The pay needs whole numbers/),
    ).toBeVisible();
    expect(api.patches).toEqual([]);
  });

  it("links to the existing posting on a duplicate", async () => {
    const other = "00000000-0000-4000-9000-000000000099";
    fakeEditableJob(makeDetail(), {
      fail: () =>
        HttpResponse.json(
          {
            detail: "This posting is already in your list.",
            existing_id: other,
            existing_slug: "acme-x",
          },
          { status: 409 },
        ),
    });
    const user = userEvent.setup();
    await openDetail();
    await user.click(screen.getByRole("button", { name: "Edit details" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Title"), " 2");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    const link = await within(dialog).findByRole("link", {
      name: "Open the existing posting",
    });
    expect(link).toHaveAttribute("href", `/jobs/${other}`);
    expect(dialog).toHaveTextContent(
      "Another posting in your list has these details.",
    );
  });

  it("needs a company and a title; Cancel sends nothing", async () => {
    const api = fakeEditableJob(makeDetail());
    const user = userEvent.setup();
    await openDetail();
    await user.click(screen.getByRole("button", { name: "Edit details" }));
    const dialog = await screen.findByRole("dialog");
    await user.clear(within(dialog).getByLabelText("Title"));
    expect(within(dialog).getByRole("button", { name: "Save" })).toBeDisabled();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    await delay(50);
    expect(api.patches).toEqual([]);
  });
});
