package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

type RoundConfig struct {
	RoundID         string `json:"round_id"`
	TaskID          string `json:"task_id"`
	TaskRepo        string `json:"task_repo"`
	GraderScript    string `json:"grader_script"`
	DurationSeconds int    `json:"duration_seconds"`
	CPUGroupOffset  int    `json:"cpu_group_offset"`
}

type ExperimentConfig struct {
	ExperimentID          string        `json:"experiment_id"`
	Model                 string        `json:"model"`
	Provider              string        `json:"provider"`
	ReasoningEffort       string        `json:"reasoning_effort"`
	ResultsRoot           string        `json:"results_root"`
	WorkspacesRoot        string        `json:"workspaces_root"`
	MemoryMaxBytes        int64         `json:"memory_max_bytes"`
	CPUQuota              int64         `json:"cpu_quota"`
	CPUPeriod             int64         `json:"cpu_period"`
	PidsMax               int64         `json:"pids_max"`
	RelayBasePort         int           `json:"relay_base_port"`
	GlobalDeadlineSeconds int           `json:"global_deadline_seconds"`
	GraderTimeoutSeconds  int           `json:"grader_timeout_seconds"`
	CPUGroups             []string      `json:"cpu_groups"`
	Agents                []AgentEntry  `json:"agents"`
	Rounds                []RoundConfig `json:"rounds"`
	MinFreeDiskBytes      int64         `json:"min_free_disk_bytes"`
}

type AgentEntry struct {
	ID     string `json:"id"`
	Binary string `json:"binary"`
}

type TrialRuntime struct {
	Round          RoundConfig
	Agent          AgentEntry
	Workspace      string
	Home           string
	CG             *Scope
	Relay          *Relay
	RelayAddr      string
	StdoutPath     string
	StderrPath     string
	StartSHA       string
	StartUTC       time.Time
	EndUTC         time.Time
	BarrierMono    time.Time
	ExecMono       time.Time
	ExitMono       time.Time
	ExitCode       int
	TimedOut       bool
	FirstReqMono   int64
	FirstTokenMono int64
	FirstToolMono  int64
	CPUs           string
	Err            error
	Notes          []string
	Summary        RunSummary
}

func loadConfig(path string) (*ExperimentConfig, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var c ExperimentConfig
	if err := json.Unmarshal(b, &c); err != nil {
		return nil, err
	}
	if c.Model == "" || c.Provider == "" {
		return nil, errors.New("config must pin provider and model")
	}
	if c.GlobalDeadlineSeconds <= 0 {
		c.GlobalDeadlineSeconds = 7200
	}
	if c.MemoryMaxBytes == 0 {
		c.MemoryMaxBytes = 3 * 1024 * 1024 * 1024
	}
	if c.CPUQuota == 0 {
		c.CPUQuota, c.CPUPeriod = 200000, 100000
	}
	if c.PidsMax == 0 {
		c.PidsMax = 4096
	}
	if c.RelayBasePort == 0 {
		c.RelayBasePort = 8781
	}
	if c.GraderTimeoutSeconds == 0 {
		c.GraderTimeoutSeconds = 600
	}
	return &c, nil
}

