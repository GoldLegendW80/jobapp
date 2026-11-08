import { useId } from "react";
import { Link } from "react-router";

import { AdvancedSection } from "@/components/blocks/advanced-section";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { PlaceSuggestion } from "@/components/shared/profile/title-suggestions";
import { WORDING } from "@/lib/wording";

import {
  splitTerms,
  type PanelProps,
} from "@/features/onboarding/helpers/steps";
import { REMOTE_WORDS } from "@/features/onboarding/helpers/words";
import { ONE_BOX, Radios } from "./choice-cards";
import { TextAnswer } from "./text-answer";

/** Step 5, Where: anywhere, or the places; then remote, hybrid or on site. */
export function StepWhere({ draft, change, patterns, problem }: PanelProps) {
  const id = useId();
  const places = (
    <TextAnswer
      fieldId={`${id}-places`}
      label={WORDING.places}
      hint="Cities, regions or countries, separated by commas, for example Lyon, Remote Europe."
      value={draft.locations}
      readOnly={patterns?.places}
      onChange={(locations) => {
        change({ locations });
      }}
    />
  );
  return (
    <div className="flex flex-col gap-6">
      <Label htmlFor={id} className={ONE_BOX}>
        <Checkbox
          id={id}
          checked={draft.anywhere}
          onCheckedChange={(checked) => {
            change({ anywhere: checked === true });
          }}
        />
        {WORDING.anywhere}
      </Label>
      {draft.anywhere ? null : patterns?.places ? (
        <AdvancedSection
          pageKey="welcome-where"
          errorFieldId={
            problem && !draft.locations.trim() ? `${id}-places` : undefined
          }
        >
          {places}
          <Link className="text-sm underline" to="/settings/search">
            {WORDING.searchOptions}
          </Link>
        </AdvancedSection>
      ) : (
        <div className="flex flex-col gap-3">
          {places}
          <PlaceSuggestion
            label={WORDING.places}
            current={splitTerms(draft.locations)}
            onAdd={(place) => {
              change({
                locations: [...splitTerms(draft.locations), place].join(", "),
              });
            }}
          />
        </div>
      )}
      <Radios
        legend={WORDING.remoteHybridOnSite}
        options={["remote", "hybrid", "office", "any"] as const}
        words={(value) => REMOTE_WORDS[value]}
        value={draft.remote}
        onChange={(remote) => {
          change({ remote });
        }}
      />
    </div>
  );
}
