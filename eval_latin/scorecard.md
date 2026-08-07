# Classical Latin Retrieval Evaluation — Synthetic Track
N = 100 | Pool = 1,877

| System | C-R@1 | C-R@5 | C-R@10 | C-MRR | C-nDCG@10 | T-R@5 |
|---|---|---|---|---|---|---|
| fts_only | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.02 |
| dense | 0.56 | 0.83 | 0.9 | 0.6863 | 0.7341 | 0.99 |
| hybrid | 0.55 | 0.83 | 0.9 | 0.6788 | 0.7284 | 0.99 |
| dense_mmr | 0.56 | 0.86 | 0.91 | 0.6862 | 0.736 | 0.99 |
| dense_text_dedup | 0.56 | 0.6 | 0.6 | 0.5783 | 0.5839 | 1.0 |
| dense_two_level | 0.56 | 0.77 | 0.78 | 0.6539 | 0.6859 | 0.99 |