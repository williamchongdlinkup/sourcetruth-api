# Christianity Retrieval Evaluation — Realistic Track (LLM Judge)

N = 30 positive queries | Judge: claude-haiku-4-5-20251001


| System | Chunk nDCG@5 | Source nDCG@5 |
|---|---|---|
| dense | 0.8062 | 0.9217 |
| dense_text_dedup | 0.8133 | 0.9287 |
| dense_two_level | 0.8089 | 0.9334 |
| dense_mmr | 0.7635 | 0.9308 |
| hybrid | 0.8062 | 0.9217 |
| fts | 0.0 | 0.0 |