"""Allen interval algebra (1983) + path-consistency network.

MICRO stage 5 subsystem. Stdlib only: no app.models, no app.core, no third-party.
Composition table is the standard 13x13 Allen 1983 table (endpoint enumeration).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

# ruff: noqa: E501

RelazioneAllen = Literal[
    "before",
    "after",
    "meets",
    "met_by",
    "overlaps",
    "overlapped_by",
    "during",
    "contains",
    "starts",
    "started_by",
    "finishes",
    "finished_by",
    "equals",
]

ALLEN_13: tuple[str, ...] = (
    "before",
    "after",
    "meets",
    "met_by",
    "overlaps",
    "overlapped_by",
    "during",
    "contains",
    "starts",
    "started_by",
    "finishes",
    "finished_by",
    "equals",
)

_ALLEN_SET: frozenset[str] = frozenset(ALLEN_13)

INVERSE: dict[str, str] = {
    "before": "after",
    "after": "before",
    "meets": "met_by",
    "met_by": "meets",
    "overlaps": "overlapped_by",
    "overlapped_by": "overlaps",
    "during": "contains",
    "contains": "during",
    "starts": "started_by",
    "started_by": "starts",
    "finishes": "finished_by",
    "finished_by": "finishes",
    "equals": "equals",
}

_COMPOSE: dict[tuple[str, str], frozenset[str]] = {
    ('before', 'before'): frozenset({'before'}),
    ('before', 'after'): frozenset({'after', 'before', 'contains', 'during', 'equals', 'finished_by', 'finishes', 'meets', 'met_by', 'overlapped_by', 'overlaps', 'started_by', 'starts'}),
    ('before', 'meets'): frozenset({'before'}),
    ('before', 'met_by'): frozenset({'before', 'during', 'meets', 'overlaps', 'starts'}),
    ('before', 'overlaps'): frozenset({'before'}),
    ('before', 'overlapped_by'): frozenset({'before', 'during', 'meets', 'overlaps', 'starts'}),
    ('before', 'during'): frozenset({'before', 'during', 'meets', 'overlaps', 'starts'}),
    ('before', 'contains'): frozenset({'before'}),
    ('before', 'starts'): frozenset({'before'}),
    ('before', 'started_by'): frozenset({'before'}),
    ('before', 'finishes'): frozenset({'before', 'during', 'meets', 'overlaps', 'starts'}),
    ('before', 'finished_by'): frozenset({'before'}),
    ('before', 'equals'): frozenset({'before'}),
    ('after', 'before'): frozenset({'after', 'before', 'contains', 'during', 'equals', 'finished_by', 'finishes', 'meets', 'met_by', 'overlapped_by', 'overlaps', 'started_by', 'starts'}),
    ('after', 'after'): frozenset({'after'}),
    ('after', 'meets'): frozenset({'after', 'during', 'finishes', 'met_by', 'overlapped_by'}),
    ('after', 'met_by'): frozenset({'after'}),
    ('after', 'overlaps'): frozenset({'after', 'during', 'finishes', 'met_by', 'overlapped_by'}),
    ('after', 'overlapped_by'): frozenset({'after'}),
    ('after', 'during'): frozenset({'after', 'during', 'finishes', 'met_by', 'overlapped_by'}),
    ('after', 'contains'): frozenset({'after'}),
    ('after', 'starts'): frozenset({'after', 'during', 'finishes', 'met_by', 'overlapped_by'}),
    ('after', 'started_by'): frozenset({'after'}),
    ('after', 'finishes'): frozenset({'after'}),
    ('after', 'finished_by'): frozenset({'after'}),
    ('after', 'equals'): frozenset({'after'}),
    ('meets', 'before'): frozenset({'before'}),
    ('meets', 'after'): frozenset({'after', 'contains', 'met_by', 'overlapped_by', 'started_by'}),
    ('meets', 'meets'): frozenset({'before'}),
    ('meets', 'met_by'): frozenset({'equals', 'finished_by', 'finishes'}),
    ('meets', 'overlaps'): frozenset({'before'}),
    ('meets', 'overlapped_by'): frozenset({'during', 'overlaps', 'starts'}),
    ('meets', 'during'): frozenset({'during', 'overlaps', 'starts'}),
    ('meets', 'contains'): frozenset({'before'}),
    ('meets', 'starts'): frozenset({'meets'}),
    ('meets', 'started_by'): frozenset({'meets'}),
    ('meets', 'finishes'): frozenset({'during', 'overlaps', 'starts'}),
    ('meets', 'finished_by'): frozenset({'before'}),
    ('meets', 'equals'): frozenset({'meets'}),
    ('met_by', 'before'): frozenset({'before', 'contains', 'finished_by', 'meets', 'overlaps'}),
    ('met_by', 'after'): frozenset({'after'}),
    ('met_by', 'meets'): frozenset({'equals', 'started_by', 'starts'}),
    ('met_by', 'met_by'): frozenset({'after'}),
    ('met_by', 'overlaps'): frozenset({'during', 'finishes', 'overlapped_by'}),
    ('met_by', 'overlapped_by'): frozenset({'after'}),
    ('met_by', 'during'): frozenset({'during', 'finishes', 'overlapped_by'}),
    ('met_by', 'contains'): frozenset({'after'}),
    ('met_by', 'starts'): frozenset({'during', 'finishes', 'overlapped_by'}),
    ('met_by', 'started_by'): frozenset({'after'}),
    ('met_by', 'finishes'): frozenset({'met_by'}),
    ('met_by', 'finished_by'): frozenset({'met_by'}),
    ('met_by', 'equals'): frozenset({'met_by'}),
    ('overlaps', 'before'): frozenset({'before'}),
    ('overlaps', 'after'): frozenset({'after', 'contains', 'met_by', 'overlapped_by', 'started_by'}),
    ('overlaps', 'meets'): frozenset({'before'}),
    ('overlaps', 'met_by'): frozenset({'contains', 'overlapped_by', 'started_by'}),
    ('overlaps', 'overlaps'): frozenset({'before', 'meets', 'overlaps'}),
    ('overlaps', 'overlapped_by'): frozenset({'contains', 'during', 'equals', 'finished_by', 'finishes', 'overlapped_by', 'overlaps', 'started_by', 'starts'}),
    ('overlaps', 'during'): frozenset({'during', 'overlaps', 'starts'}),
    ('overlaps', 'contains'): frozenset({'before', 'contains', 'finished_by', 'meets', 'overlaps'}),
    ('overlaps', 'starts'): frozenset({'overlaps'}),
    ('overlaps', 'started_by'): frozenset({'contains', 'finished_by', 'overlaps'}),
    ('overlaps', 'finishes'): frozenset({'during', 'overlaps', 'starts'}),
    ('overlaps', 'finished_by'): frozenset({'before', 'meets', 'overlaps'}),
    ('overlaps', 'equals'): frozenset({'overlaps'}),
    ('overlapped_by', 'before'): frozenset({'before', 'contains', 'finished_by', 'meets', 'overlaps'}),
    ('overlapped_by', 'after'): frozenset({'after'}),
    ('overlapped_by', 'meets'): frozenset({'contains', 'finished_by', 'overlaps'}),
    ('overlapped_by', 'met_by'): frozenset({'after'}),
    ('overlapped_by', 'overlaps'): frozenset({'contains', 'during', 'equals', 'finished_by', 'finishes', 'overlapped_by', 'overlaps', 'started_by', 'starts'}),
    ('overlapped_by', 'overlapped_by'): frozenset({'after', 'met_by', 'overlapped_by'}),
    ('overlapped_by', 'during'): frozenset({'during', 'finishes', 'overlapped_by'}),
    ('overlapped_by', 'contains'): frozenset({'after', 'contains', 'met_by', 'overlapped_by', 'started_by'}),
    ('overlapped_by', 'starts'): frozenset({'during', 'finishes', 'overlapped_by'}),
    ('overlapped_by', 'started_by'): frozenset({'after', 'met_by', 'overlapped_by'}),
    ('overlapped_by', 'finishes'): frozenset({'overlapped_by'}),
    ('overlapped_by', 'finished_by'): frozenset({'contains', 'overlapped_by', 'started_by'}),
    ('overlapped_by', 'equals'): frozenset({'overlapped_by'}),
    ('during', 'before'): frozenset({'before'}),
    ('during', 'after'): frozenset({'after'}),
    ('during', 'meets'): frozenset({'before'}),
    ('during', 'met_by'): frozenset({'after'}),
    ('during', 'overlaps'): frozenset({'before', 'during', 'meets', 'overlaps', 'starts'}),
    ('during', 'overlapped_by'): frozenset({'after', 'during', 'finishes', 'met_by', 'overlapped_by'}),
    ('during', 'during'): frozenset({'during'}),
    ('during', 'contains'): frozenset({'after', 'before', 'contains', 'during', 'equals', 'finished_by', 'finishes', 'meets', 'met_by', 'overlapped_by', 'overlaps', 'started_by', 'starts'}),
    ('during', 'starts'): frozenset({'during'}),
    ('during', 'started_by'): frozenset({'after', 'during', 'finishes', 'met_by', 'overlapped_by'}),
    ('during', 'finishes'): frozenset({'during'}),
    ('during', 'finished_by'): frozenset({'before', 'during', 'meets', 'overlaps', 'starts'}),
    ('during', 'equals'): frozenset({'during'}),
    ('contains', 'before'): frozenset({'before', 'contains', 'finished_by', 'meets', 'overlaps'}),
    ('contains', 'after'): frozenset({'after', 'contains', 'met_by', 'overlapped_by', 'started_by'}),
    ('contains', 'meets'): frozenset({'contains', 'finished_by', 'overlaps'}),
    ('contains', 'met_by'): frozenset({'contains', 'overlapped_by', 'started_by'}),
    ('contains', 'overlaps'): frozenset({'contains', 'finished_by', 'overlaps'}),
    ('contains', 'overlapped_by'): frozenset({'contains', 'overlapped_by', 'started_by'}),
    ('contains', 'during'): frozenset({'contains', 'during', 'equals', 'finished_by', 'finishes', 'overlapped_by', 'overlaps', 'started_by', 'starts'}),
    ('contains', 'contains'): frozenset({'contains'}),
    ('contains', 'starts'): frozenset({'contains', 'finished_by', 'overlaps'}),
    ('contains', 'started_by'): frozenset({'contains'}),
    ('contains', 'finishes'): frozenset({'contains', 'overlapped_by', 'started_by'}),
    ('contains', 'finished_by'): frozenset({'contains'}),
    ('contains', 'equals'): frozenset({'contains'}),
    ('starts', 'before'): frozenset({'before'}),
    ('starts', 'after'): frozenset({'after'}),
    ('starts', 'meets'): frozenset({'before'}),
    ('starts', 'met_by'): frozenset({'met_by'}),
    ('starts', 'overlaps'): frozenset({'before', 'meets', 'overlaps'}),
    ('starts', 'overlapped_by'): frozenset({'during', 'finishes', 'overlapped_by'}),
    ('starts', 'during'): frozenset({'during'}),
    ('starts', 'contains'): frozenset({'before', 'contains', 'finished_by', 'meets', 'overlaps'}),
    ('starts', 'starts'): frozenset({'starts'}),
    ('starts', 'started_by'): frozenset({'equals', 'started_by', 'starts'}),
    ('starts', 'finishes'): frozenset({'during'}),
    ('starts', 'finished_by'): frozenset({'before', 'meets', 'overlaps'}),
    ('starts', 'equals'): frozenset({'starts'}),
    ('started_by', 'before'): frozenset({'before', 'contains', 'finished_by', 'meets', 'overlaps'}),
    ('started_by', 'after'): frozenset({'after'}),
    ('started_by', 'meets'): frozenset({'contains', 'finished_by', 'overlaps'}),
    ('started_by', 'met_by'): frozenset({'met_by'}),
    ('started_by', 'overlaps'): frozenset({'contains', 'finished_by', 'overlaps'}),
    ('started_by', 'overlapped_by'): frozenset({'overlapped_by'}),
    ('started_by', 'during'): frozenset({'during', 'finishes', 'overlapped_by'}),
    ('started_by', 'contains'): frozenset({'contains'}),
    ('started_by', 'starts'): frozenset({'equals', 'started_by', 'starts'}),
    ('started_by', 'started_by'): frozenset({'started_by'}),
    ('started_by', 'finishes'): frozenset({'overlapped_by'}),
    ('started_by', 'finished_by'): frozenset({'contains'}),
    ('started_by', 'equals'): frozenset({'started_by'}),
    ('finishes', 'before'): frozenset({'before'}),
    ('finishes', 'after'): frozenset({'after'}),
    ('finishes', 'meets'): frozenset({'meets'}),
    ('finishes', 'met_by'): frozenset({'after'}),
    ('finishes', 'overlaps'): frozenset({'during', 'overlaps', 'starts'}),
    ('finishes', 'overlapped_by'): frozenset({'after', 'met_by', 'overlapped_by'}),
    ('finishes', 'during'): frozenset({'during'}),
    ('finishes', 'contains'): frozenset({'after', 'contains', 'met_by', 'overlapped_by', 'started_by'}),
    ('finishes', 'starts'): frozenset({'during'}),
    ('finishes', 'started_by'): frozenset({'after', 'met_by', 'overlapped_by'}),
    ('finishes', 'finishes'): frozenset({'finishes'}),
    ('finishes', 'finished_by'): frozenset({'equals', 'finished_by', 'finishes'}),
    ('finishes', 'equals'): frozenset({'finishes'}),
    ('finished_by', 'before'): frozenset({'before'}),
    ('finished_by', 'after'): frozenset({'after', 'contains', 'met_by', 'overlapped_by', 'started_by'}),
    ('finished_by', 'meets'): frozenset({'meets'}),
    ('finished_by', 'met_by'): frozenset({'contains', 'overlapped_by', 'started_by'}),
    ('finished_by', 'overlaps'): frozenset({'overlaps'}),
    ('finished_by', 'overlapped_by'): frozenset({'contains', 'overlapped_by', 'started_by'}),
    ('finished_by', 'during'): frozenset({'during', 'overlaps', 'starts'}),
    ('finished_by', 'contains'): frozenset({'contains'}),
    ('finished_by', 'starts'): frozenset({'overlaps'}),
    ('finished_by', 'started_by'): frozenset({'contains'}),
    ('finished_by', 'finishes'): frozenset({'equals', 'finished_by', 'finishes'}),
    ('finished_by', 'finished_by'): frozenset({'finished_by'}),
    ('finished_by', 'equals'): frozenset({'finished_by'}),
    ('equals', 'before'): frozenset({'before'}),
    ('equals', 'after'): frozenset({'after'}),
    ('equals', 'meets'): frozenset({'meets'}),
    ('equals', 'met_by'): frozenset({'met_by'}),
    ('equals', 'overlaps'): frozenset({'overlaps'}),
    ('equals', 'overlapped_by'): frozenset({'overlapped_by'}),
    ('equals', 'during'): frozenset({'during'}),
    ('equals', 'contains'): frozenset({'contains'}),
    ('equals', 'starts'): frozenset({'starts'}),
    ('equals', 'started_by'): frozenset({'started_by'}),
    ('equals', 'finishes'): frozenset({'finishes'}),
    ('equals', 'finished_by'): frozenset({'finished_by'}),
    ('equals', 'equals'): frozenset({'equals'}),
}


def _as_rel(rel: str) -> str:
    if rel not in _ALLEN_SET:
        raise ValueError(f"unknown Allen relation: {rel!r}")
    return rel


def inverse_set(rels: Iterable[str]) -> frozenset[str]:
    return frozenset(INVERSE[_as_rel(rel)] for rel in rels)


def compose(a: RelazioneAllen, b: RelazioneAllen) -> frozenset[RelazioneAllen]:
    """Allen composition. Complete 13x13 table (frozenset of possible relations)."""
    key = (_as_rel(str(a)), _as_rel(str(b)))
    got = _COMPOSE.get(key)
    if not got:
        return frozenset(_ALLEN_SET)  # type: ignore[return-value]
    return frozenset(got)  # type: ignore[return-value]


def compose_sets(A: frozenset, B: frozenset) -> frozenset:
    """Union of compose(a,b) for a in A, b in B."""
    if not A or not B:
        return frozenset()
    out: set[str] = set()
    for left in A:
        if left not in _ALLEN_SET:
            continue
        for right in B:
            if right not in _ALLEN_SET:
                continue
            out.update(compose(left, right))  # type: ignore[arg-type]
    return frozenset(out)


class AllenNetwork:
    """Constraint network over Allen base relations, closed by path-consistency."""

    def __init__(self) -> None:
        self._nodes: set[str] = set()
        self._constraints: dict[tuple[str, str], frozenset[str]] = {}
        self._direct: list[tuple[str, str]] = []

    def add_constraint(self, i: str, j: str, rels: set[str]) -> None:
        """Intersect with existing; also set inverse on (j,i)."""
        if not i or not j or i == j:
            return
        raw = set(rels)
        allowed = {rel for rel in raw if rel in _ALLEN_SET}
        if raw and not allowed:
            return
        self._nodes.add(i)
        self._nodes.add(j)
        current = set(self.inferred(i, j))
        new = current & allowed if raw else set()
        self._constraints[(i, j)] = frozenset(new)
        self._constraints[(j, i)] = inverse_set(new)
        self._direct.append((i, j))

    def nodes(self) -> frozenset[str]:
        return frozenset(self._nodes)

    def inferred(self, i: str, j: str) -> frozenset:
        if i == j:
            return frozenset({"equals"})
        got = self._constraints.get((i, j))
        if got is not None:
            return got
        return frozenset(_ALLEN_SET)

    def snapshot(
        self,
    ) -> tuple[set[str], dict[tuple[str, str], frozenset[str]], list[tuple[str, str]]]:
        return (
            set(self._nodes),
            dict(self._constraints),
            list(self._direct),
        )

    def restore(
        self,
        snap: tuple[set[str], dict[tuple[str, str], frozenset[str]], list[tuple[str, str]]],
    ) -> None:
        self._nodes, self._constraints, self._direct = snap[0], snap[1], snap[2]

    def empty_pairs(self) -> list[tuple[str, str]]:
        return [pair for pair, rels in self._constraints.items() if not rels]

    def path_consistent(self) -> bool:
        """Allen path-consistency: Cij <- Cij intersect compose(Cik, Ckj). Fixpoint.

        Empty Cij => inconsistent.
        """
        if any(not rels for rels in self._constraints.values()):
            return False
        nodes = list(self._nodes)
        changed = True
        while changed:
            changed = False
            for i in nodes:
                for k in nodes:
                    if k == i:
                        continue
                    cik = self.inferred(i, k)
                    if not cik:
                        return False
                    for j in nodes:
                        if j == i or j == k:
                            continue
                        composed = compose_sets(cik, self.inferred(k, j))
                        current = self.inferred(i, j)
                        new = current & composed
                        if new != current:
                            if not new:
                                self._constraints[(i, j)] = frozenset()
                                self._constraints[(j, i)] = frozenset()
                                return False
                            self._constraints[(i, j)] = frozenset(new)
                            self._constraints[(j, i)] = inverse_set(new)
                            changed = True
        return True

    def path_between(self, start: str, goal: str) -> list[str] | None:
        """Node-id path along recorded (undirected) direct constraints."""
        if start == goal:
            return [start]
        graph: dict[str, set[str]] = {}
        for left, right in self._direct:
            graph.setdefault(left, set()).add(right)
            graph.setdefault(right, set()).add(left)
        seen = {start}
        stack: list[tuple[str, list[str]]] = [(start, [start])]
        while stack:
            cur, path = stack.pop()
            for nxt in graph.get(cur, ()):
                if nxt == goal:
                    return path + [nxt]
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append((nxt, path + [nxt]))
        return None


__all__ = [
    "ALLEN_13",
    "AllenNetwork",
    "INVERSE",
    "RelazioneAllen",
    "compose",
    "compose_sets",
    "inverse_set",
]
