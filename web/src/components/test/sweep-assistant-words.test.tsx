import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { SweepAssistantWords } from "@/components/shared/jobs/sweep-assistant-words";
import { workflowRunKey } from "@/features/agents/api/activity-api";
import {
  assistantWordsHandlers,
  sweepWorkflowRun,
  WEB_RUN_ID,
  WEB_WORDS,
  WEBSITE_RUN_ID,
  WEBSITE_WORDS,
  WORDS_SWEEP_ID,
} from "@/design-system/fixtures/assistant-words";
import { renderWithQuery } from "@/test/render";
import { server } from "@/test/server";

function show(sweepId = WORDS_SWEEP_ID) {
  return renderWithQuery(
    <MemoryRouter>
      <SweepAssistantWords sweepId={sweepId} />
    </MemoryRouter>,
  );
}

describe("what the assistants of a search said (J30)", () => {
  it("shows each assistant's closing words one click away, as plain text", async () => {
    server.use(...assistantWordsHandlers);
    show();
    const section = await screen.findByRole("region", {
      name: "What the assistants said",
    });
    const website = within(section).getByRole("button", {
      name: /^Website search, /,
    });
    expect(within(section).getAllByRole("button")).toHaveLength(2);
    expect(section).not.toHaveTextContent("read about 80 results");

    const user = userEvent.setup();
    await user.click(website);
    const quote = await within(section).findByText(/read about 80 results/);
    expect(quote.textContent).toBe(WEBSITE_WORDS);
    expect(section).toHaveTextContent(
      "In the assistant's own words, which the app did not check:",
    );
    expect(section).not.toHaveTextContent("Searching.");

    await user.click(
      within(section).getByRole("button", { name: /^Web search, / }),
    );
    const markup = await within(section).findByText(/this posting/);
    expect(markup.textContent).toBe(WEB_WORDS);
    expect(markup.querySelector("a, b")).toBeNull();
    expect(
      within(section).queryByRole("link", { name: "this posting" }),
    ).toBeNull();
  });

  it("leads to Activity when a run's words cannot be shown", async () => {
    // The first handler that matches answers: these come before the fixture's.
    server.use(
      http.get(`*/api/agents/runs/${WEBSITE_RUN_ID}`, () =>
        HttpResponse.json({ detail: "Not found" }, { status: 404 }),
      ),
      http.get(`*/api/agents/runs/${WEB_RUN_ID}`, () =>
        HttpResponse.json({
          id: WEB_RUN_ID,
          agent: "web_discovery",
          job_id: null,
          status: "done",
          input: {},
          output: null,
          error: null,
          model: null,
          input_tokens: null,
          output_tokens: null,
          cost_usd: null,
          cancel_requested: false,
          created_at: "2026-11-26T08:38:00Z",
          started_at: "2026-11-26T08:38:00Z",
          finished_at: "2026-11-26T08:44:00Z",
          duration_s: 360,
          events: [
            {
              seq: 1,
              at: "2026-11-26T08:39:00Z",
              kind: "text",
              data: { text: "An early text, not the last one." },
            },
          ],
          events_truncated: true,
        }),
      ),
      ...assistantWordsHandlers,
    );
    show();
    const section = await screen.findByRole("region", {
      name: "What the assistants said",
    });
    const user = userEvent.setup();
    await user.click(
      within(section).getByRole("button", { name: /^Website search, / }),
    );
    expect(
      await within(section).findByText(/What it said could not be loaded\./),
    ).toBeInTheDocument();
    await user.click(
      within(section).getByRole("button", { name: /^Web search, / }),
    );
    expect(
      await within(section).findByText(
        /This run is too long to show its last words here\./,
      ),
    ).toBeInTheDocument();
    expect(section).not.toHaveTextContent("An early text");
    const links = within(section).getAllByRole("link", {
      name: "Open it in Activity",
    });
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      `/activity/runs/${WORDS_SWEEP_ID}?step=${WEBSITE_RUN_ID}`,
      `/activity/runs/${WORDS_SWEEP_ID}?step=${WEB_RUN_ID}`,
    ]);
  });

  it("shows nothing for a search no assistant ran", async () => {
    server.use(
      http.get(`*/api/activity/runs/${WORDS_SWEEP_ID}`, () =>
        HttpResponse.json({
          ...sweepWorkflowRun,
          steps: sweepWorkflowRun.steps.filter(
            (step) => step.kind === "system",
          ),
        }),
      ),
    );
    const { container, client } = show();
    await waitFor(() => {
      expect(client.getQueryState(workflowRunKey(WORDS_SWEEP_ID))?.status).toBe(
        "success",
      );
    });
    expect(container).toBeEmptyDOMElement();
    const gone = "00000000-0000-4000-8000-000000000000";
    const other = show(gone);
    await waitFor(() => {
      expect(other.client.getQueryState(workflowRunKey(gone))?.status).toBe(
        "error",
      );
    });
    expect(other.container).toBeEmptyDOMElement();
  });
});
