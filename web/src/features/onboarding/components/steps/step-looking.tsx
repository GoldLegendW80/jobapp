import { useId } from "react";
import { Link } from "react-router";

import { AdvancedSection } from "@/components/blocks/advanced-section";
import { COMPANY_TYPES, SENIORITIES } from "@/features/profile/api/profile-api";
import { TitleSuggestions } from "@/components/shared/profile/title-suggestions";
import { WORDING } from "@/lib/wording";

import {
  splitTerms,
  type PanelProps,
} from "@/features/onboarding/helpers/steps";
import {
  COMPANY_WORDS,
  SENIORITY_WORDS,
} from "@/features/onboarding/helpers/words";
import { Choices } from "./choice-cards";
import { TextAnswer } from "./text-answer";

/** Step 2, What you are looking for: the roles, and the rest of the targeting under Advanced. */
export function StepLooking({ draft, change, patterns, problem }: PanelProps) {
  const rolesId = useId();
  const roles = (
    <TextAnswer
      fieldId={rolesId}
      label={WORDING.roles}
      hint="Separate them with commas, for example Data engineer, Analytics engineer."
      value={draft.roles}
      onChange={(roles) => {
        change({ roles });
      }}
      readOnly={patterns?.roles}
    />
  );
  return (
    <div className="flex flex-col gap-6">
      {patterns?.roles ? (
        <p className="text-sm text-muted-foreground">
          {WORDING.savedPatternHint}
        </p>
      ) : (
        <div className="flex flex-col gap-3">
          {roles}
          {/* The job titles agent's ideas, once there is a key and something to read. */}
          <TitleSuggestions
            quiet
            label={WORDING.roles}
            current={splitTerms(draft.roles)}
            onAdd={(title) => {
              change({ roles: [...splitTerms(draft.roles), title].join(", ") });
            }}
          />
        </div>
      )}
      <AdvancedSection
        pageKey="welcome-looking"
        errorFieldId={
          patterns?.roles && problem && !draft.roles.trim()
            ? rolesId
            : undefined
        }
      >
        {patterns?.roles ? (
          <>
            {roles}
            <Link className="text-sm underline" to="/settings/search">
              {WORDING.searchOptions}
            </Link>
          </>
        ) : null}
        <Choices
          legend={WORDING.seniority}
          options={SENIORITIES}
          words={(value) => SENIORITY_WORDS[value]}
          chosen={draft.seniority}
          onChange={(seniority) => {
            change({ seniority });
          }}
        />
        <TextAnswer
          label={WORDING.industriesOptional}
          hint="For example Healthcare, Energy. They raise the fit of postings that name them, and hide none."
          value={draft.industries}
          onChange={(industries) => {
            change({ industries });
          }}
        />
        <Choices
          legend={WORDING.companyTypesOptional}
          options={COMPANY_TYPES}
          words={(value) => COMPANY_WORDS[value]}
          chosen={draft.companyTypes}
          onChange={(companyTypes) => {
            change({ companyTypes });
          }}
        />
      </AdvancedSection>
    </div>
  );
}
