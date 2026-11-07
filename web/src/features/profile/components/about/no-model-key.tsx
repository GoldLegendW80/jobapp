import { Link } from "react-router";

import { ABOUT_WORDS as W } from "@/features/profile/helpers/about-words";

/** Without a model key the text saves, but the profile is not updated from it. */
export function NoModelKey() {
  return (
    <p className="text-sm text-muted-foreground">
      {W.noKey}{" "}
      <Link
        to="/account"
        className="font-medium text-foreground underline underline-offset-4"
      >
        {W.addKey}
      </Link>
    </p>
  );
}
