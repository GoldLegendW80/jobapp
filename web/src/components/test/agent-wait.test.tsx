import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import {
  chatWith,
  CODE_ASK,
  SIGNIN_ASK,
  STEERED_ASK,
  WAIT_NOW,
} from "@/design-system/fixtures/agent-wait";
import type { Application, Question } from "@/features/apply/api/apply-api";
import { WAIT_POLL_MS } from "@/features/apply/api/use-agent-wait";
import {
  AFTER_SEND_WAIT,
  CODE_IN_BROWSER,
  NO_CODE_HERE,
  NO_PASSWORD,
  SAID_NOTHING_SENT,
} from "@/features/apply/helpers/agent-wait";
import { DECISIONS } from "@/features/apply/helpers/decisions";
import { fakeSession, member } from "@/test/auth";
import { fakeApplication, fakeApply } from "@/test/apply";
import { fakeJobDetail, makeDetail } from "@/test/jobs";
import { fakeProfile } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

const JOB_ID = "00000000-0000-4000-9000-000000000001";
const SEPARATOR = /[–—]| - /;
const BOX = "Your answer to the assistant";

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

/** One click Apply's run waits on `ask` (A25), with the earlier asks and answers. */
function waiting(
  ask: Question = SIGNIN_ASK,
  overrides: Partial<Application> = {},
) {
  const fake = fakeApply({
    application: fakeApplication("needs_person", {
      mode: "direct",
      needs: "agent_waits",
      ...overrides,
    }),
  });
  fake.detail = { question: ask, chat: chatWith(ask), now: WAIT_NOW };
  return fake;
}

async function openSection() {
  const user = userEvent.setup();
  renderApp(`/jobs/${JOB_ID}`);
  const heading = await screen.findByRole("heading", {
    name: "Your application",
  });
  const section = heading.closest("section") as HTMLElement;
  const wait = await within(section).findByTestId("agent-wait");
  return { user, section, wait: within(wait) };
}

const DECIDE = [
  "It was sent",
  "It was not sent",
  "Try again",
  "Cancel application",
];

/** The decisions the section offers, in their order on the page. */
const buttons = (section: HTMLElement) =>
  within(section)
    .getAllByRole("button")
    .map((button) => button.textContent)
    .filter((name) => DECIDE.includes(name));

