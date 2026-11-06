import { Link } from "react-router";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

import type { Estimate } from "@/features/ingestion/api/runs-api";

/** Why the run cannot start yet, as the estimate says: the button stays disabled. */
export function EstimateBlockers({ estimate }: { estimate: Estimate }) {
  return (
    <Alert variant="destructive">
      <AlertTitle>The run cannot start yet</AlertTitle>
      <AlertDescription>
        <ul className="flex flex-col gap-1">
          {estimate.blockers.map((one) => (
            <li key={one.code}>
              {one.reason}{" "}
              {one.code === "no_credential" ? (
                <Link to="/account" className="font-medium underline">
                  Open your account page
                </Link>
              ) : null}
            </li>
          ))}
        </ul>
      </AlertDescription>
    </Alert>
  );
}