func cmdPreflight(args []string) {
	fs := newFlagSet("preflight")
	cfgPath := fs.String("config", "config/experiment.json", "experiment config")
	fs.Parse(args)
	cfg, err := loadConfig(*cfgPath)
	if err != nil {
		fmt.Println("PREFLIGHT FAIL:", err)
		os.Exit(1)
	}
	ok := true
	check := func(name string, cond bool, detail string) {
		status := "PASS"
		if !cond {
			status = "FAIL"
			ok = false
		}
		fmt.Printf("%-28s %s  %s\n", name, status, detail)
	}
	warn := func(name string, cond bool, detail string) {
		status := "PASS"
		if !cond {
			status = "WARN"
		}
		fmt.Printf("%-28s %s  %s\n", name, status, detail)
	}
	host := captureHostInfo()
	check("host.cpu", host.LogicalCPUs >= 3, fmt.Sprintf("%d logical CPUs, %s", host.LogicalCPUs, host.CPUModel))
	check("host.memory", host.MemTotalBytes > 8*1024*1024*1024, fmt.Sprintf("%.1f GiB total", float64(host.MemTotalBytes)/(1<<30)))
	check("host.kernel", strings.Contains(host.Kernel, "6."), host.Kernel)
	supported, detail := CgroupSupported()
	check("cgroup.v2.delegation", supported, detail)
	key, src, kerr := loadAPIKey("")
	check("credential.available", kerr == nil, fmt.Sprintf("source=%s fingerprint=%s", src, keyFingerprint(key)))
	primary := fileExists(os.Getenv("HOME") + "/opencode.txt")
	primaryDetail := "~/opencode.txt present and used"
	if !primary {
		primaryDetail = "~/opencode.txt absent; verified fallback credential in use: " + src
	}
	warn("credential.primary_file", primary, primaryDetail)
	for _, a := range cfg.Agents {
		_, err := exec.LookPath(a.Binary)
		check("agent."+a.ID, err == nil, a.Binary)
	}
	free := freeDiskBytes(cfg.ResultsRoot)
	check("disk.free", free > 2*1024*1024*1024, fmt.Sprintf("%.1f GiB free", float64(free)/(1<<30)))
	for _, r := range cfg.Rounds {
		check("task."+r.TaskID+".repo", dirExists(r.TaskRepo), r.TaskRepo)
		check("task."+r.TaskID+".grader", fileExists(r.GraderScript), r.GraderScript)
	}
	// live API preflight through the relay (one tiny request, excluded from scores)
	if kerr == nil {
		ctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
		defer cancel()
		addr := fmt.Sprintf("0.0.0.0:%d", cfg.RelayBasePort+99)
		relay, err := NewRelay(cfg.ExperimentID, "preflight", "preflight", "preflight", "preflight", key, filepath.Join(cfg.ResultsRoot, "preflight_api.jsonl"))
		if err != nil {
			check("api.preflight", false, err.Error())
		} else {
			_, actual, err := StartRelay(ctx, relay, addr)
			if err != nil {
				check("api.preflight", false, err.Error())
			} else {
				okReq, detail := relaySelfTest(actual, cfg.Model)
				check("api.preflight", okReq, detail)
			}
		}
	}
	fmt.Println()
	if !ok {
		fmt.Println("PREFLIGHT: BLOCKED")
		os.Exit(1)
	}
	fmt.Println("PREFLIGHT: READY")
}

func fileExists(p string) bool { _, err := os.Stat(p); return err == nil }
func dirExists(p string) bool  { st, err := os.Stat(p); return err == nil && st.IsDir() }

func freeDiskBytes(path string) int64 {
	var st syscall.Statfs_t
	if err := syscall.Statfs(pathOrCwd(path), &st); err != nil {
		return -1
	}
	return int64(st.Bavail) * int64(st.Bsize)
}

func pathOrCwd(p string) string {
	if p == "" {
		return "."
	}
	if err := os.MkdirAll(p, 0o750); err != nil {
		return "."
	}
	return p
}

func relaySelfTest(addr, model string) (bool, string) {
	body := fmt.Sprintf(`{"model":%q,"messages":[{"role":"user","content":"Reply with exactly: OK"}],"max_tokens":8,"reasoning_effort":"low","stream":false}`, model)
	out, err := execCommand("curl", "-sS", "-m", "90", "-X", "POST",
		"http://"+addr+"/v1/chat/completions", "-H", "Content-Type: application/json", "-d", body)
	if err != nil {
		return false, "curl failed: " + err.Error()
	}
	var resp struct {
		Model   string `json:"model"`
		Choices []struct {
			FinishReason string `json:"finish_reason"`
		} `json:"choices"`
		Usage map[string]any `json:"usage"`
	}
	if err := json.Unmarshal([]byte(out), &resp); err != nil {
		return false, "unparseable preflight response: " + truncate(out, 200)
	}
	if resp.Model != model {
		return false, "model mismatch: requested " + model + " got " + resp.Model
	}
	if len(resp.Choices) == 0 {
		return false, "no choices in preflight response"
	}
	return true, fmt.Sprintf("model_returned=%s usage_present=%v", resp.Model, len(resp.Usage) > 0)
}

func cmdRun(args []string) {
	fs := newFlagSet("run")
	cfgPath := fs.String("config", "config/experiment.json", "experiment config")
	runID := fs.String("run-id", "", "run identifier (default: utc timestamp)")
	dry := fs.Bool("dry", false, "validated dry-run: fake agents, no API spend")
	fs.Parse(args)
	cfg, err := loadConfig(*cfgPath)
	if err != nil {
		fatal("config: %v", err)
	}
	if *runID == "" {
		*runID = time.Now().UTC().Format("20060102T150405Z")
	}
	if *dry {
		*runID = *runID + "-dry"
	}
	key, keySrc, kerr := loadAPIKey("")
	if kerr != nil {
		fatal("credential: %v", kerr)
	}
	c := &Controller{
		cfg: cfg, runID: *runID, key: key, keySrc: keySrc, dryRun: *dry,
		active: map[*TrialRuntime]bool{},
	}
	if err := c.Run(); err != nil {
		fatal("run failed: %v", err)
	}
}

