# Classical Latin Retrieval Evaluation — Realistic Track (LLM Judge)

N = 18 positive queries | Judge: claude-haiku-4-5-20251001


| System | Chunk nDCG@5 | Source nDCG@5 |
|---|---|---|
| dense | 0.9201 | 1.0 |
| dense_text_dedup | 0.8866 | 0.9972 |
| dense_two_level | 0.932 | 1.0 |
| dense_mmr | 0.8862 | 0.99 |
| hybrid | 0.9201 | 1.0 |
| fts | 0.0 | 0.0 |