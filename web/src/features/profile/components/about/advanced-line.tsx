import { PROFILE_ANCHORS } from "@/features/profile/helpers/anchors";
import { ABOUT_WORDS as W } from "@/features/profile/helpers/about-words";

/** The regular expression categories the skills in words leave out, with the way to them. */
export function AdvancedLine({ count }: { count: number }) {
  return (
    <p className="text-sm text-muted-foreground">
      {W.advanced(count)}{" "}
      <a
        href={`#${PROFILE_ANCHORS.matching}`}
        className="font-medium text-foreground underline underline-offset-4"
      >
        {W.advancedLink}
      </a>
    </p>
  );
}
