# 라벨 기준 평가 — 연결 게이트(기간 창·지역) + 후속 신호(8a9a6e9·c88b64d) 최종 재실행 (2026-10-25)

- 전: backend/data/_backup/migyeol_before_window_20261025.db (§6 상태) · 후: 실험 DB 현재 상태 (link → followup → report 녹화 재생, 실패 0)
- 라벨: gold/cluster_sample.csv · gold/experiment_labels.csv · gold/cluster_full.csv (full_*)

| db | cluster_kept | cluster_precision | o_retained | x_removed | full_kept | full_precision | full_recall | system_positive | tp | fp | fn | followup_precision | followup_recall | followup_f1 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| before (migyeol_before_window_20261025.db) | 345 | 0.652 | 0.9 | 0.348 | 13544 | 0.671 | 0.803 | 60 | 34 | 26 | 8 | 0.567 | 0.81 | 0.667 |
| current (migyeol.db) | 297 | 0.744 | 0.884 | 0.587 | 11545 | 0.766 | 0.782 | 54 | 34 | 20 | 8 | 0.63 | 0.81 | 0.709 |

## 일반 시설어 보정 직전(migyeol_before_generic_place_20261025.db) 대비

| db | cluster_kept | cluster_precision | o_retained | x_removed | full_kept | full_precision | full_recall | system_positive | tp | fp | fn | followup_precision | followup_recall | followup_f1 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| before (migyeol_before_generic_place_20261025.db) | 298 | 0.745 | 0.888 | 0.587 | 11637 | 0.76 | 0.782 | 54 | 34 | 20 | 8 | 0.63 | 0.81 | 0.709 |
