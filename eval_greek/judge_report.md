# Greek Philosophy Retrieval Evaluation — Realistic Track (LLM Judge)

N = 32 positive queries | Judge: claude-haiku-4-5-20251001


| System | Chunk nDCG@5 | Source nDCG@5 |
|---|---|---|
| dense | 0.7809 | 0.8222 |
| dense_text_dedup | 0.8279 | 0.8566 |
| dense_two_level | 0.776 | 0.8372 |
| dense_mmr | 0.7739 | 0.8488 |
| hybrid | 0.7809 | 0.8222 |
| fts | 0.0 | 0.0 |