import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { fn, userEvent, within } from "storybook/test";

import { ApproveDialog } from "@/components/shared/apply/approve-dialog";
import { Button } from "@/components/ui/button";
import { FILLED, ONE_LEFT } from "@/design-system/fixtures/applications";
import {
  approvalOf,
  emailApproval,
  OPTIONAL_LEFT,
} from "@/design-system/fixtures/shared-apply";

const meta = {
  title: "Shared/Apply/Approve dialog",
  component: ApproveDialog,
  tags: ["autodocs"],
  parameters: {
    docs: {
      // Each story in its own frame on the docs page, so an open dialog covers only its own.
      story: { inline: false, height: "640px" },
      description: {
        component:
          "The last confirmation before an application is sent: the company, the role, the documents with their versions and what stays empty; for an e-mail, the recipient and every claim to confirm. From Approve and submit in Your application and in the review queue.",
      },
    },
  },
  args: {
    open: true,
    onOpenChange: fn(),
    company: "Lumen Freight",
    role: "Senior data engineer",
    approval: approvalOf(FILLED),
    pending: false,
    onConfirm: fn(),
  },
} satisfies Meta<typeof ApproveDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

const tick = (label: RegExp) => async () => {
  const page = within(document.body);
  await userEvent.click(await page.findByRole("checkbox", { name: label }));
};

/** A form filled from the profile: the documents it sends, nothing left empty. */
export const ApproveAFilledForm: Story = {
  name: "Approve a filled form",
};

/** An optional question and a file stay empty: the dialog names them in amber before the click. */
export const SomeFieldsLeftEmpty: Story = {
  name: "Some fields are sent empty",
  args: { approval: approvalOf(OPTIONAL_LEFT) },
};

/** A form that takes no document says so. */
export const NoDocument: Story = {
  name: "No document is attached",
  args: { approval: approvalOf(ONE_LEFT.slice(0, 3), { documents: {} }) },
};

/** After the click, both buttons wait for the server and the dialog cannot be closed. */
export const Submitting: Story = {
  name: "Submitting",
  args: { pending: true },
};

/** An application by e-mail: the message in full, and Approve waits for the new recipient to be confirmed. */
export const EmailConfirmTheRecipient: Story = {
  name: "E-mail, confirm the recipient",
  args: { approval: emailApproval() },
};

/** The recipient is confirmed: Approve and submit can be clicked. */
export const EmailRecipientConfirmed: Story = {
  name: "E-mail, recipient confirmed",
  args: { approval: emailApproval() },
  play: tick(/I confirm this new email recipient/),
};

/** The fact check flagged a claim the person wrote: each claim is ticked after checking it against the profile. */
export const EmailClaimsToCheck: Story = {
  name: "E-mail, a claim to check",
  args: { approval: emailApproval({ claims: true }) },
  play: tick(/I confirm this new email recipient/),
};

/** The assistant wrote the claim: it cannot be ticked away, the e-mail answer must be edited first. */
export const EmailClaimsWrittenByTheAssistant: Story = {
  name: "E-mail, a claim the assistant wrote",
  args: { approval: emailApproval({ claims: true, writtenBy: "agent" }) },
  play: tick(/I confirm this new email recipient/),
};

/** No recipient was found on the posting: nothing can be sent until one is chosen. */
export const EmailWithoutRecipient: Story = {
  name: "E-mail without a recipient",
  args: { approval: emailApproval({ to: null }) },
};

/** A long company name and role wrap in the title. */
export const LongNames: Story = {
  name: "Long company and role",
  args: {
    company: "Compagnie Générale de Logistique Fluviale et Ferroviaire",
    role: "Staff data platform engineer, streaming, pricing and real time forecasting",
  },
};

/** Closed, then opened by the button the page shows, as in Your application. */
export const OpenFromTheButton: Story = {
  name: "Open it from Approve and submit",
  args: { open: false },
  render: function Render(args) {
    const [open, setOpen] = useState(false);
    return (
      <>
        <Button
          onClick={() => {
            setOpen(true);
          }}
        >
          Approve and submit
        </Button>
        <ApproveDialog
          {...args}
          open={open}
          onOpenChange={setOpen}
          onConfirm={() => {
            setOpen(false);
          }}
        />
      </>
    );
  },
};
