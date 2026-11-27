import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  CODE_ASK,
  CODE_ASKED_TWICE,
  chatWith,
  SIGNIN_ASK,
  WAIT_NOW,
} from "@/design-system/fixtures/agent-wait";
import {
  OnceCode,
  type Application,
  type ChatLine,
  type Question,
} from "@/features/apply/api/apply-api";
import {
  CODE_ASKED_AGAIN,
  CODE_TYPED_BY_APP,
} from "@/features/apply/helpers/agent-wait";
import { SAID } from "@/test/agent-wait";
import { fakeSession, member } from "@/test/auth";
import { fakeApplication, fakeApply } from "@/test/apply";
import { fakeJobDetail, makeDetail } from "@/test/jobs";
import { fakeProfile } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

const JOB_ID = "00000000-0000-4000-9000-000000000001";
const SEPARATOR = /[–—]| - /;
const CODE_BOX = "The code the site sent you";
const WORDS_BOX = "Or words to the assistant";
const CONSOLE = ["log", "info", "warn", "error", "debug"] as const;

/** A planted code: 10 capitals and digits, random, so a match anywhere is this test's code. */
function plantedCode() {
  const random = crypto.randomUUID().replaceAll("-", "").toUpperCase();
  return `7${random.slice(0, 9)}`;
}

let consoleCalls: unknown[][] = [];
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
  consoleCalls = [];
  for (const level of CONSOLE)
    vi.spyOn(console, level).mockImplementation((...args: unknown[]) => {
      consoleCalls.push(args);
    });
});
afterEach(() => {
  vi.restoreAllMocks();
});

function waiting(
  ask: Question = CODE_ASK,
  chat: ChatLine[] = chatWith(ask),
  overrides: Partial<Application> = {},
) {
  const fake = fakeApply({
    application: fakeApplication("needs_person", {
      mode: "direct",
      needs: "agent_waits",
      ...overrides,
    }),
  });
  fake.detail = { question: ask, chat, now: WAIT_NOW };
  return fake;
}

async function openSection() {
  const user = userEvent.setup();
  const app = renderApp(`/jobs/${JOB_ID}`);
  const heading = await screen.findByRole("heading", {
    name: "Your application",
  });
  const section = heading.closest("section") as HTMLElement;
  const wait = await within(section).findByTestId("agent-wait");
  return { user, app, section, wait: within(wait) };
}

/** Everything the page keeps: the page, the query cache, the mutations, storage, the console. */
function kept(client: ReturnType<typeof renderApp>["client"]): string {
  const variables = client
    .getMutationCache()
    .getAll()
    .map((mutation) => {
      const sent: unknown = mutation.state.variables;
      return sent instanceof OnceCode ? sent.take() : sent;
    });
  return JSON.stringify([
    document.body.innerHTML,
    client
      .getQueryCache()
      .getAll()
      .map((query) => query.state),
    variables,
    Object.entries(window.localStorage),
    Object.entries(window.sessionStorage),
    consoleCalls.map((args) => args.map(String)),
  ]);
}

