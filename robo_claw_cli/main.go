package main

import (
	"errors"
	"fmt"
	"os"

	"robo_claw_cli/cmd"

	"github.com/wkqco33/wcli"
	"github.com/wkqco33/wcli/logging"
)

const version = "0.1.1"

func main() {
	logger := logging.NewDefaultLogger(os.Stderr, logging.LevelInfo, true)
	logging.SetLogger(logger)

	var debug bool
	var verbose bool

	root := &wcli.Command{
		Use:     "rclaw",
		Aliases: []string{"robo_claw_cli"},
		Short:   "RoboClaw CLI (rclaw) 도구",
		Version: version,
		PersistentPreRun: func(ctx *wcli.Context) error {
			if debug || verbose {
				logger.MinLevel = logging.LevelDebug
			}
			if err := cmd.InitConfig(); err != nil {
				return err
			}
			return nil
		},
	}

	root.PersistentFlags().BoolVar(&debug, "debug", "d", false, "디버그 로그 활성화")
	root.PersistentFlags().BoolVar(&verbose, "verbose", "v", false, "상세 로그 활성화")

	root.AddCommand(
		cmd.ConfigCmd(),
		cmd.VersionCmd(version),
		cmd.ListCmd(),
		cmd.FetchCmd(),
		cmd.DoctorCmd(),
		cmd.EffectiveCmd(),
		cmd.LaunchCmd(),
		cmd.RunCmd(),
		cmd.SimCmd(),
		cmd.ContractCmd(),
		cmd.ContractUpdateCmd(),
		cmd.KillCmd(),
		cmd.ResetRobotCmd(),
		cmd.StatusCmd(),
		cmd.SkillsCmd(),
		cmd.TaskCmd(),
		cmd.TestCmd(),
		wcli.NewCompletionCommand(root),
	)

	if err := root.Execute(os.Args[1:]); err != nil {
		var friendly interface{ FriendlyMessage() string }
		if errors.As(err, &friendly) {
			fmt.Fprintf(os.Stderr, "\n💡 조치 방법: %s\n", friendly.FriendlyMessage())
		}
		os.Exit(1)
	}
}