func fatal(format string, a ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", a...)
	os.Exit(1)
}

var secretPatterns = []*regexp.Regexp{
	regexp.MustCompile(`(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}`),
	regexp.MustCompile(`(?i)(sk-[A-Za-z0-9._\-]{8,})`),
	regexp.MustCompile(`(?i)(api[_-]?key[=:]\s*)[A-Za-z0-9._\-]{8,}`),
}

func redactSecrets(s string) string {
	for _, re := range secretPatterns {
		s = re.ReplaceAllString(s, "${1}<REDACTED>")
	}
	return s
}

// ------------------------------------------------------------------ controller

type Controller struct {
	activeMu  sync.Mutex
	active    map[*TrialRuntime]bool
	cfg       *ExperimentConfig
	runID     string
	key       string
	keySrc    string
	dryRun    bool
	resDir    string
	events    *EventLog
	mu        sync.Mutex
	summaries []*RunSummary
}

func (c *Controller) Run() error {
	c.resDir = filepath.Join(c.cfg.ResultsRoot, c.runID)
	for _, d := range []string{
		c.resDir, filepath.Join(c.resDir, "telemetry"), filepath.Join(c.resDir, "runs"),
		filepath.Join(c.resDir, "graders"), filepath.Join(c.cfg.WorkspacesRoot, c.runID),
	} {
		if err := os.MkdirAll(d, 0o750); err != nil {
			return err
		}
	}
	evLog, err := OpenEventLog(c.cfg.ExperimentID, c.runID, filepath.Join(c.resDir, "telemetry", "events.jsonl"))
	if err != nil {
		return err
	}
	c.events = evLog
	host := captureHostInfo()
	_ = writeJSON(filepath.Join(c.resDir, "telemetry", "environment.json"), map[string]any{
		"schema_version": 1, "host": host, "experiment": c.cfg, "run_id": c.runID,
		"relay_base_url":    "http://0.0.0.0:" + strconv.Itoa(c.cfg.RelayBasePort),
		"credential_source": c.keySrc, "credential_fingerprint": keyFingerprint(c.key),
		"key_redaction": "credentials are held by the relay only; agents receive a dummy token",
		"dry_run":       c.dryRun,
		"captured_utc":  time.Now().UTC().Format(time.RFC3339Nano),
	})

	globalStart := time.Now()
	watchdog := time.AfterFunc(time.Duration(c.cfg.GlobalDeadlineSeconds)*time.Second, func() {
		evLog.Emit("", "", "", "controller", "GLOBAL_WATCHDOG_FIRED",
			map[string]any{"wall_seconds": time.Since(globalStart).Seconds()})
		c.activeMu.Lock()
		for t := range c.active {
			evLog.Emit(t.Round.RoundID, t.Round.TaskID, t.Agent.ID, "controller", "GLOBAL_WATCHDOG_KILL", nil)
			if t.CG != nil {
				killScope(t.CG.Unit, t.CG.CGPath)
			}
		}
		c.activeMu.Unlock()
	})
	defer watchdog.Stop()

	var runErr error
	for _, round := range c.cfg.Rounds {
		if time.Since(globalStart) > time.Duration(c.cfg.GlobalDeadlineSeconds-60)*time.Second {
			c.events.Emit(round.RoundID, round.TaskID, "", "controller", "ROUND_SKIPPED_GLOBAL_DEADLINE", nil)
			runErr = errors.New("global deadline reached before round " + round.RoundID)
			break
		}
		if err := c.runRound(round); err != nil {
			c.events.Emit(round.RoundID, round.TaskID, "", "controller", "ROUND_ERROR",
				map[string]any{"error": err.Error()})
			runErr = err
		}
	}

	_ = writeJSON(filepath.Join(c.resDir, "summary_raw.json"), map[string]any{
		"run_id": c.runID, "experiment_id": c.cfg.ExperimentID,
		"started_utc":  globalStart.UTC().Format(time.RFC3339Nano),
		"finished_utc": time.Now().UTC().Format(time.RFC3339Nano),
		"wall_seconds": time.Since(globalStart).Seconds(),
		"config":       c.cfg, "host": host, "runs": c.summaries,
		"credential_source": c.keySrc,
	})
	if err := c.seal(); err != nil {
		return err
	}
	return runErr
}

