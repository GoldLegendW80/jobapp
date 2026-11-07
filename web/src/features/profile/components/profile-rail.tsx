import { CircleCheck, CircleDashed } from "lucide-react";
import { Link } from "react-router";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

import { photoUrl } from "../api/photo-api";
import type { Profile } from "../api/profile-api";
import { PROFILE_ANCHORS } from "../helpers/anchors";
import { readiness, type Readiness } from "../helpers/readiness";

/**
 * The side rail of the profile page, the person at a glance: photo, name and headline, how
 * complete the profile is, and "Ready for" (three uses of the profile, each with what it still
 * lacks and the one button that fixes it).
 */
export function ProfileRail({ profile }: { profile: Profile }) {
  const identity = profile.sections.identity;
  const name = identity.full_name ?? "Your name";
  const initials =
    (identity.full_name ?? "")
      .split(/\s+/)
      .map((word) => word[0] ?? "")
      .join("")
      .slice(0, 2)
      .toUpperCase() || "?";
  return (
    <>
      <Card role="region" aria-label="You">
        <CardContent className="flex flex-col gap-6">
          <div className="flex items-center gap-4">
            <Avatar className="size-14">
              {profile.has_photo && profile.photo_etag ? (
                <AvatarImage src={photoUrl(profile.photo_etag)} alt="" />
              ) : null}
              <AvatarFallback>{initials}</AvatarFallback>
            </Avatar>
            <div className="min-w-0">
              <p className="font-medium break-words">{name}</p>
              {identity.headline ? (
                <p className="text-sm text-muted-foreground">
                  {identity.headline}
                </p>
              ) : (
                <a
                  href={`#${PROFILE_ANCHORS.contact}`}
                  className="text-sm text-muted-foreground underline underline-offset-4"
                >
                  Add a headline
                </a>
              )}
            </div>
          </div>
          <div className="flex flex-col gap-3">
            <div className="flex items-baseline justify-between gap-3">
              <p id="ready-for" className="text-sm font-medium">
                Ready for
              </p>
              <p
                className="text-sm text-muted-foreground tabular-nums"
                data-testid="profile-completeness"
              >
                {profile.completeness.overall}% complete
              </p>
            </div>
            <ul aria-labelledby="ready-for" className="flex flex-col gap-3">
              {readiness(profile).map((line) => (
                <ReadyLine key={line.name} line={line} />
              ))}
            </ul>
          </div>
        </CardContent>
      </Card>
      <p className="text-sm text-muted-foreground">
        What the search looks for is in{" "}
        <Link
          to="/settings/search"
          className="font-medium text-foreground underline underline-offset-4"
        >
          Settings, Search
        </Link>
        .
      </p>
    </>
  );
}

function ReadyLine({ line }: { line: Readiness }) {
  return (
    <li className="flex flex-col items-start gap-2 text-sm">
      <span className="flex items-center gap-2">
        {line.ready ? (
          <CircleCheck aria-hidden="true" className="size-4 text-success" />
        ) : (
          <CircleDashed
            aria-hidden="true"
            className="size-4 text-muted-foreground"
          />
        )}
        <span>
          {line.name}
          <span className="sr-only">
            {line.ready ? ": ready" : ": not ready yet"}
          </span>
        </span>
      </span>
      {!line.ready && line.missing.length > 0 ? (
        <p className="ml-6 text-muted-foreground">
          Missing: {line.missing.join(", ")}.
        </p>
      ) : null}
      {line.fix ? (
        <Button asChild variant="outline" size="sm" className="ml-6">
          {line.fix.href.startsWith("#") ? (
            <a href={line.fix.href}>{line.fix.label}</a>
          ) : (
            <Link to={line.fix.href}>{line.fix.label}</Link>
          )}
        </Button>
      ) : null}
    </li>
  );
}
