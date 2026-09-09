"""M-ponte — verify macro (M2) arcs against micro (stage 3/4/5) evidence.

After each zone expansion: for every macro arc that touches that zone and
another already-expanded zone, look for a justifying micro arc. If none,
``verificato=False`` on the macro (append-only: never deleted).
``verificato`` stays ``None`` until both endpoints are expanded
(``None`` ≠ ``False``).

Pure functions for M-flash to call from ``espandi_zona``. No flash flag,
no endpoints, no pipeline rewrite.
"""

from __future__ import annotations

from app.models.event_graph import ArcoEvento, EventoRisolto, SottoGrafo
from app.pipeline.event_graph.zona_edges import ArcoZona
from app.pipeline.event_graph.zona_segmentation import Zona

_ARG_TYPES = frozenset({"SOGG", "OGG", "OBL", "TEMPO", "LUOGO", "MODO"})
_UNDIRECTED = "COLLEGATO"


def evento_nella_zona(evento: EventoRisolto, zona: Zona) -> bool:
    """Offsets overlap/contained in ``[zona.offset_inizio, zona.offset_fine)``."""
    start = evento.offset_inizio
    end = evento.offset_fine
    if start is None and end is None:
        return False
    if start is None:
        start = end
    if end is None:
        end = start
    assert start is not None and end is not None
    z0, z1 = zona.offset_inizio, zona.offset_fine
    if end == start:
        return z0 <= start < z1
    lo, hi = (start, end) if start <= end else (end, start)
    return lo < z1 and z0 < hi


def _testa_key(evento: EventoRisolto) -> tuple[int, int, int, int, str]:
    return (
        evento.posizione_doc if evento.posizione_doc is not None else -1,
        evento.posizione_chunk if evento.posizione_chunk is not None else -1,
        evento.offset_inizio if evento.offset_inizio is not None else -1,
        evento.offset_fine if evento.offset_fine is not None else -1,
        evento.id or "",
    )


def testa_della_zona(zona: Zona, sotto: SottoGrafo) -> EventoRisolto | None:
    """``e_testa`` event in the zone; if several, last by posizione/offset."""
    teste = [
        event
        for event in sotto.eventi
        if event.e_testa and not event.fuso_in and evento_nella_zona(event, zona)
    ]
    if not teste:
        return None
    return max(teste, key=_testa_key)


testa_della_zona.__test__ = False  # pytest collects test* names


def _evento_by_id(sotto: SottoGrafo, event_id: str) -> EventoRisolto | None:
    for event in sotto.eventi:
        if event.id == event_id:
            return event
    return None


def _micro_zone_sides(
    macro: ArcoZona,
    micro: ArcoEvento,
    zone_by_id: dict[str, Zona],
    sotto: SottoGrafo,
) -> tuple[bool, bool, bool, bool] | None:
    ev_da = _evento_by_id(sotto, micro.da_id)
    ev_a = _evento_by_id(sotto, micro.a_id)
    z_da = zone_by_id.get(macro.da_id)
    z_a = zone_by_id.get(macro.a_id)
    if ev_da is None or ev_a is None or z_da is None or z_a is None:
        return None
    return (
        evento_nella_zona(ev_da, z_da),
        evento_nella_zona(ev_a, z_a),
        evento_nella_zona(ev_da, z_a),
        evento_nella_zona(ev_a, z_da),
    )


def direzione_compatibile(
    macro: ArcoZona,
    micro: ArcoEvento,
    zone_by_id: dict[str, Zona] | None = None,
    sotto: SottoGrafo | None = None,
) -> bool:
    """Same orientation, or both reversed together.

    Inverse pair (micro flipped vs macro) is NOT compatible for CAUSA/PRECEDE.
    COLLEGATO is undirected-ish: either orientation OK.
    """
    if zone_by_id is None or sotto is None:
        return False
    sides = _micro_zone_sides(macro, micro, zone_by_id, sotto)
    if sides is None:
        return False
    same, inverse = (sides[0] and sides[1]), (sides[2] and sides[3])
    if not same and not inverse:
        return False
    macro_tipo = str(macro.tipo)
    micro_tipo = str(micro.tipo)
    if macro_tipo == _UNDIRECTED or micro_tipo == _UNDIRECTED:
        return True
    return same


