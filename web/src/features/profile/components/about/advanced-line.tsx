import { ABOUT_WORDS as W } from "@/features/profile/helpers/about-words";

/** The regular expression categories the skills in words leave out: they stay and keep scoring. */
export function AdvancedLine({ count }: { count: number }) {
  return <p className="text-sm text-muted-foreground">{W.advanced(count)}</p>;
}