func (c *Controller) runRound(round RoundConfig) error {
	start := time.Now()
	c.events.Emit(round.RoundID, round.TaskID, "", "controller", "ROUND_STARTED",
		map[string]any{"duration_s": round.DurationSeconds,
			"cpu_groups": c.cfg.CPUGroups, "agent_count": len(c.cfg.Agents)})

	type prepared struct {
		t *TrialRuntime
	}
	trials := make([]*TrialRuntime, 0, len(c.cfg.Agents))
	var prepErr error

	for i, agent := range c.cfg.Agents {
		trialDir := filepath.Join(c.cfg.WorkspacesRoot, c.runID, round.RoundID, agent.ID)
		ws := filepath.Join(trialDir, "workspace")
		home := filepath.Join(trialDir, "home")
		if err := os.MkdirAll(trialDir, 0o700); err != nil {
			prepErr = err
			break
		}
		if err := copyTreeRR(round.TaskRepo, ws); err != nil {
			prepErr = fmt.Errorf("workspace copy: %w", err)
			break
		}
		// each workspace starts from the pinned source commit
		_, _ = runGit(ws, "checkout", "-q", "HEAD")
		startSHA, _ := runGit(ws, "rev-parse", "HEAD")
		// strip any origin remote so agents cannot fetch reference material
		_, _ = runGit(ws, "remote", "remove", "origin")

		port := c.cfg.RelayBasePort + i
		relayAddr := fmt.Sprintf("0.0.0.0:%d", port)
		if err := prepareHome(trialDir, agent.ID, home, relayAddr); err != nil {
			prepErr = err
			break
		}
		cpu := c.cfg.CPUGroups[(i+round.CPUGroupOffset)%len(c.cfg.CPUGroups)]
		relay, err := NewRelay(c.cfg.ExperimentID, c.runID, round.RoundID, agent.ID, round.TaskID,
			c.key, filepath.Join(c.resDir, "telemetry", "api_usage.jsonl"))
		if err != nil {
			prepErr = err
			break
		}
		runDir := filepath.Join(c.resDir, "runs", round.TaskID, agent.ID)
		if err := os.MkdirAll(runDir, 0o750); err != nil {
			prepErr = err
			break
		}
		tr := &TrialRuntime{
			Round: round, Agent: agent, Workspace: ws, Home: home, Relay: relay,
			RelayAddr: relayAddr, CPUs: cpu,
			StdoutPath: filepath.Join(runDir, "stdout.log"),
			StderrPath: filepath.Join(runDir, "stderr.log"),
			StartSHA:   strings.TrimSpace(startSHA),
		}
		trials = append(trials, tr)
		c.activeMu.Lock()
		c.active[tr] = true
		c.activeMu.Unlock()
		c.events.Emit(round.RoundID, round.TaskID, agent.ID, "controller", "WORKSPACE_READY",
			map[string]any{"workspace": ws, "cpu_affinity": cpu, "relay": relayAddr,
				"memory_max_bytes": c.cfg.MemoryMaxBytes, "cpu_max": fmt.Sprintf("%d/%d", c.cfg.CPUQuota, c.cfg.CPUPeriod)})
	}
	if prepErr != nil {
		for _, t := range trials {
			if t.CG != nil {
				killScope(t.CG.Unit, t.CG.CGPath)
			}
		}
		return prepErr
	}

	// Launch all relays, then all agent processes behind a single barrier.
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	for _, t := range trials {
		if _, _, err := StartRelay(ctx, t.Relay, t.RelayAddr); err != nil {
			return fmt.Errorf("relay %s: %w", t.Agent.ID, err)
		}
	}
	c.events.Emit(round.RoundID, round.TaskID, "", "controller", "BARRIER_READY",
		map[string]any{"agents": len(trials)})

	roundDeadline := time.Duration(round.DurationSeconds) * time.Second
	var wg sync.WaitGroup
	release := make(chan struct{})
	for _, t := range trials {
		wg.Add(1)
		go func(t *TrialRuntime) {
			defer wg.Done()
			<-release
			t.BarrierMono = time.Now()
			c.launchTrial(t, roundDeadline)
		}(t)
	}
	// sampler
	sampleStop := make(chan struct{})
	sampleDone := make(chan struct{})
	go func() {
		defer close(sampleDone)
		c.sampleLoop(trials, sampleStop)
	}()
	time.Sleep(25 * time.Millisecond)
	close(release)
	c.events.Emit(round.RoundID, round.TaskID, "", "controller", "BARRIER_RELEASED",
		map[string]any{"monotonic_ns": time.Since(processStart).Nanoseconds()})
	wg.Wait()
	close(sampleStop)
	<-sampleDone

	// Force-stop any stragglers in the scopes (defensive).
	for _, t := range trials {
		if t.CG == nil {
			continue
		}
		remaining := readScopeProcs(t.CG.CGPath)
		if len(remaining) > 0 {
			c.events.Emit(round.RoundID, round.TaskID, t.Agent.ID, "controller", "CGROUP_CLEANUP",
				map[string]any{"remaining_pids": remaining})
		}
		stopScope(t.CG.Unit)
	}

	// Freeze snapshots and grade.
	for _, t := range trials {
		c.finalizeTrial(t)
	}
	for _, t := range trials {
		c.gradeTrial(t)
	}

	c.activeMu.Lock()
	for t := range c.active {
		if t.Round.RoundID == round.RoundID {
			delete(c.active, t)
		}
	}
	c.activeMu.Unlock()
	c.events.Emit(round.RoundID, round.TaskID, "", "controller", "ROUND_FINISHED",
		map[string]any{"wall_seconds": time.Since(start).Seconds()})
	c.mu.Lock()
	for _, t := range trials {
		s := t.Summary
		c.summaries = append(c.summaries, &s)
	}
	c.mu.Unlock()
	return nil
}

