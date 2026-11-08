import { FieldDescription, FieldLegend, FieldSet } from "@/components/ui/field";
import { CONTRACT_TYPES } from "@/features/profile/api/profile-api";
import { useProfile } from "@/features/profile/api/use-profile";
import { contractWord, WORDING } from "@/lib/wording";

import type {
  AuthorizationDraft,
  PanelProps,
  YesNo,
} from "@/features/onboarding/helpers/steps";
import { CHOICE_CARD, Choices, Radios } from "./choice-cards";
import { Picks } from "./picks";
import { TextAnswer } from "./text-answer";

/** The contracts, one group whose last box is Freelance mission; a code this build does not
 * offer stays saved as it was. */
const CONTRACT_ORDER: readonly string[] = [
  ...CONTRACT_TYPES.filter((type) => type !== "Freelance"),
  "Freelance",
];

const NOTICE_PICKS = [
  { value: "None" },
  { value: "1 month" },
  { value: "3 months" },
];
const START_PICKS = [
  { value: "As soon as possible" },
  { value: "After my notice period" },
];
const YES_NO = ["yes", "no"] as const;
const YES_NO_WORDS: Record<YesNo, string> = { yes: "Yes", no: "No" };

/** Step 7, How you want to work: the contracts, when the person can start, and where they may
 * work, the answers application forms ask. Every one may stay empty. */
export function StepWork({ draft, change }: PanelProps) {
  const authorization = draft.authorization;
  const setAuthorization = (patch: Partial<AuthorizationDraft>) => {
    change({ authorization: { ...authorization, ...patch } });
  };
  // The profile the page already loaded: never a request of its own.
  const home = useProfile({ enabled: false }).data?.sections.identity.country;
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <Choices
          legend={WORDING.contractsChooseAny}
          options={CONTRACT_ORDER}
          words={contractWord}
          chosen={draft.contracts.filter((type) =>
            CONTRACT_ORDER.includes(type),
          )}
          onChange={(chosen) => {
            change({
              contracts: [
                ...draft.contracts.filter(
                  (type) => !CONTRACT_ORDER.includes(type),
                ),
                ...chosen,
              ],
            });
          }}
        />
        <p className="text-xs text-muted-foreground">
          None ticked: any contract.
        </p>
      </div>
      <div className="grid items-start gap-4 sm:grid-cols-2">
        <TextAnswer
          label={WORDING.noticePeriod}
          hint="How long before you can leave your current role."
          value={draft.notice}
          onChange={(notice) => {
            change({ notice });
          }}
        >
          {draft.notice.trim() ? null : (
            <Picks
              label={WORDING.noticePeriod}
              options={NOTICE_PICKS}
              onPick={(notice) => {
                change({ notice });
              }}
            />
          )}
        </TextAnswer>
        <TextAnswer
          label={WORDING.earliestStart}
          hint="For example January 2027."
          maxLength={200}
          value={draft.start}
          onChange={(start) => {
            change({ start });
          }}
        >
          {draft.start.trim() ? null : (
            <Picks
              label={WORDING.earliestStart}
              options={START_PICKS}
              onPick={(start) => {
                change({ start });
              }}
            />
          )}
        </TextAnswer>
      </div>
      <FieldSet className={CHOICE_CARD}>
        <FieldLegend variant="label" className="mb-0">
          {WORDING.workAuthorisation}
        </FieldLegend>
        <FieldDescription className="mb-2 text-xs">
          What application forms get when they ask whether you may work in a
          country.
        </FieldDescription>
        <TextAnswer
          label={WORDING.countryOrRegion}
          hint="Where you want to work. Add more countries later in your profile."
          maxLength={200}
          value={authorization.country}
          onChange={(country) => {
            // The stored answer was about the country first shown.
            setAuthorization({ country, kept: "" });
          }}
        >
          {home?.trim() && !authorization.country.trim() ? (
            <Picks
              label={WORDING.countryOrRegion}
              options={[{ value: home.trim(), words: "from your profile" }]}
              onPick={(country) => {
                setAuthorization({ country, kept: "" });
              }}
            />
          ) : null}
        </TextAnswer>
        <div className="grid items-start gap-4 sm:grid-cols-2">
          <Radios
            legend={WORDING.mayWorkThere}
            options={YES_NO}
            inline
            words={(value) => YES_NO_WORDS[value]}
            value={authorization.allowed}
            onChange={(allowed) => {
              setAuthorization({ allowed });
            }}
          />
          <Radios
            legend={WORDING.needsSponsorship}
            options={YES_NO}
            inline
            words={(value) => YES_NO_WORDS[value]}
            value={authorization.sponsorship}
            onChange={(sponsorship) => {
              setAuthorization({ sponsorship });
            }}
          />
        </div>
      </FieldSet>
    </div>
  );
}
