import { type queries, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import {
  AT_WORK_CHAT,
  CODE_ASK,
  chatWith,
  messageLine,
  SIGNIN_ASK,
  WAIT_NOW,
} from "@/design-system/fixtures/agent-wait";
import type { Application } from "@/features/apply/api/apply-api";
import { WAIT_POLL_MS } from "@/features/apply/api/use-agent-wait";
import { NO_PASSWORD } from "@/features/apply/helpers/agent-wait";
import { statusLine } from "@/features/apply/helpers/apply-state";
import { readMessages, SAID } from "@/test/agent-wait";
import { fakeSession, member } from "@/test/auth";
import { fakeApplication, fakeApply } from "@/test/apply";
import { fakeJobDetail, makeDetail } from "@/test/jobs";
import { fakeProfile } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

const JOB_ID = "00000000-0000-4000-9000-000000000001";
const SEPARATOR = /[–—]| - /;
const BOX = "Write to the assistant";

beforeEach(() => {
  fakeSession(member);
  fakeProfile();
  fakeJobDetail(
    makeDetail({
      id: JOB_ID,
      company: "Example Robotics",
      title: "Robotics Engineer",
    }),
  );
});

/** One click Apply's agent fills the form (A19), with `detail` added to the application's detail. */
function filling(
  overrides: Partial<Application> = {},
  detail: Record<string, unknown> = {},
) {
  const fake = fakeApply({
    application: fakeApplication("filling", { mode: "direct", ...overrides }),
  });
  fake.detail = { chat: [], now: WAIT_NOW, ...detail };
  return fake;
}

async function openSection() {
  const user = userEvent.setup();
  renderApp(`/jobs/${JOB_ID}`);
  const heading = await screen.findByRole("heading", {
    name: "Your application",
  });
  const section = heading.closest("section") as HTMLElement;
  const work = await within(section).findByTestId("agent-wait");
  return { user, section, work: within(work) };
}

const conversation = (work: ReturnType<typeof within<typeof queries>>) =>
  within(
    work.getByRole("list", { name: "Your conversation with the assistant" }),
  ).getAllByRole("listitem");

describe("writing to the assistant while it works (A25c)", () => {
  it("sends the message, shows it in the chat, and says it was read only once the api says so", async () => {
    const fake = filling();
    const { user, section, work } = await openSection();
    expect(work.getByText(NO_PASSWORD)).toBeInTheDocument();
    const box = work.getByLabelText(BOX);
    await user.type(box, "Please skip the optional survey.");
    await user.click(work.getByRole("button", { name: "Send the message" }));
    expect(fake.agent.sent).toEqual([
      { route: "messages", body: { text: "Please skip the optional survey." } },
    ]);
    const lines = await work.findAllByRole("listitem");
    expect(lines.at(-1)).toHaveTextContent(
      "Please skip the optional survey.You, to the assistant. The assistant has not read it yet.",
    );
    expect(box).toHaveValue("");

    // The run takes it with its next tool call's answer: the page says so at its next read.
    readMessages(fake.detail);
    expect(
      await work.findByText(
        "You, to the assistant. The assistant has read it.",
        {},
        { timeout: WAIT_POLL_MS * 3 },
      ),
    ).toBeInTheDocument();
    expect(section.textContent).not.toMatch(SEPARATOR);
  });

  it("shows the messages in the api's order among the asks and answers, each read or not", async () => {
    filling({}, { chat: AT_WORK_CHAT });
    const { work } = await openSection();
    expect(conversation(work).map((line) => line.textContent)).toEqual([
      expect.stringMatching(/^The assistant said:The site sent a link/),
      expect.stringMatching(/^The assistant said:The form asks whether/),
      expect.stringMatching(/^You said:Yes, from January\.You\.$/),
      expect.stringMatching(
        /second phone number.*The assistant has read it\.$/,
      ),
      expect.stringMatching(
        /optional survey.*The assistant has not read it yet\.$/,
      ),
    ]);
  });

  it.each([
    [
      "past the rate",
      SAID.tooMany,
      (fake: ReturnType<typeof filling>) => {
        fake.detail.chat = Array.from({ length: 5 }, (_, n) =>
          messageLine(`Message number ${String(n + 1)}.`, true),
        );
      },
      "One more thing.",
    ],
    [
      "too long",
      SAID.messageLong,
      () => undefined,
      "Please read this. ".repeat(56),
    ],
  ])(
    "shows the api's own refusal of a message %s, and keeps the text to correct",
    async (_, sentence, arrange, text) => {
      const fake = filling();
      const { user, work } = await openSection();
      const box = work.getByLabelText(BOX);
      await user.click(box);
      await user.paste(text);
      arrange(fake);
      await user.click(work.getByRole("button", { name: "Send the message" }));
      const alert = await work.findByRole("alert");
      expect(alert).toHaveTextContent(sentence);
      expect(box).toHaveValue(text);
    },
  );

  it("says in a toast, in the api's words, that the assistant no longer works on it, as the box goes", async () => {
    const fake = filling();
    const { user, work } = await openSection();
    await user.type(work.getByLabelText(BOX), "One more thing.");
    fake.application = fakeApplication("failed", { mode: "direct" });
    await user.click(work.getByRole("button", { name: "Send the message" }));
    expect(await screen.findByText(SAID.noRun)).toBeInTheDocument();
  });

  it("refuses a message that may hold a code while a code question waits, in the api's words and without the text", async () => {
    const fake = fakeApply({
      application: fakeApplication("needs_person", {
        mode: "direct",
        needs: "agent_waits",
      }),
    });
    fake.detail = {
      question: CODE_ASK,
      chat: chatWith(CODE_ASK),
      now: WAIT_NOW,
    };
    const { user, work } = await openSection();
    expect(
      work.getByText(
        "It reads your message once this wait ends. To answer what it asks, use the boxes above.",
      ),
    ).toBeInTheDocument();
    await user.type(work.getByLabelText(BOX), "Here it is: 7KD-3QX");
    await user.click(work.getByRole("button", { name: "Send the message" }));
    const alert = await work.findByRole("alert");
    expect(alert).toHaveTextContent(SAID.codeAlone);
    expect(alert.textContent).not.toContain("7KD");
  });

  it("shows a refusal the api words differently as it comes", async () => {
    filling();
    const newer = "A newer sentence of the api about this message.";
    server.use(
      http.post("*/api/applications/:id/messages", () =>
        HttpResponse.json({ detail: newer }, { status: 422 }),
      ),
    );
    const { user, work } = await openSection();
    await user.type(work.getByLabelText(BOX), "Hello");
    await user.click(work.getByRole("button", { name: "Send the message" }));
    expect(await work.findByRole("alert")).toHaveTextContent(newer);
  });

  it("offers the box to Review first by the agent while it fills, and while a sign in waits", async () => {
    filling({ mode: "review" }, { review_agent: true });
    const first = await openSection();
    expect(first.work.getByLabelText(BOX)).toBeInTheDocument();
  });

  it("keeps the box beside the answer box of a wait", async () => {
    const fake = fakeApply({
      application: fakeApplication("needs_person", {
        mode: "direct",
        needs: "agent_waits",
      }),
    });
    fake.detail = {
      question: SIGNIN_ASK,
      chat: chatWith(SIGNIN_ASK),
      now: WAIT_NOW,
    };
    const { work } = await openSection();
    expect(
      work.getByLabelText("Your answer to the assistant"),
    ).toBeInTheDocument();
    expect(work.getByLabelText(BOX)).toBeInTheDocument();
  });

  // Apply myself and an e-mail application: the helper's table (agent-wait.test.ts) and the
  // page's own test of Apply myself, whose views read routes this fake does not serve.
  it.each([
    ["Review first by the adapters", { mode: "review" }],
    ["a rehearsal", { dry_run: true }],
    ["a run not started", { state: "queued" }],
    ["a run that ended", { state: "failed" }],
  ])("offers no box for %s", async (_, overrides) => {
    const fake = filling(overrides, { chat: AT_WORK_CHAT });
    renderApp(`/jobs/${JOB_ID}`);
    expect(
      await screen.findByText(statusLine(fake.application) ?? "a status line"),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText(BOX)).toBeNull();
  });
});
