import { useQuery } from "@tanstack/react-query";

import {
  emailApplicationKey,
  getEmailApplication,
} from "./email-application-api";

// While the page is open, the message is read again this often.
const EMAIL_POLL_MS = 3000;

/** The application's e-mail as it will be sent, read again while the page is open. */
export function useEmailApplication(applicationId: string) {
  return useQuery({
    queryKey: emailApplicationKey(applicationId),
    queryFn: () => getEmailApplication(applicationId),
    refetchInterval: EMAIL_POLL_MS,
    retry: false,
  });
}
