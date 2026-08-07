# Hindu Retrieval Evaluation — Synthetic Track

N = 98 queries | Pool = 157 chunks


## System comparison

| System | C-R@1 | C-R@5 | C-R@10 | C-MRR | C-nDCG@10 | T-R@5 |
|---|---|---|---|---|---|---|
| fts_only | 0.0 | 0.0102 | 0.0102 | 0.0026 | 0.0044 | 0.0204 |
| dense | 0.6531 | 0.8776 | 0.9184 | 0.7475 | 0.7868 | 0.9286 |
| hybrid | 0.6531 | 0.8673 | 0.9082 | 0.745 | 0.7817 | 0.9286 |
| dense_mmr | 0.6531 | 0.8571 | 0.898 | 0.7423 | 0.7778 | 0.9082 |
| dense_text_dedup | 0.6531 | 0.7449 | 0.7653 | 0.6944 | 0.7113 | 0.949 |
| dense_two_level | 0.6531 | 0.8469 | 0.898 | 0.736 | 0.7737 | 0.8776 |

## Per-corpus (Dense)

| Corpus | n | C-R@1 | C-R@5 | C-MRR | C-nDCG@10 |
|---|---|---|---|---|---|
| bhagavad-gita | 50 | 0.68 | 0.82 | 0.0 | 0.0 |
| upanishads | 48 | 0.625 | 0.9375 | 0.0 | 0.0 |