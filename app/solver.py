"""Non-crossing track-pair audit solver for a column of silicon-strip hits.

Hits are strictly ordered points on a line. A candidate pair is an arc
between two distinct hits. Two arcs ``(a, b)`` and ``(c, d)`` cross iff
``a < c < b < d`` or the symmetric order holds; arcs may nest
(``a < c < d < b``) but may never share an endpoint.

The service must, in strict priority order:

1. maximize the number of paired hits (twice the number of chosen pairs);
2. minimize the sum of residuals;
3. break remaining ties by the lexicographically smallest sequence of
   candidate ids ordered by left-endpoint position.

It also reports the arbitrary-precision number of solutions attaining
objectives 1 and 2, the canonical solution, the unpaired hits, and for
every candidate whether it is required (in every optimum), optional (in
some but not all) or never (in no optimum).

The solver is an interval DP over hit ranges: ``F(i, j)`` is the optimum
on hits ``i..j``. Hit ``i`` is either unmatched (``F(i+1, j)``) or paired
with some ``k`` (``i < k <= j``), contributing the pair plus the inner
range ``i+1..k-1`` (nested arcs) and the right range ``k+1..j``.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from typing import Any

MIN_HITS = 4
MAX_HITS = 180
MAX_CANDIDATES = 4000


class ValidationError(Exception):
    """A request-level validation error, pinned to a request field path."""

    def __init__(self, message: str, path: str):
        super().__init__(message)
        self.message = message
        self.path = path


def _identity_key(value: Any) -> str:
    """Stable hashable identity for a JSON id value (1 and True stay distinct)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _id_sort_key(value: Any) -> tuple:
    """Deterministic total order for candidate ids of mixed JSON types."""
    if value is None:
        return (0,)
    if isinstance(value, bool):
        return (1, int(value))
    if isinstance(value, (int, float)):
        return (2, float(value))
    if isinstance(value, str):
        return (3, value)
    if isinstance(value, (list, tuple)):
        return (4, tuple(_id_sort_key(v) for v in value))
    if isinstance(value, dict):
        return (
            5,
            tuple(sorted((str(k), _id_sort_key(v)) for k, v in value.items())),
        )
    return (9, repr(value))


