package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"os"
	"path/filepath"
	"strings"
)

// loadAPIKey reads the OpenCode Go credential without ever logging it.
// Order: ~/opencode.txt (per operator instruction), then the Pi auth store
// (~/.pi/agent/auth.json -> opencode-go.key) as a documented fallback.
func loadAPIKey(keyFile string) (string, string, error) {
	if env := strings.TrimSpace(os.Getenv("OPENCODE_API_KEY")); env != "" {
		return env, "env:OPENCODE_API_KEY", nil
	}
	home, _ := os.UserHomeDir()
	txt := filepath.Join(home, "opencode.txt")
	if b, err := os.ReadFile(txt); err == nil {
		k := strings.TrimSpace(string(b))
		if k != "" {
			return k, "file:" + txt, nil
		}
	}
	auth := keyFile
	if auth == "" {
		auth = filepath.Join(home, ".pi/agent/auth.json")
	}
	if b, err := os.ReadFile(auth); err == nil {
		var m map[string]struct {
			Key  string `json:"key"`
			Type string `json:"type"`
		}
		if json.Unmarshal(b, &m) == nil {
			if v, ok := m["opencode-go"]; ok && strings.TrimSpace(v.Key) != "" {
				return strings.TrimSpace(v.Key), "pi-auth-store:" + auth, nil
			}
		}
	}
	return "", "", errors.New("no OpenCode Go credential found (~/opencode.txt absent and no opencode-go entry in auth store)")
}

func keyFingerprint(k string) string {
	h := sha256.Sum256([]byte(k))
	return hex.EncodeToString(h[:])[:12]
}

func newFlagSet(name string) *flag.FlagSet { return flag.NewFlagSet(name, flag.ExitOnError) }