func (c *Controller) launchTrial(t *TrialRuntime, deadline time.Duration) {
	runDir := filepath.Dir(t.StdoutPath)
	_ = os.MkdirAll(runDir, 0o750)
	stdout, err := os.OpenFile(t.StdoutPath, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o640)
	if err != nil {
		t.Err = err
		return
	}
	defer stdout.Close()
	stderr, err := os.OpenFile(t.StderrPath, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o640)
	if err != nil {
		t.Err = err
		return
	}
	defer stderr.Close()

	prompt := agentPrompt(t.Round.TaskID, t.Agent.ID)
	argv, env, err := buildCommand(t.Agent.ID, prompt, t.Workspace, t.RelayAddr, t.Home)
	if err != nil {
		t.Err = err
		return
	}
	if c.dryRun {
		argv, env, err = dryCommand(t, c.dryRun)
		if err != nil {
			t.Err = err
			return
		}
	}
	// Each trial runs inside a systemd user scope with cgroup-enforced CPU,
	// memory and task limits. The wrapper records post-exit counters from its
	// own cgroup before the scope disappears.
	unit := sanitizeUnit(fmt.Sprintf("bench-%s-%s-%s", c.runID, t.Round.RoundID, t.Agent.ID))
	finalPath := filepath.Join(runDir, "cgroup_final.txt")
	wrapper := filepath.Join(runDir, "launch.sh")
	script := fmt.Sprintf(`#!/bin/bash
set +e
CG=/sys/fs/cgroup$(awk -F: '$1=="0"{print $3}' /proc/self/cgroup)
START=$(date +%%s%%N)
taskset -c %s "$@"
rc=$?
END=$(date +%%s%%N)
OUT=%q
{
  echo "start_ns=$START"
  echo "end_ns=$END"
  echo "exit_code=$rc"
  echo "cgroup=$CG"
  echo "---cpu.stat"; cat "$CG/cpu.stat" 2>/dev/null
  echo "---memory.peak"; cat "$CG/memory.peak" 2>/dev/null
  echo "---memory.events"; cat "$CG/memory.events" 2>/dev/null
  echo "---pids.events"; cat "$CG/pids.events" 2>/dev/null
} > "$OUT"
exit $rc
`, t.CPUs, finalPath)
	if err := os.WriteFile(wrapper, []byte(script), 0o750); err != nil {
		t.Err = err
		return
	}
	agentExec, err := exec.LookPath(argv[0])
	if err != nil {
		agentExec = argv[0]
	}
	args := append([]string{agentExec}, argv[1:]...)
	cmd := buildScopeCommand(unit, t.CPUs, c.cfg.MemoryMaxBytes, c.cfg.CPUQuota,
		c.cfg.CPUPeriod, c.cfg.PidsMax, wrapper, args, t.Workspace)
	cmd.Env = append(append([]string{}, env...),
		"XDG_RUNTIME_DIR="+xdgRuntimeDir(),
		"DBUS_SESSION_BUS_ADDRESS=unix:path="+filepath.Join(xdgRuntimeDir(), "bus"))
	cmd.Stdout = stdout
	cmd.Stderr = stderr
	cmd.WaitDelay = 5 * time.Second

	t.StartUTC = time.Now().UTC()
	if err := cmd.Start(); err != nil {
		t.Err = err
		t.ExitCode = -1
		return
	}
	t.ExecMono = time.Now()
	scope := &Scope{Unit: unit, FinalPath: finalPath}
	if cgPath, err := scopeCgroupPath(unit); err == nil {
		scope.CGPath = cgPath
	} else {
		t.Notes = append(t.Notes, "cgroup path discovery failed: "+err.Error())
	}
	t.CG = scope
	c.events.Emit(t.Round.RoundID, t.Round.TaskID, t.Agent.ID, "controller", "PROCESS_EXEC_START",
		map[string]any{"pid": cmd.Process.Pid, "unit": unit, "cgroup": scope.CGPath,
			"launch_delay_ms": msBetween(t.BarrierMono, t.ExecMono)})

	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()

	var waitErr error
	select {
	case waitErr = <-done:
	case <-time.After(deadline):
		t.TimedOut = true
		c.events.Emit(t.Round.RoundID, t.Round.TaskID, t.Agent.ID, "controller", "RUN_TIMEOUT",
			map[string]any{"deadline_s": deadline.Seconds()})
		killScope(unit, scope.CGPath)
		waitErr = <-done
	}
	if t.CG == nil {
		t.Notes = append(t.Notes, "scope unavailable")
	}
	t.ExitMono = time.Now()
	t.EndUTC = time.Now().UTC()
	if waitErr != nil {
		if ee, ok := waitErr.(*exec.ExitError); ok {
			t.ExitCode = ee.ExitCode()
		} else {
			t.ExitCode = -1
		}
	} else {
		t.ExitCode = 0
	}
	c.events.Emit(t.Round.RoundID, t.Round.TaskID, t.Agent.ID, "controller", "AGENT_EXIT",
		map[string]any{"exit_code": t.ExitCode, "timed_out": t.TimedOut,
			"elapsed_ms": msBetween(t.BarrierMono, t.ExitMono)})
}

