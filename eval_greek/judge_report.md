# Greek Philosophy Retrieval Evaluation — Realistic Track (LLM Judge)

N = 18 positive queries | Judge: claude-haiku-4-5-20251001


| System | Chunk nDCG@5 | Source nDCG@5 |
|---|---|---|
| dense | 0.8702 | 0.909 |
| dense_text_dedup | 0.9178 | 0.9451 |
| dense_two_level | 0.8676 | 0.9347 |
| dense_mmr | 0.853 | 0.9332 |
| hybrid | 0.8702 | 0.909 |
| fts | 0.0 | 0.0 |