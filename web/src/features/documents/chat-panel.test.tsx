import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";

import { createQueryClient } from "@/lib/query";
import {
  fakeCv,
  fakeDocumentOf,
  fakeDocuments,
  fakeVersion,
} from "@/test/documents";
import { server } from "@/test/server";

import { DocumentView } from "./document-view";
import type {
  Consent,
  DocumentOut,
  GenerationDetail,
  Message,
} from "./documents-api";

/** A separator dash: an em dash, an en dash, or a hyphen between spaces. */
const SEPARATOR = /[–—]| - /;

const CV: DocumentOut = {
  ...fakeDocumentOf(fakeCv({ id: "b", name: "Main CV" }), {
    latest: fakeVersion({ number: 1 }),
  }),
  id: "p",
  scope: "posting",
  posting_id: "job-1",
  base_document_id: "b",
  name: null,
};

const AT = "2026-10-19T10:00:00Z";

function message(overrides: Partial<Message>): Message {
  return {
    id: 1,
    channel: "content",
    role: "user",
    text: "shorter summary",
    generation_id: "g-chat",
    version_number: null,
    at: AT,
    ...overrides,
  };
}

function detail(status: GenerationDetail["status"]): GenerationDetail {
  return {
    id: "g-chat",
    status,
    wait_reason: null,
    error: null,
    document_id: "p",
    mode: "chat",
    trigger: "chat",
    outcome: status === "done" ? "draft_replaced" : null,
    version_number: status === "done" ? 2 : null,
    corrections: 0,
    created_at: AT,
    started_at: AT,
    finished_at: status === "done" ? AT : null,
  };
}

/** The chat api of one document: its messages, the generation a message starts, its polls. */
function fakeChat(options: { refuse?: string } = {}) {
  const state = {
    messages: [] as Message[],
    polls: 0,
    sent: [] as unknown[],
  };
  server.use(
    http.get("*/api/documents/p/messages", () =>
      HttpResponse.json({ messages: state.messages }),
    ),
    http.post("*/api/documents/p/messages", async ({ request }) => {
      const body = (await request.json()) as { text: string };
      state.sent.push(body);
      if (options.refuse)
        return HttpResponse.json({ detail: options.refuse }, { status: 409 });
      const made = message({ id: state.messages.length + 1, text: body.text });
      state.messages.push(made);
      return HttpResponse.json(
        {
          message: made,
          generation: {
            id: "g-chat",
            status: "running",
            wait_reason: null,
            error: null,
          },
        },
        { status: 202 },
      );
    }),
    http.get("*/api/document-generations/g-chat", () => {
      state.polls += 1;
      if (state.polls < 2) return HttpResponse.json(detail("running"));
      if (!state.messages.some((one) => one.role === "assistant")) {
        state.messages.push(
          message({
            id: state.messages.length + 1,
            role: "assistant",
            text: "Changed the summary.\nThis is version 2.",
            version_number: 2,
          }),
        );
      }
      return HttpResponse.json(detail("done"));
    }),
  );
  return state;
}

async function openChat(consent: Partial<Consent> = {}) {
  const api = fakeDocuments({ consent });
  api.documents.set("p", CV);
  api.versions.set("p", [fakeVersion({ number: 1 })]);
  const user = userEvent.setup();
  const client = createQueryClient();
  const router = createMemoryRouter(
    [{ path: "/doc", element: <DocumentView documentId="p" /> }],
    { initialEntries: ["/doc"] },
  );
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  await user.click(await screen.findByRole("tab", { name: "Chat" }));
  const chat = await screen.findByTestId("document-chat");
  return { api, user, chat, client };
}

describe("the Chat view", () => {
  it("sends a message, follows its generation, then shows the answer and its version", async () => {
    const state = fakeChat();
    const { user, chat, api } = await openChat({
      accepted: true,
      model: "claude-opus-5-5",
    });
    expect(within(chat).getByTestId("document-chat-empty")).toBeVisible();
    await user.type(
      within(chat).getByRole("textbox", { name: "Message" }),
      "shorter summary{Enter}",
    );
    expect(state.sent).toEqual([
      { text: "shorter summary", channel: "content" },
    ]);
    await waitFor(
      () => {
        expect(
          within(chat).getAllByTestId("document-chat-message").at(-1),
        ).toHaveTextContent("Changed the summary. This is version 2.Version 2");
      },
      { timeout: 10_000 },
    );
    expect(within(chat).queryByTestId("document-chat-writing")).toBeNull();
    expect(within(chat).getByRole("textbox", { name: "Message" })).toHaveValue(
      "",
    );
    // The document and its versions are read again for the new version.
    await waitFor(() => {
      expect(
        api.requests.filter((one) => one.path === "/api/documents/p").length,
      ).toBeGreaterThan(1);
    });
    expect(chat.textContent).not.toMatch(SEPARATOR);
  }, 15_000);

  it("waits for the consent: nothing is sent before it", async () => {
    const state = fakeChat();
    const { chat } = await openChat({
      accepted: false,
      model: "claude-opus-5-5",
    });
    expect(
      await within(chat).findByRole("button", { name: "Read and confirm" }),
    ).toBeVisible();
    expect(
      within(chat).getByRole("textbox", { name: "Message" }),
    ).toBeDisabled();
    expect(within(chat).getByRole("button", { name: "Send" })).toBeDisabled();
    expect(state.sent).toEqual([]);
  });

  it("without a model credential, points to the Account page", async () => {
    fakeChat();
    const { chat } = await openChat({ accepted: true, model: null });
    expect(
      await within(chat).findByRole("link", {
        name: "Add one on the Account page",
      }),
    ).toHaveAttribute("href", "/account");
    expect(
      within(chat).getByRole("textbox", { name: "Message" }),
    ).toBeDisabled();
  });

  it("shows why a message was refused and keeps what was typed", async () => {
    fakeChat({
      refuse:
        "The assistant is still answering the last message of this document.",
    });
    const { user, chat } = await openChat({
      accepted: true,
      model: "claude-opus-5-5",
    });
    const box = within(chat).getByRole("textbox", { name: "Message" });
    await user.type(box, "and the headline{Enter}");
    expect(
      await within(chat).findByText(
        "The assistant is still answering the last message of this document.",
      ),
    ).toBeVisible();
    expect(box).toHaveValue("and the headline");
  });

  it("counts the characters against the limit", async () => {
    fakeChat();
    const { user, chat } = await openChat({
      accepted: true,
      model: "claude-opus-5-5",
    });
    const box = within(chat).getByRole("textbox", { name: "Message" });
    expect(box).toHaveAttribute("maxLength", "2000");
    await user.type(box, "abc");
    expect(within(chat).getByText("3 of 2000")).toBeVisible();
  });
});
