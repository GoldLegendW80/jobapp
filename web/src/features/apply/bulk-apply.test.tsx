import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { useState } from "react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { renderWithQuery } from "@/test/render";
import { server } from "@/test/server";

import { BulkApplyDialog, type BulkApplyRequest } from "./bulk-apply";
import type { BulkPreview, BulkStarted } from "./bulk-apply-api";

const P1 = "00000000-0000-4000-9000-000000000001";
const P2 = "00000000-0000-4000-9000-000000000002";
const P3 = "00000000-0000-4000-9000-000000000003";
const P4 = "00000000-0000-4000-9000-000000000004";

const ESTIMATE = {
  tokens: { low: 10000, high: 20000 },
  cost: { currency: "USD", low: "0.10", high: "0.20" },
};

function preview(overrides: Partial<BulkPreview> = {}): BulkPreview {
  return {
    review_before_send: false,
    limit: 25,
    items: [
      {
        posting_id: P1,
        company: "Example Robotics",
        title: "Robotics Engineer",
        documents: [],
        estimate: null,
        email: false,
        seen: "a".repeat(64),
      },
      {
        posting_id: P2,
        company: "Example Labs",
        title: "ML Engineer",
        documents: ["cv", "letter"],
        estimate: ESTIMATE,
        email: false,
        seen: "b".repeat(64),
      },
    ],
    left_out: [
      {
        posting_id: P3,
        company: "Example Works",
        title: "Platform Engineer",
        code: "already_applied",
        detail: "This posting is marked as applied.",
      },
      {
        posting_id: P4,
        company: "Example Social",
        title: "Data Engineer",
        code: "linkedin_off",
        detail:
          "LinkedIn is off for you: turn it on in Find jobs to apply there.",
      },
    ],
    beyond: [],
    estimate: ESTIMATE,
    cost_note: "Your provider's invoice is the exact figure.",
    ...overrides,
  };
}

/** Fake the two routes; `answers` are the previews in order (the last one repeats). */
function fakeBulk(
  answers: (BulkPreview | Response)[],
  started: BulkStarted | Response = {
    results: [
      { posting_id: P1, started: true, application_id: "a1", mode: "direct" },
      { posting_id: P2, started: true, application_id: "a2", mode: "direct" },
    ],
    started: 2,
    left_out: 0,
  },
) {
  const calls = { previews: [] as unknown[], starts: [] as unknown[] };
  server.use(
    http.post("*/api/apply/bulk/preview", async ({ request }) => {
      calls.previews.push(await request.json());
      const answer =
        answers[Math.min(calls.previews.length, answers.length) - 1]!;
      return answer instanceof Response ? answer : HttpResponse.json(answer);
    }),
    http.post("*/api/apply/bulk", async ({ request }) => {
      calls.starts.push(await request.json());
      return started instanceof Response ? started : HttpResponse.json(started);
    }),
  );
  return calls;
}

function Host({ onApplied }: { onApplied: () => void }) {
  const [request, setRequest] = useState<BulkApplyRequest | null>({
    ids: [P1, P2, P3, P4],
    key: 1,
  });
  return (
    <MemoryRouter>
      <BulkApplyDialog
        request={request}
        onClose={() => {
          setRequest(null);
        }}
        onApplied={onApplied}
      />
    </MemoryRouter>
  );
}

async function open() {
  const onApplied = vi.fn();
  renderWithQuery(<Host onApplied={onApplied} />);
  const dialog = await screen.findByRole("alertdialog");
  await within(dialog).findByRole("list", { name: "Postings to apply to" });
  return { dialog, onApplied };
}

const itemsOf = (dialog: HTMLElement, name: string) =>
  within(within(dialog).getByRole("list", { name }))
    .getAllByRole("listitem")
    .map((item) => item.textContent);

