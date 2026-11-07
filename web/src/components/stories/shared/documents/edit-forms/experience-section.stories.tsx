import type { Meta, StoryObj } from "@storybook/react-vite";

import { ExperienceSection } from "@/components/shared/documents/edit-forms/experience-section";
import {
  CAMILLE_FACTS,
  DocumentForm,
} from "@/design-system/fixtures/document-form";
import { CV_DRAFT } from "@/design-system/fixtures/documents";

const meta = {
  title: "Shared/Documents/Experience fields",
  component: ExperienceSection,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The experience of a CV opened in place on its page: one entry per employer of the profile, whose bullets the person writes, reordered or removed, and an employer of the profile added.",
      },
    },
  },
  decorators: [
    (Story) => (
      <div className="max-w-xl">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof ExperienceSection>;

export default meta;
// Each story renders its fields around a form of its own: no args.
type Story = StoryObj;

/** Two employers and their bullets. */
export const Typical: Story = {
  name: "Two employers",
  render: () => (
    <DocumentForm initial={CV_DRAFT}>
      {(form) => (
        <ExperienceSection
          form={form}
          employers={CAMILLE_FACTS.employers}
          known
        />
      )}
    </DocumentForm>
  ),
};

/** An employer that left the profile: its bullets kept, and why nothing prints above them. */
export const EmployerGone: Story = {
  name: "An employer no longer in the profile",
  render: () => (
    <DocumentForm
      initial={{
        experience: [
          { employer_key: "gone-works", bullets: ["Kept as written"] },
        ],
      }}
    >
      {(form) => (
        <ExperienceSection
          form={form}
          employers={CAMILLE_FACTS.employers}
          known
        />
      )}
    </DocumentForm>
  ),
};

/** No employer yet: add one of the profile. */
export const Empty: Story = {
  name: "No employer yet",
  render: () => (
    <DocumentForm initial={{ experience: [] }}>
      {(form) => (
        <ExperienceSection
          form={form}
          employers={CAMILLE_FACTS.employers}
          known
        />
      )}
    </DocumentForm>
  ),
};
