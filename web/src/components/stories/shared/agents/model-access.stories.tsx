import type { Meta, StoryObj } from "@storybook/react-vite";
import { delay, http, HttpResponse } from "msw";

import { ModelAccess } from "@/components/shared/agents/model-access";
import type { ModelCredential } from "@/features/agents/api/agents-api";
import { withApp } from "@/design-system/with-app";
import { server } from "@/test/server";

const PATH = "*/api/me/model-credential";

const KEY: ModelCredential = {
  kind: "anthropic_api_key",
  last4: "WXyz",
  verified_at: null,
  updated_at: "2026-11-07T08:00:00Z",
};

/** The credential saved, and what the check with Anthropic answers. */
function fake(
  saved: ModelCredential | null,
  verify: () => Response | Promise<Response>,
) {
  server.use(
    http.get(PATH, () =>
      saved
        ? HttpResponse.json(saved)
        : HttpResponse.json({ detail: "none" }, { status: 404 }),
    ),
    http.post(`${PATH}/verify`, verify),
  );
}

const accepted = () =>
  HttpResponse.json({
    result: "verified",
    message: "Anthropic accepted this key.",
    credential: { ...KEY, verified_at: "2026-11-07T08:01:00Z" },
  });

const meta = {
  title: "Shared/Agents/Model access",
  component: ModelAccess,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "The Claude credential the agents run on, in one field: Account's Model access and the wizard's first step. Saved, it is checked with Anthropic at once, a spinner in the field, which then turns green, or red with the reason. Remove asks first.",
      },
    },
  },
} satisfies Meta<typeof ModelAccess>;

export default meta;
type Story = StoryObj<typeof meta>;

export const NothingSaved: Story = {
  name: "Nothing saved",
  beforeEach: () => {
    fake(null, accepted);
  },
};

/** The check is running: the spinner in the field. */
export const Checking: Story = {
  beforeEach: () => {
    fake(KEY, async () => {
      await delay("infinite");
      return HttpResponse.json({});
    });
  },
};

/** Anthropic accepted the key: the field is green. */
export const Accepted: Story = {
  beforeEach: () => {
    fake(KEY, accepted);
  },
};

/** Anthropic refused the key: the field is red, with the reason. */
export const Refused: Story = {
  beforeEach: () => {
    fake(KEY, () =>
      HttpResponse.json(
        {
          detail:
            "Anthropic refused this credential: it is wrong, revoked or has no access. Paste a new one.",
        },
        { status: 400 },
      ),
    );
  },
};

const TOKEN: ModelCredential = {
  ...KEY,
  kind: "claude_subscription",
  last4: "Tok3",
};

/** A subscription token is checked by one Claude Code run in the worker: the spinner meanwhile. */
export const TokenChecking: Story = {
  name: "Subscription token, checking",
  beforeEach: () => {
    fake(
      {
        ...TOKEN,
        check: {
          state: "checking",
          message: "Checking the token with Claude Code.",
        },
      },
      accepted,
    );
  },
};

/** Claude Code answered with the token: green. */
export const TokenAccepted: Story = {
  name: "Subscription token, accepted",
  beforeEach: () => {
    fake(
      {
        ...TOKEN,
        verified_at: "2026-11-07T08:02:00Z",
        check: {
          state: "verified",
          message: "Claude Code answered with this token.",
        },
      },
      accepted,
    );
  },
};

/** The token was refused: red, with the reason. */
export const TokenRefused: Story = {
  name: "Subscription token, refused",
  beforeEach: () => {
    fake(
      {
        ...TOKEN,
        check: {
          state: "invalid",
          message:
            "Anthropic refused this credential: it is wrong, revoked or has no access. Paste a new one.",
        },
      },
      accepted,
    );
  },
};
