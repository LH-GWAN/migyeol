# 후속 신호 변형 비교 (2026-10-25, DB 사본·연결 10-24 상태, 약속 엄격화 적용 후)

promises: source=원천 발언 약속만 / fallback=원천 약속 없으면 전체 / all=전체. cutoff: source=원천 발언일 이후 / old=발생+7일·첫 약속일 / max=둘 중 늦은 날. anchor: 표현 옆 약속 대상어 요구. src_firm_only: 원천 발언의 의사표명 제외.

| promises | cutoff | anchor | src_firm_only | pos | P | R | F1 |
| source | source | True | True | 27 | 0.63 | 0.405 | 0.493 |
| source | source | True | False | 39 | 0.641 | 0.595 | 0.617 |
| source | source | False | True | 34 | 0.647 | 0.524 | 0.579 |
| source | source | False | False | 47 | 0.638 | 0.714 | 0.674 |
| source | old | True | True | 26 | 0.654 | 0.405 | 0.5 |
| source | old | True | False | 38 | 0.658 | 0.595 | 0.625 |
| source | old | False | True | 32 | 0.656 | 0.5 | 0.567 |
| source | old | False | False | 45 | 0.644 | 0.69 | 0.666 |
| fallback | source | True | True | 52 | 0.577 | 0.714 | 0.638 |
| fallback | source | True | False | 53 | 0.566 | 0.714 | 0.631 |
| fallback | source | False | True | 59 | 0.593 | 0.833 | 0.693 |
| fallback | source | False | False | 61 | 0.574 | 0.833 | 0.68 |
| fallback | old | True | True | 49 | 0.612 | 0.714 | 0.659 |
| fallback | old | True | False | 50 | 0.6 | 0.714 | 0.652 |
| fallback | old | False | True | 56 | 0.607 | 0.81 | 0.694 |
| fallback | old | False | False | 58 | 0.586 | 0.81 | 0.68 |
| all | source | True | True | 54 | 0.593 | 0.762 | 0.667 |
| all | source | True | False | 55 | 0.582 | 0.762 | 0.66 |
| all | source | False | True | 59 | 0.593 | 0.833 | 0.693 |
| all | source | False | False | 61 | 0.574 | 0.833 | 0.68 |
| all | old | True | True | 51 | 0.608 | 0.738 | 0.667 |
| all | old | True | False | 52 | 0.596 | 0.738 | 0.659 |
| all | old | False | True | 57 | 0.614 | 0.833 | 0.707 |
| all | old | False | False | 59 | 0.593 | 0.833 | 0.693 |

| promises | cutoff | anchor | src_firm_only | pos | P | R | F1 |
| all | old | False | True | 57 | 0.614 | 0.833 | 0.707 |
| all | old | w60 | True | 53 | 0.604 | 0.762 | 0.674 |
| all | old | prog | True | 54 | 0.611 | 0.786 | 0.688 |
| all | max | False | True | 56 | 0.607 | 0.81 | 0.694 |
| all | max | w60 | True | 51 | 0.608 | 0.738 | 0.667 |
| all | max | prog | True | 53 | 0.604 | 0.762 | 0.674 |

## SIGNAL_MIN_CONF (all·old·anchor 없음)

| SIGNAL_MIN_CONF | 있음 | P | R | F1 |
|---|---|---|---|---|
| 0.60 | 57 | 0.614 | 0.833 | 0.707 |
| 0.65 | 57 | 0.614 | 0.833 | 0.707 |
| 0.70 | 49 | 0.633 | 0.738 | 0.681 |
| 0.72 | 46 | 0.609 | 0.667 | 0.637 |
| 0.75 | 36 | 0.639 | 0.548 | 0.590 |
| 0.80 | 30 | 0.633 | 0.452 | 0.527 |
