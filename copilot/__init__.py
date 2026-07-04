"""copilot — 자연어 질의 → 툴화된 온톨로지 read → 문장별 cites 강제 SituationAssessment.

P4 오케스트레이터(architecture.md §4). 세 모듈로 관심사를 분리한다:
  parser.py      결정적 질의 파서(지역·시간창). LLM 파싱 비목표(DR-0006).
  tools.py       store 질의 래핑(객체 id 보존). Foundry 개통 시 OSDK read로 치환.
  assessment.py  병렬 read → 사실 → 문장 조립(cites 강제) → GenerateSituationAssessment.

핵심 불변식(DR-0006): citation은 LLM 생성이 아니라 사실→문장 조립의 부산물이다.
근거 객체 id 없는 문장은 어떤 경로로도 Assessment에 못 들어간다(CLAUDE.md 원칙 4).
"""
