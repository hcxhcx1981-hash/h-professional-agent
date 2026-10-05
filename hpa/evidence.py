"""Evidence Verification V0.1.

A PURE decision layer: it receives already-gathered, normalized evidence
records and decides a claim's status. It does not search, fetch, or execute
anything.  Real retrieval (WEB_SEARCH / WEB_EXTRACT) happens elsewhere and is
injected here as :class:`EvidenceRecord` objects, so this module stays
testable offline and stays separated from the capability router and runtime
discovery (three independent layers, one small pure function each).

Verdicts (only these, per the frozen contract):
    VERIFIED     - enough independent, properly-sourced support
    CONFLICT     - evidence contradicts itself (no silent pick of a favorite)
    INSUFFICIENT - not enough to verify (single source, snippet-only, stale,
                   or nothing relevant)

Snippet rule: a ``SEARCH_RESULT`` (engine snippet) is *discovery*, never
final evidence.  A claim may only reach VERIFIED when at least one
higher-grade record (WEB_PAGE / LOCAL_FILE extract) supports it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import urlparse

VERIFIED = "VERIFIED"
CONFLICT = "CONFLICT"
INSUFFICIENT = "INSUFFICIENT"

#: Higher-grade (original / primary) source types that can back a VERIFIED.
PRIMARY_TYPES = {"WEB_PAGE", "LOCAL_FILE"}
#: Discovery-only types: snippets / digests.  Can never be the sole support.
DISCOVERY_TYPES = {"SEARCH_RESULT", "NEWS", "DIGEST"}
#: Supports polarity.
SUPPORT_YES = "YES"
SUPPORT_NO = "NO"        # evidence actively contradicts the claim
SUPPORT_PARTIAL = "PARTIAL"
_SUPPORTING = {SUPPORT_YES, SUPPORT_PARTIAL}
DEFAULT_MIN_AGE_DAYS = 730  # 2y horizon a PUBLISHED_AT must fall inside


def _host(url: str) -> str:
    try:
        return (urlparse(url).netloc or url).lower().strip(".")
    except Exception:
        return (url or "").lower()


@dataclass
class EvidenceRecord:
    """One normalized piece of evidence about a single claim."""
    claim_id: str
    source_id: str
    source_url: str = ""
    source_type: str = "SEARCH_RESULT"
    evidence_text: str = ""
    supports: str = SUPPORT_PARTIAL
    published_at: str | None = None
    retrieved_at: str = ""
    conflict_group: str | None = None
    asserts: str | None = None          # the concrete value this source claims
    source_quality: str = "unknown"     # low | medium | high | unknown

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "source_url": self.source_url,
            "source_type": self.source_type,
            "evidence_text": self.evidence_text,
            "supports": self.supports,
            "published_at": self.published_at,
            "retrieved_at": self.retrieved_at,
            "conflict_group": self.conflict_group,
            "asserts": self.asserts,
            "source_quality": self.source_quality,
        }


def build_record(*, claim_id, source_id, source_type, evidence_text,
                 supports=SUPPORT_PARTIAL, source_url="",
                 published_at=None, retrieved_at="", conflict_group=None,
                 asserts=None, source_quality="unknown") -> EvidenceRecord:
    return EvidenceRecord(
        claim_id=claim_id, source_id=source_id, source_url=source_url,
        source_type=source_type, evidence_text=evidence_text,
        supports=supports, published_at=published_at,
        retrieved_at=retrieved_at or datetime.now().isoformat(),
        conflict_group=conflict_group, asserts=asserts,
        source_quality=source_quality,
    )


def _parse_date(s: str | None):
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        return date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None


@dataclass
class ClaimBundle:
    claim_id: str
    statement: str
    status: str
    rationale: str
    records: list[EvidenceRecord]
    counts: dict
    conflicts: list[str]
    provenance: dict
    router_decision: str | None = None

    def to_dict(self) -> dict:
        return {
            "claim_id": self.claim_id,
            "statement": self.statement,
            "router_decision": self.router_decision,
            "status": self.status,
            "evidence": [r.to_dict() for r in self.records],
            "counts": self.counts,
            "conflicts": self.conflicts,
            "provenance": self.provenance,
            "rationale": self.rationale,
        }


def verify_claim(*, claim_id, statement, records, min_independent=2,
                 require_extracted_for_verified=True, current_date=None,
                 max_age_days=DEFAULT_MIN_AGE_DAYS,
                 router_decision=None) -> ClaimBundle:
    """Pure decision: given normalized records, return a :class:`ClaimBundle`.

    No I/O.  ``records`` must already be normalized (search hits vs page
    extracts distinguished by ``source_type``)."""
    recs = [r for r in records if r.claim_id == claim_id]

    # Provenance split: discovery (snippets) vs primary (extracts/reads).
    search = [r for r in recs if r.source_type in DISCOVERY_TYPES or r.source_type not in PRIMARY_TYPES]
    primary = [r for r in recs if r.source_type in PRIMARY_TYPES]
    provenance = {
        "search_result_count": len(search),
        "primary_extract_count": len(primary),
        "retrieval_methods": sorted({r.source_type for r in recs}),
    }

    # ---- 1. contradiction: any record that actively refutes the claim.
    conflicts: list[str] = []
    for r in recs:
        if r.supports == SUPPORT_NO:
            conflicts.append(f"{r.source_id}: contradicts claim ({r.evidence_text[:80]!r})")
    # ---- 2. contradiction: two records asserting different concrete values.
    asserts = {}
    for r in recs:
        if r.asserts is not None:
            asserts.setdefault(r.asserts, []).append(r.source_id)
    for val, ids in asserts.items():
        if len(asserts) > 1:
            conflicts.append(f"assert value {val!r} -> {ids}")
    if len(asserts) > 1:
        conflicts.append("disagreement: " + " vs ".join(f"{v!r}" for v in asserts))

    if conflicts:
        return ClaimBundle(
            claim_id, statement, CONFLICT,
            f"CONTRADICTION detected ({len(conflicts)} signal(s)); refusing to "
            f"silently prefer one source. Inspect conflicts[] before deciding.",
            recs, _counts(recs), conflicts, provenance, router_decision,
        )

    # ---- 3. supporting evidence only (YES / PARTIAL).
    supporting = [r for r in recs if r.supports in _SUPPORTING]
    # Freshness: a PUBLISHED_AT older than max_age_days cannot back VERIFIED.
    ref = _parse_date(current_date) or date.today()
    fresh_support = []
    stale = []
    for r in supporting:
        p = _parse_date(r.published_at)
        if p and (ref - p).days > max_age_days:
            stale.append(r)
        else:
            fresh_support.append(r)

    # Independent sources = distinct hosts among *fresh supporting* records.
    fresh_indep = len({_host(r.source_url) for r in fresh_support if r.source_url})
    all_indep = len({_host(r.source_url) for r in supporting if r.source_url})

    has_primary_support = any(r.supports in _SUPPORTING for r in primary)
    snippet_only = (len(primary) == 0) and (len(search) > 0)

    counts = _counts(recs) | {
        "independent_source_count": fresh_indep,
        "supporting_count": len(supporting),
        "stale_count": len(stale),
        "has_primary_support": has_primary_support,
    }

    # ---- 4. verdict.
    if not supporting:
        return ClaimBundle(
            claim_id, statement, INSUFFICIENT,
            "NO_SUPPORTING_EVIDENCE: no record actively supports the claim.",
            recs, counts, conflicts, provenance, router_decision)

    if require_extracted_for_verified and not has_primary_support:
        return ClaimBundle(
            claim_id, statement, INSUFFICIENT,
            "SNIPPET_ONLY: support exists but only as discovery-grade snippets "
            "(SEARCH_RESULT/NEWS); a VERIFIED verdict requires at least one "
            "primary extract (WEB_PAGE/LOCAL_FILE). Not treating snippets as "
            "final evidence.",
            recs, counts, conflicts, provenance, router_decision)

    if fresh_indep < min_independent:
        if all_indep >= min_independent:
            return ClaimBundle(
                claim_id, statement, INSUFFICIENT,
                f"STALE: {all_indep} independent supporting source(s) would "
                f"suffice, but {len(stale)} are older than {max_age_days}d; "
                f"only {fresh_indep} are fresh (need >= {min_independent}).",
                recs, counts, conflicts, provenance, router_decision)
        return ClaimBundle(
            claim_id, statement, INSUFFICIENT,
            f"SINGLE_SOURCE: only {all_indep} independent support source(s) "
            f"(need >= {min_independent}); not enough to verify.",
            recs, counts, conflicts, provenance, router_decision)

    return ClaimBundle(
        claim_id, statement, VERIFIED,
        f"VERIFIED: {fresh_indep} independent supporting sources incl. a "
        f"primary extract; no contradiction.",
        recs, counts, conflicts, provenance, router_decision)


def _counts(recs) -> dict:
    return {
        "evidence_count": len(recs),
        "independent_source_count": len({_host(r.source_url) for r in recs if r.source_url}),
        "discovery_count": sum(1 for r in recs if r.source_type not in PRIMARY_TYPES),
        "primary_count": sum(1 for r in recs if r.source_type in PRIMARY_TYPES),
    }


def to_bundle_json(bundle: ClaimBundle) -> str:
    import json
    return json.dumps(bundle.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
