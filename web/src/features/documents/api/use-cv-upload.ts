import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
  confirmDocument,
  documentsKey,
  failureSentence as uploadFailure,
  listDocuments,
  uploadDocument,
} from "@/features/ingestion/api/documents-api";
import {
  acceptConsent as acceptRunConsent,
  consentKey as runConsentKey,
  getConsent as getRunConsent,
} from "@/features/ingestion/api/runs-api";

import {
  baseCvsKey,
  createBaseCv,
  listBaseCvs,
  patchBaseCv,
  type BaseCvs,
} from "./base-cvs-api";
import { failureSentence } from "./documents-api";
import { acceptConsent, consentKey, getConsent } from "./consent-api";
import { pollInterval } from "../helpers/labels";
import {
  isCvFile,
  isRead,
  NOT_A_CV,
  READING_POLL_MS,
  RUN_CONSENT_FAILED,
} from "../helpers/upload-cv";
import { useFollowProfileRun } from "./use-follow-profile-run";

/**
 * An uploaded CV on its way to a base CV: the file is sent to ingestion, which reads its text;
 * once read, and once what is sent is agreed to, it is converted, once. `eager` (the welcome
 * wizard) converts it as soon as it is read: the server holds the conversion until the consent,
 * which the dialog still asks for. `problem` is the sentence of the last failure; a new file
 * starts over.
 */
export function useCvUpload({ eager = false }: { eager?: boolean } = {}) {
  const client = useQueryClient();
  const [problem, setProblem] = useState<string>();
  const [uploadId, setUploadId] = useState<string>();
  const [baseId, setBaseId] = useState<string>();
  const [declined, setDeclined] = useState(false);
  /** The file whose conversion was asked for: once per upload. */
  const started = useRef<string>(undefined);

  const consent = useQuery({
    queryKey: consentKey,
    queryFn: ({ signal }) => getConsent(signal),
    enabled: uploadId !== undefined,
  });
  // Asked once a file is sent: the consent dialog may show it beside the documents consent.
  const runConsent = useQuery({
    queryKey: runConsentKey,
    queryFn: ({ signal }) => getRunConsent(signal),
    enabled: uploadId !== undefined,
  });
  const profileRun = useFollowProfileRun();
  const files = useQuery({
    queryKey: documentsKey,
    queryFn: ({ signal }) => listDocuments(signal),
    enabled: uploadId !== undefined && baseId === undefined,
    refetchInterval: (current) =>
      current.state.data?.items.find((one) => one.id === uploadId)?.status ===
      "pending"
        ? READING_POLL_MS
        : false,
  });
  const cvs = useQuery({
    queryKey: baseCvsKey,
    queryFn: ({ signal }) => listBaseCvs(signal),
    enabled: baseId !== undefined,
    refetchInterval: (current) => {
      const cv = current.state.data?.base_cvs.find((one) => one.id === baseId);
      return cv === undefined
        ? false
        : pollInterval(cv.render_state, cv.generation);
    },
  });

  const send = useMutation({
    mutationFn: (file: File) => uploadDocument(file, { forBaseCv: true }),
    onSuccess: (stored) => {
      setUploadId(stored.id);
      void client.invalidateQueries({ queryKey: documentsKey });
    },
    onError: (error) => {
      setProblem(uploadFailure(error));
    },
  });
  const convert = useMutation({
    mutationFn: (id: string) =>
      createBaseCv({ from: "upload", ingestion_document_id: id }),
    onSuccess: (made) => {
      setBaseId(made.id);
      client.setQueryData<BaseCvs>(baseCvsKey, (old) =>
        old === undefined ? old : { ...old, base_cvs: [...old.base_cvs, made] },
      );
      void client.invalidateQueries({ queryKey: baseCvsKey });
    },
    onError: (error) => {
      setProblem(failureSentence(error));
    },
  });
  // The documents consent first, then the run's when its box was ticked: each its own record.
  const agree = useMutation({
    mutationFn: async ({
      version,
      runVersion,
    }: {
      version: number;
      runVersion: number | null;
    }) => {
      const accepted = await acceptConsent(version);
      client.setQueryData(consentKey, accepted);
      if (runVersion === null) return;
      try {
        client.setQueryData(runConsentKey, await acceptRunConsent(runVersion));
      } catch {
        void client.invalidateQueries({ queryKey: runConsentKey });
        toast.error(RUN_CONSENT_FAILED);
      }
    },
    onError: () => {
      void client.invalidateQueries({ queryKey: consentKey });
    },
  });
  const keep = useMutation({
    mutationFn: confirmDocument,
    onSuccess: () => client.invalidateQueries({ queryKey: documentsKey }),
    onError: (error) => {
      setProblem(uploadFailure(error));
    },
  });
  const makeDefault = useMutation({
    mutationFn: (id: string) => patchBaseCv(id, { default: true }),
    onSuccess: () => client.invalidateQueries({ queryKey: baseCvsKey }),
    onError: (error) => {
      setProblem(failureSentence(error));
    },
  });

  /** A file chosen or dropped: the panel starts over with it. */
  const take = (file: File | undefined) => {
    if (file === undefined) return;
    setProblem(undefined);
    setUploadId(undefined);
    setBaseId(undefined);
    setDeclined(false);
    if (!isCvFile(file.name)) {
      setProblem(NOT_A_CV);
      return;
    }
    send.mutate(file);
  };

  const file = files.data?.items.find((one) => one.id === uploadId);
  const cv = cvs.data?.base_cvs.find((one) => one.id === baseId);
  const read = isRead(file);
  const accepted = consent.data?.accepted;
  const convertNow = convert.mutate;
  // The text is read and what is sent agreed to (or `eager`): convert it, once.
  useEffect(() => {
    if (!read || uploadId === undefined || (!eager && accepted !== true))
      return;
    if (started.current === uploadId) return;
    started.current = uploadId;
    convertNow(uploadId);
  }, [read, uploadId, accepted, eager, convertNow]);

  return {
    problem,
    uploadId,
    baseId,
    declined,
    /** The person closed the consent dialog; "Convert it into my CV" asks again. */
    decline: () => {
      setDeclined(true);
    },
    askAgain: () => {
      setDeclined(false);
    },
    consent,
    runConsent,
    profileRun,
    file,
    cv,
    send,
    convert,
    agree,
    keep,
    makeDefault,
    take,
    busy: send.isPending || convert.isPending,
    /** The consent dialog is due: the text is read and what is sent is not agreed to yet. */
    asking:
      read &&
      accepted === false &&
      !declined &&
      (eager || baseId === undefined),
  };
}
