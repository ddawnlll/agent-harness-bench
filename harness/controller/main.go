package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"
	"time"
)

var processStart = time.Now()

func main() {
	if os.Getenv("XDG_RUNTIME_DIR") == "" {
		os.Setenv("XDG_RUNTIME_DIR", fmt.Sprintf("/run/user/%d", os.Getuid()))
	}
	if os.Getenv("DBUS_SESSION_BUS_ADDRESS") == "" {
		os.Setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path="+os.Getenv("XDG_RUNTIME_DIR")+"/bus")
	}
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: benchctl <relay|run|preflight|version>")
		os.Exit(2)
	}
	switch os.Args[1] {
	case "relay":
		cmdRelay(os.Args[2:])
	case "run":
		cmdRun(os.Args[2:])
	case "preflight":
		cmdPreflight(os.Args[2:])
	case "selftest":
		cmdSelftest(os.Args[2:])
	case "version":
		fmt.Println("benchctl 1.0.0")
	default:
		fmt.Fprintln(os.Stderr, "unknown command", os.Args[1])
		os.Exit(2)
	}
}

func cmdRelay(args []string) {
	fs := flag.NewFlagSet("relay", flag.ExitOnError)
	addr := fs.String("addr", "0.0.0.0:8781", "listen address")
	agent := fs.String("agent", "test", "agent id")
	task := fs.String("task", "t0", "task id")
	round := fs.String("round", "r0", "round id")
	exp := fs.String("exp", "relaytest", "experiment id")
	run := fs.String("run", "relaytest", "run id")
	logPath := fs.String("log", "", "jsonl log path")
	keyFile := fs.String("keyfile", os.Getenv("HOME")+"/.pi/agent/auth.json", "credential source")
	fs.Parse(args)
	key, src, err := loadAPIKey(*keyFile)
	if err != nil {
		fmt.Fprintln(os.Stderr, "credential error:", err)
		os.Exit(3)
	}
	fmt.Fprintf(os.Stderr, "relay: credential source=%s len=%d\n", src, len(key))
	if *logPath == "" {
		*logPath = "evidence/api_usage.jsonl"
	}
	r, err := NewRelay(*exp, *run, *round, *agent, *task, key, *logPath)
	if err != nil {
		panic(err)
	}
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()
	_, actual, err := StartRelay(ctx, r, *addr)
	if err != nil {
		panic(err)
	}
	b, _ := json.Marshal(map[string]any{"listening": actual, "agent": *agent, "log": *logPath})
	fmt.Println(string(b))
	<-ctx.Done()
	time.Sleep(100 * time.Millisecond)
}