describe("the code box of a code question (A25c)", () => {
  it("gives the code alone, empties the box at once, and keeps it nowhere", async () => {
    const fake = waiting();
    const code = plantedCode();
    const typed = `${code.slice(0, 5)}-${code.slice(5)}`;
    const { user, app, wait } = await openSection();
    const box = wait.getByLabelText(CODE_BOX);
    expect(box).toHaveAttribute("autocomplete", "one-time-code");
    expect(box).toHaveAttribute("spellcheck", "false");
    expect(box).toHaveAttribute("autocapitalize", "off");
    expect(box).toHaveAccessibleDescription(CODE_TYPED_BY_APP);

    await user.type(box, typed);
    await user.click(wait.getByRole("button", { name: "Give the code" }));
    expect(box).toHaveValue("");
    // The api's own line of a code given, and the box gone until the run reads it.
    expect(await wait.findByText(SAID.codeSaid)).toBeInTheDocument();
    expect(
      wait.getByText(
        "The app has your code, to type on the site. The assistant goes on in a moment.",
      ),
    ).toBeInTheDocument();
    expect(wait.queryByLabelText(CODE_BOX)).toBeNull();
    expect(fake.agent.sent).toEqual([
      { route: "question", body: { code: typed } },
    ]);

    await waitFor(() => {
      expect(app.client.isMutating()).toBe(0);
    });
    const everything = kept(app.client);
    expect(everything).not.toContain(code);
    expect(everything).not.toContain(code.slice(5));
  });

  it.each<[string, () => string, string, "onItsWay" | "browserTakes" | null]>([
    ["a code of five characters", () => "48151", SAID.codeInBrowser, null],
    ["a label with the code", () => "Code: 7KD-3QX", SAID.codeForm, null],
    ["small letters and no digit", () => "xkcdqw", SAID.codeForm, null],
    ["another tab's code on its way", plantedCode, SAID.onItsWay, "onItsWay"],
    [
      "a browser that cannot take it",
      plantedCode,
      SAID.codeNotTaken,
      "browserTakes",
    ],
  ])(
    "shows the api's own sentence for %s, never the code, and the box empty",
    async (_, code, sentence, flag) => {
      const fake = waiting();
      if (flag === "onItsWay") fake.agent.onItsWay = true;
      if (flag === "browserTakes") fake.agent.browserTakes = false;
      const typed = code();
      const { user, app, wait } = await openSection();
      const box = wait.getByLabelText(CODE_BOX);
      await user.type(box, typed);
      await user.click(wait.getByRole("button", { name: "Give the code" }));
      expect(box).toHaveValue("");
      const alert = await wait.findByRole("alert");
      expect(alert).toHaveTextContent(sentence);
      expect(alert.textContent).not.toContain(typed);
      // The way of the browser stays beside the box.
      expect(
        wait.getByRole("button", { name: "Open the browser" }),
      ).toBeInTheDocument();
      await waitFor(() => {
        expect(app.client.isMutating()).toBe(0);
      });
      expect(kept(app.client)).not.toContain(typed);
    },
  );

  it("shows a refusal the api words differently as it comes, with nothing of its own", async () => {
    waiting();
    const newer = "A newer sentence of the api about this code.";
    server.use(
      http.post("*/api/applications/:id/question", () =>
        HttpResponse.json({ detail: newer }, { status: 422 }),
      ),
    );
    const { user, wait } = await openSection();
    await user.type(wait.getByLabelText(CODE_BOX), plantedCode());
    await user.click(wait.getByRole("button", { name: "Give the code" }));
    expect(await wait.findByRole("alert")).toHaveTextContent(newer);
  });

  it("opens the browser on Open the browser, for a shorter code or when the person prefers", async () => {
    waiting();
    const { user, wait } = await openSection();
    expect(wait.queryByTestId("live-view-slot")).toBeNull();
    await user.click(wait.getByRole("button", { name: "Open the browser" }));
    expect(await wait.findByTestId("live-view-slot")).toBeInTheDocument();
  });

  it("keeps the words box for words, and shows the api's refusal of words that may hold a code without them", async () => {
    const fake = waiting();
    const code = plantedCode();
    const { user, wait } = await openSection();
    const words = wait.getByLabelText(WORDS_BOX);
    await user.type(words, `The code is ${code}`);
    await user.click(wait.getByRole("button", { name: "Send the answer" }));
    const alert = await wait.findByRole("alert");
    expect(alert).toHaveTextContent(SAID.codeAlone);
    expect(alert.textContent).not.toContain(code);
    expect(fake.agent.sent).toEqual([
      {
        route: "question",
        body: { answer: `The code is ${code}`, remember: false },
      },
    ]);

    await user.clear(words);
    await user.type(words, "No mail came");
    await user.click(wait.getByRole("button", { name: "Send the answer" }));
    expect(
      await wait.findByText(
        "Your answer was sent. The assistant reads it in a moment.",
      ),
    ).toBeInTheDocument();
  });

  it("says the site may not have taken an earlier code when the assistant asks again", async () => {
    waiting(CODE_ASK, CODE_ASKED_TWICE);
    const { wait } = await openSection();
    expect(wait.getByText(CODE_ASKED_AGAIN)).toBeInTheDocument();
    expect(wait.getByText(SAID.codeSaid)).toBeInTheDocument();
    expect(wait.getByLabelText(CODE_BOX)).toBeInTheDocument();
  });

  it("offers no code box for another kind of ask", async () => {
    waiting(SIGNIN_ASK);
    const { wait } = await openSection();
    expect(wait.queryByLabelText(CODE_BOX)).toBeNull();
    expect(wait.queryByText(CODE_ASKED_AGAIN)).toBeNull();
  });

  it.each([
    [
      "failed",
      "The last attempt failed: the code you gave showed again, so the assistant stopped. Nothing was sent.",
    ],
    [
      "submit_unknown",
      "The code you gave showed again, so the assistant stopped, and the site may already have the application. Look for a confirmation mail or on the site.",
    ],
  ])(
    "says the run stopped because a page showed the code again (%s)",
    async (state, line) => {
      const fake = fakeApply({
        application: fakeApplication(state, {
          mode: "direct",
          error_code: "code_shown",
        }),
      });
      fake.detail = { summary: SAID.codeShown, now: WAIT_NOW };
      renderApp(`/jobs/${JOB_ID}`);
      expect(await screen.findByText(line)).toBeInTheDocument();
      expect(line).not.toMatch(SEPARATOR);
      if (state === "failed")
        expect(await screen.findByText(SAID.codeShown)).toBeInTheDocument();
    },
  );
});