def _is_real_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_nonneg_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _validate(payload: Any):
    if not isinstance(payload, dict):
        raise ValidationError("request body must be a JSON object", "")

    if "hits" not in payload:
        raise ValidationError("'hits' is required", "hits")
    if "candidates" not in payload:
        raise ValidationError("'candidates' is required", "candidates")
    raw_hits = payload["hits"]
    raw_candidates = payload["candidates"]
    if not isinstance(raw_hits, list):
        raise ValidationError("'hits' must be an array", "hits")
    if not isinstance(raw_candidates, list):
        raise ValidationError("'candidates' must be an array", "candidates")

    n = len(raw_hits)
    if n < MIN_HITS or n > MAX_HITS:
        raise ValidationError(
            f"number of hits must be in [{MIN_HITS}, {MAX_HITS}], got {n}", "hits"
        )
    if len(raw_candidates) > MAX_CANDIDATES:
        raise ValidationError(
            f"number of candidates must not exceed {MAX_CANDIDATES}, "
            f"got {len(raw_candidates)}",
            "candidates",
        )

    hit_ext_ids: list[Any] = []
    hit_index: dict[str, int] = {}
    positions: list[float] = []
    previous_position: float | None = None
    for idx, hit in enumerate(raw_hits):
        path = f"hits[{idx}]"
        if not isinstance(hit, dict):
            raise ValidationError("hit must be an object", path)
        if "id" not in hit:
            raise ValidationError("hit id is required", f"{path}.id")
        hit_id = hit["id"]
        id_key = _identity_key(hit_id)
        if id_key in hit_index:
            raise ValidationError("hit id must be unique", f"{path}.id")
        hit_index[id_key] = idx + 1
        hit_ext_ids.append(hit_id)

        if "position" not in hit:
            raise ValidationError("hit position is required", f"{path}.position")
        position = hit["position"]
        if not _is_real_number(position) or not math.isfinite(position):
            raise ValidationError(
                "position must be a finite number", f"{path}.position"
            )
        if previous_position is not None and not position > previous_position:
            raise ValidationError(
                "hit positions must be strictly increasing", f"{path}.position"
            )
        previous_position = position
        positions.append(position)

    # Each candidate: {"id", "l", "r" (1-based hit indices), "residual"}
    candidates: list[dict] = []
    seen_candidate_ids: set[str] = set()
    seen_pairs: set[tuple[int, int]] = set()
    for idx, cand in enumerate(raw_candidates):
        path = f"candidates[{idx}]"
        if not isinstance(cand, dict):
            raise ValidationError("candidate must be an object", path)
        if "id" not in cand:
            raise ValidationError("candidate id is required", f"{path}.id")
        cand_id = cand["id"]
        cand_id_key = _identity_key(cand_id)
        if cand_id_key in seen_candidate_ids:
            raise ValidationError("candidate id must be unique", f"{path}.id")
        seen_candidate_ids.add(cand_id_key)

        if "endpoints" not in cand:
            raise ValidationError(
                "candidate endpoints are required", f"{path}.endpoints"
            )
        endpoints = cand["endpoints"]
        if not isinstance(endpoints, list) or len(endpoints) != 2:
            raise ValidationError(
                "endpoints must be an array of exactly two hit ids",
                f"{path}.endpoints",
            )
        left_raw, right_raw = endpoints
        left_key = _identity_key(left_raw)
        right_key = _identity_key(right_raw)
        if left_key not in hit_index or right_key not in hit_index:
            raise ValidationError(
                "both endpoints must reference known hit ids",
                f"{path}.endpoints",
            )
        left = hit_index[left_key]
        right = hit_index[right_key]
        if left == right:
            raise ValidationError(
                "endpoints of a pair must be distinct hits", f"{path}.endpoints"
            )
        if left > right:
            left, right = right, left

        if "residual" not in cand:
            raise ValidationError("residual is required", f"{path}.residual")
        residual = cand["residual"]
        if not _is_nonneg_int(residual):
            raise ValidationError(
                "residual must be a non-negative integer", f"{path}.residual"
            )

        pair_key = (left, right)
        if pair_key in seen_pairs:
            raise ValidationError(
                "duplicate candidate with the same (unordered) endpoint pair",
                f"{path}.endpoints",
            )
        seen_pairs.add(pair_key)

        candidates.append(
            {"id": cand_id, "l": left, "r": right, "residual": residual}
        )

    return n, hit_ext_ids, positions, candidates


