# Hindu Retrieval Evaluation — Realistic Track (LLM Judge)

N = 26 positive queries | Judge: claude-haiku-4-5-20251001


| System | Chunk nDCG@5 | Source nDCG@5 |
|---|---|---|
| dense | 0.8177 | 0.7762 |
| dense_text_dedup | 0.8098 | 0.7933 |
| dense_two_level | 0.8142 | 0.7817 |
| dense_mmr | 0.8613 | 0.812 |
| hybrid | 0.8197 | 0.7766 |
| fts | 0.0323 | 0.0385 |