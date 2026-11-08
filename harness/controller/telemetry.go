package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

// ---------------------------------------------------------------- host stats

func readLoad1() float64 {
	b, err := os.ReadFile("/proc/loadavg")
	if err != nil {
		return -1
	}
	f := strings.Fields(string(b))
	if len(f) == 0 {
		return -1
	}
	v, _ := strconv.ParseFloat(f[0], 64)
	return v
}

func readHostSteal() int64 {
	f, err := os.Open("/proc/stat")
	if err != nil {
		return -1
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := sc.Text()
		if !strings.HasPrefix(line, "cpu ") {
			continue
		}
		fields := strings.Fields(line)
		// user nice system idle iowait irq softirq steal
		if len(fields) >= 9 {
			v, _ := strconv.ParseInt(fields[8], 10, 64)
			return v * 10000 // clock ticks (USER_HZ=100) -> microseconds
		}
	}
	return -1
}

type HostInfo struct {
	SchemaVersion  int               `json:"schema_version"`
	Type           string            `json:"type"`
	Hostname       string            `json:"hostname"`
	Kernel         string            `json:"kernel"`
	Debian         string            `json:"debian_version"`
	CPUModel       string            `json:"cpu_model"`
	LogicalCPUs    int               `json:"logical_cpus"`
	ThreadSiblings string            `json:"thread_siblings"`
	MemTotalBytes  int64             `json:"mem_total_bytes"`
	MemAvailable   int64             `json:"mem_available_bytes"`
	SwapTotal      int64             `json:"swap_total_bytes"`
	CgroupPath     string            `json:"cgroup_path"`
	CgroupControls string            `json:"cgroup_controllers"`
	CgroupSelf     string            `json:"cgroup_self"`
	Load1          float64           `json:"load1"`
	NoSudo         bool              `json:"passwordless_sudo_unavailable"`
	CapturedUTCNS  int64             `json:"utc_ns"`
	Tools          map[string]string `json:"tool_versions"`
}

func captureHostInfo() HostInfo {
	h := HostInfo{SchemaVersion: 1, Type: "ENVIRONMENT", CapturedUTCNS: time.Now().UTC().UnixNano()}
	h.Hostname, _ = os.Hostname()
	if b, err := os.ReadFile("/proc/sys/kernel/osrelease"); err == nil {
		h.Kernel = strings.TrimSpace(string(b))
	}
	if b, err := os.ReadFile("/etc/debian_version"); err == nil {
		h.Debian = strings.TrimSpace(string(b))
	}
	if b, err := os.ReadFile("/proc/cpuinfo"); err == nil {
		for _, line := range strings.Split(string(b), "\n") {
			if strings.HasPrefix(line, "model name") {
				parts := strings.SplitN(line, ":", 2)
				if len(parts) == 2 {
					h.CPUModel = strings.TrimSpace(parts[1])
					break
				}
			}
		}
	}
	if b, err := os.ReadFile("/proc/meminfo"); err == nil {
		for _, line := range strings.Split(string(b), "\n") {
			f := strings.Fields(line)
			if len(f) < 2 {
				continue
			}
			v, _ := strconv.ParseInt(f[1], 10, 64)
			switch f[0] {
			case "MemTotal:":
				h.MemTotalBytes = v * 1024
			case "MemAvailable:":
				h.MemAvailable = v * 1024
			case "SwapTotal:":
				h.SwapTotal = v * 1024
			}
		}
	}
	if out, err := execCommand("nproc"); err == nil {
		h.LogicalCPUs, _ = strconv.Atoi(strings.TrimSpace(out))
	}
	if out, err := execCommand("taskset", "-pc", strconv.Itoa(os.Getpid())); err == nil {
		h.ThreadSiblings = strings.TrimSpace(out)
	}
	if out, err := execCommand("lscpu", "-e=CPU,CORE,SOCKET,ONLINE"); err == nil {
		h.ThreadSiblings = strings.ReplaceAll(strings.TrimSpace(out), "\n", " | ")
	}
	if own, err := findOwnCgroup(); err == nil {
		h.CgroupSelf = own
		if c, err := cgroupControllers(cgroupRoot(own)); err == nil {
			h.CgroupControls = c
		}
	}
	h.Load1 = readLoad1()
	// passwordless sudo is not assumed; detect absence without prompting.
	if _, err := execCommand("sudo", "-n", "true"); err != nil {
		h.NoSudo = true
	}
	h.Tools = map[string]string{}
	for _, tool := range [][]string{
		{"go", "version"}, {"python3", "--version"}, {"git", "--version"},
		{"pi-bolt", "--version"}, {"jcode", "version"}, {"hax", "--version"},
	} {
		out, err := execCommand(tool[0], tool[1:]...)
		if err == nil {
			h.Tools[tool[0]] = strings.TrimSpace(strings.SplitN(out, "\n", 2)[0])
		} else {
			h.Tools[tool[0]] = "UNAVAILABLE"
		}
	}
	return h
}

func execCommand(name string, args ...string) (string, error) {
	cmd := exec.Command(name, args...)
	out, err := cmd.Output()
	return string(out), err
}

