package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// Cgroup v2 helpers. The benchmark runs inside a delegated user scope whose
// cgroup.controllers exposes cpu, memory and pids. No privileges are assumed.
type Cgroup struct {
	Path string
}

func findOwnCgroup() (string, error) {
	b, err := os.ReadFile("/proc/self/cgroup")
	if err != nil {
		return "", err
	}
	for _, line := range strings.Split(string(b), "\n") {
		if strings.HasPrefix(line, "0::") {
			return strings.TrimPrefix(line, "0::"), nil
		}
	}
	return "", fmt.Errorf("no cgroup v2 entry in /proc/self/cgroup")
}

func cgroupRoot(rel string) string {
	return filepath.Join("/sys/fs/cgroup", rel)
}

func cgroupControllers(path string) (string, error) {
	b, err := os.ReadFile(filepath.Join(path, "cgroup.controllers"))
	return strings.TrimSpace(string(b)), err
}

func CgroupSupported() (bool, string) {
	own, err := findOwnCgroup()
	if err != nil {
		return false, err.Error()
	}
	base := cgroupRoot(own)
	ctrl, err := cgroupControllers(base)
	if err != nil {
		return false, "cannot read cgroup.controllers: " + err.Error()
	}
	for _, need := range []string{"cpu", "memory", "pids"} {
		if !strings.Contains(ctrl, need) {
			return false, "missing controller " + need + " in [" + ctrl + "]"
		}
	}
	probe := filepath.Join(base, fmt.Sprintf(".bench-probe-%d", os.Getpid()))
	if err := os.Mkdir(probe, 0o755); err != nil {
		return false, "cannot create sub-cgroup: " + err.Error()
	}
	_ = os.Remove(probe)
	return true, "cpu,memory,pids delegated at " + base
}

// Scope is a systemd --user transient scope with delegated cgroup limits.
// It is created with `systemd-run --user --scope`, which needs no privileges.
type Scope struct {
	Unit      string
	CGPath    string
	FinalPath string
}

func scopeCgroupPath(unit string) (string, error) {
	// Fast path: transient --scope units land in app.slice deterministically.
	guess := filepath.Join("<cgroup>", unit+".scope")
	if uid := os.Getuid(); uid != 1000 {
		guess = filepath.Join(fmt.Sprintf("<cgroup>", uid),
			fmt.Sprintf("user@%d.service/app.slice", uid), unit+".scope")
	}
	for i := 0; i < 60; i++ {
		if st, err := os.Stat(guess); err == nil && st.IsDir() {
			return guess, nil
		}
		if out, err := execCommand("systemctl", "--user", "show", "-p", "ControlGroup", "--value", unit); err == nil {
			p := strings.TrimSpace(out)
			if strings.HasPrefix(p, "/") {
				return filepath.Join("/sys/fs/cgroup", p), nil
			}
		}
		time.Sleep(50 * time.Millisecond)
	}
	return "", fmt.Errorf("scope %s did not appear", unit)
}

func buildScopeCommand(unit, cpus string, memMax, quota, period, pidsMax int64, wrapper string, agentArgs []string, dir string) *exec.Cmd {
	quotaPct := fmt.Sprintf("%d%%", (quota*100)/period)
	cmd := exec.Command("systemd-run", "--user", "--scope", "--unit="+unit,
		"-p", fmt.Sprintf("MemoryMax=%d", memMax),
		"-p", fmt.Sprintf("CPUQuota=%s", quotaPct),
		"-p", fmt.Sprintf("TasksMax=%d", pidsMax),
		"--expand-environment=no", "--", "/bin/bash", wrapper)
	cmd.Args = append(cmd.Args, agentArgs...)
	cmd.Dir = dir
	return cmd
}

func stopScope(unit string) {
	_, _ = execCommand("systemctl", "--user", "stop", unit)
	_, _ = execCommand("systemctl", "--user", "reset-failed", unit)
}

func readScopeProcs(cgPath string) []int {
	return (&Cgroup{Path: cgPath}).procs()
}

