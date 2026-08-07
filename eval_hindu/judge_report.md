# Hindu Retrieval Evaluation — Realistic Track (LLM Judge)

N = 15 positive queries | Judge: claude-haiku-4-5-20251001


| System | Chunk nDCG@5 | Source nDCG@5 |
|---|---|---|
| dense | 0.8776 | 0.8471 |
| dense_text_dedup | 0.8818 | 0.8484 |
| dense_two_level | 0.8765 | 0.8548 |
| dense_mmr | 0.9282 | 0.8532 |
| hybrid | 0.8776 | 0.848 |
| fts | 0.0565 | 0.0667 |