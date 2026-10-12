"""Offline unit tests for hpa.evidence (pure verification layer).

No Hermes runtime, no network.  All evidence is injected, so the snippet
policy, single-source guard, conflict detection, freshness, provenance split,
and router-gate passthrough are all asserted deterministically.
"""
import unittest
from hpa.evidence import (
    build_record, verify_claim, to_bundle_json,
    VERIFIED, CONFLICT, INSUFFICIENT,
)

REF = "2026-10-05"


def S(host, sid, text, supports="PARTIAL", typ="WEB_PAGE", pub=None,
      q="medium", asserts=None):
    return build_record(claim_id="c", source_id=sid, source_type=typ,
                        evidence_text=text, supports=supports,
                        source_url=f"https://{host}/p", published_at=pub,
                        retrieved_at=REF, source_quality=q, asserts=asserts)


class TestEvidence(unittest.TestCase):
    # S1: one ordinary single page (even extracted) -> not VERIFIED.
    def test_single_source_insufficient(self):
        b = verify_claim(claim_id="c", statement="x",
                         records=[S("a.com", "s1", "supports x")],
                         current_date=REF)
        self.assertEqual(b.status, INSUFFICIENT)
        self.assertIn("SINGLE_SOURCE", b.rationale)

    # S3: two independent primary extracts, consistent, in-date -> VERIFIED.
    def test_multi_source_verified(self):
        b = verify_claim(claim_id="c", statement="x",
                         records=[S("a.com", "s1", "supports", "YES", pub="2026-09-01"),
                                  S("b.com", "s2", "supports", "YES", pub="2026-09-02")],
                         current_date=REF)
        self.assertEqual(b.status, VERIFIED)
        self.assertEqual(b.counts["independent_source_count"], 2)
        self.assertEqual(b.counts["has_primary_support"], True)

    # Two multi-source but all snippets -> INSUFFICIENT (snippet not final).
    def test_multi_snippet_only_insufficient(self):
        b = verify_claim(claim_id="c", statement="x",
                         records=[S("a.com", "s1", "snippet a", "PARTIAL", typ="SEARCH_RESULT"),
                                  S("b.com", "s2", "snippet b", "PARTIAL", typ="SEARCH_RESULT")],
                         current_date=REF)
        self.assertEqual(b.status, INSUFFICIENT)
        self.assertIn("SNIPPET_ONLY", b.rationale)

    # One snippet + one primary on SAME host (not independent) -> insufficient.
    def test_same_host_not_independent(self):
        b = verify_claim(claim_id="c", statement="x",
                         records=[S("a.com", "s1", "page", "YES", pub="2026-09-01"),
                                  S("a.com", "s2", "snippet same host", "PARTIAL",
                                    typ="SEARCH_RESULT")],
                         current_date=REF)
        self.assertEqual(b.status, INSUFFICIENT)

    # S4: a record that refutes the claim -> CONFLICT.
    def test_contradiction_supports_no(self):
        b = verify_claim(claim_id="c", statement="x",
                         records=[S("a.com", "s1", "yes", "YES", pub="2026-09-01"),
                                  S("b.com", "s2", "no actually not", "NO",
                                    pub="2026-09-01")],
                         current_date=REF)
        self.assertEqual(b.status, CONFLICT)
        self.assertGreaterEqual(len(b.conflicts), 1)

    # S4b: two records asserting different values -> CONFLICT.
    def test_contradiction_assert_values(self):
        b = verify_claim(claim_id="c", statement="x",
                         records=[S("a.com", "s1", "v=1.2", "YES", asserts="1.2",
                                    pub="2026-09-01"),
                                  S("b.com", "s2", "v=2.0", "YES", asserts="2.0",
                                    pub="2026-09-01")],
                         current_date=REF)
        self.assertEqual(b.status, CONFLICT)
        self.assertTrue(any("disagreement" in c for c in b.conflicts))

    # S5: supporting but published 3y ago -> stale -> INSUFFICIENT.
    def test_stale_published_insufficient(self):
        b = verify_claim(claim_id="c", statement="x",
                         records=[S("a.com", "s1", "old", "YES", pub="2023-01-01"),
                                  S("b.com", "s2", "old", "YES", pub="2023-02-02")],
                         current_date=REF)
        self.assertEqual(b.status, INSUFFICIENT)
        self.assertIn("STALE", b.rationale)

    # REGRESSION (V0.1 evidence-expiry fix): a STALE primary (2020 WEB_PAGE)
    # must NOT satisfy the "at least one valid primary evidence" requirement.
    # The only fresh evidence here is snippets, so the claim must stay
    # INSUFFICIENT.  The old code incorrectly returned VERIFIED because the
    # stale primary still counted toward has_primary_support.
    def test_stale_primary_plus_fresh_snippets_insufficient(self):
        b = verify_claim(
            claim_id="c", statement="x",
            records=[
                S("a.com", "sA", "old 2020 page", "YES",
                  typ="WEB_PAGE", pub="2020-01-01"),
                S("b.com", "sB", "fresh snippet", "PARTIAL",
                  typ="SEARCH_RESULT", pub="2026-05-01"),
                S("c.com", "sC", "fresh snippet", "PARTIAL",
                  typ="SEARCH_RESULT", pub="2026-06-01"),
            ],
            current_date="2026-10-13")
        self.assertEqual(b.status, INSUFFICIENT)
        self.assertFalse(b.counts["has_primary_support"])

    # 4B: a FRESH primary + an independent fresh supporting snippet still backs
    # VERIFIED (freshness-aware primary support works in the positive direction).
    def test_fresh_primary_plus_independent_snippet_verified(self):
        b = verify_claim(
            claim_id="c", statement="x",
            records=[
                S("a.com", "sA", "fresh page", "YES",
                  typ="WEB_PAGE", pub="2026-09-01"),
                S("b.com", "sB", "fresh snippet", "PARTIAL",
                  typ="SEARCH_RESULT", pub="2026-09-02"),
            ],
            current_date="2026-10-13")
        self.assertEqual(b.status, VERIFIED)
        self.assertTrue(b.counts["has_primary_support"])

    # 4C: an explicit conflict still wins (CONFLICT precedence is untouched by
    # the freshness fix), even when a stale primary is also present.
    def test_conflict_wins_over_stale_primary(self):
        b = verify_claim(
            claim_id="c", statement="x",
            records=[
                S("a.com", "sA", "old 2020 page", "YES",
                  typ="WEB_PAGE", pub="2020-01-01"),
                S("b.com", "sB", "fresh contradiction", "NO",
                  typ="SEARCH_RESULT", pub="2026-09-01"),
            ],
            current_date="2026-10-13")
        self.assertEqual(b.status, CONFLICT)
        self.assertGreaterEqual(len(b.conflicts), 1)

    # Nothing at all -> INSUFFICIENT.
    def test_no_evidence(self):
        b = verify_claim(claim_id="c", statement="x", records=[],
                         current_date=REF)
        self.assertEqual(b.status, INSUFFICIENT)
        self.assertIn("NO_SUPPORTING_EVIDENCE", b.rationale)

    # Provenance split + machine-readable bundle.
    def test_provenance_and_bundle(self):
        b = verify_claim(claim_id="c", statement="x",
                         records=[S("a.com", "s1", "page", "YES", pub="2026-09-01"),
                                  S("a.com", "s1x", "snippet", "PARTIAL",
                                    typ="SEARCH_RESULT")],
                         current_date=REF, router_decision="ALLOW")
        self.assertEqual(b.provenance["primary_extract_count"], 1)
        self.assertEqual(b.provenance["search_result_count"], 1)
        self.assertEqual(b.router_decision, "ALLOW")
        json_blob = to_bundle_json(b)
        import json
        self.assertEqual(json.loads(json_blob)["claim_id"], "c")


if __name__ == "__main__":
    unittest.main()