func killScope(unit, cgPath string) {
	if cgPath != "" {
		(&Cgroup{Path: cgPath}).killAll("TERM")
	}
	_, _ = execCommand("systemctl", "--user", "kill", "--kill-whom=all", unit)
	time.Sleep(8 * time.Second)
	stopScope(unit)
	if cgPath != "" {
		(&Cgroup{Path: cgPath}).killAll("KILL")
	}
}

func parseScopeFinal(path string) map[string]int64 {
	out := map[string]int64{}
	b, err := os.ReadFile(path)
	if err != nil {
		return out
	}
	section := ""
	for _, line := range strings.Split(string(b), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		if strings.HasPrefix(line, "---") {
			section = strings.TrimPrefix(line, "---")
			continue
		}
		if k, v, ok := strings.Cut(line, "="); ok && section == "" {
			if n, err := strconv.ParseInt(v, 10, 64); err == nil {
				out[k] = n
			}
			continue
		}
		fields := strings.Fields(line)
		if section != "" && len(fields) == 1 {
			if n, err := strconv.ParseInt(fields[0], 10, 64); err == nil {
				out[section] = n
				continue
			}
		}
		if len(fields) == 2 && section != "" {
			if n, err := strconv.ParseInt(fields[1], 10, 64); err == nil {
				out[section+"."+fields[0]] = n
			}
		}
	}
	return out
}

func (c *Cgroup) addPID(pid int) error {
	return os.WriteFile(filepath.Join(c.Path, "cgroup.procs"), []byte(strconv.Itoa(pid)), 0o644)
}

func (c *Cgroup) procs() []int {
	b, err := os.ReadFile(filepath.Join(c.Path, "cgroup.procs"))
	if err != nil {
		return nil
	}
	var out []int
	for _, f := range strings.Fields(string(b)) {
		if pid, err := strconv.Atoi(f); err == nil {
			out = append(out, pid)
		}
	}
	return out
}

func (c *Cgroup) killAll(signal string) {
	for _, pid := range c.procs() {
		_, _ = execCommand("kill", "-"+signal, strconv.Itoa(pid))
	}
}

func readInt(path string) int64 {
	b, err := os.ReadFile(path)
	if err != nil {
		return -1
	}
	v, err := strconv.ParseInt(strings.TrimSpace(string(b)), 10, 64)
	if err != nil {
		return -1
	}
	return v
}

func readKV(path string) map[string]int64 {
	out := map[string]int64{}
	b, err := os.ReadFile(path)
	if err != nil {
		return out
	}
	for _, line := range strings.Split(string(b), "\n") {
		fields := strings.Fields(line)
		if len(fields) != 2 {
			continue
		}
		v, err := strconv.ParseInt(fields[1], 10, 64)
		if err == nil {
			out[fields[0]] = v
		}
	}
	return out
}

func readPressure(path string) map[string]int64 {
	// "some avg10=0.00 avg60=0.00 avg300=0.00 total=123"
	out := map[string]int64{}
	b, err := os.ReadFile(path)
	if err != nil {
		return out
	}
	for _, line := range strings.Split(strings.TrimSpace(string(b)), "\n") {
		fields := strings.Fields(line)
		if len(fields) < 2 {
			continue
		}
		kind := fields[0]
		for _, f := range fields[1:] {
			kv := strings.SplitN(f, "=", 2)
			if len(kv) != 2 {
				continue
			}
			if kv[0] == "total" {
				if v, err := strconv.ParseInt(kv[1], 10, 64); err == nil {
					out[kind+"_total_us"] = v
				}
			}
			if kv[0] == "avg10" {
				if v, err := strconv.ParseFloat(kv[1], 64); err == nil {
					out[kind+"_avg10"] = int64(v * 100)
				}
			}
		}
	}
	return out
}

