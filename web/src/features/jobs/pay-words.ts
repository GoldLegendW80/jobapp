/**
 * The pay a posting states (opportunities O3) in the words the deck's card uses, built from the
 * four pay fields: "45,000 to 55,000 EUR a year", "600 EUR a day". Null when the posting states
 * none, never a guess. No dash is used as a separator.
 */
import { PAY_MAX } from "@/features/profile/api";
import { PAY_PERIOD_WORDS } from "@/lib/wording";

import type { PayPatch } from "./api";

export interface PayFields {
  pay_min?: number | null;
  pay_max?: number | null;
  pay_currency?: string | null;
  pay_period?: string | null;
}

const FIGURE = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

export function payWords(job: PayFields): string | null {
  const { pay_min: low, pay_max: high, pay_currency: currency } = job;
  const period = job.pay_period ? PAY_PERIOD_WORDS[job.pay_period] : undefined;
  if (low == null || high == null || !currency || !period) return null;
  const figures =
    low === high
      ? FIGURE.format(low)
      : `${FIGURE.format(low)} to ${FIGURE.format(high)}`;
  return `${figures} ${currency} ${period}`;
}

/**
 * A pay as typed in the details dialog: null when both figures are empty (no pay), undefined
 * when it is not valid (whole numbers from 1 to PAY_MAX, the lowest first, a currency and a
 * period). One figure alone is the lowest and the highest alike.
 */
export function typedPay(
  low: string,
  high: string,
  currency: string,
  period: string,
): PayPatch | null | undefined {
  const from = low.trim();
  const to = high.trim();
  if (!from && !to) return null;
  const read = (text: string) => (/^\d{1,8}$/.test(text) ? Number(text) : NaN);
  const min = read(from || to);
  const max = read(to || from);
  if (!(min >= 1 && max <= PAY_MAX && min <= max)) return undefined;
  if (!currency || !(period in PAY_PERIOD_WORDS)) return undefined;
  return { min, max, currency, period };
}