describe("Apply to the selected postings", () => {
  it("names every posting, its documents and their estimate, what the click does, and those left out with why", async () => {
    const calls = fakeBulk([preview()]);
    const { dialog } = await open();
    expect(calls.previews).toEqual([{ posting_ids: [P1, P2, P3, P4] }]);
    expect(
      within(dialog).getByRole("heading", { name: "Apply to 2 postings?" }),
    ).toBeInTheDocument();
    expect(dialog).toHaveTextContent(
      "The assistant fills each form and sends it. Your click approves exactly the postings listed here.",
    );
    expect(itemsOf(dialog, "Postings to apply to")).toEqual([
      "Example Robotics, Robotics EngineerCV and letter ready.",
      "Example Labs, ML EngineerCV and letter written first, about 10,000 to 20,000 tokens, about USD 0.10 to USD 0.20.",
    ]);
    expect(dialog).toHaveTextContent(
      "Writing the missing documents: about 10,000 to 20,000 tokens, about USD 0.10 to USD 0.20 in all. Your provider's invoice is the exact figure.",
    );
    expect(
      within(dialog).getByRole("heading", { name: "Not applied to (2)" }),
    ).toBeInTheDocument();
    expect(itemsOf(dialog, "Not applied to (2)")).toEqual([
      "Example Works, Platform EngineerThis posting is marked as applied.",
      "Example Social, Data EngineerLinkedIn is off for you: turn it on in Find jobs to apply there.",
    ]);
    expect(
      within(dialog).getByRole("button", { name: "Apply to 2" }),
    ).toBeEnabled();
    expect(
      within(dialog).getByRole("button", { name: "Cancel" }),
    ).toBeEnabled();
    expect(dialog.textContent).not.toMatch(/[–—]| - /);
  });

  it("with the review box ticked, says the person checks and sends each form", async () => {
    fakeBulk([preview({ review_before_send: true })]);
    const { dialog } = await open();
    expect(dialog).toHaveTextContent(
      "The assistant fills each form, then you check it and send it. Your click approves exactly the postings listed here.",
    );
  });

  it("says a letter alone, and an e-mail application the person sends", async () => {
    const [first, second] = preview().items;
    fakeBulk([
      preview({
        items: [
          { ...first!, email: true },
          { ...second!, documents: ["letter"] },
        ],
      }),
    ]);
    const { dialog } = await open();
    expect(itemsOf(dialog, "Postings to apply to")).toEqual([
      "Example Robotics, Robotics EngineerCV and letter ready. An e-mail application: you check it and send it in Applications.",
      "Example Labs, ML EngineerLetter written first, about 10,000 to 20,000 tokens, about USD 0.10 to USD 0.20.",
    ]);
  });

  it("says the bound: the first 25 now, the others after", async () => {
    fakeBulk([
      preview({
        left_out: [],
        beyond: [
          { posting_id: P3, company: "Example Works", title: "A" },
          { posting_id: P4, company: "Example Works", title: "B" },
        ],
      }),
    ]);
    const { dialog } = await open();
    expect(within(dialog).getByRole("note")).toHaveTextContent(
      "Apply to the first 25; select the others after. 2 postings are not in this click.",
    );
    expect(
      within(dialog).queryByRole("heading", { name: /Not applied to/ }),
    ).toBeNull();
  });

  it("sends exactly the postings shown, then says what started, what was left out since, and links to Applications", async () => {
    const calls = fakeBulk([preview()], {
      results: [
        { posting_id: P1, started: true, application_id: "a1", mode: "direct" },
        {
          posting_id: P2,
          started: false,
          code: "changed",
          detail:
            "This posting or its documents changed since you opened this. Select it again to apply.",
        },
      ],
      started: 1,
      left_out: 1,
    });
    const user = userEvent.setup();
    const { dialog, onApplied } = await open();
    await user.click(
      within(dialog).getByRole("button", { name: "Apply to 2" }),
    );
    await within(dialog).findByRole("heading", {
      name: "Applying to 1 posting",
    });
    expect(calls.starts).toEqual([
      {
        review_before_send: false,
        items: [
          { posting_id: P1, seen: "a".repeat(64), documents: [] },
          { posting_id: P2, seen: "b".repeat(64), documents: ["cv", "letter"] },
        ],
      },
    ]);
    expect(onApplied).toHaveBeenCalledTimes(1);
    const status = within(dialog).getByRole("status");
    expect(status).toHaveTextContent("Applying to 1 posting. 1 was left out:");
    expect(
      within(status)
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual([
      "Example Labs, ML Engineer: This posting or its documents changed since you opened this. Select it again to apply.",
    ]);
    expect(
      within(dialog).getByRole("link", { name: "Open Applications" }),
    ).toHaveAttribute("href", "/applications");
    // Nothing more is sent: the summary has no Apply button.
    expect(
      within(dialog).queryByRole("button", { name: /^Apply to/ }),
    ).toBeNull();
    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).toBeNull();
    });
    expect(calls.starts).toHaveLength(1);
  });

  it("a changed review box starts nothing: it says so and shows the confirmation as it reads now", async () => {
    const calls = fakeBulk(
      [preview(), preview({ review_before_send: true })],
      HttpResponse.json(
        {
          detail:
            "Your review setting changed since you opened this. Open Apply again to see what it does now.",
          code: "settings_changed",
        },
        { status: 409 },
      ),
    );
    const user = userEvent.setup();
    const { dialog, onApplied } = await open();
    await user.click(
      within(dialog).getByRole("button", { name: "Apply to 2" }),
    );
    expect(
      await within(dialog).findByText(/Your review setting changed/),
    ).toBeInTheDocument();
    await within(dialog).findByText(/then you check it and send it\./);
    expect(calls.previews).toHaveLength(2);
    expect(onApplied).not.toHaveBeenCalled();
  });

  it("a click whose answer is lost says to look in Applications", async () => {
    fakeBulk(
      [preview()],
      HttpResponse.json({ detail: "boom" }, { status: 500 }),
    );
    const user = userEvent.setup();
    const { dialog, onApplied } = await open();
    await user.click(
      within(dialog).getByRole("button", { name: "Apply to 2" }),
    );
    expect(
      await within(dialog).findByText(/The click could not be confirmed\./),
    ).toHaveTextContent("Open Applications to see what started.");
    expect(onApplied).not.toHaveBeenCalled();
  });

  it("offers no Apply when none can be applied to, and a failed read can be tried again", async () => {
    const calls = fakeBulk([
      HttpResponse.json({ detail: "boom" }, { status: 500 }),
      preview({ items: [], estimate: null }),
    ]);
    const user = userEvent.setup();
    renderWithQuery(<Host onApplied={vi.fn()} />);
    const dialog = await screen.findByRole("alertdialog");
    expect(
      await within(dialog).findByText(
        /The selected postings could not be read\./,
      ),
    ).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Try again" }));
    expect(
      await within(dialog).findByRole("heading", {
        name: "None of these can be applied to",
      }),
    ).toBeInTheDocument();
    expect(
      within(dialog).queryByRole("button", { name: /^Apply to/ }),
    ).toBeNull();
    expect(itemsOf(dialog, "Not applied to (2)")).toHaveLength(2);
    expect(calls.previews).toHaveLength(2);
  });
});
