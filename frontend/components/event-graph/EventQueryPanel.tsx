"use client";

import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { queryNl, queryStructured } from "@/lib/event-graph/api";
import {
  HIGHLIGHT_COLORS,
  idsFromQueryResult,
  mergeHighlights,
  type HighlightKind,
} from "@/lib/event-graph/highlight";
import type {
  EventNlQueryResponse,
  EventQueryEvento,
  EventQueryRisultato,
  EventQuerySpec,
  EventStructuredQueryResponse,
  Fattualita,
  PianoNarrativo,
  TempoVerbale,
  TipoRelazione,
  TraversalKind,
} from "@/lib/event-graph/types";
import { cn } from "@/lib/utils";

const PIANI: PianoNarrativo[] = ["PRIMO_PIANO", "SFONDO", "FUORI_LINEA"];
const FATTUALITA: Fattualita[] = ["FATTUALE", "NON_FATTUALE", "IPOTETICO"];
const TEMPI: TempoVerbale[] = [
  "presente",
  "imperfetto",
  "passato",
  "futuro",
  "non_finito",
];
const TRAVERSALS: TraversalKind[] = [
  "catena_di",
  "spina_dorsale_di",
  "prima_di",
  "dopo_di",
  "vicinato_temporale",
];
const RELAZIONI: TipoRelazione[] = [
  "SOGG",
  "OGG",
  "OBL",
  "TEMPO",
  "LUOGO",
  "MODO",
  "CAUSA",
  "LIMITE",
  "CONDIZIONE",
  "SCOPO",
  "CONCESSIONE",
  "CONTRASTO",
  "SEQUENZA",
  "CONTENUTO",
  "COLLEGATO",
  "SATELLITE_DI",
];

const fieldClass =
  "rounded border border-input bg-background px-2 py-1.5 text-sm";

type EventQueryPanelProps = {
  onHighlightsChange?: (highlights: Record<string, HighlightKind>) => void;
};

function emptySpec(): EventQuerySpec {
  return {};
}

