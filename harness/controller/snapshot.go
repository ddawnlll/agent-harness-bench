package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
)

// Snapshot freezes a finished (or timed-out) workspace: it archives the tree,
// produces a diff against the starting commit, and reports code metrics.
type Snapshot struct {
	TaskID        string         `json:"task_id"`
	AgentID       string         `json:"agent_id"`
	Workspace     string         `json:"workspace"`
	FrozenDir     string         `json:"frozen_dir"`
	ArchiveSHA256 string         `json:"archive_sha256"`
	DiffSHA256    string         `json:"diff_sha256"`
	TreeSHA256    string         `json:"tree_sha256"`
	DiffPath      string         `json:"diff_path"`
	Metrics       map[string]any `json:"code_metrics"`
	Committed     bool           `json:"agent_committed"`
	HeadSHA       string         `json:"head_sha"`
}

func runGit(dir string, args ...string) (string, error) {
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "GIT_CONFIG_NOSYSTEM=1", "HOME="+dir)
	out, err := cmd.CombinedOutput()
	return string(out), err
}

func sha256File(path string) string {
	f, err := os.Open(path)
	if err != nil {
		return ""
	}
	defer f.Close()
	h := sha256.New()
	buf := make([]byte, 1<<16)
	for {
		n, err := f.Read(buf)
		if n > 0 {
			h.Write(buf[:n])
		}
		if err != nil {
			break
		}
	}
	return hex.EncodeToString(h.Sum(nil))
}

func treeDigest(dir string) string {
	var files []string
	filepath.Walk(dir, func(path string, info os.FileInfo, err error) error {
		if err != nil || info == nil || info.IsDir() {
			return nil
		}
		rel, _ := filepath.Rel(dir, path)
		if strings.HasPrefix(rel, ".git/") {
			return nil
		}
		files = append(files, rel)
		return nil
	})
	sort.Strings(files)
	h := sha256.New()
	for _, rel := range files {
		sum := sha256File(filepath.Join(dir, rel))
		fmt.Fprintf(h, "%s %s\n", sum, rel)
	}
	return hex.EncodeToString(h.Sum(nil))
}

// freezeWorkspace copies the workspace, commits any uncommitted work in the
// copy (never in the agent's tree), and extracts a patch vs the start commit.
func freezeWorkspace(ws, frozenDir, startSHA, taskID, agentID string) (*Snapshot, error) {
	if err := copyTree(ws, frozenDir); err != nil {
		return nil, err
	}
	snap := &Snapshot{TaskID: taskID, AgentID: agentID, Workspace: ws, FrozenDir: frozenDir}
	snap.TreeSHA256 = treeDigest(frozenDir)

	head, _ := runGit(frozenDir, "rev-parse", "HEAD")
	snap.HeadSHA = strings.TrimSpace(head)
	status, _ := runGit(frozenDir, "status", "--porcelain")
	snap.Committed = strings.TrimSpace(status) == ""

	if !snap.Committed {
		_, _ = runGit(frozenDir, "add", "-A")
		_, _ = runGit(frozenDir, "-c", "user.email=bench@local", "-c", "user.name=bench",
			"commit", "-m", "benchmark snapshot of agent workspace")
	}
	diff, _ := runGit(frozenDir, "diff", "--binary", startSHA, "HEAD")
	diffPath := filepath.Join(filepath.Dir(frozenDir), "diff.patch")
	if err := os.WriteFile(diffPath, []byte(diff), 0o640); err != nil {
		return nil, err
	}
	snap.DiffPath = diffPath
	snap.DiffSHA256 = sha256File(diffPath)

	numstat, _ := runGit(frozenDir, "diff", "--numstat", startSHA, "HEAD")
	filesChanged, added, deleted, testFiles := 0, 0, 0, 0
	for _, line := range strings.Split(strings.TrimSpace(numstat), "\n") {
		if line == "" {
			continue
		}
		f := strings.Fields(line)
		if len(f) < 3 {
			continue
		}
		filesChanged++
		a, _ := strconv.Atoi(f[0])
		d, _ := strconv.Atoi(f[1])
		added += a
		deleted += d
		if strings.HasPrefix(f[2], "tests/") {
			testFiles++
		}
	}
	snap.Metrics = map[string]any{
		"files_changed": filesChanged, "lines_added": added, "lines_deleted": deleted,
		"test_files_touched": testFiles, "numstat": strings.TrimSpace(numstat),
		"head_sha": snap.HeadSHA, "clean_tree": snap.Committed,
	}

	// Archive the frozen tree for independent audit.
	archive := filepath.Join(filepath.Dir(frozenDir), "workspace_frozen.tar.gz")
	if out, err := execCommand("tar", "-C", filepath.Dir(frozenDir), "-czf", archive,
		filepath.Base(frozenDir)); err == nil {
		snap.ArchiveSHA256 = sha256File(archive)
	} else {
		_ = out
	}
	return snap, nil
}

func copyTree(src, dst string) error {
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

func execCommandBool(name string, args ...string) error {
	cmd := exec.Command(name, args...)
	cmd.Stdout = nil
	cmd.Stderr = nil
	return cmd.Run()
}

type RunSummary struct {
	TaskID            string         `json:"task_id"`
	AgentID           string         `json:"agent_id"`
	RunID             string         `json:"run_id"`
	RoundID           string         `json:"round_id"`
	Status            string         `json:"status"` // completed|timeout|crashed|aborted
	ExitCode          int            `json:"exit_code"`
	ElapsedSeconds    float64        `json:"elapsed_seconds"`
	LaunchDelayMS     float64        `json:"launch_delay_ms"`
	StartupMS         float64        `json:"startup_ms"`
	TTFTMS            float64        `json:"first_token_ms"`
	FirstToolMS       float64        `json:"first_tool_ms"`
	ExitReason        string         `json:"exit_reason"`
	CPUSeconds        float64        `json:"cpu_seconds"`
	CPUUserSeconds    float64        `json:"cpu_user_seconds"`
	CPUSystemSeconds  float64        `json:"cpu_system_seconds"`
	ThrottledSeconds  float64        `json:"cpu_throttled_seconds"`
	MemPeakBytes      int64          `json:"peak_memory_bytes"`
	PidsPeak          int64          `json:"pids_peak"`
	OOMEvents         int64          `json:"oom_events"`
	Tokens            map[string]any `json:"tokens"`
	Requests          int            `json:"model_requests"`
	APIWallSeconds    float64        `json:"api_wall_seconds"`
	APIErrors         int            `json:"api_errors"`
	ThrottleEvents    int            `json:"api_throttle_events"`
	AgentVersion      string         `json:"agent_version"`
	AgentBinarySHA256 string         `json:"agent_binary_sha256"`
	Snapshot          *Snapshot      `json:"snapshot"`
	Grade             map[string]any `json:"grade"`
	GraderWallSeconds float64        `json:"grader_wall_seconds"`
	GraderCPUSeconds  float64        `json:"grader_cpu_seconds"`
	HostCPU           string         `json:"cpu_affinity"`
	StartSHA          string         `json:"start_sha"`
	RelayAddr         string         `json:"relay_addr"`
	StdoutPath        string         `json:"stdout_path"`
	StderrPath        string         `json:"stderr_path"`
	TracePath         string         `json:"trace_path"`
	StartUTC          string         `json:"start_utc"`
	EndUTC            string         `json:"end_utc"`
	Notes             []string       `json:"notes"`
}

func writeJSON(path string, v any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return err
	}
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, b, 0o640); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}
