# Legal corpus build

- documents: 68
- cells: 546
- generated_at: 2026-10-21T09:11:12+00:00

| document_type | documents |
|---|---|
| constitution | 2 |
| fee_schedule | 1 |
| judgment | 14 |
| statute | 51 |

## Storage rules

The stored text is a metadata header from `sources.json` followed by the
source's own body. Every field quote is a slice of that stored text; the
header is never treated as body evidence.
