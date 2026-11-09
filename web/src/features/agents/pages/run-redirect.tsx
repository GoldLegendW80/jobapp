import { Navigate, useParams } from "react-router";

import { stepHref } from "../helpers/activity";

/** /runs/:id, an agent run's old address: its workflow run's page with that step's log open. */
export function RunRedirect() {
  const { id = "" } = useParams();
  return <Navigate to={stepHref(id, id)} replace />;
}