def tipo_compatibile(macro_tipo: object, micro_tipo: object) -> bool:
    """Same type → True.

    Micro PRECEDE justifies macro PRECEDE.
    Micro CAUSA justifies macro CAUSA (not the reverse unless same).
    A typed micro (CAUSA/PRECEDE/...) MAY justify macro COLLEGATO.
    Macro CAUSA is NOT justified by micro COLLEGATO or CONTRASTO.
    """
    macro = str(macro_tipo)
    micro = str(micro_tipo)
    if macro == micro:
        return True
    if macro == _UNDIRECTED and micro != _UNDIRECTED and micro not in _ARG_TYPES:
        return True
    return False


def giustifica(
    macro: ArcoZona,
    micro: ArcoEvento,
    zone_by_id: dict[str, Zona],
    sotto: SottoGrafo,
) -> bool:
    """tipo + direzione compatible, and micro endpoints sit in the two zones (one each)."""
    if not tipo_compatibile(macro.tipo, micro.tipo):
        return False
    sides = _micro_zone_sides(macro, micro, zone_by_id, sotto)
    if sides is None:
        return False
    one_each = (sides[0] and sides[1]) or (sides[2] and sides[3])
    if not one_each:
        return False
    return direzione_compatibile(macro, micro, zone_by_id, sotto)


def _union_sotto(
    sotto_per_zona: dict[str, SottoGrafo] | SottoGrafo,
    zona_ids: tuple[str, str],
) -> SottoGrafo:
    if isinstance(sotto_per_zona, SottoGrafo):
        return sotto_per_zona
    merged = SottoGrafo()
    seen_event: set[str] = set()
    for key in zona_ids:
        piece = sotto_per_zona.get(key)
        if piece is None:
            continue
        for event in piece.eventi:
            if event.id and event.id in seen_event:
                continue
            if event.id:
                seen_event.add(event.id)
            merged.eventi.append(event)
        merged.archi.extend(piece.archi)
    return merged


def verifica_dopo_espansione(
    zona_id: str,
    zone: list[Zona],
    archi_macro: list[ArcoZona],
    sotto_per_zona: dict[str, SottoGrafo] | SottoGrafo,
) -> list[ArcoZona]:
    """Mark ``zona_id`` expanded; verify incident macros against the other side.

    For each macro arc incident to ``zona_id``:
      other = the other endpoint
      if other.espansa is False: leave verificato=None
      if both expanded:
        collect micro arcs from sotto of both zones
        if any micro giustifica → verificato=True
        else → verificato=False
    Never set True/False when only one side is expanded.
    Never delete arcs.
    """
    zone_by_id = {item.id: item for item in zone}
    current = zone_by_id.get(zona_id)
    if current is not None:
        current.espansa = True

    for arco in archi_macro:
        if zona_id not in (arco.da_id, arco.a_id):
            continue
        other_id = arco.a_id if arco.da_id == zona_id else arco.da_id
        if other_id == zona_id:
            continue
        other = zone_by_id.get(other_id)
        if other is None or not other.espansa:
            continue
        sotto = _union_sotto(sotto_per_zona, (zona_id, other_id))
        if any(giustifica(arco, micro, zone_by_id, sotto) for micro in sotto.archi):
            arco.verificato = True
        else:
            arco.verificato = False
    return archi_macro


__all__ = [
    "direzione_compatibile",
    "evento_nella_zona",
    "giustifica",
    "testa_della_zona",
    "tipo_compatibile",
    "verifica_dopo_espansione",
]
