# Cursor Agent Harness

이 문서의 **하네스**는 ML 평가 코드(`src/kostolany/harness/`)가 아니라,  
**Cursor agent를 SOTA로 돌리기 위한 프로젝트 인프라**다.

## Stack

```
AGENTS.md                     # 에이전트 진입 가이드 + 라우팅
.cursor/rules/*.mdc           # always + glob 가드레일
.cursor/skills/*/SKILL.md     # 구현/검증/도메인 워크플로
.cursor/hooks.json            # stop verify reminder, ML edit flag, destructive shell gate
scripts/agent_verify.py       # done-gate (pytest + leakage smoke)
```

## Skills

| Skill | When |
|---|---|
| `kostolany-implement` | 기능 구현 시작 |
| `kostolany-verify` | 완료 게이트 / ralph 검증 |
| `kostolany-regime` | 국면·라벨·게이지·권고 문구 |

Invoke by name in chat, or rely on description-based discovery.

## Rules

| Rule | Scope |
|---|---|
| `kostolany-core` | always |
| `leakage-safe-ml` | harness/labels/models/engine/tests |
| `web-egg-ux` | `web/**` |

## Hooks

1. **afterFileEdit** → ML 민감 파일 수정 시 context 주입 + `.needs_verify` 마커
2. **stop** → 마커 있으면 verify follow-up 메시지 (loop_limit 2)
3. **beforeShellExecution** → force-push / hard reset 등 destructive 패턴은 ask

## Recommended agent modes

- 작은 수정: Agent + `kostolany-implement` → `kostolany-verify`
- 병렬 대형: `/ulw` + lane별 executor
- 완료 고정: `/ralph` (verify 루프)
- 기획 모호: `/ralplan` or deep-interview 후 구현

## Relation to ML harness code

`docs/HARNESS.md` = **모델 평가** purged-CV/leakage 러너.  
이 문서 = **에이전트 운용** harness. 둘 다 필요하지만 목적이 다르다.
