"""Tests for the non-crossing track-pair audit solver."""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from solver import ValidationError, audit  # noqa: E402


def make_hits(n, prefix="h"):
    return [{"id": f"{prefix}{i}", "position": i} for i in range(1, n + 1)]


def pair(cid, a, b, residual=0):
    return {"id": cid, "endpoints": [f"h{a}", f"h{b}"], "residual": residual}


def solve(n, pairs, hits=None):
    return audit(
        {
            "hits": hits if hits is not None else make_hits(n),
            "candidates": pairs,
        }
    )


class TestBasicSolving(unittest.TestCase):
    def test_two_disjoint_pairs(self):
        result = solve(4, [pair("c1", 1, 2, 5), pair("c2", 3, 4, 5)])
        self.assertEqual(result["solution_count"], "1")
        self.assertEqual(result["paired_hit_count"], 4)
        self.assertEqual(result["total_residual"], 10)
        self.assertEqual(result["canonical_pair_ids"], ["c1", "c2"])
        self.assertEqual(result["unpaired_hit_ids"], [])
        self.assertEqual(result["candidate_audit"]["required"], ["c1", "c2"])
        self.assertEqual(result["candidate_audit"]["optional"], [])
        self.assertEqual(result["candidate_audit"]["never"], [])

    def test_unpaired_hits_reported_from_canonical(self):
        result = solve(4, [pair("only", 1, 2, 0)])
        self.assertEqual(result["paired_hit_count"], 2)
        self.assertEqual(result["canonical_pair_ids"], ["only"])
        self.assertEqual(result["unpaired_hit_ids"], ["h3", "h4"])
        self.assertEqual(result["candidate_audit"]["required"], ["only"])

    def test_empty_candidates_unique_empty_solution(self):
        result = solve(4, [])
        self.assertEqual(result["solution_count"], "1")
        self.assertEqual(result["paired_hit_count"], 0)
        self.assertEqual(result["total_residual"], 0)
        self.assertEqual(result["canonical_pair_ids"], [])
        self.assertEqual(result["canonical_pairs"], [])
        self.assertEqual(result["unpaired_hit_ids"], ["h1", "h2", "h3", "h4"])
        audit_groups = result["candidate_audit"]
        self.assertEqual(
            audit_groups, {"required": [], "optional": [], "never": []}
        )


class TestCrossingAndNesting(unittest.TestCase):
    def test_cheap_crossing_decoy_loses_to_cardinality(self):
        # Greedy-by-residual grabs the zero-residual crossing edges and is
        # then limited to one pair; the expensive disjoint pair set wins on
        # the primary objective (number of paired hits).
        result = solve(
            4,
            [
                pair("x1", 1, 3, 0),
                pair("x2", 2, 4, 0),
                pair("a", 1, 2, 100),
                pair("b", 3, 4, 100),
            ],
        )
        self.assertEqual(result["paired_hit_count"], 4)
        self.assertEqual(result["total_residual"], 200)
        self.assertEqual(result["canonical_pair_ids"], ["a", "b"])
        groups = result["candidate_audit"]
        self.assertEqual(sorted(groups["required"]), ["a", "b"])
        self.assertEqual(sorted(groups["never"]), ["x1", "x2"])
        self.assertEqual(groups["optional"], [])
        self.assertEqual(result["solution_count"], "1")

    def test_nested_matching_ties_with_disjoint_matching(self):
        # Both solutions pair all four hits with residual 0:
        #   S1 = (1,4) nesting (2,3);  S2 = (1,2) + (3,4)
        result = solve(
            4,
            [
                pair("p1", 1, 4, 0),
                pair("p2", 2, 3, 0),
                pair("p3", 1, 2, 0),
                pair("p4", 3, 4, 0),
            ],
        )
        self.assertEqual(result["paired_hit_count"], 4)
        self.assertEqual(result["total_residual"], 0)
        self.assertEqual(result["solution_count"], "2")
        # Canonical tie-break: lexicographically smallest id sequence in
        # left-endpoint order. Both sequences start with the pair on hit 1;
        # "p1" < "p3", so the nested solution is canonical.
        self.assertEqual(result["canonical_pair_ids"], ["p1", "p2"])
        groups = result["candidate_audit"]
        self.assertEqual(groups["required"], [])
        self.assertEqual(sorted(groups["optional"]), ["p1", "p2", "p3", "p4"])
        self.assertEqual(groups["never"], [])
        self.assertEqual(result["unpaired_hit_ids"], [])

    def test_canonical_prefers_smaller_id_at_first_divergence(self):
        # Same geometry as above, but ids on the nested solution sort later.
        result = solve(
            4,
            [
                pair("zz_outer", 1, 4, 0),
                pair("zz_inner", 2, 3, 0),
                pair("aa_left", 1, 2, 0),
                pair("aa_right", 3, 4, 0),
            ],
        )
        self.assertEqual(result["solution_count"], "2")
        self.assertEqual(
            result["canonical_pair_ids"], ["aa_left", "aa_right"]
        )

    def test_residual_breaks_cardinality_tie(self):
        # S1 nested costs 0, S2 disjoint costs 2: residual decides.
        result = solve(
            4,
            [
                pair("outer", 1, 4, 0),
                pair("inner", 2, 3, 0),
                pair("left", 1, 2, 1),
                pair("right", 3, 4, 1),
            ],
        )
        self.assertEqual(result["solution_count"], "1")
        self.assertEqual(result["canonical_pair_ids"], ["outer", "inner"])
        self.assertEqual(sorted(result["candidate_audit"]["never"]),
                         ["left", "right"])

    def test_catalan_count_for_all_pairs_eight_hits(self):
        # All pairs available at zero residual: only perfect (max-cardinality)
        # matchings are level-1/2 optimal; non-crossing perfect matchings on
        # 8 points are counted by Catalan C4 = 14.
        pairs = [
            pair(f"e_{a}_{b}", a, b, 0)
            for a in range(1, 9)
            for b in range(a + 1, 9)
        ]
        result = solve(8, pairs)
        self.assertEqual(result["paired_hit_count"], 8)
        self.assertEqual(result["solution_count"], "14")


