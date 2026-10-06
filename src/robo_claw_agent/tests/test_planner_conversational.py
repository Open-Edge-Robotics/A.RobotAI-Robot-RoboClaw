"""대화형 쿼리 판정(``_is_likely_conversational``) 회귀 테스트.

이 판정이 True 가 되면 planner 는 스킬 실행을 거부하고 LLM 에게 "대화로만
답하라"고 재요구한다. 따라서 오탐 1건은 곧 "정상 명령을 수행하지 않음"이다.
과거 길이 기반 휴리스틱("10자 이하 + 작업 키워드 없음 -> 대화형")이 짧은 실행
명령과 분해기가 만든 하위 지시문까지 삼켰던 회귀를 고정한다.
"""

from robo_claw_agent.agent_node.planner import _is_likely_conversational


class TestActionCommandsAreNotConversational:
    def test_short_action_commands(self):
        """10자 이하라도 실행 명령이면 대화형이 아니다."""
        for text in ("컵 가져와", "쓰레기 치워", "문 열기", "테이블 청소", "물체 파지"):
            assert _is_likely_conversational(text) is False, text

    def test_decomposed_substeps(self):
        """분해기가 만드는 짧은 명사구 하위 지시문이 차단되면 안 된다."""
        for text in ("지도 캡처", "주방 이동", "충전 시작", "정리 수행", "회의실로 이동해라"):
            assert _is_likely_conversational(text) is False, text

    def test_question_word_inside_action_command(self):
        """'뭐야' 등이 문장 중간에 있어도 실행 명령이면 대화형이 아니다."""
        assert _is_likely_conversational("저기 앞에 뭐야 확인하고 알려줘") is False
        assert _is_likely_conversational("지금 뭐해야 하는지 확인해줘") is False
        assert _is_likely_conversational("이름이 뭐야 라고 적힌 파일 읽어줘") is False


class TestSmallTalkIsStillDetected:
    def test_greetings(self):
        for text in ("안녕", "안녕하세요", "고마워", "잘가", "수고했어", "ㅋㅋ", "ok"):
            assert _is_likely_conversational(text) is True, text

    def test_vocative_prefixed_greetings(self):
        for text in ("로봇아 안녕", "야 잘했어", "로봇 고마워"):
            assert _is_likely_conversational(text) is True, text

    def test_self_introduction_and_capability_questions(self):
        for text in ("너 누구야", "뭐야?", "뭐하는 로봇이야", "뭐 할 수 있어?", "뭐해?", "할 일 있어?"):
            assert _is_likely_conversational(text) is True, text

    def test_empty_input(self):
        assert _is_likely_conversational("") is True
        assert _is_likely_conversational("   ") is True