describe("the apply agent waits for the person (A25)", () => {
  it("shows the conversation in order with who said each, what it asks for, the time left, the box and the standing line", async () => {
    waiting();
    const { section, wait } = await openSection();
    const lines = within(
      wait.getByRole("list", { name: "Your conversation with the assistant" }),
    ).getAllByRole("listitem");
    expect(lines.map((line) => line.textContent)).toEqual([
      expect.stringMatching(/^The assistant said:The site sent a link/),
      expect.stringMatching(/^The assistant said:The form asks whether/),
      expect.stringMatching(/^You said:Yes, from January\./),
      expect.stringContaining(SIGNIN_ASK.sentence),
    ]);
    expect(lines[0]).toHaveTextContent(
      "You used the browser, then let the assistant continue.",
    );
    expect(wait.getByText("It asks for a sign in.")).toBeInTheDocument();
    // 12 minutes from the server's time, never the page's clock.
    expect(wait.getByRole("timer").textContent).toMatch(/^1[12]:\d\d$/);
    // The answer goes to the assistant: the box says a code is typed in the browser.
    expect(wait.getByLabelText(BOX)).toHaveAccessibleDescription(NO_CODE_HERE);
    expect(wait.getByText(NO_PASSWORD)).toBeInTheDocument();
    expect(
      within(section).getByRole("button", { name: "Cancel application" }),
    ).toBeInTheDocument();
    expect(section.textContent).not.toMatch(SEPARATOR);
  });

  it("says when the assistant's ask could not be read, with Retry", async () => {
    waiting();
    server.use(
      http.get("*/api/applications/:id", () =>
        HttpResponse.json({ detail: "Boom" }, { status: 500 }),
      ),
    );
    renderApp(`/jobs/${JOB_ID}`);
    expect(
      await screen.findByText(/What the assistant asks could not be loaded\./),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("opens the browser only on a click", async () => {
    waiting();
    const { user, wait } = await openSection();
    expect(wait.queryByTestId("live-view-slot")).toBeNull();
    await user.click(wait.getByRole("button", { name: "Watch the browser" }));
    expect(await wait.findByTestId("live-view-slot")).toBeInTheDocument();
  });

  it("sends the answer to the waiting run, then follows the run without a reload", async () => {
    const fake = waiting();
    const sent: unknown[] = [];
    server.use(
      http.post("*/api/applications/:id/question", async ({ request }) => {
        sent.push(await request.json());
        fake.detail = {
          ...fake.detail,
          chat: [
            ...chatWith(SIGNIN_ASK),
            {
              at: "2026-11-26T10:04:00Z",
              who: "person",
              kind: "answer",
              text: "Create the account, please.",
              end: null,
            },
          ],
        };
        return HttpResponse.json(fake.application);
      }),
    );
    const { user, section, wait } = await openSection();
    await user.type(wait.getByLabelText(BOX), "Create the account, please.");
    await user.click(wait.getByRole("button", { name: "Send the answer" }));
    expect(
      await wait.findByText(
        "Your answer was sent. The assistant reads it in a moment.",
      ),
    ).toBeInTheDocument();
    expect(sent).toEqual([
      { answer: "Create the account, please.", remember: false },
    ]);
    expect(wait.queryByLabelText(BOX)).toBeNull();

    // The run read the answer and fills again: the section follows it at the next read.
    fake.application = fakeApplication("filling", { mode: "direct" });
    fake.detail = { chat: fake.detail.chat, now: WAIT_NOW, question: null };
    expect(
      await within(section).findByText(
        "The assistant is filling the form. It sends it by itself once it is filled.",
        {},
        { timeout: WAIT_POLL_MS * 3 },
      ),
    ).toBeInTheDocument();
    expect(within(section).queryByTestId("agent-wait")).toBeNull();
  });

  it("asks a code in the browser, with no box", async () => {
    waiting(CODE_ASK);
    const { wait } = await openSection();
    expect(wait.getByText("It asks for a code.")).toBeInTheDocument();
    expect(wait.getByText(CODE_IN_BROWSER)).toBeInTheDocument();
    expect(wait.queryByLabelText(BOX)).toBeNull();
    expect(
      wait.getByRole("button", { name: "Watch the browser" }),
    ).toBeInTheDocument();
  });

  it("shows a steered sentence as plain text: no link, no markup", async () => {
    waiting(STEERED_ASK);
    const { wait } = await openSection();
    const chat = wait.getByRole("list", {
      name: "Your conversation with the assistant",
    });
    expect(within(chat).getByText(STEERED_ASK.sentence)).toBeInTheDocument();
    expect(within(chat).queryByRole("link")).toBeNull();
    expect(within(chat).queryByRole("button")).toBeNull();
    expect(chat.querySelector("a, strong, em, b")).toBeNull();
  });

  it.each([
    ["the wait ended", true],
    ["another tab answered first", false],
  ])(
    "says in the api's words why an answer was not taken (%s)",
    async (_, ended) => {
      const fake = waiting();
      const refused = "This application waits for no answer now.";
      server.use(
        http.post("*/api/applications/:id/question", () => {
          if (ended) {
            fake.application = fakeApplication("needs_person", {
              mode: "direct",
              needs: "agent_asked",
            });
            fake.detail = {
              question: { ...SIGNIN_ASK, live: false },
              chat: chatWith(SIGNIN_ASK, "person_late"),
              now: WAIT_NOW,
            };
          }
          return HttpResponse.json({ detail: refused }, { status: 409 });
        }),
      );
      const { user, wait } = await openSection();
      await user.type(wait.getByLabelText(BOX), "Too late");
      await user.click(wait.getByRole("button", { name: "Send the answer" }));
      const alert = await wait.findByRole("alert");
      expect(alert).toHaveTextContent(refused);
      expect(alert.textContent).not.toMatch(/came after/);
    },
  );

  it.each([
    [
      "person_late",
      "Nobody answered the assistant in time, so it stopped and gave the browser back.",
    ],
    [
      "run_ended",
      "The assistant's run ended while it waited for you (its time limit, or the browser closed).",
    ],
    [
      "interrupted",
      "The app was interrupted while the assistant waited for you.",
    ],
  ])(
    "once the wait ended (%s), keeps the sentence and says why, with Try again and Cancel",
    async (end, why) => {
      const fake = waiting(
        { ...SIGNIN_ASK, live: false },
        {
          needs: "agent_asked",
        },
      );
      fake.detail = { ...fake.detail, chat: chatWith(SIGNIN_ASK, end) };
      const { user, section, wait } = await openSection();
      expect(wait.getByText(SIGNIN_ASK.sentence)).toBeInTheDocument();
      expect(wait.getByText(why)).toBeInTheDocument();
      expect(wait.queryByLabelText(BOX)).toBeNull();
      expect(
        wait.queryByRole("button", { name: "Watch the browser" }),
      ).toBeNull();
      expect(buttons(section)).toEqual(["Try again", "Cancel application"]);
      await user.click(
        within(section).getByRole("button", { name: "Try again" }),
      );
      const dialog = await screen.findByRole("alertdialog");
      expect(dialog).toHaveTextContent(DECISIONS.retryAsked.sentence);
      await user.click(
        within(dialog).getByRole("button", { name: "Try again" }),
      );
      await waitFor(() => {
        expect(fake.decisions).toEqual(["retry"]);
      });
    },
  );

  it("after a send button, says the site may have it, and its Cancel says so too", async () => {
    waiting({ ...SIGNIN_ASK, after_send: true }, { state: "submitting" });
    const { user, section, wait } = await openSection();
    expect(wait.getByText(AFTER_SEND_WAIT)).toBeInTheDocument();
    await user.click(
      within(section).getByRole("button", { name: "Cancel application" }),
    );
    expect(await screen.findByRole("alertdialog")).toHaveTextContent(
      DECISIONS.cancelAfterSend.sentence,
    );
  });

  it("nobody came after a send button: the ask stays, It was sent first, then It was not sent and Try again", async () => {
    const fake = waiting(
      { ...SIGNIN_ASK, live: false, after_send: true },
      { state: "submit_unknown", error_code: "person_late" },
    );
    fake.detail = { ...fake.detail, agent: true };
    const { section, wait } = await openSection();
    expect(
      within(section).getByText(
        "Nobody answered the assistant in time, after a send button had been clicked. Look for a confirmation mail or on the site.",
      ),
    ).toBeInTheDocument();
    expect(wait.getByText(AFTER_SEND_WAIT)).toBeInTheDocument();
    expect(buttons(section)).toEqual([
      "It was sent",
      "It was not sent",
      "Try again",
    ]);
  });

  it.each([
    [
      "send_clicked",
      "The assistant says it clicked a send button, but it could not confirm the application. Look for a confirmation mail or on the site.",
    ],
    [
      "send_unsure",
      "The assistant is not sure whether it clicked a send button, and it could not confirm the application. Look for a confirmation mail or on the site.",
    ],
  ])("says what the assistant said of the send (%s)", async (code, line) => {
    fakeApply({
      application: fakeApplication("submit_unknown", {
        mode: "direct",
        error_code: code,
      }),
    });
    renderApp(`/jobs/${JOB_ID}`);
    expect(await screen.findByText(line)).toBeInTheDocument();
    expect(line).not.toMatch(SEPARATOR);
  });

  it("puts It was not sent first when the assistant says nothing was sent", async () => {
    const fake = fakeApply({
      application: fakeApplication("submit_unknown", {
        mode: "direct",
        error_code: "no_confirmation",
      }),
    });
    fake.events = [
      { at: "2026-11-26T10:00:00Z", kind: "agent_done", data: { sent: "no" } },
    ];
    renderApp(`/jobs/${JOB_ID}`);
    const said = await screen.findByText(SAID_NOTHING_SENT);
    const section = said.closest("section") as HTMLElement;
    expect(buttons(section)).toEqual([
      "It was not sent",
      "It was sent",
      "Try again",
    ]);
  });

  it("records a send the person made in the browser as theirs", async () => {
    fakeApply({
      application: fakeApplication("submitted", {
        mode: "direct",
        error_code: "sent_by_person",
      }),
    });
    renderApp(`/jobs/${JOB_ID}`);
    expect(
      await screen.findByText(
        "Applied: you sent it yourself in the browser, and the site confirmed it.",
      ),
    ).toBeInTheDocument();
  });

  it("leaves One click Apply's earlier question (A19) as it was", async () => {
    const fake = waiting(SIGNIN_ASK, { needs: "agent_question" });
    fake.detail = {
      question: {
        sentence:
          'The assistant needs your answer for "Notice period" on jobs.example.org.',
        label: "Notice period",
        host: "jobs.example.org",
        kind: "fact",
      },
    };
    renderApp(`/jobs/${JOB_ID}`);
    expect(await screen.findByTestId("agent-question")).toBeInTheDocument();
    expect(screen.queryByTestId("agent-wait")).toBeNull();
  });

  it("shows nothing new for Apply myself", async () => {
    waiting(SIGNIN_ASK, { mode: "manual", state: "filling" });
    renderApp(`/jobs/${JOB_ID}`);
    await screen.findByRole("heading", { name: "Your application" });
    await screen.findByText(/You fill it and click its Submit button yourself/);
    expect(screen.queryByTestId("agent-wait")).toBeNull();
  });
});
