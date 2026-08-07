# Classical Chinese Retrieval Evaluation — Synthetic Track
N = 100 | Pool = 319

| System | C-R@1 | C-R@5 | C-R@10 | C-MRR | C-nDCG@10 | T-R@5 |
|---|---|---|---|---|---|---|
| fts_only | 0.01 | 0.01 | 0.01 | 0.01 | 0.01 | 0.03 |
| dense | 0.59 | 0.82 | 0.92 | 0.7044 | 0.7507 | 1.0 |
| hybrid | 0.59 | 0.83 | 0.92 | 0.7082 | 0.7539 | 1.0 |
| dense_mmr | 0.59 | 0.84 | 0.92 | 0.6903 | 0.7416 | 1.0 |
| dense_text_dedup | 0.59 | 0.6 | 0.6 | 0.595 | 0.5963 | 1.0 |
| dense_two_level | 0.59 | 0.79 | 0.79 | 0.6762 | 0.7052 | 1.0 |