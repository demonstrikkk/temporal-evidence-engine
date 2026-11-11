"""Claim-level corroboration: who agrees, and whether they are the same voice.

Composes source independence (identical stored text) with source authority
signals. Both are descriptive: they never alter the verdict, they explain how
much independent support a `supported` claim actually has.
"""
from __future__ import annotations

from typing import Any

from app.research.source_independence import assess_independence
from app.research.source_quality import score_evidence, summarise_quality
from app.verify.report import evidence_view
from app.verify.store import VerifyStore


def assess_claim(store: VerifyStore, claim_id: str,
                 subject: str = "") -> dict[str, Any]:
    rows = store.list_evidence(claim_id)
    views = [evidence_view(row, store) for row in rows]
    return {
        "independence": assess_independence(store, views),
        "quality": summarise_quality(score_evidence(store, subject, views)),
    }
