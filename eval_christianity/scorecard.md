# Christianity Retrieval Evaluation — Synthetic Track

N = 500 queries | Pool = 6,753 chunks | KJV Bible


## System comparison (Dense)

| System | C-R@1 | C-R@5 | C-R@10 | C-MRR | C-nDCG@10 | T-R@5 | T-nDCG@10 |
|---|---|---|---|---|---|---|---|
| fts_only | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| dense | 0.304 | 0.764 | 0.85 | 0.4894 | 0.5728 | 0.876 | 1.3826 |
| hybrid | 0.304 | 0.764 | 0.85 | 0.4894 | 0.5728 | 0.876 | 1.3826 |
| dense_mmr | 0.304 | 0.456 | 0.572 | 0.391 | 0.418 | 0.648 | 1.1556 |
| dense_text_dedup | 0.304 | 0.712 | 0.748 | 0.4586 | 0.5291 | 0.914 | 0.6809 |
| dense_two_level | 0.304 | 0.526 | 0.794 | 0.4129 | 0.4948 | 0.57 | 1.2437 |

## Per-corpus breakdown (Dense)

| Corpus | n | C-R@1 | C-R@5 | C-MRR | C-nDCG@10 |
|---|---|---|---|---|---|
| kjv | 100 | 0.43 | 0.83 | 0.0 | 0.0 |
| bible-web | 100 | 0.25 | 0.74 | 0.0 | 0.0 |
| bible-asv | 100 | 0.12 | 0.7 | 0.0 | 0.0 |
| bible-ylt | 100 | 0.18 | 0.7 | 0.0 | 0.0 |
| christian-theology | 100 | 0.54 | 0.85 | 0.0 | 0.0 |