def _solve(n: int, hit_ext_ids: list, candidates: list[dict]):
    # edges_by_left[i]: candidates whose left endpoint is hit i, sorted by right
    edges_by_left: list[list[tuple[int, int, int]]] = [
        [] for _ in range(n + 1)
    ]
    for ci, cand in enumerate(candidates):
        edges_by_left[cand["l"]].append((cand["r"], cand["residual"], ci))
    for edges in edges_by_left:
        edges.sort()

    # A cell is (pairs, residual, solution_count, canonical_ci_sequence).
    empty = (0, 0, 1, ())
    cells: list[list[tuple | None]] = [
        [None] * (n + 2) for _ in range(n + 2)
    ]

    def get(i: int, j: int):
        return empty if i > j else cells[i][j]

    for length in range(n):  # length = j - i, from 0 to n-1
        for i in range(1, n - length + 1):
            j = i + length

            # Option A: hit i stays unpaired.
            skip = get(i + 1, j)
            options: list[tuple[tuple[int, int], tuple, int]] = [
                ((skip[0], -skip[1]), skip[3], -1)
            ]
            # Option B: pair hit i with hit k (nested inner + free right range).
            for k, residual, ci in edges_by_left[i]:
                if k > j:
                    break
                inner = get(i + 1, k - 1)
                right = get(k + 1, j)
                pairs = inner[0] + 1 + right[0]
                total_residual = inner[1] + residual + right[1]
                # Left endpoints are ordered: i, then inner (i+1..k-1),
                # then the free right range (k+1..j).
                sequence = (ci,) + inner[3] + right[3]
                options.append(((pairs, -total_residual), sequence, ci))

            best_value = max(option[0] for option in options)
            tied = [option for option in options if option[0] == best_value]

            count = 0
            for _, sequence, ci in tied:
                if ci == -1:
                    count += skip[2]
                else:
                    k = candidates[ci]["r"]
                    inner = get(i + 1, k - 1)
                    right = get(k + 1, j)
                    count += inner[2] * right[2]

            canonical = min(
                (seq for _, seq, _ in tied),
                key=lambda seq: tuple(
                    _id_sort_key(candidates[ci]["id"]) for ci in seq
                ),
            )

            cells[i][j] = (
                best_value[0],
                -best_value[1],
                count,
                canonical,
            )

    root = cells[1][n]
    pairs_total, residual_total, solution_count, canonical = root

    @lru_cache(maxsize=None)
    def analyze(i: int, j: int):
        """Bitmasks (used_in_some_optimum, required_in_every_optimum).

        Bit c is set iff candidate index c belongs to the set. Integer
        bitmasks keep memory bounded even on dense 4000-edge instances.
        """
        if i > j:
            return 0, 0
        cell = get(i, j)
        best_value = (cell[0], -cell[1])

        used = 0
        required = (1 << len(candidates)) - 1  # intersection identity

        skip = get(i + 1, j)
        if (skip[0], -skip[1]) == best_value:
            skip_used, skip_required = analyze(i + 1, j)
            used |= skip_used
            required &= skip_required

        for k, _, ci in edges_by_left[i]:
            if k > j:
                break
            inner = get(i + 1, k - 1)
            right = get(k + 1, j)
            value = (
                inner[0] + 1 + right[0],
                -(inner[1] + candidates[ci]["residual"] + right[1]),
            )
            if value != best_value:
                continue
            inner_used, inner_required = analyze(i + 1, k - 1)
            right_used, right_required = analyze(k + 1, j)
            edge_bit = 1 << ci
            used |= inner_used | right_used | edge_bit
            # Within this option the edge is always taken, alongside every
            # edge forced inside either subproblem; between options we AND.
            required &= edge_bit | inner_required | right_required

        return used, required

    used_mask, required_mask = analyze(1, n)
    optional_mask = used_mask & ~required_mask

    paired_hit_indexes: set[int] = set()
    canonical_pairs = []
    for ci in canonical:
        cand = candidates[ci]
        paired_hit_indexes.add(cand["l"])
        paired_hit_indexes.add(cand["r"])
        canonical_pairs.append(
            {
                "id": cand["id"],
                "endpoints": [
                    hit_ext_ids[cand["l"] - 1],
                    hit_ext_ids[cand["r"] - 1],
                ],
                "residual": cand["residual"],
            }
        )

    unpaired_hit_ids = [
        hit_ext_ids[idx - 1]
        for idx in range(1, n + 1)
        if idx not in paired_hit_indexes
    ]

    def ids_from_mask(mask: int) -> list:
        return [
            candidates[ci]["id"]
            for ci in range(len(candidates))
            if mask & (1 << ci)
        ]

    all_mask = (1 << len(candidates)) - 1
    return {
        "solution_count": str(solution_count),
        "paired_hit_count": 2 * pairs_total,
        "total_residual": residual_total,
        "canonical_pair_ids": [pair["id"] for pair in canonical_pairs],
        "canonical_pairs": canonical_pairs,
        "unpaired_hit_ids": unpaired_hit_ids,
        "candidate_audit": {
            "required": ids_from_mask(required_mask),
            "optional": ids_from_mask(optional_mask),
            "never": ids_from_mask(all_mask & ~used_mask),
        },
    }


def audit(payload: Any) -> dict:
    """Validate a request payload and compute the audit result."""
    n, hit_ext_ids, _positions, candidates = _validate(payload)
    return _solve(n, hit_ext_ids, candidates)
