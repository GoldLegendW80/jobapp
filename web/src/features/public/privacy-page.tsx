import {
  AFTER_DISCONNECT,
  ASSISTANT,
  BROWSER_DISK,
  BROWSER_FORGET,
  BROWSER_KEPT,
  BROWSER_WHERE,
  contactEmail,
  DELETE_ACCOUNT,
  DISABLED_ACCOUNT,
  DISCONNECT,
  GOOGLE_POLICY_URL,
  HOW_LONG,
  LIMITED_USE,
  NO_CONTACT,
  SEARCH_COMPANIES,
  SEARCH_KEYWORDS,
  SEARCH_SYSTEMS,
  VERSION,
  WHAT_IS_READ,
  WHERE_STORED,
} from "./texts";
import { PublicLayout, Section } from "./public-layout";

/** What the app does with a connected Gmail and with its browser, in the words of ./texts.
 * Public: no session. */
export function PrivacyPage() {
  const contact = contactEmail();
  return (
    <PublicLayout title="Privacy">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Privacy</h1>
        <p className="text-sm text-muted-foreground">Version {VERSION}</p>
      </div>
      <Section id="read" title="What is read">
        <p>{WHAT_IS_READ}</p>
        <ul
          aria-label="Search terms"
          className="list-disc space-y-1 pl-6 text-sm"
        >
          <li>
            Words: <span>{SEARCH_KEYWORDS.join(", ")}</span>
          </li>
          <li>
            Systems: <span>{SEARCH_SYSTEMS.join(", ")}</span>
          </li>
          <li>{SEARCH_COMPANIES}</li>
        </ul>
      </Section>
      <Section id="stored" title="Where it is stored">
        <p>{WHERE_STORED}</p>
      </Section>
      <Section id="applications" title="Applications and site profiles">
        <p>
          Applications keep the answers, events, document versions and
          screenshots used for that application. The before and after
          screenshots stay with the posting. Temporary step and error
          screenshots are removed after the configured retention period, 30 days
          by default. Deleting a posting removes its applications and their
          screenshots.
        </p>
        <p>
          Completing a profile on another site keeps the fields, sources, review
          decisions and screenshots in this app. Before and after screenshots
          stay with the run; temporary step screenshots follow the same
          retention period. Deleting the account removes these runs and
          application data. Changes already saved on another site remain on that
          site.
        </p>
      </Section>
      <Section id="assistant" title="The assistant">
        <p>{ASSISTANT}</p>
      </Section>
      <Section id="long" title="How long">
        <p>{HOW_LONG}</p>
      </Section>
      <Section id="after" title="What stays after a disconnect">
        <p>{AFTER_DISCONNECT}</p>
      </Section>
      <Section id="disabled" title="A disabled account">
        <p>{DISABLED_ACCOUNT}</p>
      </Section>
      <Section id="disconnect" title="How to disconnect">
        <p>{DISCONNECT}</p>
      </Section>
      <Section id="browser" title="The app's browser">
        <p>{BROWSER_KEPT}</p>
        <p>{BROWSER_WHERE}</p>
        <p>{BROWSER_FORGET}</p>
        <p>{BROWSER_DISK}</p>
      </Section>
      <Section id="delete" title="Deleting the account">
        <p>{DELETE_ACCOUNT}</p>
      </Section>
      <Section id="contact" title="Contact">
        <p>
          {contact === "" ? (
            NO_CONTACT
          ) : (
            <a
              className="underline underline-offset-4"
              href={`mailto:${contact}`}
            >
              {contact}
            </a>
          )}
        </p>
      </Section>
      <Section id="google" title="Google API Services User Data Policy">
        <p>{LIMITED_USE}</p>
        <p className="text-sm">
          <a
            className="underline underline-offset-4"
            href={GOOGLE_POLICY_URL}
            rel="noopener noreferrer"
          >
            Google API Services User Data Policy
          </a>
        </p>
      </Section>
    </PublicLayout>
  );
}
