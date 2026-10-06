package cmd

import (
	"fmt"

	"github.com/wkqco33/wcli"
)

func VersionCmd(version string) *wcli.Command {
	return &wcli.Command{
		Use:   "version",
		Short: "버전 정보를 출력합니다",
		Run: func(ctx *wcli.Context) error {
			fmt.Printf("robo_claw_cli version %s\n", version)
			return nil
		},
	}
}
