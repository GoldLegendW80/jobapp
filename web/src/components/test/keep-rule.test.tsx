import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { KeepRule } from "@/components/shared/documents/chat-panel/keep-rule";
import { CHAT_MESSAGES } from "@/design-system/fixtures/documents";
import { createQueryClient } from "@/lib/query";
import { server } from "@/test/server";

import type { Message } from "@/features/documents/api/chat-api";

const SEPARATOR = /[–—]| - /;

const ASKED: Message = {
  ...CHAT_MESSAGES[0]!,
  id: 7,
  text: "Never say leveraged",
  rule: "Never say leveraged",
};
const ANSWERED: Message = { ...CHAT_MESSAGES[1]!, id: 8 };

function fakeKeep(answer: (text: string) => Response) {
  const sent: { path: string; text: string }[] = [];
  server.use(
    http.post(
      "*/api/documents/:id/messages/:message/rule",
      async ({ request, params }) => {
        const body = (await request.json()) as { text: string };
        sent.push({
          path: `${String(params.id)}/${String(params.message)}`,
          text: body.text,
        });
        return answer(body.text);
      },
    ),
  );
  return sent;
}

function show(messages: Message[], answering = false) {
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={createQueryClient()}>
      <KeepRule documentId="doc-1" messages={messages} answering={answering} />
    </QueryClientProvider>,
  );
  return user;
}

describe("KeepRule", () => {
  it("offers nothing for a message that is no correction, or while it is answered", () => {
    const { unmount } = render(
      <QueryClientProvider client={createQueryClient()}>
        <KeepRule
          documentId="doc-1"
          messages={CHAT_MESSAGES}
          answering={false}
        />
      </QueryClientProvider>,
    );
    expect(screen.queryByTestId("rule-offer")).toBeNull();
    unmount();
    show([ASKED], true);
    expect(screen.queryByTestId("rule-offer")).toBeNull();
  });

  it("writes nothing before Keep, then keeps the rule as edited", async () => {
    const sent = fakeKeep((text) =>
      HttpResponse.json({ rule: text, kept: true }),
    );
    const user = show([ASKED, ANSWERED]);
    await user.click(screen.getByRole("button", { name: "Keep as a rule" }));
    const box = screen.getByRole("textbox", { name: "Your writing rule" });
    expect(box).toHaveValue("Never say leveraged");
    expect(box).toHaveAccessibleDescription(
      /^From your message: “Never say leveraged”\./,
    );
    expect(sent).toHaveLength(0);
    await user.clear(box);
    await user.type(box, "Never write leveraged");
    await user.click(screen.getByRole("button", { name: "Keep" }));
    expect(await screen.findByTestId("rule-kept")).toHaveTextContent(
      "Kept in your writing rules: “Never write leveraged”.",
    );
    expect(sent).toEqual([{ path: "doc-1/7", text: "Never write leveraged" }]);
    expect(document.body.textContent).not.toMatch(SEPARATOR);
  });

  it("says when the rule was there already", async () => {
    fakeKeep((text) => HttpResponse.json({ rule: text, kept: false }));
    const user = show([ASKED, ANSWERED]);
    await user.click(screen.getByRole("button", { name: "Keep as a rule" }));
    await user.click(screen.getByRole("button", { name: "Keep" }));
    expect(await screen.findByTestId("rule-kept")).toHaveTextContent(
      "This rule is in your writing rules already.",
    );
  });

  it("shows the api's sentence when the rule states a fact, and keeps the box", async () => {
    fakeKeep(() =>
      HttpResponse.json(
        {
          detail: "A writing rule says how to write, not what is true of you.",
        },
        { status: 422 },
      ),
    );
    const user = show([ASKED, ANSWERED]);
    await user.click(screen.getByRole("button", { name: "Keep as a rule" }));
    await user.click(screen.getByRole("button", { name: "Keep" }));
    expect(await screen.findByText(/not what is true of you/)).toBeVisible();
    expect(
      screen.getByRole("textbox", { name: "Your writing rule" }),
    ).toBeVisible();
  });

  it("goes away on Not now", async () => {
    const user = show([ASKED, ANSWERED]);
    await user.click(screen.getByRole("button", { name: "Not now" }));
    expect(screen.queryByTestId("rule-offer")).toBeNull();
  });
});
