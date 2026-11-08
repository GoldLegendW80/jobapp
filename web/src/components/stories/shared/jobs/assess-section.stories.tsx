import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";

import { AssessSection } from "@/components/shared/jobs/assess/assess-section";
import {
  ASSESSING,
  assessmentView,
  fakeAssessment,
  NEVER_ASSESSED,
} from "@/design-system/fixtures/posting-assessment";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";
import { server } from "@/test/server";

const meta = {
  title: "Shared/Jobs/Assess this posting",
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "A section of a posting's side rail, under its documents: Assess starts an agent on the person's own credential, a line follows its run, and once it ends, the verdict, how sure it is, the gaps to prepare and the keyword fit, which the assessment never changes. The whole assessment is folded under the posting.",
      },
      story: { inline: false, height: "420px" },
    },
  },
  render: () => (
    <div className="max-w-sm">
      <AssessSection job={POSTING_DETAIL} />
    </div>
  ),
  beforeEach: () => {
    fakeAssessment();
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** Assessed: the verdict, how sure, the gaps and the keyword fit side by side. */
export const Assessed: Story = {};

/** Never assessed: only the button and what it does. */
export const NeverAssessed: Story = {
  beforeEach: () => {
    fakeAssessment(NEVER_ASSESSED);
  },
};

/** The agent at work: the button waits and a line says so. */
export const Assessing: Story = {
  beforeEach: () => {
    fakeAssessment(ASSESSING);
  },
};

/** The last run failed: its reason shows, and the assessment from before stays. */
export const LastRunFailed: Story = {
  beforeEach: () => {
    fakeAssessment(
      assessmentView({
        run: {
          id: "88888888-8888-4888-8888-888888888888",
          status: "failed",
          error: "The run stopped reporting and was marked failed.",
        },
      }),
    );
  },
};

/** No model credential: the button is off and the line says where to add one. */
export const NoCredential: Story = {
  beforeEach: () => {
    fakeAssessment(NEVER_ASSESSED);
    server.use(
      http.get("*/api/me/model-credential", () =>
        HttpResponse.json({ detail: "none" }, { status: 404 }),
      ),
    );
  },
};
