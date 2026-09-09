"""M3 — three structured views on the same zone graph.

None is the navigation default: conseguenze, protagonista, and cronologia
are always computed as independent queries. Deterministic, no LLM.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, replace
from typing import Literal

from app.pipeline.event_graph.zona_edges import ArcoZona
from app.pipeline.event_graph.zona_segmentation import Zona

ViaNome = Literal["conseguenze", "protagonista", "cronologia"]

VIA_NOMI: tuple[ViaNome, ...] = ("conseguenze", "protagonista", "cronologia")

# Causal-temporal types carry weight (= confidenza) on the conseguenze view.
_CAUSALE_TEMPORALE = frozenset({"CAUSA", "PRECEDE", "LIMITE", "CONDIZIONE", "SCOPO"})
# Explicit zero-weight types for this view (excluded from the path search).
_CONSEGUENZE_ZERO = frozenset({"COLLEGATO", "CONTRASTO", "SEQUENZA", "CONTENUTO"})

_TEMPORALE_PURO = frozenset({"PRECEDE", "LIMITE"})
_SPINA_DEBOLE = frozenset({"COLLEGATO", "SEQUENZA"})

ArcoKey = tuple[str, str, str]


@dataclass
class ViaPrincipale:
    nome: ViaNome
    zona_ids: list[str]
    arco_keys: list[ArcoKey]


def calcola_vie(zone: list[Zona], archi: list[ArcoZona]) -> dict[ViaNome, ViaPrincipale]:
    """Always returns all three keys. Paths may be empty or a single node."""
    zone_map = {item.id: item for item in zone}
    return {
        "conseguenze": _via_conseguenze(zone, archi, zone_map),
        "protagonista": _via_protagonista(zone, archi, zone_map),
        "cronologia": _via_cronologia(zone, archi, zone_map),
    }


def annota_vie(
    zone: list[Zona],
    archi: list[ArcoZona],
    vie: dict[ViaNome, ViaPrincipale] | None = None,
) -> tuple[list[Zona], list[ArcoZona]]:
    """Copy su_via_principale lists onto Zona and ArcoZona (add field if missing)."""
    computed = vie if vie is not None else calcola_vie(zone, archi)
    zona_names: dict[str, list[str]] = {
        item.id: _copy_names(item) for item in zone
    }
    arco_names: list[list[str]] = [_copy_names(item) for item in archi]

    for nome in VIA_NOMI:
        via = computed.get(nome)
        if via is None:
            continue
        on_zone = set(via.zona_ids)
        on_arco = set(via.arco_keys)
        for zona_id in on_zone:
            if zona_id in zona_names:
                zona_names[zona_id] = _append_unique(zona_names[zona_id], nome)
        for index, arco in enumerate(archi):
            if _arco_key(arco) in on_arco:
                arco_names[index] = _append_unique(arco_names[index], nome)

    out_zone = [_with_names(item, zona_names.get(item.id, [])) for item in zone]
    out_archi = [
        _with_names(item, names) for item, names in zip(archi, arco_names, strict=True)
    ]
    return out_zone, out_archi


def _empty(nome: ViaNome) -> ViaPrincipale:
    return ViaPrincipale(nome=nome, zona_ids=[], arco_keys=[])


def _tipo(arco: ArcoZona) -> str:
    return str(arco.tipo)


def _arco_key(arco: ArcoZona) -> ArcoKey:
    return (arco.da_id, arco.a_id, _tipo(arco))


def _ord(zone_map: dict[str, Zona], zona_id: str) -> int:
    item = zone_map.get(zona_id)
    return item.ordinale if item is not None else 0


def _copy_names(obj: object) -> list[str]:
    raw = getattr(obj, "su_via_principale", None)
    if not raw:
        return []
    return [str(name) for name in raw]


def _append_unique(names: list[str], nome: str) -> list[str]:
    if nome in names:
        return names
    return [*names, nome]


def _with_names(obj: Zona | ArcoZona, names: list[str]) -> Zona | ArcoZona:
    owned = list(names)
    try:
        return replace(obj, su_via_principale=owned)
    except TypeError:
        object.__setattr__(obj, "su_via_principale", owned)
        return obj


def _norm_entita(name: str) -> str:
    return name.strip().casefold()


def _entita_set(zona: Zona) -> set[str]:
    found: set[str] = set()
    for raw in zona.entita_principali:
        if raw and raw.strip():
            found.add(_norm_entita(raw))
    return found


def _dedup_archi(archi: list[ArcoZona]) -> list[ArcoZona]:
    best: dict[ArcoKey, ArcoZona] = {}
    for arco in archi:
        if arco.da_id == arco.a_id:
            continue
        key = _arco_key(arco)
        prev = best.get(key)
        if prev is None or arco.confidenza > prev.confidenza:
            best[key] = arco
    return list(best.values())


def _acyclic_edges(
    archi: list[ArcoZona], zone_map: dict[str, Zona]
) -> list[ArcoZona]:
    """Prefer increasing-ordinale edges; otherwise drop back-edges."""
    usable = [arco for arco in archi if arco.da_id != arco.a_id]
    forward = [
        arco
        for arco in usable
        if _ord(zone_map, arco.da_id) < _ord(zone_map, arco.a_id)
    ]
    if forward:
        return forward

    ordered = sorted(
        usable,
        key=lambda arco: (
            _ord(zone_map, arco.da_id),
            arco.da_id,
            _ord(zone_map, arco.a_id),
            arco.a_id,
            _tipo(arco),
        ),
    )
    adj: dict[str, list[ArcoZona]] = defaultdict(list)
    for arco in ordered:
        adj[arco.da_id].append(arco)
    nodes = sorted(
        {arco.da_id for arco in ordered} | {arco.a_id for arco in ordered},
        key=lambda zona_id: (_ord(zone_map, zona_id), zona_id),
    )
    color: dict[str, int] = {}
    kept: list[ArcoZona] = []
    white, gray, black = 0, 1, 2

    def visit(node: str) -> None:
        color[node] = gray
        for arco in adj.get(node, []):
            dest = arco.a_id
            state = color.get(dest, white)
            if state == gray:
                continue
            kept.append(arco)
            if state == white:
                visit(dest)
        color[node] = black

    for node in nodes:
        if color.get(node, white) == white:
            visit(node)
    return kept


def _topo_nodes(
    nodes: list[str],
    edges: list[ArcoZona],
    zone_map: dict[str, Zona],
) -> list[str]:
    adj: dict[str, list[str]] = defaultdict(list)
    indeg: dict[str, int] = {node: 0 for node in nodes}
    for arco in edges:
        if arco.da_id not in indeg or arco.a_id not in indeg:
            continue
        adj[arco.da_id].append(arco.a_id)
        indeg[arco.a_id] += 1
    ready = sorted(
        [node for node in nodes if indeg[node] == 0],
        key=lambda zona_id: (_ord(zone_map, zona_id), zona_id),
    )
    topo: list[str] = []
    while ready:
        node = ready.pop(0)
        topo.append(node)
        for dest in sorted(adj[node], key=lambda zona_id: (_ord(zone_map, zona_id), zona_id)):
            indeg[dest] -= 1
            if indeg[dest] == 0:
                ready.append(dest)
                ready.sort(key=lambda zona_id: (_ord(zone_map, zona_id), zona_id))
    leftover = [node for node in nodes if node not in set(topo)]
    leftover.sort(key=lambda zona_id: (_ord(zone_map, zona_id), zona_id))
    return topo + leftover


def _reconstruct(
    end: str, pred: dict[str, tuple[str, ArcoZona] | None]
) -> tuple[list[str], list[ArcoKey]]:
    zona_ids = [end]
    keys: list[ArcoKey] = []
    seen: set[str] = set()
    current = end
    while pred.get(current) is not None:
        if current in seen:
            break
        seen.add(current)
        prev, arco = pred[current]  # type: ignore[misc]
        keys.append(_arco_key(arco))
        zona_ids.append(prev)
        current = prev
    zona_ids.reverse()
    keys.reverse()
    return zona_ids, keys


def _is_increasing(zona_ids: list[str], zone_map: dict[str, Zona]) -> bool:
    return all(
        _ord(zone_map, zona_ids[i]) < _ord(zone_map, zona_ids[i + 1])
        for i in range(len(zona_ids) - 1)
    )


def _longest_weighted_path(
    edges: list[ArcoZona],
    zone_map: dict[str, Zona],
    *,
    weight_fn,
    maximize_weight: bool,
) -> tuple[list[str], list[ArcoKey]]:
    edges = _acyclic_edges(_dedup_archi(edges), zone_map)
    if not edges:
        return [], []
    nodes = sorted(
        {arco.da_id for arco in edges} | {arco.a_id for arco in edges},
        key=lambda zona_id: (_ord(zone_map, zona_id), zona_id),
    )
    adj: dict[str, list[ArcoZona]] = defaultdict(list)
    for arco in edges:
        adj[arco.da_id].append(arco)

    pred: dict[str, tuple[str, ArcoZona] | None] = {node: None for node in nodes}
    best_w = {node: 0.0 for node in nodes}
    best_len = {node: 1 for node in nodes}

    for node in _topo_nodes(nodes, edges, zone_map):
        outgoing = sorted(
            adj[node],
            key=lambda arco: (arco.a_id, _tipo(arco)),
        )
        for arco in outgoing:
            dest = arco.a_id
            if dest not in best_w:
                continue
            weight = best_w[node] + float(weight_fn(arco))
            length = best_len[node] + 1
            ids_probe = _reconstruct(node, pred)[0] + [dest]
            new_score = (
                weight if maximize_weight else float(length),
                int(_is_increasing(ids_probe, zone_map)),
                length,
                tuple(ids_probe),
            )
            old_ids = _reconstruct(dest, pred)[0]
            old_score = (
                best_w[dest] if maximize_weight else float(best_len[dest]),
                int(_is_increasing(old_ids, zone_map)),
                best_len[dest],
                tuple(old_ids),
            )
            if pred[dest] is None or new_score > old_score:
                best_w[dest] = weight
                best_len[dest] = length
                pred[dest] = (node, arco)

    best_score: tuple | None = None
    best_path: tuple[list[str], list[ArcoKey]] = ([], [])
    for node in nodes:
        ids, keys = _reconstruct(node, pred)
        if not keys:
            continue
        score = (
            best_w[node] if maximize_weight else float(len(ids)),
            int(_is_increasing(ids, zone_map)),
            len(ids),
            tuple(ids),
        )
        if best_score is None or score > best_score:
            best_score = score
            best_path = (ids, keys)
    return best_path


def _via_conseguenze(
    zone: list[Zona],
    archi: list[ArcoZona],
    zone_map: dict[str, Zona],
) -> ViaPrincipale:
    qualifying = [
        arco
        for arco in archi
        if _tipo(arco) in _CAUSALE_TEMPORALE
        and _tipo(arco) not in _CONSEGUENZE_ZERO
    ]
    if not qualifying:
        return _empty("conseguenze")
    ids, keys = _longest_weighted_path(
        qualifying,
        zone_map,
        weight_fn=lambda arco: max(float(arco.confidenza), 0.0),
        maximize_weight=True,
    )
    return ViaPrincipale(nome="conseguenze", zona_ids=ids, arco_keys=keys)


def _forward_adj(
    archi: list[ArcoZona], zone_map: dict[str, Zona]
) -> dict[str, list[ArcoZona]]:
    edges = _acyclic_edges(_dedup_archi(list(archi)), zone_map)
    adj: dict[str, list[ArcoZona]] = defaultdict(list)
    for arco in edges:
        adj[arco.da_id].append(arco)
    for dests in adj.values():
        dests.sort(key=lambda arco: (arco.a_id, _tipo(arco), -float(arco.confidenza)))
    return adj


def _shortest_path(
    start: str,
    goal: str,
    adj: dict[str, list[ArcoZona]],
) -> tuple[list[str], list[ArcoKey]] | None:
    if start == goal:
        return [start], []
    prev: dict[str, tuple[str, ArcoZona] | None] = {start: None}
    queue: deque[str] = deque([start])
    while queue:
        current = queue.popleft()
        if current == goal:
            break
        for arco in adj.get(current, []):
            dest = arco.a_id
            if dest in prev:
                continue
            prev[dest] = (current, arco)
            queue.append(dest)
    if goal not in prev:
        return None
    return _reconstruct(goal, prev)


def _concat_segments(
    segments: list[tuple[list[str], list[ArcoKey]]],
) -> tuple[list[str], list[ArcoKey]] | None:
    if not segments:
        return None
    ids = list(segments[0][0])
    keys = list(segments[0][1])
    for next_ids, next_keys in segments[1:]:
        if not next_ids:
            return None
        if ids and next_ids[0] != ids[-1]:
            return None
        ids.extend(next_ids[1:])
        keys.extend(next_keys)
    return ids, keys


def _recurring_locations(zone: list[Zona]) -> dict[str, list[str]]:
    locations: dict[str, list[str]] = defaultdict(list)
    for item in sorted(zone, key=lambda z: (z.ordinale, z.id)):
        seen: set[str] = set()
        for raw in item.entita_principali:
            if not raw or not raw.strip():
                continue
            key = _norm_entita(raw)
            if key in seen:
                continue
            seen.add(key)
            locations[key].append(item.id)
    return {key: ids for key, ids in locations.items() if len(ids) >= 2}


def _via_protagonista(
    zone: list[Zona],
    archi: list[ArcoZona],
    zone_map: dict[str, Zona],
) -> ViaPrincipale:
    recurring = _recurring_locations(zone)
    if not recurring:
        return _empty("protagonista")
    adj = _forward_adj(archi, zone_map)
    if not adj:
        return _empty("protagonista")

    best_score: tuple | None = None
    best_path: tuple[list[str], list[ArcoKey]] = ([], [])
    entity_of: dict[str, set[str]] = {
        item.id: _entita_set(item) for item in zone
    }

    def consider(ids: list[str], keys: list[ArcoKey], entity: str) -> None:
        nonlocal best_score, best_path
        if len(ids) < 2:
            return
        occurrences = sum(1 for zona_id in ids if entity in entity_of.get(zona_id, set()))
        if occurrences < 2:
            return
        density = occurrences / max(len(ids), 1)
        overlap = 0
        for i in range(len(ids) - 1):
            overlap += len(
                entity_of.get(ids[i], set()) & entity_of.get(ids[i + 1], set())
            )
        score = (density, occurrences, overlap, len(ids), tuple(ids))
        if best_score is None or score > best_score:
            best_score = score
            best_path = (ids, keys)

    for entity, host_ids in recurring.items():
        for i, start in enumerate(host_ids):
            for j in range(i + 1, len(host_ids)):
                goal = host_ids[j]
                direct = _shortest_path(start, goal, adj)
                if direct is not None:
                    consider(direct[0], direct[1], entity)
                segments: list[tuple[list[str], list[ArcoKey]]] = []
                ok = True
                for left, right in zip(host_ids[i:j], host_ids[i + 1 : j + 1]):
                    hop = _shortest_path(left, right, adj)
                    if hop is None:
                        ok = False
                        break
                    segments.append(hop)
                if ok:
                    concatenated = _concat_segments(segments)
                    if concatenated is not None:
                        consider(concatenated[0], concatenated[1], entity)

    ids, keys = best_path
    return ViaPrincipale(nome="protagonista", zona_ids=ids, arco_keys=keys)


def _keys_along(
    zona_ids: list[str],
    archi: list[ArcoZona],
    prefer: frozenset[str] | None = None,
) -> list[ArcoKey]:
    keys: list[ArcoKey] = []
    for left, right in zip(zona_ids, zona_ids[1:]):
        candidates = [
            arco for arco in archi if arco.da_id == left and arco.a_id == right
        ]
        if not candidates:
            continue
        chosen = max(
            candidates,
            key=lambda arco: (
                1 if prefer and _tipo(arco) in prefer else 0,
                float(arco.confidenza),
                _tipo(arco),
            ),
        )
        keys.append(_arco_key(chosen))
    return keys


def _via_cronologia(
    zone: list[Zona],
    archi: list[ArcoZona],
    zone_map: dict[str, Zona],
) -> ViaPrincipale:
    precede = [arco for arco in archi if _tipo(arco) in _TEMPORALE_PURO]
    if precede:
        ids, keys = _longest_weighted_path(
            precede,
            zone_map,
            weight_fn=lambda _arco: 1.0,
            maximize_weight=False,
        )
        if ids:
            return ViaPrincipale(nome="cronologia", zona_ids=ids, arco_keys=keys)

    anchored = [
        item
        for item in sorted(zone, key=lambda z: (z.ordinale, z.id))
        if item.ancore_temporali
    ]
    if anchored:
        ids = [item.id for item in anchored]
        prefer = _TEMPORALE_PURO | _SPINA_DEBOLE
        return ViaPrincipale(
            nome="cronologia",
            zona_ids=ids,
            arco_keys=_keys_along(ids, archi, prefer),
        )

    weak = [arco for arco in archi if _tipo(arco) in _SPINA_DEBOLE]
    if weak:
        ids, keys = _longest_weighted_path(
            weak,
            zone_map,
            weight_fn=lambda _arco: 1.0,
            maximize_weight=False,
        )
        if ids:
            return ViaPrincipale(nome="cronologia", zona_ids=ids, arco_keys=keys)

    if zone:
        ids = [item.id for item in sorted(zone, key=lambda z: (z.ordinale, z.id))]
        return ViaPrincipale(
            nome="cronologia",
            zona_ids=ids,
            arco_keys=_keys_along(ids, archi, _SPINA_DEBOLE),
        )
    return _empty("cronologia")


__all__ = [
    "VIA_NOMI",
    "ViaNome",
    "ViaPrincipale",
    "annota_vie",
    "calcola_vie",
]
