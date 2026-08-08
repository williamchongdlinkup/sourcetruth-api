# Western Philosophy Retrieval Evaluation — Synthetic Track

N = 200 queries | Pool = 6,085 chunks


## System comparison

| System | C-R@1 | C-R@5 | C-R@10 | C-MRR | C-nDCG@10 | T-R@5 |
|---|---|---|---|---|---|---|
| fts_only | 0.005 | 0.01 | 0.015 | 0.007 | 0.0085 | 0.02 |
| dense | 0.635 | 0.865 | 0.915 | 0.7334 | 0.7755 | 0.995 |
| hybrid | 0.645 | 0.86 | 0.91 | 0.7374 | 0.777 | 0.995 |
| dense_mmr | 0.635 | 0.815 | 0.875 | 0.7132 | 0.7478 | 0.995 |
| dense_text_dedup | 0.635 | 0.66 | 0.66 | 0.6467 | 0.6501 | 1.0 |
| dense_two_level | 0.635 | 0.795 | 0.805 | 0.705 | 0.7302 | 0.99 |