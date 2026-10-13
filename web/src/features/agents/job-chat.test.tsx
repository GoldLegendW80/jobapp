import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { FakeEventSource } from "@/test/fake-event-source";
import { makeDetail } from "@/test/jobs";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import {
  jobThreadKey,
  threadKey,
  type ChatMessage,
  type Thread,
  type ThreadDetail,
} from "./chat-api";
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
    // While the answer is pending: Send is disabled, and the hint says what Enter will do.
    await user.type(composer(), "Next question");
    expect(state.sends).toHaveLength(1);
    expect(sendButton()).toBeDisabled();
    expect(
      within(panel).getByText(
        "Enter sends your message once the answer is complete.",
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

  const HOLDING =
    "Your message will be sent when the answer is complete. Edit or clear it to cancel.";

  /** The answer of the chat of `answering()` is stored and its run ends. */
  function settle(
    chat: Awaited<ReturnType<typeof answering>>,
    answer = "It fits.",
  ) {
    chat.state.messages = [
      ...chat.state.messages.map((m) =>
        m.id === 3 ? { ...m, run_status: "done" as const } : m,
      ),
      message(4, "assistant", answer),
    ];
    chat.state.active = null;
    chat.emit("step", chat.stepOf(9, "status", { status: "done" }));
  }

  // Issue #37: the composer is enabled while an answer is pending and while it is being
  // settled (the answer is on screen, the thread's read is still out). Enter was dropped
  // there, silently. It now holds the message, which goes out when the answer is complete.
  it("holds a message sent with Enter while an answer is pending, and sends it afterwards", async () => {
    const chat = await answering();
    await chat.user.type(composer(), "And the salary?{Enter}");
    expect(chat.state.sends).toEqual([]);
    expect(composer()).toHaveValue("And the salary?");
    expect(within(chat.panel).getByText(HOLDING)).toBeInTheDocument();
    settle(chat);
    await waitFor(() => {
      expect(chat.state.sends).toEqual(["And the salary?"]);
    });
    await waitFor(() => {
      expect(composer()).toHaveValue("");
    });
    expect(within(chat.panel).queryByText(HOLDING)).not.toBeInTheDocument();
    expect(
      await within(chat.panel).findByText("And the salary?"),
    ).toBeInTheDocument();
    expect(chat.state.sends).toHaveLength(1); // once
  });

  it("holds the text as it is when the answer is complete, and nothing once it was cleared", async () => {
    const chat = await answering();
    await chat.user.type(composer(), "First idea{Enter}");
    expect(within(chat.panel).getByText(HOLDING)).toBeInTheDocument();
    await chat.user.clear(composer());
    expect(within(chat.panel).queryByText(HOLDING)).not.toBeInTheDocument();
    // What is typed next, even the same words, is a new draft: Enter was not pressed for it.
    await chat.user.type(composer(), "First idea");
    settle(chat);
    await waitFor(() => {
      expect(sendButton()).toBeEnabled();
    });
    expect(chat.state.sends).toEqual([]);
    expect(composer()).toHaveValue("First idea");
  });

  it("drops the hold on any edit after Enter: it is sent only by an Enter on that exact text", async () => {
    const chat = await answering();
    await chat.user.type(composer(), "First idea{Enter}");
    expect(within(chat.panel).getByText(HOLDING)).toBeInTheDocument();
    await chat.user.type(composer(), " and a second one");
    expect(within(chat.panel).queryByText(HOLDING)).not.toBeInTheDocument();
    settle(chat);
    await waitFor(() => {
      expect(sendButton()).toBeEnabled();
    });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(chat.state.sends).toEqual([]);
    expect(composer()).toHaveValue("First idea and a second one");
    // His Enter, on the text as it stands now, sends it.
    await chat.user.keyboard("{Enter}");
    await waitFor(() => {
      expect(chat.state.sends).toEqual(["First idea and a second one"]);
    });
  });

  /** The send of the first message of a chat whose conversation exists, held back until released. */
  function slowSend(state: ReturnType<typeof fakeApi>, fails = false) {
    let release = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.post(
        `*/api/agents/threads/${THREAD}/messages`,
        async ({ request }) => {
          const body = (await request.json()) as { text: string };
          state.sends.push(body.text);
          await gate;
          if (fails) {
            return HttpResponse.json({ detail: "Broken." }, { status: 500 });
          }
          const sent = message(state.nextId++, "user", body.text, {
            run_id: RUN_1,
            run_status: "queued",
          });
          state.messages = [...state.messages, sent];
          state.active = RUN_1;
          return HttpResponse.json(
            { message: sent, run: run(RUN_1) },
            { status: 202 },
          );
        },
      ),
    );
    return release;
  }
  const HOLD_HINT = /will be sent when/;

  // Review of PR #160, B1: a second Enter while the first send is still on its way repeats a
  // message already sent. It must not arm a hold that would send the next draft by itself.
  it("does not hold a second Enter pressed while the send is in flight, nor send the next draft by itself", async () => {
    const state = fakeApi({ thread: true });
    const release = slowSend(state);
    const { user, panel } = await openChat(`/jobs/${JOB}?chat=open`);
    await waitFor(() => {
      expect(composer()).toBeEnabled();
    });
    await user.type(composer(), "First question{Enter}");
    await waitFor(() => {
      expect(state.sends).toEqual(["First question"]);
    });
    await user.keyboard("{Enter}");
    expect(within(panel).queryByText(HOLD_HINT)).not.toBeInTheDocument();
    release();
    await waitFor(() => {
      expect(composer()).toHaveValue("");
    });
    await waitFor(() => {
      expect(streamOf(RUN_1)).toBeDefined();
    });
    await user.type(composer(), "half typed tho");
    expect(within(panel).queryByText(HOLD_HINT)).not.toBeInTheDocument();
    // The answer completes.
    state.messages = [
      ...state.messages.map((m) => ({ ...m, run_status: "done" as const })),
      message(300, "assistant", "Done."),
    ];
    state.active = null;
    act(() => {
      streamOf(RUN_1)!.open();
      streamOf(RUN_1)!.emit("step", {
        seq: 1,
        at: "2026-10-10T08:00:01Z",
        kind: "status",
        data: { status: "done" },
      });
    });
    await waitFor(() => {
      expect(sendButton()).toBeEnabled();
    });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(state.sends).toEqual(["First question"]);
    expect(composer()).toHaveValue("half typed tho");
  });

  it("does not send again by itself a message whose send failed after a second Enter", async () => {
    const state = fakeApi({ thread: true });
    const release = slowSend(state, true);
    const { user, panel } = await openChat(`/jobs/${JOB}?chat=open`);
    await waitFor(() => {
      expect(composer()).toBeEnabled();
    });
    await user.type(composer(), "First question{Enter}");
    await waitFor(() => {
      expect(state.sends).toEqual(["First question"]);
    });
    await user.keyboard("{Enter}");
    release();
    await waitFor(() => {
      expect(sendButton()).toBeEnabled(); // the send failed, the draft is kept
    });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(state.sends).toEqual(["First question"]);
    expect(composer()).toHaveValue("First question");
    expect(within(panel).queryByText(HOLD_HINT)).not.toBeInTheDocument();
  });

  it("sends a message entered while the conversation loads, once it has loaded", async () => {
    const state = fakeApi({
      thread: true,
      messages: [message(1, "user", "Earlier question")],
    });
    let release = () => {};
    const loaded = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get("*/api/agents/threads", async () => {
        await loaded;
        return HttpResponse.json({ items: [thread] });
      }),
    );
    const { user, panel } = await openChat(`/jobs/${JOB}?chat=open`);
    await user.type(composer(), "Hello{Enter}");
    expect(state.sends).toEqual([]);
    expect(composer()).toHaveValue("Hello");
    expect(
      within(panel).getByText(
        "Your message will be sent when the conversation has loaded. Edit or clear it to cancel.",
      ),
    ).toBeInTheDocument();
    release();
    await waitFor(() => {
      expect(state.sends).toEqual(["Hello"]);
    });
    expect(await within(panel).findByText("Hello")).toBeInTheDocument();
    expect(state.opens).toBe(0); // to the conversation that was loading, not a new one
  });

  // Issue #37, point 1: a read of the job's conversation that started before the first
  // message (a refetch) and answers "none" after it must not bring the empty state back.
  it("keeps the new conversation when an older read of the job's conversation answers late", async () => {
    const state = fakeApi();
    let reads = 0;
    let release = () => {};
    const late = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get("*/api/agents/threads", async () => {
        reads += 1;
        if (reads !== 2)
          return HttpResponse.json({ items: state.exists ? [thread] : [] });
        await late;
        return HttpResponse.json({ items: [] }); // as it was before the message
      }),
    );
    const { user, app, panel } = await openChat();
    await within(panel).findByTestId("chat-empty");
    await waitFor(() => {
      expect(composer()).toBeEnabled();
    });
    void app.client.refetchQueries({
      queryKey: jobThreadKey(JOB),
      exact: true,
    });
    await waitFor(() => {
      expect(reads).toBe(2);
    });
    await user.type(composer(), "Does it fit?{Enter}");
    await waitFor(() => {
      expect(composer()).toHaveValue("");
    });
    release();
    await waitFor(() => {
      expect(streamOf(RUN_1)).toBeDefined();
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 60));
    });
    expect(within(panel).queryByTestId("chat-empty")).not.toBeInTheDocument();
    expect(within(panel).getByText("Does it fit?")).toBeInTheDocument();
    expect(within(panel).getByTestId("chat-live-reply")).toBeInTheDocument();
    expect(app.client.getQueryData(jobThreadKey(JOB))).toEqual(thread);
  });

  // Issue #37, point 3: the conversation was deleted in another tab.
  it("starts over when a send finds the conversation deleted somewhere else", async () => {
    const state = fakeApi({
      thread: true,
      messages: [message(1, "user", "Earlier question")],
    });
    server.use(
      http.post(`*/api/agents/threads/${THREAD}/messages`, () =>
        HttpResponse.json(
          { detail: "Conversation not found." },
          { status: 404 },
        ),
      ),
    );
    const { user, panel } = await openChat(`/jobs/${JOB}?chat=open`);
    await within(panel).findByText("Earlier question");
    state.exists = false; // deleted in another tab
    state.messages = [];
    await user.type(composer(), "Still there?{Enter}");
    expect(
      await screen.findByText(
        "This conversation was deleted. Send again to start a new one.",
      ),
    ).toBeInTheDocument();
    // The empty state, not an error that only a reload clears, and the draft is kept.
    expect(await within(panel).findByTestId("chat-empty")).toBeInTheDocument();
    expect(
      within(panel).queryByText("Earlier question"),
    ).not.toBeInTheDocument();
    expect(
      within(panel).queryByText(/could not be loaded/i),
    ).not.toBeInTheDocument();
    expect(composer()).toHaveValue("Still there?");
  });

  it("shows the empty state when a read finds the conversation deleted somewhere else", async () => {
    const state = fakeApi({
      thread: true,
      messages: [message(1, "user", "Earlier question")],
    });
    const { app, panel } = await openChat(`/jobs/${JOB}?chat=open`);
    await within(panel).findByText("Earlier question");
    state.exists = false;
    state.messages = [];
    void app.client.invalidateQueries({ queryKey: threadKey(THREAD) });
    expect(await within(panel).findByTestId("chat-empty")).toBeInTheDocument();
    expect(
      within(panel).queryByText(/could not be loaded/i),
    ).not.toBeInTheDocument();
    expect(app.client.getQueryData(jobThreadKey(JOB))).toBeNull();
  });

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

  /** A chat on RUN_2 being answered, its stream open, with the thread's reads under control. */
  async function answering() {
    const state = fakeApi({
      thread: true,
      messages: [
        message(1, "user", "Earlier question"),
        message(2, "assistant", "Earlier answer"),
        message(3, "user", "Does it fit?", {
          run_id: RUN_2,
          run_status: "running",
        }),
      ],
      active: RUN_2,
    });
    const opened = await openChat(`/jobs/${JOB}?chat=open`);
    await within(opened.panel).findByTestId("chat-live-reply");
    await waitFor(() => {
      expect(streamOf(RUN_2)).toBeDefined();
    });
    const source = streamOf(RUN_2)!;
    act(() => {
      source.open();
      source.emit("step", {
        seq: 1,
        at: "2026-10-10T08:00:01Z",
        kind: "status",
        data: { status: "running" },
      });
    });
    const emit = (name: string, data: unknown) => {
      act(() => {
        source.emit(name, data);
      });
    };
    const stepOf = (seq: number, kind: string, data: object) => ({
      seq,
      at: "2026-10-10T08:00:02Z",
      kind,
      data,
    });
    return { ...opened, state, source, emit, stepOf };
  }

  it("shows the answer growing piece by piece, without announcing the pieces", async () => {
    const { panel, emit, stepOf } = await answering();
    const live = within(panel).getByTestId("chat-live-reply");
    const text = within(panel).getByTestId("chat-live-text");
    const region = within(panel).getByRole("status");
    expect(text).toHaveTextContent("Thinking");
    expect(live).toHaveAttribute("aria-busy", "true");
    expect(live).toHaveAttribute("aria-live", "off");
    // An intermediate state, then a longer one, then the whole text: each is on screen.
    emit("partial", { block: 1, after: 1, offset: 0, text: "It fi" });
    expect(text.textContent).toBe("It fi");
    expect(live).toHaveAttribute("data-writing", "true");
    expect(live).toHaveTextContent("Writing");
    emit("partial", {
      block: 1,
      after: 1,
      offset: 5,
      text: "ts, with 👩\u200d",
    });
    expect(text.textContent).toBe("It fits, with 👩\u200d");
    emit("partial", { block: 1, after: 1, offset: 17, text: "💻 a gap." });
    expect(text.textContent).toBe("It fits, with 👩\u200d💻 a gap.");
    // The same node all along: the bubble is updated in place, not replaced.
    expect(within(panel).getByTestId("chat-live-text")).toBe(text);
    // Nothing was announced: the live region speaks once, for the stored answer.
    expect(region).toBeEmptyDOMElement();
    // The block's stored text takes over without a change on screen.
    emit(
      "step",
      stepOf(2, "text", { text: "It fits, with 👩\u200d💻 a gap." }),
    );
    expect(text.textContent).toBe("It fits, with 👩\u200d💻 a gap.");
    expect(live).not.toHaveAttribute("data-writing");
    expect(region).toBeEmptyDOMElement();
    // The earlier messages never moved or changed.
    const items = within(panel).getAllByTestId("chat-message");
    expect(items.map((item) => item.textContent)).toEqual([
      "You:Earlier question",
      "Agent:Earlier answer",
      "You:Does it fit?",
    ]);
  });

  it("writes what the agent wrote, with no escaping in the growing text", async () => {
    const { panel, emit } = await answering();
    const raw = "a < b && c &lt; d &amp; <b>not bold</b> </message>";
    emit("partial", { block: 1, after: 1, offset: 0, text: raw });
    const text = within(panel).getByTestId("chat-live-text");
    expect(text.textContent).toBe(raw);
    expect(text.querySelector("b")).toBeNull();
  });

  it("keeps the answer on screen from the last piece to the stored message", async () => {
    const { panel, state, emit, stepOf } = await answering();
    emit("partial", { block: 1, after: 1, offset: 0, text: "It fits" });
    // The thread's next read is held back: the run is done, the stored answer not here yet.
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get(`*/api/agents/threads/${THREAD}`, async () => {
        await held;
        return HttpResponse.json({
          ...thread,
          messages: state.messages,
          messages_truncated: false,
          active_run_id: state.active,
        });
      }),
    );
    emit("step", stepOf(2, "text", { text: "It fits well." }));
    emit("step", stepOf(3, "status", { status: "done" }));
    // In between: the final text is there, drawn as an answer, and nothing says "writing".
    expect(within(panel).queryByTestId("chat-live-reply")).toBeNull();
    let answers = within(panel)
      .getAllByTestId("chat-message")
      .filter((item) => item.dataset.role === "assistant");
    expect(answers.map((item) => item.textContent)).toEqual([
      "Agent:Earlier answer",
      "Agent:It fits well.",
    ]);
    const settling = answers[1]!;
    expect(settling).toHaveAttribute("data-settling", "true");
    const box = settling.querySelector("div")!.className;
    // The stored answer arrives: one answer, the same box, announced once.
    state.messages = [
      ...state.messages.map((m) =>
        m.id === 3 ? { ...m, run_status: "done" as const } : m,
      ),
      message(4, "assistant", "It fits well.", { run_id: RUN_2 }),
    ];
    state.active = null;
    release();
    await waitFor(() => {
      expect(
        within(panel).queryByText("It fits well.")?.closest("li"),
      ).not.toHaveAttribute("data-settling");
    });
    answers = within(panel)
      .getAllByTestId("chat-message")
      .filter((item) => item.dataset.role === "assistant");
    expect(answers.map((item) => item.textContent)).toEqual([
      "Agent:Earlier answer",
      "Agent:It fits well.",
    ]);
    expect(answers[1]!.querySelector("div")!.className).toBe(box);
    await waitFor(() => {
      expect(within(panel).getByRole("status")).toHaveTextContent(
        "Answer: It fits well.",
      );
    });
  });

  it.each([
    ["cancelled", "the answer was stopped."],
    ["failed", "the agent failed."],
  ] as const)(
    "drops the unfinished answer when the run is %s in the middle of it",
    async (status, reason) => {
      const { panel, state, emit, stepOf } = await answering();
      emit("partial", { block: 1, after: 1, offset: 0, text: "Half an ans" });
      expect(within(panel).getByTestId("chat-live-text").textContent).toBe(
        "Half an ans",
      );
      state.messages = state.messages.map((m) =>
        m.id === 3 ? { ...m, run_status: status } : m,
      );
      state.active = null;
      emit("step", stepOf(2, "status", { status }));
      expect(within(panel).queryByText("Half an ans")).not.toBeInTheDocument();
      expect(
        await within(panel).findByText(new RegExp(`No answer: ${reason}`)),
      ).toBeInTheDocument();
      expect(within(panel).queryByTestId("chat-live-reply")).toBeNull();
      expect(
        within(panel)
          .getAllByTestId("chat-message")
          .map((item) => item.dataset.role),
      ).toEqual(["user", "assistant", "user"]);
    },
  );

  it("follows the growing answer only while the reader is at the bottom", async () => {
    const { panel, emit } = await answering();
    const list = within(panel).getByTestId("chat-scroll");
    // jsdom lays nothing out: give the list a height and a content that grows with the text.
    let height = 0;
    Object.defineProperty(list, "clientHeight", { get: () => 400 });
    Object.defineProperty(list, "scrollHeight", { get: () => height });
    height = 1000;
    const grow = (offset: number, text: string) => {
      height += 60;
      emit("partial", { block: 1, after: 1, offset, text });
    };
    // At the bottom: each piece brings the list to its new bottom.
    grow(0, "One ");
    expect(list.scrollTop).toBe(1060);
    grow(4, "two ");
    expect(list.scrollTop).toBe(1120);
    // The reader scrolls up to read an earlier message: the view is left alone.
    list.scrollTop = 200;
    grow(8, "three ");
    grow(14, "four ");
    expect(list.scrollTop).toBe(200);
    // Back at the bottom (within a few pixels): it follows again.
    list.scrollTop = height - 400 - 10;
    grow(19, "five");
    expect(list.scrollTop).toBe(height);
  });

  it("goes to the bottom when the reader sends, even from further up", async () => {
    fakeApi({
      thread: true,
      messages: [
        message(1, "user", "Earlier question"),
        message(2, "assistant", "Earlier answer"),
      ],
    });
    const { user, panel } = await openChat(`/jobs/${JOB}?chat=open`);
    await within(panel).findByText("Earlier answer");
    const list = within(panel).getByTestId("chat-scroll");
    Object.defineProperty(list, "clientHeight", { get: () => 400 });
    Object.defineProperty(list, "scrollHeight", { get: () => 2000 });
    list.scrollTop = 100;
    await waitFor(() => {
      expect(composer()).toBeEnabled();
    });
    await user.type(composer(), "A new question{Enter}");
    await within(panel).findByText("A new question");
    await waitFor(() => {
      expect(list.scrollTop).toBe(2000);
    });
  });
});
