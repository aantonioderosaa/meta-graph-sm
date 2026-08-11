"use client";

import { useEffect, useState } from "react";

import { ApiError, getFact } from "@/lib/api-client";
import type { FactDetailResponse } from "@/lib/types";
import { useAppStore } from "@/lib/store";

export function FactDetailPanel() {
  const selectedFactId = useAppStore((s) => s.selectedFactId);
  const setSelectedFactId = useAppStore((s) => s.setSelectedFactId);
  const [detail, setDetail] = useState<FactDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!selectedFactId) {
      setDetail(null);
      setError(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    getFact(selectedFactId)
      .then((data) => {
        if (!cancelled) {
          setDetail(data);
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setError("Fatto non trovato");
        } else {
          setError(err instanceof Error ? err.message : "Errore di caricamento");
        }
        setDetail(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedFactId]);

  if (!selectedFactId) {
    return null;
  }

  return (
    <aside
      aria-label="Dettaglio fatto"
      className="absolute bottom-3 right-3 z-10 w-[min(100%-1.5rem,22rem)] rounded-lg border border-border bg-background/95 p-3 shadow-md backdrop-blur"
    >
      <div className="mb-2 flex items-start justify-between gap-2">
        <h3 className="text-sm font-medium">Dettaglio fatto</h3>
        <button
          type="button"
          className="text-xs text-muted-foreground hover:text-foreground"
          onClick={() => setSelectedFactId(null)}
        >
          Chiudi
        </button>
      </div>
      {loading ? (
        <p className="text-xs text-muted-foreground">Caricamento…</p>
      ) : error ? (
        <p className="text-xs text-destructive">{error}</p>
      ) : detail ? (
        <dl className="space-y-2 text-xs">
          <div>
            <dt className="text-muted-foreground">Testo</dt>
            <dd className="text-sm text-foreground">{detail.text}</dd>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <dt className="text-muted-foreground">Type</dt>
              <dd>{detail.type}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Confidence</dt>
              <dd>{detail.confidence}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">is_latest</dt>
              <dd>{detail.is_latest ? "true" : "false"}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">created_at</dt>
              <dd className="break-all">{detail.created_at}</dd>
            </div>
          </div>
          <div>
            <dt className="text-muted-foreground">Doc</dt>
            <dd>{detail.source_doc_id}</dd>
          </div>
          <div>
            <dt className="mb-1 text-muted-foreground">Provenienza</dt>
            <dd className="space-y-1">
              {detail.provenance.length === 0 ? (
                <span>—</span>
              ) : (
                detail.provenance.map((p) => (
                  <div
                    key={p.chunk_id}
                    className="rounded border border-border/70 bg-muted/40 px-2 py-1"
                  >
                    <div className="font-mono text-[10px] text-muted-foreground">
                      {p.chunk_id} · {p.doc_id}
                    </div>
                    <div>{p.snippet}</div>
                  </div>
                ))
              )}
            </dd>
          </div>
        </dl>
      ) : null}
    </aside>
  );
}
