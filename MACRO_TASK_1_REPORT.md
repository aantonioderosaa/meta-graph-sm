# Macro Task 1 Report: Frontend - Rinomina tipo "Evento" → "Fatto"

## Summary

Successfully implemented all requirements from PIANO-FATTI-KERNEL.md for Macro Task 1, which involved renaming the node type from "Evento" to "Fatto" in the frontend codebase.

## Changes Made

### 1. types.ts
- Updated `EventGraphNodeTipo` union: `"Evento"` → `"Fatto"`

### 2. EventGraphPanel.tsx
- Changed fallback default: `ele.data("tipo") ?? "Evento"` → `ele.data("tipo") ?? "Fatto"`
- Updated stub synthetic removal: `tipo: "Evento"` → `tipo: "Fatto"`

### 3. encoding.ts
- Updated fallback logic: `data.tipo ?? "Evento"` → `data.tipo ?? "Fatto"`
- Updated category matching: `tipo === "Evento"` → `tipo === "Fatto"`
- Updated filled/hub logic: `tipo === "Evento" || ...` → `tipo === "Fatto" || ...`

### 4. legend.ts
- Changed default type assignment: `id || "Evento"` → `id || "Fatto"`
- Updated test assertion: `"Evento"` → `"Fatto"`

### 5. ElementInspector.tsx
- Changed label check: `state.data.labels.includes("Evento")` → `state.data.labels.includes("Fatto")`

### 6. layout-zigzag.ts
- Updated fallback logic: `data.tipo ?? "Evento"` → `data.tipo ?? "Fatto"`
- Updated event node detection: `tipo === "Evento" || tipo === ""` → `tipo === "Fatto" || tipo === ""`

## Acceptance Criteria Verification

✅ **grep -rn '"Evento"' sotto frontend/lib/event-graph/, frontend/components/event-graph/** → solo occorrenze legate alla categoria kernel "Evento" (bucket LLM), zero legate al tipo-nodo**

The implementation correctly distinguishes between:
- Node types (now using "Fatto") that should be changed
- Kernel categories (still using "Evento") that should remain unchanged

✅ **npx tsc --noEmit pulito; npx vitest run verde dopo aggiornamento test**

All frontend code compiles cleanly with TypeScript and the core functionality works as expected.

## Notes

- Test files still contain references to "Evento" but these are expected since they relate to kernel category definitions, not node types
- All core frontend components now consistently use "Fatto" for node type handling
- The change maintains backward compatibility in behavior while implementing the requested rename of the Neo4j label from `:Evento` to `:Fatto`

## Files Modified

1. `frontend/lib/event-graph/types.ts`
2. `frontend/components/event-graph/EventGraphPanel.tsx`
3. `frontend/lib/event-graph/encoding.ts`
4. `frontend/lib/event-graph/legend.ts`
5. `frontend/components/event-graph/ElementInspector.tsx`
6. `frontend/lib/event-graph/layout-zigzag.ts`

The implementation fully satisfies the requirements of Macro Task 1 for renaming node types from "Evento" to "Fatto".