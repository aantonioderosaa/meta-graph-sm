"use client";

/**
 * Documents page: ingest form + ingested document list (F3.3 / F3.5).
 * R3.3: reset KB with explicit two-step confirmation dialog.
 */

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  ApiError,
  getDocuments,
  postDocuments,
  postDreamingRun,
  resetKnowledgeBase,
} from "@/lib/api-client";
import { useAppStore } from "@/lib/store";
import type { DocumentSummary } from "@/lib/types";

export function DocumentsPage() {
  const setActiveJobId = useAppStore((s) => s.setActiveJobId);
  const lastPipelineEvent = useAppStore((s) => s.lastPipelineEvent);
  const notifyKnowledgeBaseReset = useAppStore(
    (s) => s.notifyKnowledgeBaseReset,
  );

  const [docId, setDocId] = useState("doc-1");
  const [docText, setDocText] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  const [resetOpen, setResetOpen] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [resetError, setResetError] = useState<string | null>(null);

  const loadDocuments = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getDocuments();
      setDocuments(data.documents);
    } catch (err) {
      setDocuments([]);
      if (err instanceof ApiError) {
        setError(`Impossibile caricare i documenti (${err.status})`);
      } else {
        setError(err instanceof Error ? err.message : "Errore sconosciuto");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadDocuments();
  }, [loadDocuments, reloadToken]);

  useEffect(() => {
    if (!lastPipelineEvent) return;
    if (
      lastPipelineEvent.stage === "done" &&
      lastPipelineEvent.event === "pipeline_complete"
    ) {
      setReloadToken((t) => t + 1);
    }
  }, [lastPipelineEvent]);

  async function onIngest() {
    if (!docText.trim()) return;
    setBusy(true);
    setStatus(null);
    try {
      const { job_id } = await postDocuments({
        doc_id: docId || "doc-1",
        text: docText,
      });
      setActiveJobId(job_id);
      setStatus(`Ingest avviato · job_id=${job_id}`);
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Ingest fallito");
    } finally {
      setBusy(false);
    }
  }

  async function onDream() {
    setBusy(true);
    setStatus(null);
    try {
      const { job_id } = await postDreamingRun({});
      setActiveJobId(job_id);
      setStatus(`Dreaming avviato · job_id=${job_id}`);
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Dreaming fallito");
    } finally {
      setBusy(false);
    }
  }

  function openResetDialog() {
    setResetError(null);
    setResetOpen(true);
  }

  async function onConfirmReset() {
    setResetting(true);
    setResetError(null);
    try {
      await resetKnowledgeBase();
      // R3.4: clear shared client state + notify mounted views (graph, query history).
      notifyKnowledgeBaseReset();
      setResetOpen(false);
      setStatus("Knowledge base eliminata.");
      setReloadToken((t) => t + 1);
    } catch (err) {
      if (err instanceof ApiError) {
        setResetError(`Reset fallito (${err.status}): ${err.message}`);
      } else {
        setResetError(err instanceof Error ? err.message : "Reset fallito");
      }
    } finally {
      setResetting(false);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-6 p-4 md:p-6">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Documenti</h1>
          <p className="text-sm text-muted-foreground">
            Ingerisci testi e consulta i documenti già presenti nel grafo.
          </p>
        </div>
        <Button
          variant="destructive"
          size="sm"
          disabled={busy || resetting}
          onClick={openResetDialog}
        >
          Elimina tutto
        </Button>
      </header>

      <Dialog
        open={resetOpen}
        onOpenChange={(open) => {
          if (resetting) return;
          setResetOpen(open);
          if (!open) setResetError(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Eliminare l&apos;intera knowledge base?</DialogTitle>
            <DialogDescription>
              Questa azione cancella l&apos;intero grafo e tutti i documenti
              ingeriti. Non è reversibile.
            </DialogDescription>
          </DialogHeader>
          {resetError ? (
            <p className="text-sm text-destructive" role="alert">
              {resetError}
            </p>
          ) : null}
          <DialogFooter>
            <Button
              variant="outline"
              disabled={resetting}
              onClick={() => setResetOpen(false)}
            >
              Annulla
            </Button>
            <Button
              variant="destructive"
              disabled={resetting}
              onClick={() => void onConfirmReset()}
            >
              {resetting ? "Eliminazione…" : "Conferma eliminazione"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <section
        aria-label="Form ingestione"
        className="space-y-3 border-b border-border/60 pb-6"
      >
        <h2 className="text-sm font-medium uppercase tracking-wide text-muted-foreground">
          Ingestione
        </h2>
        <div className="flex flex-col gap-3 md:flex-row md:items-end">
          <label className="flex min-w-[8rem] flex-col gap-1 text-xs">
            <span>doc_id</span>
            <input
              className="rounded border border-input bg-background px-2 py-1.5 text-sm"
              value={docId}
              onChange={(e) => setDocId(e.target.value)}
            />
          </label>
          <label className="flex min-w-0 flex-1 flex-col gap-1 text-xs">
            <span>testo documento</span>
            <textarea
              className="min-h-[4rem] rounded border border-input bg-background px-2 py-1.5 text-sm"
              value={docText}
              onChange={(e) => setDocText(e.target.value)}
              placeholder="Incolla un paragrafo da ingerire…"
            />
          </label>
          <div className="flex gap-2">
            <Button
              size="sm"
              disabled={busy || !docText.trim()}
              onClick={() => void onIngest()}
            >
              Ingest
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => void onDream()}
            >
              Dream
            </Button>
          </div>
        </div>
        {status ? <p className="text-xs text-foreground">{status}</p> : null}
      </section>

      <section aria-label="Elenco documenti" className="space-y-3">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-medium uppercase tracking-wide text-muted-foreground">
            Documenti ingeriti
          </h2>
          <Button
            variant="outline"
            size="sm"
            disabled={loading}
            onClick={() => setReloadToken((t) => t + 1)}
          >
            Aggiorna
          </Button>
        </div>

        {error ? <p className="text-xs text-destructive">{error}</p> : null}
        {loading && documents.length === 0 ? (
          <p className="text-xs text-muted-foreground">Caricamento…</p>
        ) : null}
        {!loading && !error && documents.length === 0 ? (
          <p className="text-xs text-muted-foreground">Nessun documento ingerito.</p>
        ) : null}

        {documents.length > 0 ? (
          <div className="overflow-x-auto rounded border border-border">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-border bg-muted/40 text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 font-medium">doc_id</th>
                  <th className="px-3 py-2 font-medium">Chunk</th>
                  <th className="px-3 py-2 font-medium">Nodi</th>
                  <th className="px-3 py-2 font-medium">Ultimo aggiornamento</th>
                </tr>
              </thead>
              <tbody>
                {documents.map((doc) => (
                  <tr key={doc.doc_id} className="border-b border-border/60 last:border-0">
                    <td className="px-3 py-2 font-mono text-xs">{doc.doc_id}</td>
                    <td className="px-3 py-2">{doc.chunk_count}</td>
                    <td className="px-3 py-2">{doc.node_count}</td>
                    <td className="px-3 py-2 text-xs text-muted-foreground">
                      {doc.last_ingested_at || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
    </div>
  );
}
