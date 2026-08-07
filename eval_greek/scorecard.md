# Greek Philosophy Retrieval Evaluation — Synthetic Track

N = 100 queries | Pool = 776 chunks


## System comparison

| System | C-R@1 | C-R@5 | C-R@10 | C-MRR | C-nDCG@10 | T-R@5 |
|---|---|---|---|---|---|---|
| fts_only | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.02 |
| dense | 0.6 | 0.84 | 0.9 | 0.7035 | 0.7479 | 0.98 |
| hybrid | 0.58 | 0.84 | 0.9 | 0.6905 | 0.738 | 0.98 |
| dense_mmr | 0.6 | 0.8 | 0.86 | 0.6911 | 0.7264 | 1.0 |
| dense_text_dedup | 0.6 | 0.63 | 0.63 | 0.615 | 0.6189 | 1.0 |
| dense_two_level | 0.6 | 0.8 | 0.8 | 0.6782 | 0.7088 | 0.99 |