func (c *Controller) sampleLoop(trials []*TrialRuntime, stop <-chan struct{}) {
	samplePath := filepath.Join(c.resDir, "telemetry", "resource_samples.jsonl")
	procPath := filepath.Join(c.resDir, "telemetry", "process_tree.jsonl")
	sf, err := os.OpenFile(samplePath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o640)
	if err != nil {
		return
	}
	defer sf.Close()
	pf, err := os.OpenFile(procPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o640)
	if err != nil {
		return
	}
	defer pf.Close()
	ticker := time.NewTicker(1 * time.Second)
	defer ticker.Stop()
	var seq int64
	for {
		select {
		case <-stop:
			return
		case <-ticker.C:
			seq++
			for _, t := range trials {
				cgPath := ""
				if t.CG != nil {
					cgPath = t.CG.CGPath
				}
				if cgPath == "" {
					continue
				}
				s := (&Cgroup{Path: cgPath}).sample(seq, c.runID, t.Round.RoundID, t.Round.TaskID, t.Agent.ID)
				b, _ := json.Marshal(s)
				sf.Write(append(b, '\n'))
				if seq%3 == 0 {
					ps := sampleProcessTree(&Cgroup{Path: cgPath}, seq, c.runID, t.Round.RoundID, t.Round.TaskID, t.Agent.ID)
					for i := range ps {
						ps[i].Cmdline = redactSecrets(ps[i].Cmdline)
						pb, _ := json.Marshal(ps[i])
						pf.Write(append(pb, '\n'))
					}
				}
			}
		}
	}
}

