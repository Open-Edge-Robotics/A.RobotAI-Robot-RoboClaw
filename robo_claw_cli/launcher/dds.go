package launcher

import (
	"fmt"
	"os"
	"strings"
)

// CycloneDdsMaxAutoParticipantIndex 한 도메인에서 동시에 생성할 수 있는 CycloneDDS
// 참여자(프로세스) 수 상한입니다.
//
// Gazebo 시뮬레이션(TurtleBot4 Harmonic)은 ros_gz 브리지·컨트롤러·툴박스 노드를
// 50개 이상 띄운다. CycloneDDS 기본 자동 인덱스 범위(실측 32개)를 넘으면 노드가
// 기동 직후 다음 오류로 abort 한다(-6).
//
//	Failed to find a free participant index for domain 0
//	rmw_create_node: failed to create domain, error Error
//
// 이 경우 clock 브리지까지 죽어 use_sim_time 노드가 전부 멈추고, 에이전트 노드도
// 함께 사라져 "시뮬레이터만 뜨고 RoboClaw 는 죽은" 상태가 된다.
const CycloneDdsMaxAutoParticipantIndex = 200

// CycloneDdsUri 공통 CycloneDDS 인라인 설정 문자열을 만듭니다.
//
// whcHigh 가 비어 있으면 Internal/Watermarks 항목을 넣지 않습니다(기존 동작 보존).
// ROS 배포판별 기본값(최대 메시지 크기)만 다르고 참여자 상한은 항상 포함됩니다.
func CycloneDdsUri(maxMessageSize, whcHigh string) string {
	internal := ""
	if whcHigh != "" {
		internal = fmt.Sprintf(
			"<Internal><Watermarks><WhcHigh>%s</WhcHigh></Watermarks></Internal>",
			whcHigh,
		)
	}
	return fmt.Sprintf(
		"<CycloneDDS><Domain><General><MaxMessageSize>%s</MaxMessageSize>"+
			"<FragmentSize>1300B</FragmentSize></General>"+
			"<Discovery><ParticipantIndex>auto</ParticipantIndex>"+
			"<MaxAutoParticipantIndex>%d</MaxAutoParticipantIndex></Discovery>%s</Domain></CycloneDDS>",
		maxMessageSize, CycloneDdsMaxAutoParticipantIndex, internal,
	)
}

// DefaultSimDomainID 시뮬레이션이 사용하는 기본 ROS 도메인입니다.
//
// 기본값 0 은 사무실 LAN 의 실기기·다른 개발 PC 와 같은 도메인이라, 시뮬레이션 노드가
// 실로봇의 액션/토픽을 발견하고(안전 문제) 죽은 참여자에게 계속 쓰는 실패
// (`ddsi_udp_conn_write ... failed with retcode`)가 로그를 덮습니다.
const DefaultSimDomainID = "42"

// ResolveSimDomainID 시뮬레이션이 사용할 ROS 도메인을 결정합니다.
// 우선순위: 호스트 ROS_DOMAIN_ID -> RC_SIM_DOMAIN_ID -> DefaultSimDomainID.
func ResolveSimDomainID(envMap map[string]string) string {
	if value := strings.TrimSpace(os.Getenv("ROS_DOMAIN_ID")); value != "" {
		return value
	}
	if envMap != nil {
		if value := strings.TrimSpace(envMap["ROS_DOMAIN_ID"]); value != "" {
			return value
		}
	}
	if value := strings.TrimSpace(os.Getenv("RC_SIM_DOMAIN_ID")); value != "" {
		return value
	}
	if envMap != nil {
		if value := strings.TrimSpace(envMap["RC_SIM_DOMAIN_ID"]); value != "" {
			return value
		}
	}
	return DefaultSimDomainID
}
