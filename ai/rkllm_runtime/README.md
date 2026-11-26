# RKLLM 웹 런타임 환경

실제 NPU 모델 실행 진입점과 native RKLLM library는 장비에 별도 설치된 RKLLM handoff를 사용한다. 이 디렉터리는 Python 3.11 Flask 웹 런타임의 재현 가능한 의존성만 관리한다. 모델·양자화 파일·장비 경로는 Git에 추가하지 않는다.

- Python 3.11, Flask 3.1.3
- 실제 모델 식별자: `qwen440-v37`
- 실제 RKLLM runtime/toolkit 1.2.1, RK3588 W8A8
- entrypoint와 working directory는 서비스 생성 시 전달하며 모델 경로는 해당 장비의 handoff에서 확인한다.
- backend가 사용하는 OpenAI 호환 `/v1/models`, `/v1/chat/completions`와 내부 `/health`를 제공한다.

최초 설치와 서비스 설정은 [배포 도구](../deploy/README.md)를 따른다. Python 3.10의 시스템 패키지를 교체하지 않고 별도의 `.venv`를 사용한다. 모델·prompt SHA와 실제 Pi 추론 검증 결과는 배포·시험 가이드에 기록한다.
