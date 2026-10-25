# 라벨 기준 평가 — 연결 게이트(기간 창·지역) + 후속 신호(8a9a6e9) 합친 재실행 (2026-10-25)

- 전: backend/data/_backup/migyeol_before_window_20261025.db (§6 상태) · 후: link → followup → report 녹화 재생 (약속 재검색 녹화 없음 14건은 이전 후보)
- 라벨: gold/cluster_sample.csv · gold/experiment_labels.csv · gold/cluster_full.csv (full_*)

| db | cluster_kept | cluster_precision | o_retained | x_removed | full_kept | full_precision | full_recall | system_positive | tp | fp | fn | followup_precision | followup_recall | followup_f1 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| before (migyeol_before_window_20261025.db) | 345 | 0.652 | 0.9 | 0.348 | 13544 | 0.671 | 0.803 | 60 | 34 | 26 | 8 | 0.567 | 0.81 | 0.667 |
| current (migyeol.db) | 298 | 0.745 | 0.888 | 0.587 | 11645 | 0.759 | 0.781 | 54 | 34 | 20 | 8 | 0.63 | 0.81 | 0.709 |
