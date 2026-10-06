# RoboClaw 메신저 연동 가이드

RoboClaw는 텔레그램(Telegram), 슬랙(Slack), 디스코드(Discord)를 통해 원격에서 로봇을 제어하고 대화할 수 있는 인터페이스를 제공합니다. 각 플랫폼별 설정 방법을 안내합니다.

---

## 1. Telegram 연동 (가장 간편)

1.  **봇 생성**: [@BotFather](https://t.me/botfather)에게 `/newbot` 명령을 보내 봇을 생성합니다.
2.  **토큰 복사**: 생성 완료 시 제공되는 `HTTP API Token`을 복사합니다.
3.  **.env 설정**:
    ```bash
    TELEGRAM_BOT_TOKEN=123456789:ABCDefgh-IJKLmnop...
    ```
4.  **사용**: 텔레그램에서 봇에게 말을 걸면 에이전트가 답변합니다.

---

## 2. Slack 연동 (Socket Mode 추천)

슬랙은 서버 구축 없이 실시간 통신이 가능한 **Socket Mode**를 사용합니다.

1.  **앱 생성**: [Slack API](https://api.slack.com/apps)에서 `Create New App` -> `From scratch`를 선택합니다.
2.  **Socket Mode 활성화**: `Settings` -> `Socket Mode`를 `On`으로 설정합니다. 이때 생성되는 `xapp-`로 시작하는 **App-Level Token**을 복사합니다.
3.  **Scopes 설정**: `Features` -> `OAuth & Permissions`에서 다음 범위를 추가합니다.
    - `app_mentions:read` (멘션 시 읽기)
    - `chat:write` (메시지 보내기)
    - `im:read` (DM 읽기)
4.  **Event Subscriptions**: `Enable Events`를 `On`으로 하고, `Subscribe to bot events`에 `message.channels` 및 `message.im`을 추가합니다.
5.  **토큰 설치**: `Settings` -> `Install App`을 진행하여 `xoxb-`로 시작하는 **Bot User OAuth Token**을 복사합니다.
6.  **.env 설정**:
    ```bash
    SLACK_APP_TOKEN=xapp-1-... (App Level Token)
    SLACK_BOT_TOKEN=xoxb-... (Bot User OAuth Token)
    ```

---

## 3. Discord 연동

1.  **애플리케이션 생성**: [Discord Developer Portal](https://discord.com/developers/applications)에서 `New Application`을 만듭니다.
2.  **봇 설정**: `Bot` 탭에서 봇의 이름을 정하고 **Token**을 생성하여 복사합니다.
3.  **Privileged Gateway Intents**: 같은 `Bot` 탭 아래에서 다음 인텐트를 모두 활성화(On)해야 합니다.
    - `PRESENCE INTENT`
    - `SERVER MEMBERS INTENT`
    - **`MESSAGE CONTENT INTENT`** (필수)
4.  **초기 설치**: `OAuth2` -> `URL Generator`에서 `bot` 체크 후, `Send Messages`, `Read Message History` 권한을 선택해 생성된 링크로 본인의 서버에 봇을 초대합니다.
5.  **.env 설정**:
    ```bash
    DISCORD_BOT_TOKEN=OTk5...
    ```

---

## 🚀 실행

모든 토큰이 설정되었다면 채널 노드가 자동으로 활성화됩니다.

```bash
# 권장: .env 설정 기반 실행
./rclaw run

# 또는 launch 인자 직접 전달 (provider, model, 채널 사용 여부)
./rclaw run azure gpt-4.1 true
```

각 메신저 채널은 별도 스레드에서 동시에 실행되므로, 한 대의 로봇을 텔레그램, 슬랙, 디스코드에서 동시에 제어할 수 있습니다.

토큰은 `.env`에만 넣고 커밋하지 마세요. 운영 배포에서는 AI Config Server profile + secret으로 관리하는 것을 권장합니다([CONFIGURATION_GUIDE.md](CONFIGURATION_GUIDE.md) 참고).
