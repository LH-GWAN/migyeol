# 라벨 기준 평가 (2026-10-25 17:26)

- **재순위 실험(docs/EXPERIMENT.md §9)**: before = 재순위 끔(지금 실험 DB 와 같다), current = `LINK_RERANKER=cross RERANK_BLEND=0.5 RERANK_AUTO=0.5 RERANK_REVIEW=0.2`
  (+ 바닥·교차 적합)으로 link → followup → report 를 녹화 재생한 DB 사본 `backend/data/_backup/migyeol_after_rerank_20261025.db`.
  군집은 좋아졌지만 사건 단위 후속보도 지표가 떨어져 실험 DB 는 재순위 전 상태로 되돌렸다.

- DB: `migyeol_after_rerank_20261025.db` · 라벨: gold/cluster_sample.csv · gold/experiment_labels.csv

## 현재 상태

| db | cluster_kept | cluster_precision | o_retained | x_removed | full_kept | full_precision | full_recall | system_positive | tp | fp | fn | followup_precision | followup_recall | followup_f1 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| before (migyeol_before_rerank_20261025.db) | 297 | 0.744 | 0.884 | 0.587 | 11545 | 0.766 | 0.782 | 54 | 34 | 20 | 8 | 0.63 | 0.81 | 0.709 |
| current (migyeol_after_rerank_20261025.db) | 262 | 0.84 | 0.88 | 0.772 | 11259 | 0.901 | 0.897 | 56 | 31 | 25 | 11 | 0.554 | 0.738 | 0.633 |
