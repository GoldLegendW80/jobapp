import { useId, type ReactNode } from "react";
import { Link } from "react-router";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

import { ABOUT_WORDS as W } from "@/features/profile/helpers/about-words";

/**
 * The card of the Skills or the Answers fields of About you without a model key: where the
 * part of that kind stands with a key, at the anchor "Ready for" links to.
 */
export function PlainCard({
  anchor,
  title,
  description,
  children,
}: {
  anchor: string;
  title: string;
  description: string;
  children: ReactNode;
}) {
  const headingId = useId();
  return (
    <Card
      id={anchor}
      role="region"
      aria-labelledby={headingId}
      className="scroll-mt-24 gap-4 p-4 sm:p-5"
    >
      <CardHeader className="gap-1 px-0">
        <CardTitle className="text-base">
          <h3 id={headingId}>{title}</h3>
        </CardTitle>
        <CardDescription>
          {description} {W.plainKey}{" "}
          <Link
            to="/account"
            className="font-medium text-foreground underline underline-offset-4"
          >
            {W.addKey}
          </Link>
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-8 px-0">{children}</CardContent>
    </Card>
  );
}
