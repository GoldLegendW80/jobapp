import type { Meta, StoryObj } from "@storybook/react-vite";
import { HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { EmailApplication } from "@/components/shared/apply/email-application";
import {
  EMAIL_CLAIM,
  emailDraft,
  fakeEmailApplication,
} from "@/design-system/fixtures/shared-apply";
import { withApp } from "@/design-system/with-app";
import { APP_ID } from "@/test/apply";

const meta = {
  title: "Shared/Apply/Email application",
  component: EmailApplication,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "An application sent by e-mail, shown whole before approval: recipient, subject and message, a fact check to look at, and Check Sent when the app could not confirm the send. In Your application on a posting that asks for an e-mail.",
      },
    },
  },
  args: { applicationId: APP_ID },
  beforeEach: () => {
    fakeEmailApplication(emailDraft());
  },
} satisfies Meta<typeof EmailApplication>;

export default meta;
type Story = StoryObj<typeof meta>;

const checkSent = async ({ canvasElement }: { canvasElement: HTMLElement }) => {
  await userEvent.click(
    await within(canvasElement).findByRole("button", { name: "Check Sent" }),
  );
};

/** The message as it will be sent, read before approving. */
export const ReadTheMessage: Story = {
  name: "Read the message before approving",
};

/** No recipient on the posting yet: the person chooses one below. */
export const NoRecipientYet: Story = {
  name: "No recipient yet",
  beforeEach: () => {
    fakeEmailApplication(emailDraft({ to: [] }));
  },
};

/** The fact check flagged a claim: the message is reviewed and the answer edited before approving. */
export const NeedsAFactCheck: Story = {
  name: "Needs a fact check",
  beforeEach: () => {
    fakeEmailApplication(emailDraft({ issues: [EMAIL_CLAIM] }));
  },
};

/** The send was refused: the server's sentence says what to do. */
export const SendRefused: Story = {
  name: "The send was refused",
  beforeEach: () => {
    fakeEmailApplication(
      emailDraft({
        send_id: "send-1",
        message: "Reconnect Gmail in Account to allow sending.",
      }),
    );
  },
};

/** The app could not confirm the send: look in Sent, then It was submitted or Try again. */
export const SendNotConfirmed: Story = {
  name: "The send could not be confirmed",
  beforeEach: () => {
    fakeEmailApplication(
      emailDraft({ status: "send_unknown", send_id: "send-1" }),
    );
  },
};

/** Check Sent looks in the Sent folder; every read is made again after. */
export const CheckSent: Story = {
  name: "Check Sent",
  beforeEach: () => {
    fakeEmailApplication(
      emailDraft({ status: "send_unknown", send_id: "send-1" }),
    );
  },
  play: checkSent,
};

/** Check Sent fails: its error shows under the button. */
export const CheckSentFails: Story = {
  name: "Check Sent fails",
  beforeEach: () => {
    fakeEmailApplication(
      emailDraft({ status: "send_unknown", send_id: "send-1" }),
      () =>
        HttpResponse.json(
          { detail: "Gmail could not be reached. Try again in a minute." },
          { status: 503 },
        ),
    );
  },
  play: checkSent,
};

/** A long message scrolls inside its box; a long address breaks. */
export const LongMessage: Story = {
  name: "Long message",
  beforeEach: () => {
    const draft = emailDraft();
    fakeEmailApplication(
      emailDraft({
        to: [
          "recruitment.data.platform.team@careers.lumen-freight-logistics.example.org",
        ],
        body: `${draft.body}\n\n${"P.S. I would be glad to walk you through the pipeline I run today, its dbt models and how we test them before each release. ".repeat(8)}`,
      }),
    );
  },
};

/** Still being written: nothing shows until the message exists. */
export const StillPreparing: Story = {
  name: "Still being written, nothing shows",
  beforeEach: () => {
    fakeEmailApplication(emailDraft({ status: "preparing" }));
  },
};

/** On a phone. */
export const OnAPhone: Story = {
  name: "On a phone",
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
