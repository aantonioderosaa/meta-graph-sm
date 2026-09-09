"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  countFor,
  filtersEqual,
  groupedArches,
  swatchForArc,
  swatchForNode,
  swatchForTrait,
  toggleLegendFilter,
  traitEntries,
  type LegendFilter,
} from "@/lib/event-graph/legend";
import type {
  CatalogArc,
  EventGraphCatalog,
  EventGraphStats,
} from "@/lib/event-graph/types";
import { cn } from "@/lib/utils";

const TRAIT_ORDER = [
  "tempo",
  "polarita",
  "modalizzato",
  "iterativita",
  "fattualita",
  "piano",
  "fonte",
  "catena",
];

type EventLegendProps = {
  catalog?: EventGraphCatalog | null;
  stats?: EventGraphStats | null;
  filter?: LegendFilter;
  onFilterChange?: (filter: LegendFilter) => void;
};

function Swatch({ color, dashed }: { color: string; dashed?: boolean }) {
  return (
    <span
      aria-hidden
      className={cn(
        "mt-0.5 inline-block h-2.5 w-2.5 shrink-0 rounded-sm border",
        dashed && "border-dashed",
      )}
      style={{ backgroundColor: color, borderColor: color }}
    />
  );
}

function LegendRow({
  selected,
  onClick,
  swatch,
  dashed,
  arrow,
  title,
  meaning,
  count,
}: {
  selected: boolean;
  onClick: () => void;
  swatch: string;
  dashed?: boolean;
  arrow?: string;
  title: string;
  meaning?: string;
  count: number;
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={cn(
        "flex w-full items-start gap-1.5 rounded px-1 py-1 text-left text-[11px] leading-snug",
        "hover:bg-accent hover:text-accent-foreground",
        selected && "bg-accent text-accent-foreground ring-1 ring-ring",
      )}
    >
      <Swatch color={swatch} dashed={dashed} />
      {arrow ? (
        <span className="shrink-0 font-medium text-muted-foreground" aria-hidden>
          {arrow}
        </span>
      ) : null}
      <span className="min-w-0 flex-1">
        <span className="font-medium">{title}</span>
        {meaning ? (
          <span className="ml-1 truncate text-muted-foreground">{meaning}</span>
        ) : null}
      </span>
      <span className="shrink-0 tabular-nums text-muted-foreground">{count}</span>
    </button>
  );
}

export function EventLegend({
  catalog,
  stats,
  filter = null,
  onFilterChange,
}: EventLegendProps) {
  const groups = groupedArches(catalog);
  const traitKeys = TRAIT_ORDER.filter((key) => catalog?.traits?.[key] != null);

  function select(next: NonNullable<LegendFilter>) {
    onFilterChange?.(toggleLegendFilter(filter, next));
  }

  return (
    <Card>
      <CardHeader className="p-4 pb-2">
        <CardTitle className="text-sm">Legenda</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 p-4 pt-0">
        <div className="flex flex-col gap-2">
          {groups.map((group) => (
            <section key={group.famiglia} aria-label={group.famiglia}>
              <h3 className="mb-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                {group.famiglia}
              </h3>
              <ul className="flex flex-col">
                {group.arches.map((arc: CatalogArc, index: number) => {
                  const next = { kind: "arco" as const, key: "tipo", value: arc.tipo };
                  return (
                    <li key={`${group.famiglia}-${arc.tipo}-${index}`}>
                      <LegendRow
                        selected={filtersEqual(filter, next)}
                        onClick={() => select(next)}
                        swatch={swatchForArc(arc.tipo)}
                        arrow="→"
                        title={arc.tipo}
                        meaning={arc.significato}
                        count={countFor(stats, next)}
                      />
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>

        <details className="rounded border border-border px-2 py-1.5">
          <summary className="cursor-pointer text-xs font-medium">Nodi e tratti</summary>
          <div className="mt-2 flex flex-col gap-2">
            <section aria-label="Nodi">
              <h3 className="mb-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                Nodi
              </h3>
              <ul>
                {(catalog?.nodes ?? []).map((node) => {
                  const next = { kind: "nodo" as const, key: "tipo", value: node.id };
                  return (
                    <li key={node.id}>
                      <LegendRow
                        selected={filtersEqual(filter, next)}
                        onClick={() => select(next)}
                        swatch={swatchForNode(node.id)}
                        dashed={node.id === "Quarantena"}
                        title={`:${node.label || node.id}`}
                        count={countFor(stats, next)}
                      />
                    </li>
                  );
                })}
              </ul>
            </section>

            {traitKeys.map((key) => (
              <section key={key} aria-label={key}>
                <h3 className="mb-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                  {key}
                </h3>
                <ul>
                  {traitEntries(catalog?.traits[key]).map((entry) => {
                    const next = { kind: "tratto" as const, key, value: entry.value };
                    return (
                      <li key={`${key}-${entry.value}`}>
                        <LegendRow
                          selected={filtersEqual(filter, next)}
                          onClick={() => select(next)}
                          swatch={swatchForTrait(key, entry.value)}
                          title={entry.value}
                          meaning={entry.meaning}
                          count={countFor(stats, next)}
                        />
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))}
          </div>
        </details>
      </CardContent>
    </Card>
  );
}