type ResourceSample struct {
	SchemaVersion int              `json:"schema_version"`
	Type          string           `json:"type"`
	RunID         string           `json:"run_id"`
	RoundID       string           `json:"round_id"`
	TaskID        string           `json:"task_id"`
	AgentID       string           `json:"agent_id"`
	SampleSeq     int64            `json:"sample_seq"`
	MonoNS        int64            `json:"monotonic_ns"`
	UTCNS         int64            `json:"utc_ns"`
	CPUUsageUS    int64            `json:"cpu_usage_usec"`
	CPUUserUS     int64            `json:"cpu_user_usec"`
	CPUSystemUS   int64            `json:"cpu_system_usec"`
	NRPeriods     int64            `json:"nr_periods"`
	NRThrottled   int64            `json:"nr_throttled"`
	ThrottledUS   int64            `json:"throttled_usec"`
	MemCurrent    int64            `json:"memory_current_bytes"`
	MemPeak       int64            `json:"memory_peak_bytes"`
	MemAnon       int64            `json:"memory_anon_bytes"`
	MemFile       int64            `json:"memory_file_bytes"`
	SwapCurrent   int64            `json:"swap_current_bytes"`
	OOMEvents     int64            `json:"oom_events"`
	PidsCurrent   int64            `json:"pids_current"`
	PidsPeak      int64            `json:"pids_peak"`
	CPUPressure   map[string]int64 `json:"cpu_pressure"`
	MemPressure   map[string]int64 `json:"memory_pressure"`
	IOStats       map[string]int64 `json:"io_stat"`
	HostLoad1     float64          `json:"host_load1"`
	HostStealUS   int64            `json:"host_cpu_steal_usec"`
}

func (c *Cgroup) sample(seq int64, runID, roundID, taskID, agentID string) ResourceSample {
	cpu := readKV(filepath.Join(c.Path, "cpu.stat"))
	mem := readKV(filepath.Join(c.Path, "memory.stat"))
	ev := readKV(filepath.Join(c.Path, "memory.events"))
	io := readKV(filepath.Join(c.Path, "io.stat"))
	pids := readKV(filepath.Join(c.Path, "pids.events"))
	load := readLoad1()
	steal := readHostSteal()
	return ResourceSample{
		SchemaVersion: 1, Type: "RESOURCE_SAMPLE",
		RunID: runID, RoundID: roundID, TaskID: taskID, AgentID: agentID,
		SampleSeq: seq, MonoNS: time.Since(processStart).Nanoseconds(), UTCNS: time.Now().UTC().UnixNano(),
		CPUUsageUS: cpu["usage_usec"], CPUUserUS: cpu["user_usec"], CPUSystemUS: cpu["system_usec"],
		NRPeriods: cpu["nr_periods"], NRThrottled: cpu["nr_throttled"], ThrottledUS: cpu["throttled_usec"],
		MemCurrent: readInt(filepath.Join(c.Path, "memory.current")),
		MemPeak:    readInt(filepath.Join(c.Path, "memory.peak")),
		MemAnon:    mem["anon"], MemFile: mem["file"],
		SwapCurrent: readInt(filepath.Join(c.Path, "memory.swap.current")),
		OOMEvents:   ev["oom_kill"],
		PidsCurrent: readInt(filepath.Join(c.Path, "pids.current")),
		PidsPeak:    pids["peak"],
		CPUPressure: readPressure(filepath.Join(c.Path, "cpu.pressure")),
		MemPressure: readPressure(filepath.Join(c.Path, "memory.pressure")),
		IOStats:     io,
		HostLoad1:   load, HostStealUS: steal,
	}
}

func (c *Cgroup) finalCounters() map[string]any {
	cpu := readKV(filepath.Join(c.Path, "cpu.stat"))
	ev := readKV(filepath.Join(c.Path, "memory.events"))
	return map[string]any{
		"cpu_usage_usec": cpu["usage_usec"], "cpu_user_usec": cpu["user_usec"],
		"cpu_system_usec": cpu["system_usec"], "nr_periods": cpu["nr_periods"],
		"nr_throttled": cpu["nr_throttled"], "throttled_usec": cpu["throttled_usec"],
		"memory_peak_bytes": readInt(filepath.Join(c.Path, "memory.peak")),
		"oom_events":        ev["oom_kill"],
		"memory_max_events": ev["max"],
		"cgroup":            c.Path,
	}
}
