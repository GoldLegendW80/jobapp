import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { FakeEventSource } from "@/test/fake-event-source";
import { makeDetail } from "@/test/jobs";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import type { ChatMessage, Thread, ThreadDetail } from "./chat-api";
import type { Run } from "./runs-api";

const JOB = "00000000-0000-4000-9000-000000000001";
const THREAD = "66666666-6666-4666-8666-666666666666";
const RUN_1 = "77777777-7777-4777-8777-000000000001";
const RUN_2 = "77777777-7777-4777-8777-000000000002";

const credential = {
  kind: "anthropic_api_key",
  last4: "Rn42",
  verified_at: "2026-10-10T07:00:00Z",
  updated_at: "2026-10-10T07:00:00Z",
};

const thread: Thread = {
  id: THREAD,
  job_id: JOB,
  title: "Engineer 1 at Company 1",
  created_at: "2026-10-10T08:00:00Z",
  updated_at: "2026-10-10T08:00:00Z",
};

function message(
  id: number,
  role: "user" | "assistant",
  text: string,
  changes: Partial<ChatMessage> = {},
): ChatMessage {
  return {
    id,
    role,
    text,
    run_id: RUN_1,
    run_status: "done",
    error: null,
    at: "2026-10-10T08:00:00Z",
    ...changes,
  };
}

function run(id: string, changes: Partial<Run> = {}): Run {
  return {
    id,
    agent: "job_chat",
    job_id: JOB,
    status: "queued",
    input: { thread_id: THREAD },
    output: null,
    error: null,
    model: "claude-opus-5-5",
    input_tokens: null,
    output_tokens: null,
    cost_usd: null,
    cancel_requested: false,
    created_at: "2026-10-10T08:00:00Z",
    started_at: null,
    finished_at: null,
    duration_s: null,
    ...changes,
  };
}

/** A fake api for the chat: the job, the credential, the thread and its messages. */
function fakeApi(
  options: {
    credential?: boolean;
    thread?: boolean;
    messages?: ChatMessage[];
    active?: string | null;
    sendAnswer?: Response;
  } = {},
) {
  fakeSession(admin);
  const state = {
    exists: options.thread ?? false,
    messages: options.messages ?? [],
    active: options.active ?? null,
    opens: 0,
    sends: [] as string[],
    deletes: 0,
    nextId: 100,
  };
  const detail = (): ThreadDetail => ({
    ...thread,
    messages: state.messages,
    messages_truncated: false,
    active_run_id: state.active,
  });
  server.use(
    http.get("*/api/jobs/:id", () => HttpResponse.json(makeDetail())),
    http.get("*/api/me/model-credential", () =>
      options.credential === false
        ? HttpResponse.json({ detail: "none" }, { status: 404 })
        : HttpResponse.json(credential),
    ),
    http.get("*/api/agents/runs", () => HttpResponse.json({ items: [] })),
    http.get("*/api/agents/runs/:id", ({ params }) =>
      HttpResponse.json({
        ...run(String(params.id)),
        events: [],
        events_truncated: false,
      }),
    ),
    http.get("*/api/agents/threads", ({ request }) => {
      expect(new URL(request.url).searchParams.get("job_id")).toBe(JOB);
      return HttpResponse.json({ items: state.exists ? [thread] : [] });
    }),
    http.post("*/api/agents/threads", async ({ request }) => {
      expect(request.headers.get("X-Requested-With")).toBe("jobapp");
      expect(await request.json()).toEqual({ job_id: JOB });
      state.opens += 1;
      const created = !state.exists;
      state.exists = true;
      return HttpResponse.json(thread, { status: created ? 201 : 200 });
    }),
    http.get(`*/api/agents/threads/${THREAD}`, () =>
      state.exists
        ? HttpResponse.json(detail())
        : HttpResponse.json(
            { detail: "Conversation not found." },
            { status: 404 },
          ),
    ),
    http.post(
      `*/api/agents/threads/${THREAD}/messages`,
      async ({ request }) => {
        const body = (await request.json()) as { text: string };
        state.sends.push(body.text);
        if (options.sendAnswer) return options.sendAnswer;
        const runId = state.sends.length === 1 ? RUN_1 : RUN_2;
        const sent = message(state.nextId++, "user", body.text, {
          run_id: runId,
          run_status: "queued",
        });
        state.messages = [...state.messages, sent];
        state.active = runId;
        return HttpResponse.json(
          { message: sent, run: run(runId) },
          { status: 202 },
        );
      },
    ),
    http.delete(`*/api/agents/threads/${THREAD}`, ({ request }) => {
      expect(request.headers.get("X-Requested-With")).toBe("jobapp");
      state.deletes += 1;
      state.exists = false;
      state.messages = [];
      return new HttpResponse(null, { status: 204 });
    }),
  );
  return state;
}