function ResultList({
  risultato,
  kind,
}: {
  risultato: EventQueryRisultato | null;
  kind: HighlightKind;
}) {
  if (!risultato) {
    return <p className="text-xs text-muted-foreground">Nessun risultato.</p>;
  }
  const eventi = risultato.eventi ?? [];
  return (
    <div className="flex flex-col gap-1">
      <p className="text-xs text-foreground" data-testid={`${kind}-count`}>
        <span
          className="mr-1 inline-block h-2 w-2 rounded-full"
          style={{ backgroundColor: HIGHLIGHT_COLORS[kind] }}
          aria-hidden
        />
        {eventi.length} eventi
        {risultato.archi?.length ? ` · ${risultato.archi.length} archi` : ""}
      </p>
      <ul className="max-h-36 overflow-y-auto text-xs">
        {eventi.map((evento: EventQueryEvento) => (
          <li key={evento.id} className="truncate">
            {evento.lemma ?? "—"}{" "}
            <code className="rounded bg-muted px-1">{evento.id}</code>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function EventQueryPanel({ onHighlightsChange }: EventQueryPanelProps) {
  const [tab, setTab] = useState("structured");
  const [spec, setSpec] = useState<EventQuerySpec>(emptySpec);
  const [finestraDa, setFinestraDa] = useState("");
  const [finestraA, setFinestraA] = useState("");
  const [nlText, setNlText] = useState("");

  const [structuredResult, setStructuredResult] =
    useState<EventStructuredQueryResponse | null>(null);
  const [nlResult, setNlResult] = useState<EventNlQueryResponse | null>(null);
  const [structuredPinned, setStructuredPinned] = useState(false);
  const [nlPinned, setNlPinned] = useState(false);
  const [structuredBusy, setStructuredBusy] = useState(false);
  const [nlBusy, setNlBusy] = useState(false);
  const [structuredError, setStructuredError] = useState<string | null>(null);
  const [nlError, setNlError] = useState<string | null>(null);

  useEffect(() => {
    onHighlightsChange?.(
      mergeHighlights(
        idsFromQueryResult(structuredResult?.risultato),
        idsFromQueryResult(nlResult?.risultato),
      ),
    );
  }, [structuredResult, nlResult, onHighlightsChange]);

  function patchSpec<K extends keyof EventQuerySpec>(
    key: K,
    value: EventQuerySpec[K] | "",
  ) {
    setSpec((prev) => {
      const next = { ...prev };
      if (value === "" || value == null) {
        delete next[key];
      } else {
        next[key] = value;
      }
      return next;
    });
  }

  function buildSpec(): EventQuerySpec {
    const next: EventQuerySpec = { ...spec };
    if (finestraDa || finestraA) {
      next.finestra_tempo_assoluto = {
        ...(finestraDa ? { da: finestraDa } : {}),
        ...(finestraA ? { a: finestraA } : {}),
      };
    } else {
      delete next.finestra_tempo_assoluto;
    }
    return next;
  }

  async function onStructuredSubmit(event: FormEvent) {
    event.preventDefault();
    setStructuredBusy(true);
    setStructuredError(null);
    try {
      const result = await queryStructured(buildSpec());
      if (!structuredPinned) setStructuredResult(result);
    } catch (err) {
      setStructuredError(err instanceof Error ? err.message : "Query fallita");
    } finally {
      setStructuredBusy(false);
    }
  }

  async function onNlSubmit(event: FormEvent) {
    event.preventDefault();
    if (!nlText.trim()) return;
    setNlBusy(true);
    setNlError(null);
    try {
      const result = await queryNl(nlText);
      if (!nlPinned) setNlResult(result);
    } catch (err) {
      setNlError(err instanceof Error ? err.message : "Query fallita");
    } finally {
      setNlBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader className="p-4 pb-2">
        <CardTitle className="text-sm">Query eventi</CardTitle>
      </CardHeader>
      <CardContent className="p-4 pt-0">
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList className="grid h-auto w-full grid-cols-2">
            <TabsTrigger value="structured" className="text-xs">
              Strutturata
            </TabsTrigger>
            <TabsTrigger value="nl" className="text-xs">
              Linguaggio naturale
            </TabsTrigger>
          </TabsList>

          <TabsContent value="structured" className="mt-3">
            <form
              onSubmit={(e) => void onStructuredSubmit(e)}
              className="flex flex-col gap-2"
            >
              <label className="flex flex-col gap-1 text-xs">
                <span>lemma</span>
                <input
                  className={fieldClass}
                  name="lemma"
                  value={spec.lemma ?? ""}
                  onChange={(e) => patchSpec("lemma", e.target.value)}
                />
              </label>
              <label className="flex flex-col gap-1 text-xs">
                <span>piano</span>
                <select
                  className={fieldClass}
                  name="piano"
                  value={spec.piano ?? ""}
                  onChange={(e) =>
                    patchSpec("piano", e.target.value as PianoNarrativo | "")
                  }
                >
                  <option value="">—</option>
                  {PIANI.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1 text-xs">
                <span>fattualità</span>
                <select
                  className={fieldClass}
                  name="fattualita"
                  value={spec.fattualita ?? ""}
                  onChange={(e) =>
                    patchSpec("fattualita", e.target.value as Fattualita | "")
                  }
                >
                  <option value="">—</option>
                  {FATTUALITA.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1 text-xs">
                <span>tempo</span>
                <select
                  className={fieldClass}
                  name="tempo"
                  value={spec.tempo ?? ""}
                  onChange={(e) =>
                    patchSpec("tempo", e.target.value as TempoVerbale | "")
                  }
                >
                  <option value="">—</option>
                  {TEMPI.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1 text-xs">
                <span>fonte</span>
                <input
                  className={fieldClass}
                  name="fonte"
                  value={spec.fonte ?? ""}
                  onChange={(e) => patchSpec("fonte", e.target.value)}
                />
              </label>
              <label className="flex flex-col gap-1 text-xs">
                <span>tipo_relazione</span>
                <select
                  className={fieldClass}
                  name="tipo_relazione"
                  value={spec.tipo_relazione ?? ""}
                  onChange={(e) =>
                    patchSpec(
                      "tipo_relazione",
                      e.target.value as TipoRelazione | "",
                    )
                  }
                >
                  <option value="">—</option>
                  {RELAZIONI.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
              <div className="grid grid-cols-2 gap-2">
                <label className="flex flex-col gap-1 text-xs">
                  <span>finestra da</span>
                  <input
                    className={fieldClass}
                    name="finestra_da"
                    value={finestraDa}
                    onChange={(e) => setFinestraDa(e.target.value)}
                    placeholder="YYYY-MM-DD"
                  />
                </label>
                <label className="flex flex-col gap-1 text-xs">
                  <span>finestra a</span>
                  <input
                    className={fieldClass}
                    name="finestra_a"
                    value={finestraA}
                    onChange={(e) => setFinestraA(e.target.value)}
                    placeholder="YYYY-MM-DD"
                  />
                </label>
              </div>
              <label className="flex flex-col gap-1 text-xs">
                <span>documento</span>
                <input
                  className={fieldClass}
                  name="documento"
                  value={spec.documento ?? ""}
                  onChange={(e) => patchSpec("documento", e.target.value)}
                />
              </label>
              <label className="flex flex-col gap-1 text-xs">
                <span>traversal</span>
                <select
                  className={fieldClass}
                  name="traversal"
                  value={spec.traversal ?? ""}
                  onChange={(e) =>
                    patchSpec(
                      "traversal",
                      e.target.value as TraversalKind | "",
                    )
                  }
                >
                  <option value="">—</option>
                  {TRAVERSALS.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1 text-xs">
                <span>traversal_target</span>
                <input
                  className={fieldClass}
                  name="traversal_target"
                  value={spec.traversal_target ?? ""}
                  onChange={(e) =>
                    patchSpec("traversal_target", e.target.value)
                  }
                />
              </label>
              <div className="flex flex-wrap gap-2">
                <Button type="submit" size="sm" disabled={structuredBusy}>
                  {structuredBusy ? "Invio…" : "Esegui"}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant={structuredPinned ? "default" : "outline"}
                  onClick={() => setStructuredPinned((v) => !v)}
                >
                  {structuredPinned ? "Sblocca risultato" : "Blocca risultato"}
                </Button>
              </div>
              {structuredError ? (
                <p className="text-xs text-destructive" role="alert">
                  {structuredError}
                </p>
              ) : null}
              <ResultList
                risultato={structuredResult?.risultato ?? null}
                kind="structured"
              />
            </form>
          </TabsContent>

          <TabsContent value="nl" className="mt-3">
            <form
              onSubmit={(e) => void onNlSubmit(e)}
              className="flex flex-col gap-2"
            >
              <label className="flex flex-col gap-1 text-xs">
                <span>testo</span>
                <textarea
                  className={cn(fieldClass, "min-h-[6rem]")}
                  name="testo"
                  value={nlText}
                  onChange={(e) => setNlText(e.target.value)}
                  placeholder="Chi è arrivato prima della pioggia?"
                />
              </label>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="submit"
                  size="sm"
                  disabled={nlBusy || !nlText.trim()}
                >
                  {nlBusy ? "Invio…" : "Esegui"}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant={nlPinned ? "default" : "outline"}
                  onClick={() => setNlPinned((v) => !v)}
                >
                  {nlPinned ? "Sblocca risultato" : "Blocca risultato"}
                </Button>
              </div>
              {nlError ? (
                <p className="text-xs text-destructive" role="alert">
                  {nlError}
                </p>
              ) : null}
              {nlResult?.spec_generata ? (
                <div className="flex flex-col gap-1">
                  <span className="text-xs text-muted-foreground">
                    spec_generata
                  </span>
                  <pre
                    className="max-h-36 overflow-auto rounded bg-muted p-2 text-[10px] leading-snug"
                    data-testid="spec-generata"
                  >
                    {JSON.stringify(nlResult.spec_generata, null, 2)}
                  </pre>
                </div>
              ) : null}
              <ResultList
                risultato={nlResult?.risultato ?? null}
                kind="nl"
              />
            </form>
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}
