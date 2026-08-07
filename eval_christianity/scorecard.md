# Christianity Retrieval Evaluation — Synthetic Track

N = 128 queries | Pool = 4,846 chunks | KJV Bible


## System comparison (Dense)

| System | C-R@1 | C-R@5 | C-R@10 | C-MRR | C-nDCG@10 | T-R@5 | T-nDCG@10 |
|---|---|---|---|---|---|---|---|
| fts_only | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| dense | 0.3672 | 0.7734 | 0.8906 | 0.5433 | 0.6236 | 0.9062 | 1.158 |
| hybrid | 0.3672 | 0.7734 | 0.8906 | 0.5433 | 0.6236 | 0.9062 | 1.158 |
| dense_mmr | 0.3672 | 0.5156 | 0.6875 | 0.4568 | 0.4961 | 0.7109 | 1.0943 |
| dense_text_dedup | 0.3672 | 0.7734 | 0.7891 | 0.5251 | 0.5903 | 0.9453 | 0.7157 |
| dense_two_level | 0.3672 | 0.6172 | 0.8203 | 0.4678 | 0.5436 | 0.6562 | 1.3314 |

## Per-corpus breakdown (Dense)

| Corpus | n | C-R@1 | C-R@5 | C-MRR | C-nDCG@10 |
|---|---|---|---|---|---|
| kjv | 100 | 0.39 | 0.79 | 0.0 | 0.0 |
| bible-web | 28 | 0.2857 | 0.7143 | 0.0 | 0.0 |
| bible-asv | 0 | 0.0 | 0.0 | 0.0 | 0.0 |
| bible-ylt | 0 | 0.0 | 0.0 | 0.0 | 0.0 |