class TestAuditClassification(unittest.TestCase):
    def test_required_edge_shared_by_all_optima(self):
        # Six hits, two equally good perfect matchings, both use (1,2):
        #   S1 = (1,2),(3,4),(5,6)
        #   S2 = (1,2),(3,6) nesting (4,5)
        result = solve(
            6,
            [
                pair("r", 1, 2, 0),
                pair("a", 3, 4, 0),
                pair("b", 5, 6, 0),
                pair("c", 3, 6, 0),
                pair("d", 4, 5, 0),
            ],
        )
        self.assertEqual(result["solution_count"], "2")
        groups = result["candidate_audit"]
        self.assertEqual(groups["required"], ["r"])
        self.assertEqual(sorted(groups["optional"]), ["a", "b", "c", "d"])
        self.assertEqual(groups["never"], [])

    def test_edge_only_in_costlier_solution_is_never(self):
        result = solve(
            4,
            [
                pair("cheap1", 1, 2, 0),
                pair("cheap2", 3, 4, 0),
                pair("pricey1", 1, 4, 100),
                pair("pricey2", 2, 3, 100),
            ],
        )
        self.assertEqual(result["solution_count"], "1")
        self.assertEqual(
            sorted(result["candidate_audit"]["never"]),
            ["pricey1", "pricey2"],
        )

    def test_expensive_edge_that_blocks_two_pairs_never_chosen(self):
        result = solve(
            4,
            [
                pair("mid", 2, 3, 0),
                pair("side1", 1, 2, 5),
                pair("side2", 3, 4, 5),
            ],
        )
        self.assertEqual(result["paired_hit_count"], 4)
        self.assertEqual(result["total_residual"], 10)
        self.assertEqual(result["canonical_pair_ids"], ["side1", "side2"])
        self.assertEqual(result["candidate_audit"]["never"], ["mid"])


class TestCounting(unittest.TestCase):
    def test_solution_count_multiplies_across_independent_blocks(self):
        # k independent 4-hit blocks, each with 2 perfect solutions => 2**k.
        k = 40
        n = 4 * k
        pairs = []
        for block in range(k):
            base = 4 * block
            pairs += [
                pair(f"o{block}", base + 1, base + 4, 0),
                pair(f"i{block}", base + 2, base + 3, 0),
                pair(f"l{block}", base + 1, base + 2, 0),
                pair(f"r{block}", base + 3, base + 4, 0),
            ]
        result = solve(n, pairs)
        self.assertEqual(result["paired_hit_count"], n)
        self.assertEqual(result["solution_count"], str(2**k))
        # No edge is forced when every block has two ways.
        self.assertEqual(result["candidate_audit"]["required"], [])

    def test_max_size_input_runs_quickly(self):
        n = 180
        pairs = []
        # Four thousand unique pairs, residuals varied.
        distance = 1
        while len(pairs) < 4000:
            for a in range(1, n - distance + 1):
                b = a + distance
                pairs.append(
                    pair(f"p{a}_{b}", a, b, (a * 7 + b * 3) % 11)
                )
                if len(pairs) == 4000:
                    break
            distance += 1
        start = time.monotonic()
        result = solve(n, pairs)
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 10.0)
        self.assertTrue(result["paired_hit_count"] % 2 == 0)
        # Count must be an exact decimal integer string.
        self.assertRegex(result["solution_count"], r"^\d+$")


