import { CONTRACT_TYPES } from "@/features/profile/api/profile-api";
import { contractWord, WORDING } from "@/lib/wording";

import type { PanelProps } from "@/features/onboarding/helpers/steps";
import { Choices } from "./choice-cards";

/** The contracts, one group whose last box is Freelance mission; a code this build does not
 * offer stays saved as it was. */
const CONTRACT_ORDER: readonly string[] = [
  ...CONTRACT_TYPES.filter((type) => type !== "Freelance"),
  "Freelance",
];

/** Step 5, How you want to work: the contracts. */
export function StepWork({ draft, change }: PanelProps) {
  return (
    <div className="flex flex-col gap-4">
      <Choices
        legend={WORDING.contractsChooseAny}
        options={CONTRACT_ORDER}
        words={contractWord}
        chosen={draft.contracts.filter((type) => CONTRACT_ORDER.includes(type))}
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
    </div>
  );
}
