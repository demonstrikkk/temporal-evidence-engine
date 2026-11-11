"""Which prompt produced a call, and the evidence that the label is true.

The trace records `prompt_version` on every model span. The reason it is a
version string and not the prompt hash alone: a hash changes on every whitespace
edit, so it can tell you two calls are different but never that they are the
*second* version of the same instruction. A version can.

The failure mode a hand-written version invites is drift - someone edits the
prompt and forgets to bump the label, and every span then claims a provenance it
does not have. `REGISTERED` closes that hole the only way it can be closed in a
codebase where the prompt is a module constant: it records the digest of the
exact text each version names, and `test_prompt_versions.py` recomputes it. Edit
a prompt without bumping the version and the suite fails, naming the version
that no longer matches its own text.

The digest covers the system instruction **and** the user-message template,
because both change what the model is asked. It does not cover the interpolated
data - that is what `trace_spans.prompt_hash` is for.
"""
from __future__ import annotations

import hashlib

#: First extraction pass: answer -> atomic claims.
EXTRACT_PROMPT_VERSION = "extract.v1"
#: The one focused re-extraction over assertions no claim covered (D-023).
REPAIR_PROMPT_VERSION = "extract.repair.v1"
#: One claim against one stored source revision -> a relationship proposal.
PROPOSE_PROMPT_VERSION = "propose.v1"

#: version -> SHA-256 prefix of `fingerprint(system, template)`.
#:
#: A missing entry means the version was never registered, which is the same
#: failure as a stale one: nothing can vouch for the label.
REGISTERED: dict[str, str] = {
    EXTRACT_PROMPT_VERSION: "ef6e3e1011ca152d",
    REPAIR_PROMPT_VERSION: "0a1bc5c7c998d2ef",
    PROPOSE_PROMPT_VERSION: "dae704467c8c9493",
}

#: Every version this build knows how to label a span with.
PROMPT_VERSIONS = frozenset(REGISTERED)


def fingerprint(system: str, template: str) -> str:
    """The digest half of a prompt version. Stable across processes and platforms."""
    blob = "\x00".join([system or "", template or ""])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
