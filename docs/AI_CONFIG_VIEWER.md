# AI Config 하이퍼파라미터 조회 (show_ai_config.py)

config 서버(`roboclaw_config_server`)에 저장된 **활성 설정**의 LLM·임베딩 하이퍼파라미터를
읽어 보기 좋게 출력합니다. 로봇이 실제로 쓰는 값(모델·num_ctx·temperature·RAG threshold 등)을
한눈에 확인해 튜닝/디버깅에 사용합니다.

## 요구사항

- Python 3.8+ (표준 라이브러리만 사용, 추가 설치 불필요)
- config 서버 접근 + **Bearer 토큰**(서버에 토큰이 설정된 경우)

## 인증 토큰

서버에 `ADMIN_TOKEN`/`DEVICE_TOKEN`이 설정돼 있으면 API 호출에 토큰이 필요합니다.

- `/api/v1/configs/active` → device 또는 admin 토큰
- `/api/v1/configs`(자동탐색) → **admin** 토큰

토큰 지정: `--token` 또는 환경변수 `ROBOCLAW_CONFIG_TOKEN` / `ADMIN_TOKEN` / `DEVICE_TOKEN`.

## 사용법

```bash
# env 명시 (device 토큰이면 env 명시 권장 → /configs/active 로 조회)
ADMIN_TOKEN=xxxx python3 scripts/show_ai_config.py --env 0047_w2_2f

# 로봇/환경/토큰 명시
python3 scripts/show_ai_config.py --robot former --env 0047_w2_2f --token xxxx

# config ID 직접 지정
python3 scripts/show_ai_config.py --config-id 7 --token xxxx

# env 생략 → is_active=true 자동 탐색(admin 토큰 필요)
ADMIN_TOKEN=xxxx python3 scripts/show_ai_config.py --robot former

# 원본 config JSON 그대로
python3 scripts/show_ai_config.py --env 0047_w2_2f --token xxxx --json
```

## 옵션

| 옵션           | 기본값                       | 설명                                             |
| :------------- | :--------------------------- | :----------------------------------------------- |
| `--config-url` | `http://10.159.172.74:30180` | config 서버 URL (`CONFIG_URL` 환경변수로도 지정) |
| `--robot`      | `former`                     | 로봇명                                           |
| `--env`        | (없음)                       | 환경명. 미지정 시 `is_active` 자동 탐색          |
| `--config-id`  | (없음)                       | config ID 직접 지정(가장 확실)                   |
| `--token`      | 환경변수                     | Bearer 토큰                                      |
| `--json`       | 꺼짐                         | 원본 config JSON 출력                            |

## 출력 예시

```
============================================================
프로파일: Former_Factory_0047  (robot=former, env=0047_w2_2f, id=7, active=True)
============================================================

LLM :
  - Model             : gemma4:31b
  - Model URL         : http://10.159.172.74:11434
  - num_ctx           : 32768
  - temperature       : 0.2
  - Repeat Penalty    : 1
  - Repeat Last N     : 64
  - Seed              : (미설정)
  - Max Predict Token : 1024
  - Top K             : 10
  - Top P             : 0.9
  - Min P             : 0.1

Embedding Model :
  - Model               : qwen3-embedding:8b
  - Qdrant URL          : http://10.159.172.74:6333
  - Qdrant Collection   : robo_claw_former_0047_w2_2f
  - Qdrant Timeout      : 5.0
  - RAG Top-K           : 5
  - RAG Score Threshold : 0.55
```

## 값의 출처(config 필드 매핑)

| 표시                                                                                                      | config 필드                                                                                                                                    |
| :-------------------------------------------------------------------------------------------------------- | :--------------------------------------------------------------------------------------------------------------------------------------------- |
| LLM Model                                                                                                 | `llm_model`                                                                                                                                    |
| LLM Model URL                                                                                             | `ollama_base_url` (없으면 `azure_openai_endpoint`)                                                                                             |
| num_ctx / temperature / Repeat Penalty / Repeat Last N / Seed / Max Predict Token / Top K / Top P / Min P | `ollama_options_json` 내 `num_ctx` / `temperature` / `repeat_penalty` / `repeat_last_n` / `seed` / `num_predict` / `top_k` / `top_p` / `min_p` |
| Embedding Model                                                                                           | `llm_embedding_model`                                                                                                                          |
| Qdrant URL / Collection / Timeout                                                                         | `qdrant_url` / `qdrant_collection` / `qdrant_timeout_sec`                                                                                      |
| RAG Top-K / Score Threshold                                                                               | `rag_top_k` / `rag_score_threshold`                                                                                                            |

- 값이 비어 있으면 `(미설정)`으로 표시합니다(예: Seed 미지정).
- LLM 세부 파라미터는 **`ollama_options_json`(JSON 문자열)** 을 파싱해서 뽑습니다. 이 JSON에
  없는 키는 서버 기본값으로 동작하며 여기서는 `(미설정)`으로 보입니다.

## 문제 해결

- **`HTTP 401/403`**: 토큰 누락/불일치 → `--token` 또는 환경변수 지정.
- **`연결 실패`**: URL/포트(30180)·네트워크 확인. `curl -s <url>/ping` 로 기본 연결 확인.
- **`is_active=true 설정이 없음`**: `--env` 또는 `--config-id` 로 대상 지정.

## 관련

- num_ctx 산정: [`PROMPT_TOKEN_COUNT.md`](./PROMPT_TOKEN_COUNT.md) (권장 `num_ctx: 32768`)
- 임베딩 검색 품질 프로브: `validation/talk/rag_score_probe.py`