func (c *Controller) finalizeTrial(t *TrialRuntime) {
	runDir := filepath.Dir(t.StdoutPath)
	frozen := filepath.Join(runDir, "workspace_frozen")
	snap, err := freezeWorkspace(t.Workspace, frozen, t.StartSHA, t.Round.TaskID, t.Agent.ID)
	if err != nil {
		c.events.Emit(t.Round.RoundID, t.Round.TaskID, t.Agent.ID, "controller", "SNAPSHOT_ERROR",
			map[string]any{"error": err.Error()})
	}
	final := map[string]int64{}
	if t.CG != nil {
		final = parseScopeFinal(t.CG.FinalPath)
	}
	if v, ok := final["cpu.stat.usage_usec"]; !ok || v == 0 {
		if t.CG != nil && t.CG.CGPath != "" {
			live := (&Cgroup{Path: t.CG.CGPath}).finalCounters()
			if x, ok := live["cpu_usage_usec"].(int64); ok {
				final["cpu.stat.usage_usec"] = x
			}
			if x, ok := live["cpu_user_usec"].(int64); ok {
				final["cpu.stat.user_usec"] = x
			}
			if x, ok := live["cpu_system_usec"].(int64); ok {
				final["cpu.stat.system_usec"] = x
			}
			if x, ok := live["throttled_usec"].(int64); ok {
				final["cpu.stat.throttled_usec"] = x
			}
			if x, ok := live["memory_peak_bytes"].(int64); ok {
				final["memory.peak"] = x
			}
			if x, ok := live["oom_events"].(int64); ok {
				final["memory.events.oom_kill"] = x
			}
		}
	}
	startSHA := t.StartSHA
	sum := RunSummary{
		TaskID: t.Round.TaskID, AgentID: t.Agent.ID, RunID: c.runID, RoundID: t.Round.RoundID,
		ExitCode: t.ExitCode, Snapshot: snap,
		CPUSeconds:       float64(final["cpu.stat.usage_usec"]) / 1e6,
		CPUUserSeconds:   float64(final["cpu.stat.user_usec"]) / 1e6,
		CPUSystemSeconds: float64(final["cpu.stat.system_usec"]) / 1e6,
		ThrottledSeconds: float64(final["cpu.stat.throttled_usec"]) / 1e6,
		MemPeakBytes:     final["memory.peak"],
		OOMEvents:        final["memory.events.oom_kill"],
		HostCPU:          t.CPUs, RelayAddr: t.RelayAddr,
		StdoutPath: t.StdoutPath, StderrPath: t.StderrPath,
		StartUTC: t.StartUTC.Format(time.RFC3339Nano), EndUTC: t.EndUTC.Format(time.RFC3339Nano),
		Notes: t.Notes,
	}
	sum.HostCPU = t.CPUs
	if path, err := exec.LookPath(t.Agent.Binary); err == nil {
		if resolved, err2 := filepath.EvalSymlinks(path); err2 == nil {
			path = resolved
		}
		sum.AgentBinarySHA256 = sha256File(path)
		if out, err2 := execCommand(path, "--version"); err2 == nil {
			sum.AgentVersion = strings.TrimSpace(strings.SplitN(out, "\n", 2)[0])
		} else if out, err2 := execCommand(path, "version"); err2 == nil {
			sum.AgentVersion = strings.TrimSpace(strings.SplitN(out, "\n", 2)[0])
		}
	}
	switch {
	case t.Err != nil:
		sum.Status, sum.ExitReason = "crashed", "LAUNCH_ERROR"
	case t.TimedOut:
		sum.Status, sum.ExitReason = "timeout", "TASK_DEADLINE_EXCEEDED"
	case t.ExitCode == 0:
		sum.Status, sum.ExitReason = "completed", "AGENT_EXITED_OK"
	default:
		sum.Status, sum.ExitReason = "crashed", "AGENT_EXITED_NONZERO"
	}
	sum.ElapsedSeconds = msBetween(t.BarrierMono, t.ExitMono) / 1000.0
	sum.LaunchDelayMS = msBetween(t.BarrierMono, t.ExecMono)
	// Relay-observed timings and token totals.
	st := t.Relay.Stats()
	sum.Requests = int(st["requests"].(int64))
	sum.APIErrors = int(st["errors"].(int64))
	sum.ThrottleEvents = int(st["throttles"].(int64))
	sum.APIWallSeconds = float64(st["api_wall_us"].(int64)) / 1e6
	barrierOffset := t.BarrierMono.Sub(processStart).Nanoseconds()
	if fm, ok := st["first_request_mono_ns"].(int64); ok && fm > 0 {
		sum.StartupMS = float64(fm-barrierOffset) / 1e6
	}
	if ft, ok := st["first_token_mono_ns"].(int64); ok && ft > 0 {
		sum.TTFTMS = float64(ft-barrierOffset) / 1e6
	}
	sum.Tokens = map[string]any{
		"input": st["input_tokens"], "output": st["output_tokens"],
		"total": st["total_tokens"], "reasoning": st["reasoning_tokens"],
		"cached_read": st["cached_read_tokens"], "cached_write": st["cached_write_tokens"],
		"cost_usd": st["cost_usd"], "list_cost_usd": st["list_cost_usd"],
		"allowance_usd": st["allowance_usd"],
		"provenance":    "PROVIDER_REPORTED via relay", "requests_without_usage": st["requests_without_usage"],
		"reported_models": st["models"], "effort_rewrites": st["effort_rewrites"],
	}
	sum.StartSHA = startSHA
	t.Summary = sum
}

