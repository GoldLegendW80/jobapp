import { BriefcaseBusiness } from "lucide-react";
import { Link } from "react-router";

import { APP_NAME } from "@/hooks/use-title";
import { cn } from "@/lib/utils";

/** The app's mark and name, a link home. Its accessible name is the app's name. */
export function Brand({ to, className }: { to: string; className?: string }) {
  return (
    <Link
      to={to}
      className={cn(
        "flex w-fit items-center gap-2 rounded-full bg-card py-1.5 pr-4 pl-1.5 text-base font-semibold tracking-tight outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
        className,
      )}
    >
      <span
        aria-hidden="true"
        className="flex size-8 items-center justify-center rounded-full bg-brand text-brand-foreground"
      >
        <BriefcaseBusiness className="size-4" />
      </span>
      {APP_NAME}
    </Link>
  );
}
