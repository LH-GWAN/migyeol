# 라벨 기준 평가 (2026-10-30 03:59)

- DB: `migyeol.db` · 라벨: gold/cluster_sample.csv · gold/experiment_labels.csv

## 현재 상태

| db | cluster_kept | cluster_precision | o_retained | x_removed | full_kept | full_precision | full_recall | system_positive | tp | fp | fn | followup_precision | followup_recall | followup_f1 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| before (baseline.db) | 309 | 0.871 | 0.9 | 0.784 | 13810 | 0.911 | 0.902 | 68 | 66 | 2 | 10 | 0.971 | 0.868 | 0.917 |
| current (migyeol.db) | 309 | 0.871 | 0.9 | 0.784 | 13884 | 0.912 | 0.908 | 68 | 66 | 2 | 10 | 0.971 | 0.868 | 0.917 |
