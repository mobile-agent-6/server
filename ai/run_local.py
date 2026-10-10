"""Run one local Qwen conversation against the configured Schedule API."""

from __future__ import annotations

import argparse

from schedule_assistant import ConversationService
from schedule_assistant.clients import OllamaClient, ScheduleClient


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="installed local Ollama model name")
    parser.add_argument("--schedule-url", required=True, help="Schedule API origin, e.g. http://127.0.0.1:8000")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    args = parser.parse_args()
    service = ConversationService(OllamaClient(args.model, args.ollama_url),
                                  ScheduleClient(args.schedule_url))
    print("일정 비서가 준비됐어요. 종료하려면 /quit, 대화를 초기화하려면 /reset을 입력하세요.")
    while True:
        try:
            utterance = input("나> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if utterance == "/quit":
            break
        if utterance == "/reset":
            service.reset()
            if service.state.uncertain_create is not None:
                print("비서> 대화를 초기화했어요. 결과가 불확실한 등록은 보관 중이에요. '재시도'로 확인해 주세요.")
            else:
                print("비서> 대화를 초기화했어요.")
            continue
        reply = service.handle(utterance)
        print("비서> " + reply.message)


if __name__ == "__main__":
    main()
