import { useState } from "react";
import { Link } from "react-router";

import { Section } from "@/components/blocks/section";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";

import { failureSentence } from "../../api/documents-api";
import type { InterviewPrepRead, Story } from "../../api/interview-prep-api";
import { useDecideStories, useKeepStories } from "../../api/use-interview-prep";

const PARTS = [
  ["situation", "Situation"],
  ["task", "Task"],
  ["action", "Action"],
  ["result", "Result"],
] as const;

/**
 * The STAR stories, with the fact check's notes on any figure the profile does not state. The
 * stories the person ticks are proposed for the profile's facts ("Interview stories") and are
 * written there only once the person accepts the proposal here.
 */
export function PrepStories({
  jobId,
  prep,
}: {
  jobId: string;
  prep: NonNullable<InterviewPrepRead["document"]> & {
    proposal: string | null;
  };
}) {
  const [chosen, setChosen] = useState<number[]>([]);
  const keep = useKeepStories(jobId, prep.id);
  const decide = useDecideStories(jobId);
  const noted = new Set(
    prep.notes
      .filter((note) => note.level === "claim")
      .map((note) => note.field.split(".").slice(0, 2).join(".")),
  );
  const stories = prep.draft.stories;
  return (
    <Section
      title="Your stories"
      description="Situation, task, action, result, from your profile. Tick the ones to keep for your next interviews."
      actions={
        <Button
          size="sm"
          variant="outline"
          disabled={chosen.length === 0 || keep.isPending}
          onClick={() => {
            keep.mutate(chosen, {
              onSuccess: () => {
                setChosen([]);
              },
            });
          }}
        >
          Keep in my profile
        </Button>
      }
    >
      <Card>
        <CardContent className="flex flex-col gap-5">
          {prep.proposal !== null ? (
            <Alert>
              <AlertDescription className="flex flex-col gap-2">
                <span>
                  Your kept stories are ready to add to your profile's facts, in
                  the block "Interview stories". Nothing changes until you
                  accept.
                </span>
                <span className="flex flex-wrap gap-2">
                  <Button
                    size="xs"
                    disabled={decide.isPending}
                    onClick={() => {
                      decide.mutate({ id: prep.proposal ?? "", accept: true });
                    }}
                  >
                    Accept
                  </Button>
                  <Button
                    size="xs"
                    variant="outline"
                    disabled={decide.isPending}
                    onClick={() => {
                      decide.mutate({ id: prep.proposal ?? "", accept: false });
                    }}
                  >
                    Reject
                  </Button>
                  <Link to="/profile" className="underline underline-offset-4">
                    Open your profile
                  </Link>
                </span>
              </AlertDescription>
            </Alert>
          ) : null}
          {keep.isSuccess && keep.data.proposal_id === null ? (
            <p className="text-sm text-muted-foreground" role="status">
              Your profile already holds these stories.
            </p>
          ) : null}
          {keep.isError || decide.isError ? (
            <Alert variant="destructive">
              <AlertDescription>
                {failureSentence(keep.error ?? decide.error)}
              </AlertDescription>
            </Alert>
          ) : null}
          <ul className="flex flex-col gap-5">
            {stories.map((story, index) => (
              <StoryItem
                key={index}
                story={story}
                index={index}
                noted={noted.has(`stories.${String(index)}`)}
                checked={chosen.includes(index)}
                onCheck={(on) => {
                  setChosen((current) =>
                    on
                      ? [...current, index]
                      : current.filter((one) => one !== index),
                  );
                }}
              />
            ))}
          </ul>
        </CardContent>
      </Card>
    </Section>
  );
}

function StoryItem({
  story,
  index,
  noted,
  checked,
  onCheck,
}: {
  story: Story;
  index: number;
  noted: boolean;
  checked: boolean;
  onCheck: (on: boolean) => void;
}) {
  const id = `story-${String(index)}`;
  return (
    <li className="flex gap-3 text-sm">
      <Checkbox
        id={id}
        checked={checked}
        onCheckedChange={(value) => {
          onCheck(value === true);
        }}
        className="mt-0.5"
      />
      <div className="flex min-w-0 flex-col gap-1">
        <Label htmlFor={id} className="font-medium">
          {story.title || `Story ${String(index + 1)}`}
        </Label>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
          {PARTS.map(([key, name]) =>
            story[key] ? (
              <div key={key} className="contents">
                <dt className="text-muted-foreground">{name}</dt>
                <dd>{story[key]}</dd>
              </div>
            ) : null,
          )}
        </dl>
        {noted ? (
          <p className="text-muted-foreground">
            Holds a claim your profile does not state. Check it before you tell
            it.
          </p>
        ) : null}
      </div>
    </li>
  );
}
