# 프롬프트 토큰 수 측정 (count_prompt_tokens.py)

로컬 LLM(Ollama)에서 시스템 프롬프트 조각들(SKILLS·soul·troubleshooting·limits 등)의 실제 토큰
수(`prompt_eval_count`)를 측정해 적정 `num_ctx`를 산정하는 도구입니다. 스크립트는
`scripts/count_prompt_tokens.py`에 있습니다. AI Config 하이퍼파라미터 조회는
[`AI_CONFIG_VIEWER.md`](./AI_CONFIG_VIEWER.md)를 참고하세요.

## 왜 필요한가

로컬 LLM(Ollama)의 `num_ctx`(컨텍스트 창)가 시스템 프롬프트보다 작으면 **입력이 잘려(truncate)**
SKILLS 가이드·스킬 목록 일부가 모델에 전달되지 않고, 라우팅 실패·비일관 동작이 생깁니다.
이 스크립트는 실제 프롬프트 조각들을 Ollama에 넣어 **`prompt_eval_count`(모델이 처리한 입력 토큰 수)**
를 읽어, `num_ctx`를 데이터 기반으로 정하도록 돕습니다.

### 요구사항

- Python 3.8+ (표준 라이브러리만 사용 — 추가 설치 불필요)
- 대상 모델이 로드된 Ollama 서버 접근(HTTP)

### 사용법

```bash
# 1) 실제 주입되는 조각들을 이어붙여 측정 (SKILLS / soul / troubleshooting / limits)
python3 scripts/count_prompt_tokens.py \
  ../roboclaw_config_server/database/seeds/SKILLS.former.md \
  ../roboclaw_config_server/database/seeds/ROBOT.md \
  ../roboclaw_config_server/database/seeds/TROUBLESHOOTING.md \
  ../roboclaw_config_server/database/seeds/ROBOT_LIMITS.json

# 2) Ollama 주소/모델 지정
OLLAMA_URL=http://10.159.172.75:11434 \
  python3 scripts/count_prompt_tokens.py --model gemma4:e4b SKILLS.former.md

# 3) 표준입력으로도 가능 (예: 실제 조립된 시스템 프롬프트 덤프를 파이프)
cat system_prompt.txt | python3 scripts/count_prompt_tokens.py -
```

### 옵션

| 옵션            | 기본값                       | 설명                                                               |
| :-------------- | :--------------------------- | :----------------------------------------------------------------- |
| `files...`      | (필수)                       | 이어붙일 프롬프트 파일들. `-` 이면 표준입력.                       |
| `--model`       | `gemma4:e4b`                 | 측정에 쓸 Ollama 모델 (`OLLAMA_MODEL` 환경변수로도 지정).          |
| `--ollama-url`  | `http://10.159.172.75:11434` | Ollama 주소 (`OLLAMA_URL` 환경변수로도 지정).                      |
| `--num-ctx`     | `65536`                      | **측정용** 컨텍스트 크기. 프롬프트가 잘리지 않게 충분히 크게 둔다. |
| `--sep`         | 빈 줄                        | 파일 사이 구분자.                                                  |
| `--show-prompt` | 꺼짐                         | 이어붙인 프롬프트 전문을 함께 출력.                                |

### 출력 예시

```
파일 수        : 4
문자 수        : 5872
prompt_eval_count(토큰): 3110
문자/토큰 비율 : 1.89   (num_ctx 산정용 참고)
권장 num_ctx   : 8192 이상 (시스템 프롬프트 3110토큰 + 이력/도구결과 여유 반영)
```

- **문자/토큰 비율**: 한글 비중이 크면 낮게(≈1.5~2.0) 나옵니다. 이후 추정에 재활용하세요.
- **권장 num_ctx**: 측정 토큰의 2배(이력·도구결과 여유)를 2의 거듭제곱으로 올린 값.
- `--num-ctx` 가 프롬프트 토큰보다 작으면 측정값이 잘릴 수 있어 경고를 출력합니다.

### 주의 — 이건 "부분" 측정입니다

스크립트는 **넣어준 파일만** 잽니다. 실제 시스템 프롬프트에는 이 외에도
**자동생성 스킬 목록**과 **정적 기본 프롬프트**가 포함됩니다. 전체를 정확히 재려면 로봇에서
**실제 조립된 시스템 프롬프트를 덤프**해 `-`(표준입력)로 넘기세요.

> 과거 실측(한글 시스템 프롬프트, 스킬 목록 포함) 기준 시스템 프롬프트만 ≈12k 토큰,
> 대화 이력·RAG 결과·응답까지 포함하면 총 ≈17k~21k 토큰이 필요했다. 이 기준으로
> `num_ctx: 32768` 이 권장되며, `8192` 계열 값은 시스템 프롬프트가 잘려(SKILLS 가이드 누락)
> 라우팅 실패·비일관 동작의 원인이 된다. 모델·가이드 변경 후에는 반드시 재측정할 것.

### 함께 보면 좋은 확인

```bash
# 지금 num_ctx 에서 프롬프트가 잘리고 있는지 (잘리면 num_ctx 상향이 즉효)
ssh former@10.159.172.69 \
  'docker logs robo_claw_container 2>&1 | grep -iE "truncat|exceeds context|context window"'
```
