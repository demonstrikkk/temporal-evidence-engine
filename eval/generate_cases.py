"""Deterministically build the generated half of the gold set.

`eval/gold.jsonl` holds the hand-written edge cases: each one was added because a
real defect or a rule boundary needed a permanent witness. This script adds
*instances* of those same rules over different entities, values and dates. It is
not a second rule engine: every expectation is the rule's documented outcome,
and `eval/run_eval.py` still has to reproduce it through the real orchestrator,
settlement gate and decision table. A wrong template fails the gate, it does not
silently become the answer key.

Run from the repo root:

    python eval/generate_cases.py

Output: `eval/gold_generated.jsonl` (one case per line, deterministic order).
`split` is `dev` or `heldout`; every fourth generated case is held out so the
final numbers are not measured on the cases used while building.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _case(case_id: str, note: str, *, prompt: str, claim: dict,
          sources: list[dict], proposals: list[dict],
          generated_at: str = "2026-05-01T09:00:00Z", split: str = "dev") -> dict:
    return {
        "id": case_id,
        "note": note,
        "prompt": prompt,
        "answer": claim["claim_text"],
        "generated_at": generated_at,
        "split": split,
        "claims": [claim],
        "sources": sources,
        "proposals": proposals,
    }


def _claim(text: str, subject: str, predicate: str, value: str, *,
           claim_type: str = "time_sensitive", time_sensitive: bool = True,
           volatility: str = "high", expect: dict) -> dict:
    return {
        "claim_text": text,
        "subject": subject,
        "predicate": predicate,
        "object_raw": value,
        "claim_type": claim_type,
        "time_sensitive": time_sensitive,
        "volatility_class": volatility,
        "extraction_confidence": 0.95,
        "expect": expect,
    }


def _source(url: str, title: str, published: str, content: str) -> dict:
    return {"url": url, "title": title,
            "published_date": published, "content": content}


# ---------------------------------------------------------------------------
# families
# ---------------------------------------------------------------------------

A_ENTITIES = [
    ("Nimbus Pro", "nimbus", 19, 10),
    ("Atlas Team", "atlas", 29, 20),
    ("Vertex Plus", "vertex", 49, 30),
    ("Harbor Cloud", "harbor", 79, 40),
    ("Lumen Studio", "lumen", 99, 50),
    ("Orbit Analytics", "orbit", 149, 60),
    ("Beacon CRM", "beacon", 249, 10),
    ("Cobalt DB", "cobalt", 399, 20),
    ("Dune Storage", "dune", 59, 30),
    ("Ember CDN", "ember", 89, 40),
    ("Falcon Mail", "falcon", 129, 50),
    ("Grove Docs", "grove", 199, 60),
    ("Juniper API", "juniper", 249, 20),
    ("Kestrel Forms", "kestrel", 79, 30),
]


def changed_after_generation() -> list[dict]:
    cases = []
    for index, (name, slug, old, step) in enumerate(A_ENTITIES):
        new = old + step
        old_date = f"2026-{1 + index % 4:02d}-15"
        new_date = f"2026-{6 + index % 4:02d}-10"
        cases.append(_case(
            f"changed-{slug}",
            f"{name}'s price moved after the answer was generated.",
            prompt=f"What does {name} cost?",
            claim=_claim(
                f"{name} costs ${old} per month.", name, "price",
                f"${old}/month",
                expect={"status": "changed",
                        "reason_code": "value_changed_after_generation",
                        "event_dates": [old_date, new_date]}),
            sources=[
                _source(f"https://{slug}.example/pricing/archive",
                        f"{name} pricing archive", old_date,
                        f"{name} pricing archive. Effective {old_date}, "
                        f"{name} costs ${old} per month."),
                _source(f"https://{slug}.example/pricing",
                        f"{name} pricing", new_date,
                        f"{name} pricing. Effective {new_date}, "
                        f"{name} costs ${new} per month."),
            ],
            proposals=[
                {"claim": 0,
                 "url": f"https://{slug}.example/pricing/archive",
                 "relationship": "supports",
                 "quote": f"Effective {old_date}, {name} costs ${old} per month",
                 "candidate_value": ""},
                {"claim": 0,
                 "url": f"https://{slug}.example/pricing",
                 "relationship": "contradicts",
                 "quote": f"Effective {new_date}, {name} costs ${new} per month",
                 "candidate_value": f"${new}/month"},
            ],
        ))
    return cases


B_FACTS = [
    ("The Great Wall of China is 21,196 kilometres long.",
     "Great Wall of China", "length", "21,196 kilometres",
     "facts.example/great-wall", "Great Wall facts", "2026-03-02"),
    ("Mount Everest is 8,849 metres above sea level.",
     "Mount Everest", "height", "8,849 metres",
     "facts.example/everest", "Everest facts", "2026-03-03"),
    ("The Amazon River is 6,400 kilometres long.",
     "Amazon River", "length", "6,400 kilometres",
     "facts.example/amazon", "Amazon facts", "2026-03-04"),
    ("The Colosseum was completed in 80 AD.",
     "Colosseum", "completion year", "80 AD",
     "facts.example/colosseum", "Colosseum facts", "2026-03-05"),
    ("The Mona Lisa was painted in 1503.",
     "Mona Lisa", "painting year", "1503",
     "facts.example/mona-lisa", "Mona Lisa facts", "2026-03-06"),
    ("The Berlin Wall fell in 1989.",
     "Berlin Wall", "fall year", "1989",
     "facts.example/berlin-wall", "Berlin Wall facts", "2026-03-07"),
    ("The Statue of Liberty was dedicated in 1886.",
     "Statue of Liberty", "dedication year", "1886",
     "facts.example/statue-of-liberty", "Statue of Liberty facts", "2026-03-08"),
    ("Mount Kilimanjaro is 5,895 metres tall.",
     "Mount Kilimanjaro", "height", "5,895 metres",
     "facts.example/kilimanjaro", "Kilimanjaro facts", "2026-03-09"),
    ("The Suez Canal opened in 1869.",
     "Suez Canal", "opening year", "1869",
     "facts.example/suez", "Suez Canal facts", "2026-03-10"),
    ("The Golden Gate Bridge is 2,737 metres long.",
     "Golden Gate Bridge", "length", "2,737 metres",
     "facts.example/golden-gate", "Golden Gate facts", "2026-03-11"),
    ("The Grand Canyon is 446 kilometres long.",
     "Grand Canyon", "length", "446 kilometres",
     "facts.example/grand-canyon", "Grand Canyon facts", "2026-03-12"),
    ("Lake Baikal is 1,642 metres deep.",
     "Lake Baikal", "depth", "1,642 metres",
     "facts.example/baikal", "Lake Baikal facts", "2026-03-13"),
    ("The Titanic sank in 1912.",
     "Titanic", "sinking year", "1912",
     "facts.example/titanic", "Titanic facts", "2026-03-14"),
    ("The Channel Tunnel opened in 1994.",
     "Channel Tunnel", "opening year", "1994",
     "facts.example/channel-tunnel", "Channel Tunnel facts", "2026-03-15"),
]


def stable_facts() -> list[dict]:
    cases = []
    for index, (sentence, subject, predicate, value, host, title,
                published) in enumerate(B_FACTS):
        quote = sentence.rstrip(".")
        cases.append(_case(
            f"stable-{index + 1:02d}",
            "A stable fact is supported and never called changed.",
            prompt=f"Tell me about {subject}.",
            claim=_claim(sentence, subject, predicate, value,
                         claim_type="stable_factual", time_sensitive=False,
                         volatility="stable",
                         expect={"status": "supported",
                                 "reason_code": "supported_by_source"}),
            sources=[_source(f"https://{host}", title, published,
                             f"{title}. {quote}.")],
            proposals=[{"claim": 0, "url": f"https://{host}",
                        "relationship": "supports", "quote": quote,
                        "candidate_value": ""}],
        ))
    return cases


C_ENTITIES = [
    ("Pinecone Pro", "pinecone", 39, 15),
    ("Redwood Team", "redwood", 69, 25),
    ("Sable Cloud", "sable", 109, 35),
    ("Tundra DB", "tundra", 159, 45),
    ("Willow API", "willow", 219, 55),
    ("Zephyr Mail", "zephyr", 89, 15),
    ("Birch Docs", "birch", 129, 25),
    ("Cedar Forms", "cedar", 189, 35),
]


def contradiction_without_event_date() -> list[dict]:
    cases = []
    for index, (name, slug, old, step) in enumerate(C_ENTITIES):
        new = old + step
        old_date = f"2026-{1 + index % 4:02d}-12"
        cases.append(_case(
            f"ambiguous-{slug}",
            "Support and refutation both exist but the change has no date.",
            prompt=f"What does {name} cost?",
            claim=_claim(
                f"{name} costs ${old} per month.", name, "price",
                f"${old}/month",
                expect={"status": "contradicted",
                        "reason_code": "temporal_ambiguity",
                        "event_dates": [old_date]}),
            sources=[
                _source(f"https://{slug}.example/history",
                        f"{name} price history", old_date,
                        f"{name} price history. Effective {old_date}, "
                        f"{name} costs ${old} per month."),
                _source(f"https://news-{slug}.example/price",
                        f"{name} raises prices", "2026-10-06",
                        f"{name} now costs ${new} per month, the company said."),
            ],
            proposals=[
                {"claim": 0, "url": f"https://{slug}.example/history",
                 "relationship": "supports",
                 "quote": f"Effective {old_date}, {name} costs ${old} per month",
                 "candidate_value": ""},
                {"claim": 0, "url": f"https://news-{slug}.example/price",
                 "relationship": "contradicts",
                 "quote": f"{name} now costs ${new} per month",
                 "candidate_value": f"${new}/month"},
            ],
        ))
    return cases


D_ENTITIES = [
    ("Aspen Pro", "aspen", 45, "per year"),
    ("Basalt Team", "basalt", 75, "per year"),
    ("Coral Cloud", "coral", 115, "per seat per year"),
    ("Dover DB", "dover", 165, "per year"),
    ("Elm API", "elm", 225, "per seat per year"),
    ("Fir Mail", "fir", 95, "per year"),
    ("Granite Docs", "granite", 135, "per year"),
    ("Hazel Forms", "hazel", 195, "per seat per year"),
]


def scope_mismatch_with_support() -> list[dict]:
    cases = []
    for index, (name, slug, old, period) in enumerate(D_ENTITIES):
        old_date = f"2026-{1 + index % 4:02d}-08"
        other_date = f"2026-{6 + index % 4:02d}-18"
        other_value = f"${old} {period}"
        cases.append(_case(
            f"scope-support-{slug}",
            "A different billing period is a different scope, not a replacement.",
            prompt=f"What does {name} cost?",
            claim=_claim(
                f"{name} costs ${old} per month.", name, "price",
                f"${old}/month",
                expect={"status": "unverifiable",
                        "reason_code": "scope_mismatch"}),
            sources=[
                _source(f"https://{slug}.example/history",
                        f"{name} price history", old_date,
                        f"{name} price history. Effective {old_date}, "
                        f"{name} costs ${old} per month."),
                _source(f"https://{slug}.example/annual-pricing",
                        f"{name} annual pricing", other_date,
                        f"{name} annual pricing. Effective {other_date}, "
                        f"{name} costs {other_value}."),
            ],
            proposals=[
                {"claim": 0, "url": f"https://{slug}.example/history",
                 "relationship": "supports",
                 "quote": f"Effective {old_date}, {name} costs ${old} per month",
                 "candidate_value": ""},
                {"claim": 0,
                 "url": f"https://{slug}.example/annual-pricing",
                 "relationship": "contradicts",
                 "quote": f"Effective {other_date}, {name} costs {other_value}",
                 "candidate_value": other_value},
            ],
        ))
    return cases


E_ENTITIES = [
    ("Ivy Pro", "ivy", 55, "per year"),
    ("Jade Team", "jade", 85, "per year"),
    ("Kelp Cloud", "kelp", 125, "per seat per year"),
    ("Larch DB", "larch", 175, "per year"),
    ("Maple API", "maple", 235, "per seat per year"),
    ("Nettle Mail", "nettle", 105, "per year"),
]


def scope_mismatch_without_support() -> list[dict]:
    cases = []
    for index, (name, slug, value, period) in enumerate(E_ENTITIES):
        event_date = f"2026-{2 + index % 4:02d}-21"
        other_value = f"${value} {period}"
        cases.append(_case(
            f"scope-only-{slug}",
            "A different scope is not a refutation when no support settles the claim.",
            prompt=f"What does {name} cost?",
            claim=_claim(
                f"{name} costs ${value} per month.", name, "price",
                f"${value}/month",
                expect={"status": "unverifiable",
                        "reason_code": "scope_mismatch"}),
            sources=[
                _source(f"https://{slug}.example/annual-pricing",
                        f"{name} annual pricing", event_date,
                        f"{name} annual pricing. Effective {event_date}, "
                        f"{name} costs {other_value}."),
            ],
            proposals=[
                {"claim": 0,
                 "url": f"https://{slug}.example/annual-pricing",
                 "relationship": "contradicts",
                 "quote": f"Effective {event_date}, {name} costs {other_value}",
                 "candidate_value": other_value},
            ],
        ))
    return cases


F_ENTITIES = [
    ("Onyx Pro", "onyx", 69, 49),
    ("Pearl Team", "pearl", 99, 79),
    ("Quartz Cloud", "quartz", 139, 119),
    ("Raven DB", "raven", 189, 169),
    ("Slate API", "slate", 249, 229),
    ("Topaz Mail", "topaz", 119, 99),
]


def conflicting_primary_sources() -> list[dict]:
    cases = []
    for index, (name, slug, left, right) in enumerate(F_ENTITIES):
        event_date = f"2026-{2 + index % 4:02d}-01"
        cases.append(_case(
            f"conflicting-{slug}",
            "Two first-party pages disagree at the same effective date.",
            prompt=f"What does {name} cost?",
            claim=_claim(
                f"{name} costs ${left} per month.", name, "price",
                f"${left}/month",
                expect={"status": "unverifiable",
                        "reason_code": "conflicting_evidence"}),
            sources=[
                _source(f"https://{slug}.example/pricing",
                        f"{name} pricing", event_date,
                        f"{name} pricing. {name} costs ${left} per month "
                        f"as of {event_date}."),
                _source(f"https://{slug}.example/legacy-pricing",
                        f"{name} legacy pricing", event_date,
                        f"{name} legacy pricing. {name} costs ${right} per month "
                        f"as of {event_date}."),
            ],
            proposals=[
                {"claim": 0, "url": f"https://{slug}.example/pricing",
                 "relationship": "supports",
                 "quote": f"{name} costs ${left} per month as of {event_date}",
                 "candidate_value": ""},
                {"claim": 0, "url": f"https://{slug}.example/legacy-pricing",
                 "relationship": "contradicts",
                 "quote": f"{name} costs ${right} per month as of {event_date}",
                 "candidate_value": f"${right}/month"},
            ],
        ))
    return cases


G_ENTITIES = [
    ("Umber Pro", "umber", 59),
    ("Violet Team", "violet", 89),
    ("Wren Cloud", "wren", 129),
    ("Xenon DB", "xenon", 179),
    ("Yarrow API", "yarrow", 239),
    ("Zircon Mail", "zircon", 109),
]


def new_article_old_fact() -> list[dict]:
    cases = []
    for index, (name, slug, price) in enumerate(G_ENTITIES):
        old_date = f"2026-{1 + index % 4:02d}-05"
        published = f"2026-{6 + index % 4:02d}-08"
        cases.append(_case(
            f"republication-{slug}",
            "A newly published article restating an old price is not a change.",
            prompt=f"What does {name} cost?",
            claim=_claim(
                f"{name} costs ${price} per month.", name, "price",
                f"${price}/month",
                expect={"status": "supported",
                        "reason_code": "supported_by_source",
                        "event_dates": [old_date]}),
            sources=[
                _source(f"https://{slug}.example/news",
                        f"{name} pricing update", published,
                        f"{name} pricing update, published {published}. "
                        f"Effective {old_date}, {name} costs ${price} per month."),
            ],
            proposals=[
                {"claim": 0, "url": f"https://{slug}.example/news",
                 "relationship": "supports",
                 "quote": f"Effective {old_date}, {name} costs ${price} per month",
                 "candidate_value": ""},
            ],
        ))
    return cases


H_ENTITIES = [
    ("Alder Pro", "alder", 49),
    ("Bramble Team", "bramble", 79),
    ("Cinder Cloud", "cinder", 119),
    ("Drift DB", "drift", 169),
    ("Echo API", "echo", 229),
    ("Flint Mail", "flint", 99),
]


def no_source() -> list[dict]:
    cases = []
    for index, (name, _, price) in enumerate(H_ENTITIES):
        cases.append(_case(
            f"no-source-{index + 1:02d}",
            "Nothing found is an abstention, not a refutation.",
            prompt=f"What does {name} cost?",
            claim=_claim(
                f"{name} costs ${price} per month.", name, "price",
                f"${price}/month",
                expect={"status": "unverifiable",
                        "reason_code": "no_source"}),
            sources=[],
            proposals=[],
        ))
    return cases


I_ENTITIES = [
    ("Garnet Pro", "garnet", 39),
    ("Hollow Team", "hollow", 69),
    ("Indigo Cloud", "indigo", 109),
    ("Jasper DB", "jasper", 159),
    ("Kite API", "kite", 219),
    ("Loom Mail", "loom", 89),
]


def quote_not_locatable() -> list[dict]:
    cases = []
    for index, (name, slug, price) in enumerate(I_ENTITIES):
        published = f"2026-{2 + index % 4:02d}-07"
        cases.append(_case(
            f"invented-quote-{index + 1:02d}",
            "A quote the model invented must not become evidence.",
            prompt=f"What does {name} cost?",
            claim=_claim(
                f"{name} costs ${price} per month.", name, "price",
                f"${price}/month",
                expect={"status": "unverifiable",
                        "reason_code": "no_source"}),
            sources=[
                _source(f"https://{slug}.example/pricing",
                        f"{name} pricing", published,
                        f"{name} pricing. {name} has several plans for teams."),
            ],
            proposals=[
                {"claim": 0, "url": f"https://{slug}.example/pricing",
                 "relationship": "supports",
                 # Not a substring of the stored page.
                 "quote": f"{name} costs ${price} per month",
                 "candidate_value": ""},
            ],
        ))
    return cases


J_ENTITIES = [
    ("Mica Pro", "mica"),
    ("Nickel Team", "nickel"),
    ("Opal Cloud", "opal"),
    ("Pyrite DB", "pyrite"),
    ("Quill API", "quill"),
    ("Rosin Mail", "rosin"),
]


def mention_only() -> list[dict]:
    cases = []
    for index, (name, slug) in enumerate(J_ENTITIES):
        published = f"2026-{2 + index % 4:02d}-09"
        cases.append(_case(
            f"mention-{slug}",
            "A page that discusses the subject but not the value establishes nothing.",
            prompt=f"What does {name} cost?",
            claim=_claim(
                f"{name} costs $79 per month.", name, "price", "$79/month",
                expect={"status": "unverifiable",
                        "reason_code": "no_entailment"}),
            sources=[
                _source(f"https://{slug}.example/product",
                        f"{name} product page", published,
                        f"{name} is a subscription plan for small teams. "
                        "It includes shared workspaces and audit logs."),
            ],
            proposals=[
                {"claim": 0, "url": f"https://{slug}.example/product",
                 "relationship": "mentions_only",
                 "quote": f"{name} is a subscription plan for small teams",
                 "candidate_value": ""},
            ],
        ))
    return cases


K_ENTITIES = [
    ("Saffron Pro", "saffron"),
    ("Tallow Team", "tallow"),
    ("Ultra Cloud", "ultra"),
    ("Vellum DB", "vellum"),
    ("Wheat API", "wheat"),
    ("Xylem Mail", "xylem"),
    ("Yonder Docs", "yonder"),
    ("Zinnia Forms", "zinnia"),
]


def negation_contradiction() -> list[dict]:
    cases = []
    for index, (name, slug) in enumerate(K_ENTITIES):
        event_date = f"2026-{6 + index % 4:02d}-14"
        published = f"2026-{6 + index % 4:02d}-15"
        cases.append(_case(
            f"negation-{slug}",
            "A discontinuation is a refutation even without a replacement value.",
            prompt=f"Can new customers subscribe to {name}?",
            claim=_claim(
                f"{name} is available to new customers.", name, "availability",
                "available to new customers",
                expect={"status": "contradicted",
                        "reason_code": "contradicted_by_source",
                        "event_dates": [event_date]}),
            sources=[
                _source(f"https://{slug}.example/status",
                        f"{name} product status", published,
                        f"{name} status. {name} is no longer available to new "
                        f"customers as of {event_date}."),
            ],
            proposals=[
                {"claim": 0, "url": f"https://{slug}.example/status",
                 "relationship": "contradicts",
                 "quote": f"{name} is no longer available to new customers "
                          f"as of {event_date}",
                 "candidate_value": ""},
            ],
        ))
    return cases


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------

FAMILIES = [
    ("changed", changed_after_generation),
    ("stable", stable_facts),
    ("ambiguous", contradiction_without_event_date),
    ("scope-support", scope_mismatch_with_support),
    ("scope-only", scope_mismatch_without_support),
    ("conflicting", conflicting_primary_sources),
    ("republication", new_article_old_fact),
    ("no-source", no_source),
    ("invented-quote", quote_not_locatable),
    ("mention", mention_only),
    ("negation", negation_contradiction),
]


def build() -> list[dict]:
    cases: list[dict] = []
    for _, factory in FAMILIES:
        cases.extend(factory())
    seen: set[str] = set()
    for index, case in enumerate(cases):
        if case["id"] in seen:
            raise SystemExit(f"duplicate case id: {case['id']}")
        seen.add(case["id"])
        # Deterministic hold-out: every fourth generated case. The split is a
        # property of position in a fixed-order file, not a random draw, so the
        # file is reproducible.
        case["split"] = "heldout" if index % 4 == 3 else "dev"
    return cases


def main() -> None:
    cases = build()
    out = ROOT / "gold_generated.jsonl"
    with out.open("w", encoding="utf-8", newline="\n") as handle:
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False) + "\n")
    heldout = sum(1 for case in cases if case["split"] == "heldout")
    print(f"wrote {len(cases)} cases to {out.name} "
          f"({heldout} held out, {len(cases) - heldout} dev)")


if __name__ == "__main__":
    main()
