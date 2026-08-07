# Hindu Retrieval Evaluation — Synthetic Track

N = 134 queries | Pool = 376 chunks


## System comparison

| System | C-R@1 | C-R@5 | C-R@10 | C-MRR | C-nDCG@10 | T-R@5 |
|---|---|---|---|---|---|---|
| fts_only | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| dense | 0.694 | 0.8582 | 0.8582 | 0.753 | 0.7765 | 0.9403 |
| hybrid | 0.694 | 0.8507 | 0.8582 | 0.7516 | 0.7752 | 0.9403 |
| dense_mmr | 0.694 | 0.7836 | 0.8582 | 0.7465 | 0.7709 | 0.9254 |
| dense_text_dedup | 0.694 | 0.7761 | 0.7761 | 0.7282 | 0.7404 | 0.9478 |
| dense_two_level | 0.694 | 0.806 | 0.8507 | 0.7416 | 0.7672 | 0.8881 |

## Per-corpus (Dense)

| Corpus | n | C-R@1 | C-R@5 | C-MRR | C-nDCG@10 |
|---|---|---|---|---|---|
| bhagavad-gita | 50 | 0.7 | 0.92 | 0.0 | 0.0 |
| upanishads | 34 | 0.5 | 0.6176 | 0.0 | 0.0 |
| yoga-sutras | 50 | 0.82 | 0.96 | 0.0 | 0.0 |