"use client";

import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  fetchDocuments,
  ingestDocument,
  wipeKnowledgeBase,
} from "@/lib/event-graph/api";
import { formatPeso } from "@/lib/event-graph/documents";
import type { EventGraphDocument } from "@/lib/event-graph/types";
import { EventGraphApiError } from "@/lib/event-graph/api";

type EventIngestPanelProps = {
  onJobStarted?: (jobId: string) => void;
  onWiped?: () => void;
  ingestionInCorso?: boolean;
};

export function EventIngestPanel({
  onJobStarted,
  onWiped,
  ingestionInCorso = false,
}: EventIngestPanelProps) {
  const [docId, setDocId] = useState("doc-1");
  const [text, setText] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [docsOpen, setDocsOpen] = useState(false);
  const [documents, setDocuments] = useState<EventGraphDocument[]>([]);
  const [docsLoading, setDocsLoading] = useState(false);
  const [docsError, setDocsError] = useState<string | null>(null);

  const [wipeOpen, setWipeOpen] = useState(false);
  const [wiping, setWiping] = useState(false);
  const [wipeError, setWipeError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!docId.trim() || !text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const result = await ingestDocument(docId.trim(), text);
      setJobId(result.job_id);
      onJobStarted?.(result.job_id);
    } catch (err) {
      // Handle 409 Conflict error
      if (err instanceof EventGraphApiError && err.status === 409) {
        const activeJobId = (err as any).body?.job_id;
        setError(`Ingestione già in corso per il job ${activeJobId}.`);
      } else {
        setError(err instanceof Error ? err.message : "Ingestione fallita");
      }
    } finally {
      setBusy(false);
    }
  }

  async function openDocuments() {
    setDocsOpen(true);
    setDocsLoading(true);
    setDocsError(null);
    try {
      const data = await fetchDocuments();
      setDocuments(data.documents ?? []);
    } catch (err) {
      setDocuments([]);
      setDocsError(
        err instanceof Error ? err.message : "Impossibile caricare i documenti",
      );
    } finally {
      setDocsLoading(false);
    }
  }

  async function onConfirmWipe() {
    setWiping(true);
    setWipeError(null);
    try {
      await wipeKnowledgeBase();
      setWipeOpen(false);
      setJobId(null);
      setText("");
      setDocuments([]);
      onWiped?.();
    } catch (err) {
      setWipeError(err instanceof Error ? err.message : "Wipe fallito");
    } finally {
      setWiping(false);
    }
  }

  return (
    <Card>
      <CardHeader className="p-4 pb-2">
        <CardTitle className="text-sm">Ingestione</CardTitle>
      </CardHeader>
      <CardContent className="p-4 pt-0">
        <form onSubmit={(e) => void onSubmit(e)} className="flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-xs">
            <span>doc_id</span>
            <input
              className="rounded border border-input bg-background px-2 py-1.5 text-sm"
              value={docId}
              onChange={(e) => setDocId(e.target.value)}
              name="doc_id"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span>testo</span>
            <textarea
              className="min-h-[6rem] rounded border border-input bg-background px-2 py-1.5 text-sm"
              value={text}
              onChange={(e) => setText(e.target.value)}
              name="text"
              placeholder="Incolla il testo da ingerire…"
            />
          </label>
          <div className="flex flex-wrap gap-2">
            <Button
              type="submit"
              size="sm"
              disabled={busy || wiping || ingestionInCorso || !text.trim() || !docId.trim()}
            >
              {busy ? "Invio…" : ingestionInCorso ? "Ingestione in corso…" : "Ingerisci"}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={busy || wiping}
              onClick={() => void openDocuments()}
            >
              Documenti
            </Button>
            <Button
              type="button"
              size="sm"
              variant="destructive"
              disabled={busy || wiping}
              onClick={() => {
                setWipeError(null);
                setWipeOpen(true);
              }}
            >
              Svuota KB
            </Button>
          </div>
          {jobId ? (
            <p className="text-xs text-foreground" data-testid="job-id">
              job_id: <code className="rounded bg-muted px-1">{jobId}</code>
            </p>
          ) : null}
          {error ? (
            <p className="text-xs text-destructive" role="alert">
              {error}
            </p>
          ) : null}
        </form>
      </CardContent>

      <Dialog open={docsOpen} onOpenChange={setDocsOpen}>
        <DialogContent className="max-h-[min(80vh,32rem)] overflow-y-auto sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Documenti ingeriti</DialogTitle>
            <DialogDescription>
              {docsLoading
                ? "Caricamento…"
                : `${documents.length} document${documents.length === 1 ? "o" : "i"}`}
            </DialogDescription>
          </DialogHeader>
          {docsError ? (
            <p className="text-sm text-destructive" role="alert">
              {docsError}
            </p>
          ) : null}
          {!docsLoading && !docsError && documents.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Nessun documento in knowledge base.
            </p>
          ) : null}
          <ul className="flex flex-col gap-3 text-sm">
            {documents.map((doc) => (
              <li
                key={doc.id}
                className="rounded-md border border-border p-2"
                data-testid="ingested-doc"
              >
                <p className="font-medium">{doc.id}</p>
                <p className="text-xs text-muted-foreground">
                  formato {doc.formato} · {formatPeso(doc.bytes)} ·{" "}
                  {doc.caratteri} caratteri
                  {doc.n_eventi != null ? ` · ${doc.n_eventi} eventi` : ""}
                </p>
                {doc.updated_at ? (
                  <p className="text-[10px] text-muted-foreground">
                    aggiornato {doc.updated_at}
                  </p>
                ) : null}
                {doc.anteprima ? (
                  <p className="mt-1 line-clamp-3 text-xs text-muted-foreground">
                    {doc.anteprima}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        </DialogContent>
      </Dialog>

      <Dialog
        open={wipeOpen}
        onOpenChange={(open) => {
          if (wiping) return;
          setWipeOpen(open);
          if (!open) setWipeError(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Svuotare l&apos;intera knowledge base?</DialogTitle>
            <DialogDescription>
              Cancella ogni nodo e relazione nel grafo eventi, lo storico query
              e lo stato della pagina. Non è reversibile.
            </DialogDescription>
          </DialogHeader>
          {wipeError ? (
            <p className="text-sm text-destructive" role="alert">
              {wipeError}
            </p>
          ) : null}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={wiping}
              onClick={() => setWipeOpen(false)}
            >
              Annulla
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={wiping}
              onClick={() => void onConfirmWipe()}
            >
              {wiping ? "Eliminazione…" : "Elimina tutto"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
