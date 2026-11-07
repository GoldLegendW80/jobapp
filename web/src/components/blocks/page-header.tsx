import { ArrowLeft } from "lucide-react";
import { Fragment, type ReactNode, type Ref } from "react";
import { Link } from "react-router";

import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/** A page above this one: its address, its name, and the state its link carries (the jobs list's query). */
export type Crumb = { to: string; label: string; state?: unknown };

/**
 * The top of every page: on a page below others, the breadcrumb of the pages above (`trail`, from
 * the top) then this one; its h1, one line on what the page is for, and the page's own actions:
 * one primary at most, first, then two others at most; anything else goes in a menu. The title is
 * the page's only h1, and names the breadcrumb's last step when it is text; `titleRef` lets a page
 * move focus to it, for instance after the item that had focus is gone.
 */
export function PageHeader({
  trail,
  title,
  titleId,
  description,
  actions,
  className,
  titleRef,
}: {
  trail?: readonly Crumb[];
  title: ReactNode;
  titleId?: string;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
  titleRef?: Ref<HTMLHeadingElement>;
}) {
  return (
    <div className={cn("flex flex-col gap-3", className)}>
      {trail?.length ? (
        <PageTrail
          trail={trail}
          current={typeof title === "string" ? title : undefined}
        />
      ) : null}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex min-w-0 flex-col gap-1">
          <h1
            id={titleId}
            ref={titleRef}
            tabIndex={titleRef ? -1 : undefined}
            className="text-3xl font-medium tracking-tight outline-none [overflow-wrap:anywhere]"
          >
            {title}
          </h1>
          {description ? (
            <p className="max-w-prose text-sm text-muted-foreground">
              {description}
            </p>
          ) : null}
        </div>
        {actions ? (
          <div className="flex flex-wrap items-center gap-2">{actions}</div>
        ) : null}
      </div>
    </div>
  );
}

/**
 * The pages above, each a link, then the current one, on one line: a long name ends in an ellipsis
 * (a link's whole name in its tooltip). A phone shows only the way up, the last page above, with an arrow.
 */
function PageTrail({
  trail,
  current,
}: {
  trail: readonly Crumb[];
  current: string | undefined;
}) {
  const up = trail.length - 1;
  return (
    // Its own provider: the header renders anywhere, with or without the app's.
    <TooltipProvider>
      <Breadcrumb aria-label="Breadcrumb">
        <BreadcrumbList className="flex-nowrap">
          {trail.map((crumb, index) => (
            <Fragment key={crumb.to}>
              <BreadcrumbItem
                className={cn(
                  "max-w-60 shrink-0",
                  index < up && "hidden sm:inline-flex",
                )}
              >
                <Tooltip>
                  <TooltipTrigger asChild>
                    <BreadcrumbLink asChild>
                      <Link
                        to={crumb.to}
                        state={crumb.state}
                        className="inline-flex min-w-0 items-center gap-1.5 rounded-md outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
                      >
                        {index === up ? (
                          <ArrowLeft
                            aria-hidden="true"
                            className="size-4 sm:hidden"
                          />
                        ) : null}
                        <span className="truncate">{crumb.label}</span>
                      </Link>
                    </BreadcrumbLink>
                  </TooltipTrigger>
                  {/* A long name is cut: its whole text on hover and focus. */}
                  <TooltipContent>{crumb.label}</TooltipContent>
                </Tooltip>
              </BreadcrumbItem>
              {index < up || current ? (
                <BreadcrumbSeparator className="hidden sm:inline-flex" />
              ) : null}
            </Fragment>
          ))}
          {current ? (
            <BreadcrumbItem className="hidden min-w-0 sm:inline-flex">
              {/* Cut when long: the h1 just below holds the whole title. */}
              <BreadcrumbPage className="truncate">{current}</BreadcrumbPage>
            </BreadcrumbItem>
          ) : null}
        </BreadcrumbList>
      </Breadcrumb>
    </TooltipProvider>
  );
}