class TestValidation(unittest.TestCase):
    def _assert_error(self, payload, path):
        with self.assertRaises(ValidationError) as ctx:
            audit(payload)
        self.assertEqual(ctx.exception.path, path)
        return ctx.exception.message

    def test_too_few_hits(self):
        self._assert_error({"hits": make_hits(3), "candidates": []}, "hits")

    def test_too_many_hits(self):
        self._assert_error(
            {"hits": make_hits(181), "candidates": []}, "hits"
        )

    def test_too_many_candidates(self):
        n = 180
        pairs = []
        d = 1
        while len(pairs) <= 4000:
            for a in range(1, n - d + 1):
                pairs.append(pair(f"q{len(pairs)}", a, a + d, 0))
                if len(pairs) > 4000:
                    break
            d += 1
        self._assert_error(
            {"hits": make_hits(n), "candidates": pairs}, "candidates"
        )

    def test_non_increasing_position(self):
        hits = make_hits(4)
        hits[2]["position"] = 2
        self._assert_error({"hits": hits, "candidates": []}, "hits[2].position")

    def test_duplicate_hit_id(self):
        hits = make_hits(4)
        hits[3]["id"] = "h1"
        self._assert_error({"hits": hits, "candidates": []}, "hits[3].id")

    def test_unknown_endpoint(self):
        self._assert_error(
            {"hits": make_hits(4), "candidates": [pair("x", 1, 9)]},
            "candidates[0].endpoints",
        )

    def test_endpoint_pair_with_same_hit(self):
        self._assert_error(
            {
                "hits": make_hits(4),
                "candidates": [{"id": "x", "endpoints": ["h1", "h1"],
                                "residual": 0}],
            },
            "candidates[0].endpoints",
        )

    def test_duplicate_endpoint_pair_even_reversed(self):
        self._assert_error(
            {
                "hits": make_hits(4),
                "candidates": [
                    pair("x", 1, 2, 0),
                    {"id": "y", "endpoints": ["h2", "h1"], "residual": 3},
                ],
            },
            "candidates[1].endpoints",
        )

    def test_duplicate_candidate_id(self):
        self._assert_error(
            {
                "hits": make_hits(4),
                "candidates": [pair("z", 1, 2), pair("z", 3, 4)],
            },
            "candidates[1].id",
        )

    def test_negative_residual(self):
        self._assert_error(
            {
                "hits": make_hits(4),
                "candidates": [
                    {"id": "x", "endpoints": ["h1", "h2"], "residual": -1}
                ],
            },
            "candidates[0].residual",
        )

    def test_boolean_residual_rejected(self):
        self._assert_error(
            {
                "hits": make_hits(4),
                "candidates": [
                    {"id": "x", "endpoints": ["h1", "h2"], "residual": True}
                ],
            },
            "candidates[0].residual",
        )

    def test_missing_fields_have_paths(self):
        self._assert_error({"candidates": []}, "hits")
        self._assert_error({"hits": make_hits(4)}, "candidates")
        self._assert_error(
            {"hits": make_hits(4), "candidates": [{"id": "x"}]},
            "candidates[0].endpoints",
        )

    def test_integer_hit_ids_and_float_positions(self):
        payload = {
            "hits": [
                {"id": 10, "position": 0.0},
                {"id": 20, "position": 0.5},
                {"id": 30, "position": 1.0},
                {"id": 40, "position": 2.25},
            ],
            "candidates": [
                {"id": "a", "endpoints": [10, 20], "residual": 0},
                {"id": "b", "endpoints": [30, 40], "residual": 1},
            ],
        }
        result = audit(payload)
        self.assertEqual(result["canonical_pairs"][0]["endpoints"], [10, 20])
        self.assertEqual(result["unpaired_hit_ids"], [])

    def test_boundary_sizes_accepted(self):
        solve(4, [])
        solve(180, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
