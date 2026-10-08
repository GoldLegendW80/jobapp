import { CircleCheck, CircleX, LoaderCircle, RotateCw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { describeError } from "@/lib/api";

import { useHealth } from "./use-health";

export function HealthCard() {
  const health = useHealth();
  const checking = health.isFetching;

  return (
    <Card
      aria-labelledby="health-title"
      role="region"
      className="w-full max-w-md"
    >
      <CardHeader>
        <CardTitle id="health-title" className="text-lg">
          API status
        </CardTitle>
        <CardDescription>
          The backend and its database answer a health check.
        </CardDescription>
        <CardAction>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void health.refetch()}
            disabled={checking}
            aria-busy={checking}
          >
            <RotateCw
              aria-hidden="true"
              className={checking ? "animate-spin" : undefined}
            />
            Retry
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent>
        <div role="status" aria-live="polite" className="flex flex-col gap-2">
          {health.isPending || (checking && health.isError) ? (
            <Badge key="checking" variant="secondary">
              <LoaderCircle aria-hidden="true" className="animate-spin" />
              Checking
            </Badge>
          ) : health.isError ? (
            <>
              <Badge key="unavailable" variant="destructive">
                <CircleX aria-hidden="true" />
                API unavailable
              </Badge>
              <p className="text-sm text-muted-foreground">
                {describeError(health.error)}
              </p>
            </>
          ) : (
            <Badge key="ok" className="bg-success text-success-foreground">
              <CircleCheck aria-hidden="true" />
              API ok
            </Badge>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
