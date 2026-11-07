import type { Meta, StoryObj } from "@storybook/react-vite";

import { PostingTerms } from "@/components/shared/jobs/posting/posting-terms";

const meta = {
  title: "Shared/Jobs/Posting terms",
  component: PostingTerms,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The competencies the person has for a posting, in green, or its gaps, in amber, as a titled row: in the Posting section of a posting. Nothing shows when the list is empty.",
      },
    },
  },
  args: {
    label: "Competencies",
    terms: ["Python", "dbt", "Kafka", "Airflow"],
    tone: "have",
  },
} satisfies Meta<typeof PostingTerms>;

export default meta;
type Story = StoryObj<typeof meta>;

/** What the person brings that the posting asks for. */
export const Competencies: Story = { name: "Competencies" };

/** What the posting asks for that the profile does not show. */
export const Gaps: Story = {
  name: "Gaps",
  args: {
    label: "Gaps",
    terms: ["Scala", "Healthcare data standards"],
    tone: "gap",
  },
};

/** Many terms wrap in a narrow column. */
export const Many: Story = {
  name: "Many terms on a phone",
  args: {
    terms: [
      "Python",
      "SQL",
      "dbt",
      "Kafka",
      "Airflow",
      "Terraform",
      "Kubernetes",
      "Data modelling",
      "Stream processing",
      "Cost monitoring",
      "Mentoring",
    ],
  },
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
