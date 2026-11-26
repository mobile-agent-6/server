# 물류 일정 Qwen 멀티턴 보완 전달 안내

전달 구성: **440건 학습 모델 + v37 후처리**. 이전 400건 + v17 공유 이후의 멀티턴 보완 자료다.

- [구성·평가·검증 방법](../datasets/qwen/experiments/logistics440_v37/README.md)
- [기존 184개 대화의 시점별 정답](../datasets/qwen/experiments/logistics440_v37/data/legacy184-prefix/턴별정답_눈검수.txt)
- [3·4·5라운드 교대 60개 대화](../datasets/qwen/experiments/logistics440_v37/data/multiturn60/멀티턴60개_읽기쉬운검수본.txt)
- [15개 잔여 오류 수정 전후](../datasets/qwen/experiments/logistics440_v37/evaluation/remaining15-resolution.json)
- [연동 안내](../datasets/qwen/experiments/logistics440_v37/INTEGRATION.txt)
- [가중치 Release](https://github.com/mobile-agent-6/server/releases/tag/qwen-logistics440-v37-20261111)

기존 184개 대화를 381개 시점별 정답으로 구성했고, 이 중 신규 중간 정답 197개는 아직 학습하지 않았다. 별도 교대 대화는 3·4·5라운드 각각 20개, 기능별 12개다.

기존 오답 15건을 보완해 전체 60대화의 고유 입력 132건은 저장 출력 재평가에서 117/132 → 132/132가 됐다. 실패 사례를 보고 수정한 개발 결과이므로 전체 서비스 정확도 또는 실제 API 성공률 100%로 해석하지 않는다.

실제 모델 가중치는 440예시 학습 상태다. Git에는 코드·합성 데이터·평가 결과만 있고 가중치와 tokenizer는 별도 Release ZIP에 포함한다. 기본 모델 본체는 식별 해시가 일치하는 기존 전달본을 재사용한다.

최종 서버는 v37 후처리를 Assistant API에 연결하며, 후처리 이후에도 기존 출력 검사, 후보 조회와 승인 절차를 유지한다. 상세 연동 구조는 [백엔드 README](../backend/README.md#모델-서버)를 확인한다.
