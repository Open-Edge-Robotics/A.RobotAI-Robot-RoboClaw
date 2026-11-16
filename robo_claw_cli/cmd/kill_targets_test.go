package cmd

import (
	"reflect"
	"sort"
	"testing"
)

func TestParseProcCmdline(t *testing.T) {
	got := parseProcCmdline(42, []byte("python3\x00/ws/x_node\x00--ros-args\x00"))
	want := procCmdline{PID: 42, Argv: []string{"python3", "/ws/x_node", "--ros-args"}}
	if got.PID != want.PID {
		t.Fatalf("PID = %d, want %d", got.PID, want.PID)
	}
	if !reflect.DeepEqual(got.Argv, want.Argv) {
		t.Fatalf("Argv = %#v, want %#v", got.Argv, want.Argv)
	}

	if empty := parseProcCmdline(7, nil); empty.PID != 7 || len(empty.Argv) != 0 {
		t.Fatalf("parseProcCmdline(nil) = %#v, want empty argv", empty)
	}
}

func TestProcMatchesKillTarget(t *testing.T) {
	nodeNames := DefaultKillNodeExecutables()
	launchFiles := DefaultKillLaunchFiles()

	cases := []struct {
		name string
		argv []string
		want bool
	}{
		{
			name: "native node executable",
			argv: []string{"/ws/install/robo_claw_core/lib/robo_claw_core/robo_claw_core_node", "--ros-args"},
			want: true,
		},
		{
			name: "python entry point node",
			argv: []string{"python3", "/ws/install/robo_claw_agent/lib/robo_claw_agent/robo_claw_agent_node", "--ros-args"},
			want: true,
		},
		{
			name: "sim node from robo_claw_sim",
			argv: []string{"python3", "/ws/install/robo_claw_sim/lib/robo_claw_sim/sim_node", "--ros-args"},
			want: true,
		},
		{
			name: "ros2 launch of a robo_claw launch file",
			argv: []string{"python3", "/opt/ros/jazzy/bin/ros2", "launch", "robo_claw_bringup", "robo_claw_sim.launch.py"},
			want: true,
		},
		{
			// Regression: the previous `pkill -9 -f` implementation killed the
			// coding-agent tool shell that merely mentioned the launch file.
			name: "shell whose command line mentions a launch file",
			argv: []string{"/bin/bash", "-c", "ros2 launch robo_claw_bringup robo_claw_sim.launch.py sim_engine:=stub"},
			want: false,
		},
		{
			name: "shell whose command line mentions a node name",
			argv: []string{"bash", "-c", "grep -n robo_claw_agent_node src/x.py"},
			want: false,
		},
		{
			name: "coding agent itself",
			argv: []string{"pi"},
			want: false,
		},
		{
			name: "rclaw cli itself",
			argv: []string{"./rclaw", "sim", "--engine", "stub"},
			want: false,
		},
		{
			name: "unrelated python process",
			argv: []string{"python3", "/home/user/tool.py"},
			want: false,
		},
		{
			name: "unrelated launch file",
			argv: []string{"python3", "/opt/ros/jazzy/bin/ros2", "launch", "nav2_bringup", "navigation.launch.py"},
			want: false,
		},
		{
			name: "empty argv",
			argv: nil,
			want: false,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, reason := procMatchesKillTarget(procCmdline{PID: 1000, Argv: tc.argv}, nodeNames, launchFiles)
			if got != tc.want {
				t.Fatalf("procMatchesKillTarget(%#v) = %v (%s), want %v", tc.argv, got, reason, tc.want)
			}
			if got && reason == "" {
				t.Fatalf("matched process must report a reason")
			}
		})
	}
}

func TestSelectKillTargetsSkipsSelfAndAncestors(t *testing.T) {
	procs := []procCmdline{
		{PID: 100, Argv: []string{"python3", "/ws/install/robo_claw_agent/lib/robo_claw_agent/robo_claw_agent_node"}}, // self
		{PID: 50, Argv: []string{"/ws/install/robo_claw_core/lib/robo_claw_core/robo_claw_core_node"}},                // parent
		{PID: 10, Argv: []string{"/busybox", "sh"}},                                                                   // grandparent
		{PID: 200, Argv: []string{"/ws/install/robo_claw_core/lib/robo_claw_core/robo_claw_core_node"}},               // target
		{PID: 300, Argv: []string{"python3", "/opt/ros/jazzy/bin/ros2", "launch", "robo_claw_bringup", "robo_claw.launch.py"}},
	}
	parents := map[int]int{100: 50, 50: 10, 10: 1}
	parentOf := func(pid int) (int, bool) {
		p, ok := parents[pid]
		return p, ok
	}

	got := selectKillTargets(procs, 100, parentOf, DefaultKillNodeExecutables(), DefaultKillLaunchFiles())
	var pids []int
	for _, p := range got {
		pids = append(pids, p.PID)
	}
	sort.Ints(pids)
	want := []int{200, 300}
	if !reflect.DeepEqual(pids, want) {
		t.Fatalf("selectKillTargets PIDs = %v, want %v", pids, want)
	}
}

func TestKillNodeExecutablesAreDisjointFromLaunchFiles(t *testing.T) {
	nodes := DefaultKillNodeExecutables()
	launches := DefaultKillLaunchFiles()
	if len(nodes) == 0 || len(launches) == 0 {
		t.Fatal("expected non-empty node and launch file lists")
	}
	for _, n := range nodes {
		for _, l := range launches {
			if n == l {
				t.Fatalf("%q must not be both a node executable and a launch file", n)
			}
		}
		if neverKillExecutables[n] {
			t.Errorf("%q is both a node executable and a never-kill wrapper", n)
		}
	}
}

func TestSelectPortKillTargetsSkipsNonRoboClawHolders(t *testing.T) {
	byPID := map[int]procCmdline{
		// pi-bot-connect 는 RoboClaw gRPC 와 같은 50052 를 점유한다. 절대 종료하면 안 된다.
		10: {PID: 10, Argv: []string{"pi"}},
		20: {PID: 20, Argv: []string{"python3", "/ws/install/robo_claw_grpc/lib/robo_claw_grpc/robo_claw_grpc_node"}},
		30: {PID: 30, Argv: []string{"python3", "-m", "http.server", "8080"}},
		40: {PID: 40, Argv: []string{"./rclaw", "kill"}},
	}

	targets, skipped := selectPortKillTargets(
		[]int{10, 20, 30, 40, 99},
		byPID,
		map[int]bool{40: true},
		DefaultKillNodeExecutables(),
		DefaultKillLaunchFiles(),
	)

	if len(targets) != 1 || targets[0].PID != 20 {
		t.Fatalf("targets = %#v, want only the RoboClaw gRPC node (PID 20)", targets)
	}
	if len(skipped) != 3 {
		t.Fatalf("skipped = %#v, want pi/http.server/protected rclaw to be skipped", skipped)
	}
	for _, p := range skipped {
		if p.PID == 20 {
			t.Fatalf("RoboClaw node must not be reported as skipped")
		}
	}
}
