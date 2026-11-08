package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

const relayDummyKey = "relay-managed" // relay injects the real credential

type AgentSpec struct {
	ID         string `json:"id"`
	Binary     string `json:"binary"`
	TaskPrompt string `json:"-"`
	RelayAddr  string `json:"-"`
	Home       string `json:"home"`
}

// prepareHome provisions an isolated HOME/XDG/TMPDIR and points the agent's
// provider configuration at the per-trial relay. Credentials never enter the
// agent-visible configuration: the relay holds the only real key.
func prepareHome(root, agentID, childHome string, relayAddr string) error {
	for _, d := range []string{
		filepath.Join(childHome, ".cache"), filepath.Join(childHome, ".config"),
		filepath.Join(childHome, "tmp"), filepath.Join(childHome, ".local", "share"),
	} {
		if err := os.MkdirAll(d, 0o700); err != nil {
			return err
		}
	}
	switch agentID {
	case "pi-bolt":
		dir := filepath.Join(childHome, ".pi", "agent")
		if err := os.MkdirAll(dir, 0o700); err != nil {
			return err
		}
		models := map[string]any{
			"providers": map[string]any{
				"opencode-go": map[string]any{"baseUrl": "http://" + relayAddr + "/v1"},
			},
		}
		b, _ := jsonMarshalIndent(models)
		if err := os.WriteFile(filepath.Join(dir, "models.json"), b, 0o600); err != nil {
			return err
		}
	case "jcode":
		dir := filepath.Join(childHome, ".jcode")
		if err := os.MkdirAll(dir, 0o700); err != nil {
			return err
		}
		cfg := fmt.Sprintf(`[session]
hibernation_idle_secs = 1800

[features]
check_updates = false
swarm = true
auto_poke = false
persist_memory_injections = false

[provider]
openai_reasoning_effort = "low"
openai_service_tier = "auto"
max_retries = 3
retry_backoff_cap_secs = 10

[providers.benchrelay]
type = "openai-compatible"
base_url = "http://%s/v1"
auth = "none"
default_model = "deepseek-v4.1-flash"
requires_api_key = false

[[providers.benchrelay.models]]
id = "deepseek-v4.1-flash"

[safety]
desktop_notifications = false

[gateway]
enabled = false
`, relayAddr)
		if err := os.WriteFile(filepath.Join(dir, "config.toml"), []byte(cfg), 0o600); err != nil {
			return err
		}
	}
	return nil
}

func buildCommand(agentID, prompt, workspace, relayAddr string, home string) ([]string, []string, error) {
	baseEnv := []string{
		"PATH=" + os.Getenv("PATH"),
		"HOME=" + home,
		"XDG_CACHE_HOME=" + filepath.Join(home, ".cache"),
		"XDG_CONFIG_HOME=" + filepath.Join(home, ".config"),
		"XDG_DATA_HOME=" + filepath.Join(home, ".local", "share"),
		"TMPDIR=" + filepath.Join(home, "tmp"),
		"LANG=C.UTF-8",
		"LC_ALL=C.UTF-8",
		"TERM=xterm-256color",
		"NO_COLOR=1",
		"CI=1",
		"PYTHONDONTWRITEBYTECODE=1",
		"OPENCODE_API_KEY=" + relayDummyKey,
		"OPENAI_API_KEY=" + relayDummyKey,
		"HAX_PROVIDER=openai-compatible",
		"HAX_OPENAI_BASE_URL=http://" + relayAddr + "/v1",
		"HAX_OPENAI_API_KEY=" + relayDummyKey,
		"HAX_MODEL=deepseek-v4.1-flash",
		"HAX_EFFORT=low",
		"JCODE_NO_UPDATE=1",
	}
	switch agentID {
	case "pi-bolt":
		return []string{
			"pi-bolt", "--provider", "opencode-go", "--model", "deepseek-v4.1-flash",
			"--thinking", "low", "--mode", "json", "-p", prompt,
		}, baseEnv, nil
	case "jcode":
		return []string{
			"jcode", "--provider-profile", "benchrelay", "--model", "deepseek-v4.1-flash",
			"run", "--json", "--no-update", "-C", workspace, prompt,
		}, baseEnv, nil
	case "hax":
		return []string{
			"hax", "--provider=openai-compatible", "--model=deepseek-v4.1-flash",
			"--effort=low", "--json", "-p", prompt,
		}, baseEnv, nil
	}
	return nil, nil, fmt.Errorf("unknown agent %q", agentID)
}

func agentPrompt(taskID, agentID string) string {
	return strings.TrimSpace(`You are an autonomous software engineer working unattended in the current git repository.

Your assignment is described in TASK.md in this directory. Read it carefully, then implement the required change directly in the repository.

Rules:
- You have a hard wall-clock limit. Work efficiently and prioritise a correct, tested implementation.
- Use only the tools available in your environment. There is NO network access; do not attempt to download dependencies.
- Python standard library only; do not install packages.
- Keep existing public tests passing. Add regression tests where TASK.md asks for them.
- Do NOT modify or delete existing test files.
- Do not search for or attempt to read hidden graders, reference solutions, other workspaces, credentials, or files outside this repository.
- When finished, make sure the working tree reflects your final implementation (committing is optional) and stop.

Begin now. Task identifier: ` + taskID + ` — competitor: ` + agentID + `.`)
}

func jsonMarshalIndent(v any) ([]byte, error) {
	return json.MarshalIndent(v, "", "  ")
}
