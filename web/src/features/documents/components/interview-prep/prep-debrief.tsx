import { useState } from "react";

import { Section } from "@/components/blocks/section";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

import { failureSentence } from "../../api/documents-api";
import type { Debrief } from "../../api/interview-prep-api";
import { useAddDebrief } from "../../api/use-interview-prep";

/**
 * The Debrief: after an interview, the person writes what was asked and how it went. Each entry
 * is appended to the preparation by hand, as a new version; the next preparation keeps them and
 * the practice partner reads them.
 */
export function PrepDebrief({
  jobId,
  entries,
}: {
  jobId: string;
  entries: Debrief[];
}) {
  const add = useAddDebrief(jobId);
  const [on, setOn] = useState("");
  const [asked, setAsked] = useState("");
  const [notes, setNotes] = useState("");
  return (
    <Section
      title="Debrief"
      description="After an interview, write down what they asked. It stays with this preparation."
    >
      <Card>
        <CardContent className="flex flex-col gap-5 text-sm">
          {entries.length > 0 ? (
            <ul className="flex flex-col gap-4">
              {entries.map((entry, index) => (
                <li key={index} className="flex flex-col gap-1">
                  {entry.on ? (
                    <span className="font-medium">{entry.on}</span>
                  ) : null}
                  <span className="whitespace-pre-wrap">{entry.asked}</span>
                  {entry.notes ? (
                    <span className="whitespace-pre-wrap text-muted-foreground">
                      {entry.notes}
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
          <form
            className="flex flex-col gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              add.mutate(
                { on: on.trim(), asked: asked.trim(), notes: notes.trim() },
                {
                  onSuccess: () => {
                    setOn("");
                    setAsked("");
                    setNotes("");
                  },
                },
              );
            }}
          >
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="debrief-on">Which interview</Label>
              <Input
                id="debrief-on"
                value={on}
                maxLength={200}
                placeholder="First call with the hiring manager"
                onChange={(event) => {
                  setOn(event.target.value);
                }}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="debrief-asked">What they asked</Label>
              <Textarea
                id="debrief-asked"
                value={asked}
                maxLength={2000}
                onChange={(event) => {
                  setAsked(event.target.value);
                }}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="debrief-notes">What went well, what to fix</Label>
              <Textarea
                id="debrief-notes"
                value={notes}
                maxLength={2000}
                onChange={(event) => {
                  setNotes(event.target.value);
                }}
              />
            </div>
            {add.isError ? (
              <Alert variant="destructive">
                <AlertDescription>
                  {failureSentence(add.error)}
                </AlertDescription>
              </Alert>
            ) : null}
            <Button
              type="submit"
              size="sm"
              variant="outline"
              className="self-start"
              disabled={asked.trim() === "" || add.isPending}
            >
              Add to the debrief
            </Button>
          </form>
        </CardContent>
      </Card>
    </Section>
  );
}