func (c *Controller) gradeTrial(t *TrialRuntime) {
	runDir := filepath.Dir(t.StdoutPath)
	frozen := filepath.Join(runDir, "workspace_frozen")
	out := filepath.Join(runDir, "hidden_grader.json")
	if t.Summary.Snapshot == nil {
		t.Summary.Notes = append(t.Summary.Notes, "no snapshot; grading skipped (NOT_EVALUATED)")
		t.Summary.Grade = map[string]any{"status": "NOT_EVALUATED", "reason": "snapshot unavailable"}
		return
	}
	c.events.Emit(t.Round.RoundID, t.Round.TaskID, t.Agent.ID, "grader", "GRADER_STARTED",
		map[string]any{"grader": t.Round.GraderScript, "workspace": frozen})
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(c.cfg.GraderTimeoutSeconds)*time.Second)
	defer cancel()
	start := time.Now()
	cmd := exec.CommandContext(ctx, "python3", t.Round.GraderScript, "--workspace", frozen, "--json-out", out)
	cmd.Dir = filepath.Dir(t.Round.GraderScript)
	_, _ = cmd.Output()
	t.Summary.GraderWallSeconds = time.Since(start).Seconds()
	gradePath := out
	if b, err := os.ReadFile(gradePath); err == nil {
		var g map[string]any
		if json.Unmarshal(b, &g) == nil {
			t.Summary.Grade = g
		}
	}
	if t.Summary.Grade == nil {
		t.Summary.Grade = map[string]any{"status": "GRADER_FAILURE", "reason": "grader produced no JSON"}
	}
	_ = writeJSON(filepath.Join(runDir, "run_manifest.json"), t.Summary)
	_ = writeJSON(filepath.Join(runDir, "resource_summary.json"), map[string]any{
		"cpu_seconds": t.Summary.CPUSeconds, "cpu_user_seconds": t.Summary.CPUUserSeconds,
		"cpu_system_seconds": t.Summary.CPUSystemSeconds, "throttled_seconds": t.Summary.ThrottledSeconds,
		"peak_memory_bytes": t.Summary.MemPeakBytes, "oom_events": t.Summary.OOMEvents,
		"cpu_affinity": t.CPUs, "cgroup": func() string {
			if t.CG != nil {
				return t.CG.CGPath
			}
			return ""
		}(),
	})
	c.events.Emit(t.Round.RoundID, t.Round.TaskID, t.Agent.ID, "grader", "GRADER_FINISHED",
		map[string]any{"status": t.Summary.Grade["status"], "total": t.Summary.Grade["total"]})
}

func (c *Controller) seal() error {
	// Hash every artifact in the run directory (excluding SHA256SUMS itself).
	var files []string
	filepath.Walk(c.resDir, func(path string, info os.FileInfo, err error) error {
		if err != nil || info == nil || info.IsDir() {
			return nil
		}
		if filepath.Base(path) == "SHA256SUMS" {
			return nil
		}
		files = append(files, path)
		return nil
	})
	var lines []string
	for _, p := range files {
		rel, _ := filepath.Rel(c.resDir, p)
		lines = append(lines, sha256File(p)+"  "+rel)
	}
	return os.WriteFile(filepath.Join(c.resDir, "SHA256SUMS"), []byte(strings.Join(lines, "\n")+"\n"), 0o640)
}

func msBetween(a, b time.Time) float64 {
	if a.IsZero() || b.IsZero() {
		return -1
	}
	return float64(b.Sub(a).Microseconds()) / 1000.0
}

// small helpers used by dry-run mode
func copyTreeRR(src, dst string) error {
	if err := os.RemoveAll(dst); err != nil {
		return err
	}
	if err := os.MkdirAll(dst, 0o755); err != nil {
		return err
	}
	out, err := execCommand("cp", "-a", src+"/.", dst)
	if err != nil {
		return fmt.Errorf("cp %s -> %s: %v: %s", src, dst, err, strings.TrimSpace(out))
	}
	return nil
}

func dryCommand(t *TrialRuntime, _ bool) ([]string, []string, error) {
	// Fake-agent driver used only for controller validation; it never touches
	// the network and is marked DRY_RUN in every artifact.
	script := os.Getenv("BENCH_FAKE_AGENT")
	if script == "" {
		script = "/home/researcher/agent-benchmark/tests/fake_agent.py"
	}
	return []string{"python3", script, t.Agent.ID, t.Round.TaskID, t.Workspace}, []string{
		"PATH=" + os.Getenv("PATH"), "HOME=" + t.Home, "TMPDIR=" + filepath.Join(t.Home, "tmp"),
		"HAX_OPENAI_BASE_URL=http://" + t.RelayAddr + "/v1",
		"HAX_OPENAI_API_KEY=" + relayDummyKey,
	}, nil
}

func sanitizeUnit(s string) string {
	var b strings.Builder
	for _, r := range s {
		if (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') || r == '-' || r == '_' || r == '.' {
			b.WriteRune(r)
		} else {
			b.WriteByte('-')
		}
	}
	out := b.String()
	if len(out) > 200 {
		out = out[:200]
	}
	return out
}

func xdgRuntimeDir() string {
	if v := os.Getenv("XDG_RUNTIME_DIR"); v != "" {
		return v
	}
	return fmt.Sprintf("/run/user/%d", os.Getuid())
}
