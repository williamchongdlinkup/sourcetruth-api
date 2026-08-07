# Christianity Retrieval Evaluation — Realistic Track (LLM Judge)

N = 18 positive queries | Judge: claude-haiku-4-5-20251001


| System | Chunk nDCG@5 | Source nDCG@5 |
|---|---|---|
| dense | 0.8435 | 0.9847 |
| dense_text_dedup | 0.8464 | 0.9853 |
| dense_two_level | 0.8266 | 1.0 |
| dense_mmr | 0.833 | 0.9751 |
| hybrid | 0.8435 | 0.9847 |
| fts | 0.0 | 0.0 |