beforeEach(() => {
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  FakeEventSource.reset();
  vi.unstubAllGlobals();
});

const chatButton = () => screen.findByRole("button", { name: "Chat" });
const composer = () => screen.getByRole("textbox", { name: "Message" });
const sendButton = () => screen.getByRole("button", { name: "Send" });

async function openChat(path = `/jobs/${JOB}`) {
  const user = userEvent.setup();
  const app = renderApp(path);
  if (!path.includes("chat=open")) await user.click(await chatButton());
  const panel = await screen.findByRole("dialog", {
    name: "Chat about this job",
  });
  return { user, app, panel };
}

function streamOf(runId: string) {
  return FakeEventSource.all.find((source) =>
    source.url.includes(`/api/agents/runs/${runId}/stream`),
  );
}

describe("job detail: chat panel", () => {
  it("opens from the Chat button, with an empty state and example questions", async () => {
    fakeApi();
    const { user, app, panel } = await openChat();
    expect(app.location()).toBe(`/jobs/${JOB}?chat=open`);
    const empty = await within(panel).findByTestId("chat-empty");
    expect(empty).toHaveTextContent("Ask anything about this posting.");
    const examples = within(empty).getByRole("list", {
      name: "Example questions",
    });
    expect(within(examples).getAllByRole("button")).toHaveLength(3);
    await waitFor(() => {
      expect(composer()).toBeEnabled();
    });
    expect(sendButton()).toBeDisabled();
    await user.click(
      within(examples).getByRole("button", {
        name: "Does this role fit my profile?",
      }),
    );
    expect(composer()).toHaveValue("Does this role fit my profile?");
    expect(composer()).toHaveFocus();
    expect(sendButton()).toBeEnabled();
    expect(document.body.textContent).not.toMatch(/[–—]| - /);
    // Closing removes the parameter again.
    await user.keyboard("{Escape}");
    await waitFor(() => {
      expect(app.location()).toBe(`/jobs/${JOB}`);
    });
  });

  it("sends with Enter, streams the answer, then shows and announces the stored one", async () => {
    const state = fakeApi();
    const { user, panel } = await openChat();
    await waitFor(() => {
      expect(composer()).toBeEnabled();
    });
    await user.type(composer(), "Does it fit?{Enter}");
    await waitFor(() => {
      expect(state.sends).toEqual(["Does it fit?"]);
    });
    expect(state.opens).toBe(1);
    expect(await within(panel).findByText("Does it fit?")).toBeInTheDocument();
    expect(composer()).toHaveValue("");
    // While the answer is pending: Send is disabled and Enter does nothing.
    await user.type(composer(), "Next question{Enter}");
    expect(state.sends).toHaveLength(1);
    expect(sendButton()).toBeDisabled();
    expect(
      within(panel).getByText(
        "Wait for the answer before sending the next message.",
      ),
    ).toBeInTheDocument();

    const live = await within(panel).findByTestId("chat-live-reply");
    expect(live).toHaveTextContent("Waiting for a free agent");
    await waitFor(() => {
      expect(streamOf(RUN_1)).toBeDefined();
    });
    const source = streamOf(RUN_1)!;
    act(() => {
      source.open();
      source.emit("step", {
        seq: 1,
        at: "2026-10-10T08:00:01Z",
        kind: "status",
        data: { status: "running" },
      });
      source.emit("step", {
        seq: 2,
        at: "2026-10-10T08:00:02Z",
        kind: "tool_call",
        data: { name: "mcp__app__get_job", input: {} },
      });
    });
    expect(live).toHaveTextContent("Reading the posting");
    act(() => {
      source.emit("step", {
        seq: 3,
        at: "2026-10-10T08:00:03Z",
        kind: "text",
        data: { text: "It fits well" },
      });
    });
    expect(live).toHaveTextContent("It fits well");
    const region = within(panel).getByRole("status");
    expect(region).toBeEmptyDOMElement();

    // The run ends: the stored answer replaces the live one, announced once.
    state.messages = [
      ...state.messages,
      message(200, "assistant", "It fits well, with one gap on years."),
    ];
    state.active = null;
    act(() => {
      source.emit("step", {
        seq: 4,
        at: "2026-10-10T08:00:04Z",
        kind: "status",
        data: { status: "done" },
      });
    });
    expect(
      await within(panel).findByText("It fits well, with one gap on years."),
    ).toBeInTheDocument();
    expect(
      within(panel).queryByTestId("chat-live-reply"),
    ).not.toBeInTheDocument();
    await waitFor(() => {
      expect(region).toHaveTextContent(
        "Answer: It fits well, with one gap on years.",
      );
    });
    const items = within(panel).getAllByTestId("chat-message");
    expect(items.map((item) => item.dataset.role)).toEqual([
      "user",
      "assistant",
    ]);
    // The draft written meanwhile is kept and can now be sent, to the same thread.
    expect(composer()).toHaveValue("Next question");
    await waitFor(() => {
      expect(sendButton()).toBeEnabled();
    });
    await user.click(sendButton());
    await waitFor(() => {
      expect(state.sends).toEqual(["Does it fit?", "Next question"]);
    });
    expect(state.opens).toBe(1);
  });

  // The first message creates the thread, and the thread's first read then runs next to the
  // send. That read can answer after the send with the thread as it was before it (empty), or
  // torn (the pending run and the count of the send, the message list from before it).
  it.each([
    [
      "from before the send",
      { active_run_id: null, messages_truncated: false },
    ],
    ["torn by the send", { active_run_id: RUN_1, messages_truncated: true }],
  ])(
    "shows the first message at once when the thread's read answers late, %s",
    async (_name, late) => {
      const state = fakeApi();
      let reads = 0;
      let release = () => {};
      const sendAnswered = new Promise<void>((resolve) => {
        release = resolve;
      });
      server.use(
        http.get(`*/api/agents/threads/${THREAD}`, async () => {
          reads += 1;
          if (reads > 1) {
            return HttpResponse.json({
              ...thread,
              messages: state.messages,
              messages_truncated: false,
              active_run_id: state.active,
            });
          }
          await sendAnswered;
          return HttpResponse.json({ ...thread, messages: [], ...late });
        }),
      );
      const { user, panel } = await openChat();
      await waitFor(() => {
        expect(composer()).toBeEnabled();
      });
      await user.type(composer(), "Does it fit?{Enter}");
      // The send was answered (the composer is emptied), the first read is still out.
      await waitFor(() => {
        expect(composer()).toHaveValue("");
      });
      expect(state.sends).toEqual(["Does it fit?"]);
      const shown = () =>
        within(panel)
          .queryAllByTestId("chat-message")
          .map((item) => item.textContent);
      expect(shown()).toEqual(["You:Does it fit?"]);
      expect(within(panel).getByTestId("chat-live-reply")).toBeInTheDocument();
      // The late answer changes nothing, and a read made after the send follows.
      release();
      await waitFor(() => {
        expect(reads).toBeGreaterThan(1);
      });
      await waitFor(() => {
        expect(streamOf(RUN_1)).toBeDefined();
      });
      expect(shown()).toEqual(["You:Does it fit?"]);
      expect(within(panel).getByTestId("chat-live-reply")).toBeInTheDocument();
      expect(
        within(panel).queryByText("Older messages are not shown."),
      ).not.toBeInTheDocument();
      expect(within(panel).queryByTestId("chat-empty")).not.toBeInTheDocument();
      expect(sendButton()).toBeDisabled();
    },
  );

  it("adds a line with Shift and Enter instead of sending", async () => {
    const state = fakeApi();
    const { user } = await openChat();
    await waitFor(() => {
      expect(composer()).toBeEnabled();
    });
    await user.type(composer(), "line one{Shift>}{Enter}{/Shift}line two");
    expect(composer()).toHaveValue("line one\nline two");
    expect(state.sends).toEqual([]);
  });

  it("shows the history after a reload, plain text, and follows a pending answer", async () => {
    fakeApi({
      thread: true,
      messages: [
        message(1, "user", "First **question**"),
        message(2, "assistant", "Line one\n<b>not bold</b>"),
        message(3, "user", "Second", { run_id: RUN_2, run_status: "running" }),
      ],
      active: RUN_2,
    });
    const { panel } = await openChat(`/jobs/${JOB}?chat=open`);
    const items = await within(panel).findAllByTestId("chat-message");
    expect(items).toHaveLength(3);
    // No markdown and no html: the text as written.
    expect(items[0]).toHaveTextContent("First **question**");
    expect(items[1]).toHaveTextContent("Line one <b>not bold</b>");
    expect(panel.querySelector("b")).toBeNull();
    expect(
      await within(panel).findByTestId("chat-live-reply"),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(streamOf(RUN_2)).toBeDefined();
    });
    expect(sendButton()).toBeDisabled();
    // Answers already there are not announced again.
    expect(within(panel).getByRole("status")).toBeEmptyDOMElement();
    expect(
      within(panel).getByRole("button", { name: "Delete conversation" }),
    ).toBeInTheDocument();
  });

  it("says when a message got no answer", async () => {
    fakeApi({
      thread: true,
      messages: [
        message(1, "user", "Hello", {
          run_status: "failed",
          error: "Anthropic refused the credential.",
        }),
      ],
    });
    const { panel } = await openChat(`/jobs/${JOB}?chat=open`);
    expect(
      await within(panel).findByText(
        /No answer: Anthropic refused the credential\./,
      ),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(composer()).toBeEnabled();
    });
  });

  it("points to the Account page without a credential", async () => {
    fakeApi({ credential: false });
    const { panel } = await openChat();
    const link = await within(panel).findByRole("link", {
      name: "Add one on the Account page",
    });
    expect(link).toHaveAttribute("href", "/account");
    expect(composer()).toBeDisabled();
    expect(sendButton()).toBeDisabled();
  });

  it("deletes the conversation after a confirmation", async () => {
    const state = fakeApi({
      thread: true,
      messages: [
        message(1, "user", "Hello"),
        message(2, "assistant", "Hi there"),
      ],
    });
    const { user, panel } = await openChat(`/jobs/${JOB}?chat=open`);
    await within(panel).findByText("Hi there");
    await user.click(
      within(panel).getByRole("button", { name: "Delete conversation" }),
    );
    expect(
      within(panel).getByText("Delete this conversation?"),
    ).toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "Keep" }));
    expect(state.deletes).toBe(0);
    await user.click(
      within(panel).getByRole("button", { name: "Delete conversation" }),
    );
    await user.click(within(panel).getByRole("button", { name: "Delete" }));
    await waitFor(() => {
      expect(state.deletes).toBe(1);
    });
    expect(await within(panel).findByTestId("chat-empty")).toBeInTheDocument();
    expect(within(panel).queryByText("Hi there")).not.toBeInTheDocument();
  });

  it("says so when the previous message is still being answered", async () => {
    const state = fakeApi({
      thread: true,
      sendAnswer: HttpResponse.json(
        { detail: "Still answering.", active_run_id: RUN_1 },
        { status: 409 },
      ),
    });
    const { user } = await openChat(`/jobs/${JOB}?chat=open`);
    await waitFor(() => {
      expect(composer()).toBeEnabled();
    });
    await user.type(composer(), "Hello{Enter}");
    expect(
      await screen.findByText("The previous message is still being answered."),
    ).toBeInTheDocument();
    expect(state.sends).toEqual(["Hello"]);
    // The draft is kept.
    expect(composer()).toHaveValue("Hello");
  });
});