// ------------------------------------------------------------- event logging

type Event struct {
	SchemaVersion int            `json:"schema_version"`
	ExperimentID  string         `json:"experiment_id"`
	RunID         string         `json:"run_id"`
	RoundID       string         `json:"round_id,omitempty"`
	TaskID        string         `json:"task_id,omitempty"`
	AgentID       string         `json:"agent_id,omitempty"`
	EventID       string         `json:"event_id"`
	ParentSpanID  string         `json:"parent_span_id,omitempty"`
	ProcessID     int            `json:"process_id,omitempty"`
	UTCNS         int64          `json:"utc_ns"`
	MonotonicNS   int64          `json:"monotonic_ns"`
	Source        string         `json:"source"`
	EventType     string         `json:"event_type"`
	EvidenceRef   string         `json:"evidence_ref,omitempty"`
	Detail        map[string]any `json:"detail,omitempty"`
}

type EventLog struct {
	mu    sync.Mutex
	file  *os.File
	expID string
	runID string
	seq   int64
}

func OpenEventLog(expID, runID, path string) (*EventLog, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o640)
	if err != nil {
		return nil, err
	}
	return &EventLog{file: f, expID: expID, runID: runID}, nil
}

func (l *EventLog) Emit(roundID, taskID, agentID, source, etype string, detail map[string]any) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.seq++
	ev := Event{
		SchemaVersion: 1, ExperimentID: l.expID, RunID: l.runID,
		RoundID: roundID, TaskID: taskID, AgentID: agentID,
		EventID: fmt.Sprintf("%s-%06d", l.runID, l.seq),
		UTCNS:   time.Now().UTC().UnixNano(), MonotonicNS: time.Since(processStart).Nanoseconds(),
		Source: source, EventType: etype, Detail: detail,
	}
	b, _ := json.Marshal(ev)
	l.file.Write(append(b, '\n'))
}

// --------------------------------------------------------- process sampling

type ProcSample struct {
	SchemaVersion int    `json:"schema_version"`
	Type          string `json:"type"`
	RunID         string `json:"run_id"`
	RoundID       string `json:"round_id"`
	TaskID        string `json:"task_id"`
	AgentID       string `json:"agent_id"`
	SampleSeq     int64  `json:"sample_seq"`
	MonoNS        int64  `json:"monotonic_ns"`
	UTCNS         int64  `json:"utc_ns"`
	PID           int    `json:"pid"`
	PPID          int    `json:"ppid"`
	Comm          string `json:"comm"`
	Cmdline       string `json:"cmdline"`
	State         string `json:"state"`
	RSSBytes      int64  `json:"rss_bytes"`
	Threads       int    `json:"threads"`
	CPUUTimeTicks int64  `json:"cpu_utime_ticks"`
	CPUSTimeTicks int64  `json:"cpu_stime_ticks"`
	StartTicks    int64  `json:"start_ticks"`
}

func sampleProcessTree(cg *Cgroup, seq int64, runID, roundID, taskID, agentID string) []ProcSample {
	pids := cg.procs()
	sort.Ints(pids)
	out := make([]ProcSample, 0, len(pids))
	now := time.Now().UTC().UnixNano()
	for _, pid := range pids {
		base := fmt.Sprintf("/proc/%d", pid)
		statBytes, err := os.ReadFile(filepath.Join(base, "stat"))
		if err != nil {
			continue
		}
		stat := string(statBytes)
		// comm may contain spaces/parentheses; split on the last ')'
		close := strings.LastIndex(stat, ")")
		if close < 0 {
			continue
		}
		comm := strings.TrimPrefix(stat[:close], stat[:strings.Index(stat, "(")+1])
		rest := strings.Fields(stat[close+1:])
		if len(rest) < 22 {
			continue
		}
		ps := ProcSample{
			SchemaVersion: 1, Type: "PROCESS_SAMPLE", RunID: runID, RoundID: roundID,
			TaskID: taskID, AgentID: agentID, SampleSeq: seq, MonoNS: time.Since(processStart).Nanoseconds(),
			UTCNS: now, PID: pid, Comm: comm,
		}
		ps.State = rest[0]
		ps.PPID, _ = strconv.Atoi(rest[1])
		ps.CPUUTimeTicks, _ = strconv.ParseInt(rest[11], 10, 64)
		ps.CPUSTimeTicks, _ = strconv.ParseInt(rest[12], 10, 64)
		ps.Threads, _ = strconv.Atoi(rest[17])
		ps.StartTicks, _ = strconv.ParseInt(rest[19], 10, 64)
		rssPages, _ := strconv.ParseInt(rest[21], 10, 64)
		ps.RSSBytes = rssPages * int64(os.Getpagesize())
		if cmd, err := os.ReadFile(filepath.Join(base, "cmdline")); err == nil {
			ps.Cmdline = truncateCmd(strings.ReplaceAll(string(cmd), "\x00", " "), 240)
		}
		out = append(out, ps)
	}
	return out
}

func truncateCmd(s string, n int) string {
	s = redactSecrets(s)
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}
