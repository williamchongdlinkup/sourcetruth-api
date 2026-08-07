# Greek Philosophy Retrieval Evaluation — Synthetic Track

N = 120 queries | Pool = 2,148 chunks


## System comparison

| System | C-R@1 | C-R@5 | C-R@10 | C-MRR | C-nDCG@10 | T-R@5 |
|---|---|---|---|---|---|---|
| fts_only | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0083 |
| dense | 0.6417 | 0.8583 | 0.9167 | 0.7427 | 0.7836 | 1.0 |
| hybrid | 0.6417 | 0.8583 | 0.9167 | 0.7406 | 0.7819 | 1.0 |
| dense_mmr | 0.6417 | 0.8167 | 0.8583 | 0.7216 | 0.7505 | 1.0 |
| dense_text_dedup | 0.6417 | 0.6833 | 0.6833 | 0.6611 | 0.6669 | 1.0 |
| dense_two_level | 0.6417 | 0.8083 | 0.8167 | 0.7123 | 0.7386 | 0.9917 |