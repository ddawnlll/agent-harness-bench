package main

// Neutral API relay for OpenCode Go (OpenAI-compatible chat completions).
//
// Every competitor (and every subagent it spawns) is configured to send its
// model traffic to a per-competitor loopback listener. The relay:
//   * forwards to the canonical OpenCode Go endpoint,
//   * injects the provider-required headers (Authorization, User-Agent,
//     x-opencode-session) so credentials never enter agent-visible config,
//   * pins the requested model and reasoning_effort to the experiment
//     constants, recording the requested values for the audit,
//   * records per-request timing, token usage and finish reasons into an
//     append-only JSONL event log,
//   * never logs credentials, prompts or response bodies.

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

const (
	canonicalModel = "deepseek-v4.1-flash"
	canonicalAPI   = "https://opencode.ai/inference/go/openai/v1"
	relayUA        = "opencode-cli/0.0.0"
)

type Usage struct {
	PromptTokens     *int64   `json:"prompt_tokens,omitempty"`
	CompletionTokens *int64   `json:"completion_tokens,omitempty"`
	TotalTokens      *int64   `json:"total_tokens,omitempty"`
	PromptCacheHit   *int64   `json:"prompt_cache_hit_tokens,omitempty"`
	PromptCacheMiss  *int64   `json:"prompt_cache_miss_tokens,omitempty"`
	CostUSD          *float64 `json:"cost,omitempty"`
}

type APIEvent struct {
	SchemaVersion      int            `json:"schema_version"`
	EventType          string         `json:"event_type"`
	ExperimentID       string         `json:"experiment_id"`
	RoundID            string         `json:"round_id"`
	RunID              string         `json:"run_id"`
	AgentID            string         `json:"agent_id"`
	TaskID             string         `json:"task_id"`
	Seq                int64          `json:"seq"`
	UTCNS              int64          `json:"utc_ns"`
	MonoNS             int64          `json:"monotonic_ns"`
	Source             string         `json:"source"`
	Path               string         `json:"path"`
	Method             string         `json:"method"`
	Stream             bool           `json:"stream"`
	StatusCode         int            `json:"http_status"`
	TTFBMS             float64        `json:"ttfb_ms"`
	DurationMS         float64        `json:"duration_ms"`
	UpstreamDurationMS float64        `json:"upstream_duration_ms"`
	ModelRequested     string         `json:"model_id_requested"`
	ModelReturned      string         `json:"model_id_returned"`
	EffortRequested    string         `json:"reasoning_effort_requested"`
	EffortForwarded    string         `json:"reasoning_effort_forwarded"`
	EffortRewritten    bool           `json:"reasoning_effort_rewritten"`
	ModelRewritten     bool           `json:"model_rewritten"`
	MaxOutputTokens    *int64         `json:"max_output_tokens,omitempty"`
	Temperature        *float64       `json:"temperature,omitempty"`
	ProviderRequestID  string         `json:"provider_request_id"`
	SessionID          string         `json:"relay_session_id"`
	InputTokens        *int64         `json:"input_tokens,omitempty"`
	OutputTokens       *int64         `json:"output_tokens,omitempty"`
	TotalTokens        *int64         `json:"total_tokens,omitempty"`
	ReasoningTokens    *int64         `json:"reasoning_tokens,omitempty"`
	CachedReadTokens   *int64         `json:"cached_read_tokens,omitempty"`
	CachedWriteTokens  *int64         `json:"cached_write_tokens,omitempty"`
	CostUSD            *float64       `json:"provider_reported_cost_usd,omitempty"`
	ListCostUSD        *float64       `json:"provider_list_cost_usd,omitempty"`
	AllowanceUSD       *float64       `json:"provider_allowance_usd,omitempty"`
	BillingSource      string         `json:"billing_source"`
	UsageRaw           map[string]any `json:"usage_raw,omitempty"`
	FinishReason       string         `json:"finish_reason"`
	UsageProvenance    string         `json:"usage_provenance"`
	RequestBodySHA256  string         `json:"request_body_sha256"`
	RequestBytes       int            `json:"request_bytes"`
	ResponseBytes      int64          `json:"response_bytes"`
	ErrorCategory      string         `json:"error_category"`
	ErrorDetail        string         `json:"error_detail"`
	RateLimitRemaining string         `json:"rate_limit_remaining"`
	Gate               string         `json:"gate"`
}

type RelayStats struct {
	Requests             int64
	Errors               int64
	Throttles            int64
	APIWallUS            int64
	InputTokens          int64
	OutputTokens         int64
	TotalTokens          int64
	ReasoningTokens      int64
	CachedReadTokens     int64
	CachedWriteTokens    int64
	CostUSD              float64
	ListCostUSD          float64
	AllowanceUSD         float64
	RequestsWithoutUsage int64
	EffortRewrites       int64
	FirstRequestMonoNS   int64
	FirstTokenMonoNS     int64
	Models               map[string]bool
}

type Relay struct {
	stats    RelayStats
	statsMu  sync.Mutex
	agentID  string
	taskID   string
	roundID  string
	expID    string
	runID    string
	apiKey   string
	logPath  string
	client   *http.Client
	mu       sync.Mutex
	logFile  *os.File
	seq      int64
	requests int64
	healthy  int32
}

func newUpstreamClient() *http.Client {
	tr := &http.Transport{
		Proxy:             nil, // never route provider traffic through an ambient proxy
		DialContext:       (&net.Dialer{Timeout: 15 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
		TLSClientConfig:   &tls.Config{MinVersion: tls.VersionTLS12},
		ForceAttemptHTTP2: true,
		MaxIdleConns:      32, IdleConnTimeout: 90 * time.Second,
		ResponseHeaderTimeout: 180 * time.Second,
	}
	return &http.Client{Transport: tr}
}

func NewRelay(expID, runID, roundID, agentID, taskID, apiKey, logPath string) (*Relay, error) {
	if err := os.MkdirAll(filepath.Dir(logPath), 0o750); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o640)
	if err != nil {
		return nil, err
	}
	return &Relay{
		agentID: agentID, taskID: taskID, roundID: roundID, expID: expID, runID: runID,
		apiKey: apiKey, logPath: logPath, client: newUpstreamClient(), logFile: f,
		stats: RelayStats{Models: map[string]bool{}},
	}, nil
}

func (r *Relay) appendEvent(ev *APIEvent) {
	ev.SchemaVersion = 1
	ev.Source = "relay"
	ev.ExperimentID = r.expID
	ev.RoundID = r.roundID
	ev.RunID = r.runID
	ev.AgentID = r.agentID
	ev.TaskID = r.taskID
	ev.Seq = atomic.AddInt64(&r.seq, 1)
	ev.UTCNS = time.Now().UTC().UnixNano()
	ev.MonoNS = time.Since(processStart).Nanoseconds()
	b, _ := json.Marshal(ev)
	r.mu.Lock()
	r.logFile.Write(append(b, '\n'))
	r.mu.Unlock()
}

func (r *Relay) Stats() map[string]any {
	r.statsMu.Lock()
	defer r.statsMu.Unlock()
	models := make([]string, 0, len(r.stats.Models))
	for m := range r.stats.Models {
		models = append(models, m)
	}
	out := map[string]any{
		"requests": r.stats.Requests, "errors": r.stats.Errors, "throttles": r.stats.Throttles,
		"api_wall_us": r.stats.APIWallUS, "input_tokens": r.stats.InputTokens,
		"output_tokens": r.stats.OutputTokens, "total_tokens": r.stats.TotalTokens,
		"reasoning_tokens": r.stats.ReasoningTokens, "cached_read_tokens": r.stats.CachedReadTokens,
		"cached_write_tokens": r.stats.CachedWriteTokens, "cost_usd": r.stats.CostUSD,
		"list_cost_usd": r.stats.ListCostUSD, "allowance_usd": r.stats.AllowanceUSD,
		"requests_without_usage": r.stats.RequestsWithoutUsage,
		"effort_rewrites":        r.stats.EffortRewrites,
		"first_request_mono_ns":  r.stats.FirstRequestMonoNS,
		"first_token_mono_ns":    r.stats.FirstTokenMonoNS,
		"models":                 models,
	}
	return out
}

func (r *Relay) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(200)
		io.WriteString(w, "ok")
	})
	mux.HandleFunc("/v1/models", r.proxy)
	mux.HandleFunc("/v1/chat/completions", r.proxy)
	mux.HandleFunc("/v1/responses", r.proxy)
	mux.HandleFunc("/", r.proxy)
	return mux
}

type reqMeta struct {
	Model           string                     `json:"model"`
	Stream          bool                       `json:"stream"`
	ReasoningEffort json.RawMessage            `json:"reasoning_effort"`
	MaxTokens       *int64                     `json:"max_tokens"`
	MaxCompletion   *int64                     `json:"max_completion_tokens"`
	Temperature     *float64                   `json:"temperature"`
	StreamOptions   map[string]any             `json:"stream_options"`
	Raw             map[string]json.RawMessage `json:"-"`
}

func (r *Relay) proxy(w http.ResponseWriter, req *http.Request) {
	atomic.AddInt64(&r.requests, 1)
	reqMono := time.Since(processStart).Nanoseconds()
	r.statsMu.Lock()
	r.stats.Requests++
	if r.stats.FirstRequestMonoNS == 0 {
		r.stats.FirstRequestMonoNS = reqMono
	}
	r.statsMu.Unlock()
	t0 := time.Now()
	mono0 := time.Since(processStart)

	body, err := io.ReadAll(io.LimitReader(req.Body, 64<<20))
	if err != nil {
		http.Error(w, "bad request", 400)
		return
	}
	sum := sha256.Sum256(body)
	ev := &APIEvent{
		EventType: "API_REQUEST", Method: req.Method, Path: req.URL.Path,
		RequestBodySHA256: hex.EncodeToString(sum[:]), RequestBytes: len(body),
		UsageProvenance: "PROVIDER_REPORTED",
	}
	ev.SessionID = relaySessionID(r.runID, r.agentID, r.taskID)

	var meta reqMeta
	if len(body) > 0 {
		_ = json.Unmarshal(body, &meta)
	}
	ev.ModelRequested = meta.Model
	ev.Stream = meta.Stream
	if len(meta.ReasoningEffort) > 0 {
		var s string
		if json.Unmarshal(meta.ReasoningEffort, &s) == nil {
			ev.EffortRequested = s
		}
	}
	if meta.MaxTokens != nil {
		ev.MaxOutputTokens = meta.MaxTokens
	} else if meta.MaxCompletion != nil {
		ev.MaxOutputTokens = meta.MaxCompletion
	}
	ev.Temperature = meta.Temperature

	// Rewrite request: pin model and effort; capture requested values.
	if len(body) > 0 && strings.Contains(req.URL.Path, "chat/completions") {
		var m map[string]any
		if err := json.Unmarshal(body, &m); err == nil {
			if cur, ok := m["model"].(string); !ok || cur != canonicalModel {
				ev.ModelRewritten = true
				m["model"] = canonicalModel
			}
			cur, _ := m["reasoning_effort"].(string)
			if cur != "low" {
				ev.EffortRewritten = true
				m["reasoning_effort"] = "low"
			}
			ev.EffortForwarded = "low"
			// Ask for usage on streaming responses when the client did not.
			if s, _ := m["stream"].(bool); s {
				so, _ := m["stream_options"].(map[string]any)
				if so == nil {
					so = map[string]any{}
					m["stream_options"] = so
				}
				if _, ok := so["include_usage"]; !ok {
					so["include_usage"] = true
				}
			}
			if cur == "" {
				ev.EffortRequested = "<absent>"
			}
			var ok2 bool
			ev.ModelReturned, ok2 = m["model"].(string)
			_ = ok2
			if nb, e := json.Marshal(m); e == nil {
				body = nb
			}
		}
	}
	if ev.EffortForwarded == "" {
		ev.EffortForwarded = "low"
	}
	if ev.ModelReturned == "" {
		ev.ModelReturned = canonicalModel
	}

	upURL := canonicalAPI + strings.TrimPrefix(req.URL.Path, "/v1")
	upReq, err := http.NewRequestWithContext(req.Context(), req.Method, upURL, bytes.NewReader(body))
	if err != nil {
		ev.StatusCode = 500
		ev.ErrorCategory = "RELAY_BUILD"
		ev.ErrorDetail = err.Error()
		r.appendEvent(ev)
		http.Error(w, "relay error", 500)
		return
	}
	upReq.Header.Set("Content-Type", "application/json")
	upReq.Header.Set("Accept", "application/json, text/event-stream")
	upReq.Header.Set("User-Agent", relayUA)
	upReq.Header.Set("Authorization", "Bearer "+r.apiKey)
	upReq.Header.Set("x-opencode-session", ev.SessionID)
	if v := req.Header.Get("x-opencode"); v != "" {
		upReq.Header.Set("x-opencode", v)
	}

	tUp := time.Now()
	resp, err := r.client.Do(upReq)
	ev.UpstreamDurationMS = msSince(tUp)
	if err != nil {
		ev.StatusCode = 502
		ev.ErrorCategory = "UPSTREAM_TRANSPORT"
		ev.ErrorDetail = err.Error()
		ev.DurationMS = msSince(t0)
		ev.TTFBMS = ev.DurationMS
		r.appendEvent(ev)
		http.Error(w, "upstream transport error", 502)
		return
	}
	defer resp.Body.Close()
	ev.StatusCode = resp.StatusCode
	if rid := resp.Header.Get("x-request-id"); rid != "" {
		ev.ProviderRequestID = rid
	} else if rid := resp.Header.Get("request-id"); rid != "" {
		ev.ProviderRequestID = rid
	}
	for k, v := range resp.Header {
		lk := strings.ToLower(k)
		if strings.Contains(lk, "ratelimit") || lk == "retry-after" {
			if ev.RateLimitRemaining == "" {
				ev.RateLimitRemaining = lk + "=" + strings.Join(v, ",")
			} else {
				ev.RateLimitRemaining += ";" + lk + "=" + strings.Join(v, ",")
			}
		}
	}

	for k, v := range resp.Header {
		if strings.EqualFold(k, "content-length") || strings.EqualFold(k, "transfer-encoding") {
			continue
		}
		w.Header()[k] = v
	}
	w.WriteHeader(resp.StatusCode)

	isStream := strings.Contains(strings.ToLower(resp.Header.Get("Content-Type")), "event-stream") || meta.Stream
	flusher, _ := w.(http.Flusher)
	firstByte := true
	var captured int64
	var finishReason string
	var usageRaw json.RawMessage
	var sawUsage bool

	if isStream {
		br := bufio.NewReaderSize(resp.Body, 64<<10)
		for {
			line, err := br.ReadBytes('\n')
			if len(line) > 0 {
				if firstByte {
					firstByte = false
					ev.TTFBMS = msSince(t0)
				}
				n, werr := w.Write(line)
				captured += int64(n)
				if flusher != nil {
					flusher.Flush()
				}
				if werr != nil {
					ev.ErrorCategory = "CLIENT_WRITE"
					break
				}
				if fr, u, saw, sawU := parseSSELine(line); saw || sawU {
					if fr != "" {
						finishReason = fr
					}
					if sawU && len(u) > 0 {
						usageRaw = append(usageRaw[:0], u...)
						sawUsage = true
					}
				}
			}
			if err != nil {
				break
			}
		}
	} else {
		ct := resp.Header.Get("Content-Type")
		_ = ct
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 64<<20))
		captured = int64(len(b))
		ev.TTFBMS = msSince(t0)
		_, _ = w.Write(b)
		var obj struct {
			Model   string          `json:"model"`
			Usage   json.RawMessage `json:"usage"`
			Choices []struct {
				FinishReason string `json:"finish_reason"`
			} `json:"choices"`
			Error *struct {
				Type    string `json:"type"`
				Message string `json:"message"`
			} `json:"error"`
		}
		if json.Unmarshal(b, &obj) == nil {
			if obj.Model != "" {
				ev.ModelReturned = obj.Model
			}
			if len(obj.Usage) > 0 && string(obj.Usage) != "null" {
				var probe map[string]any
				if json.Unmarshal(obj.Usage, &probe) == nil && len(probe) > 0 {
					usageRaw = obj.Usage
					sawUsage = true
				}
			}
			if len(obj.Choices) > 0 {
				finishReason = obj.Choices[0].FinishReason
			}
			if obj.Error != nil {
				ev.ErrorCategory = obj.Error.Type
				ev.ErrorDetail = truncate(obj.Error.Message, 300)
			}
		}
	}
	ev.ResponseBytes = captured
	ev.DurationMS = msSince(t0)
	ev.FinishReason = finishReason
	if sawUsage {
		extractUsage(ev, usageRaw)
		if ev.InputTokens == nil && ev.OutputTokens != nil {
			ev.UsageProvenance = "PROVIDER_PARTIAL"
		}
	} else {
		ev.UsageProvenance = "NOT_OBSERVED"
	}
	if resp.StatusCode >= 400 && ev.ErrorCategory == "" {
		ev.ErrorCategory = fmt.Sprintf("HTTP_%d", resp.StatusCode)
	}
	if ev.EffortRewritten {
		r.statsMu.Lock()
		r.stats.EffortRewrites++
		r.statsMu.Unlock()
	}
	if isStream && ev.TTFBMS > 0 {
		r.statsMu.Lock()
		if r.stats.FirstTokenMonoNS == 0 {
			r.stats.FirstTokenMonoNS = int64(mono0) + int64(ev.TTFBMS*1e6)
		}
		r.statsMu.Unlock()
	}
	r.statsMu.Lock()
	r.stats.APIWallUS += int64(ev.DurationMS * 1000)
	if ev.StatusCode >= 400 {
		r.stats.Errors++
	}
	if ev.StatusCode == 429 || ev.StatusCode == 503 {
		r.stats.Throttles++
	}
	if ev.InputTokens != nil {
		r.stats.InputTokens += *ev.InputTokens
	}
	if ev.OutputTokens != nil {
		r.stats.OutputTokens += *ev.OutputTokens
	}
	if ev.TotalTokens != nil {
		r.stats.TotalTokens += *ev.TotalTokens
	}
	if ev.ReasoningTokens != nil {
		r.stats.ReasoningTokens += *ev.ReasoningTokens
	}
	if ev.CachedReadTokens != nil {
		r.stats.CachedReadTokens += *ev.CachedReadTokens
	}
	if ev.CachedWriteTokens != nil {
		r.stats.CachedWriteTokens += *ev.CachedWriteTokens
	}
	if ev.CostUSD != nil {
		r.stats.CostUSD += *ev.CostUSD
	}
	if ev.ListCostUSD != nil {
		r.stats.ListCostUSD += *ev.ListCostUSD
	}
	if ev.AllowanceUSD != nil {
		r.stats.AllowanceUSD += *ev.AllowanceUSD
	}
	if ev.ModelReturned != "" {
		r.stats.Models[ev.ModelReturned] = true
	}
	if ev.UsageProvenance == "NOT_OBSERVED" || ev.UsageProvenance == "PROVIDER_UNPARSEABLE" {
		r.stats.RequestsWithoutUsage++
	}
	r.statsMu.Unlock()
	_ = mono0
	r.appendEvent(ev)
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}

func msSince(t time.Time) float64 {
	return float64(time.Since(t).Microseconds()) / 1000.0
}

// parseSSELine extracts finish_reason and the raw usage object from one SSE
// data line. Return values: finishReason, usageJSON, sawFinish, sawUsage.
func parseSSELine(line []byte) (string, json.RawMessage, bool, bool) {
	s := strings.TrimSpace(string(line))
	if !strings.HasPrefix(s, "data:") {
		return "", nil, false, false
	}
	payload := strings.TrimSpace(strings.TrimPrefix(s, "data:"))
	if payload == "" || payload == "[DONE]" {
		return "", nil, false, false
	}
	var obj struct {
		Model   string          `json:"model"`
		Usage   json.RawMessage `json:"usage"`
		Choices []struct {
			FinishReason string `json:"finish_reason"`
		} `json:"choices"`
	}
	if json.Unmarshal([]byte(payload), &obj) != nil {
		return "", nil, false, false
	}
	fr := ""
	if len(obj.Choices) > 0 {
		fr = obj.Choices[0].FinishReason
	}
	var u json.RawMessage
	if len(obj.Usage) > 0 && string(obj.Usage) != "null" {
		var probe map[string]any
		if json.Unmarshal(obj.Usage, &probe) == nil && len(probe) > 0 {
			u = obj.Usage
		}
	}
	return fr, u, fr != "", u != nil
}

// extractUsage pulls the canonical token/cost fields out of a provider usage
// object, preserving nested detail structures (cache reads, reasoning, cost,
// OpenCode allowance) without double counting.
func extractUsage(ev *APIEvent, raw json.RawMessage) {
	var m map[string]any
	if json.Unmarshal(raw, &m) != nil {
		ev.UsageProvenance = "PROVIDER_UNPARSEABLE"
		return
	}
	ev.UsageRaw = m
	num := func(v any) *int64 {
		switch t := v.(type) {
		case float64:
			x := int64(t)
			return &x
		}
		return nil
	}
	fnum := func(v any) *float64 {
		switch t := v.(type) {
		case float64:
			return &t
		}
		return nil
	}
	ev.InputTokens = num(m["prompt_tokens"])
	ev.OutputTokens = num(m["completion_tokens"])
	ev.TotalTokens = num(m["total_tokens"])
	if d, ok := m["prompt_tokens_details"].(map[string]any); ok {
		if v := num(d["cached_tokens"]); v != nil {
			ev.CachedReadTokens = v
		}
	}
	if d, ok := m["completion_tokens_details"].(map[string]any); ok {
		if v := num(d["reasoning_tokens"]); v != nil {
			ev.ReasoningTokens = v
		}
	}
	if v := fnum(m["cost"]); v != nil {
		ev.CostUSD = v
	}
	if oc, ok := m["opencode"].(map[string]any); ok {
		if v, ok := oc["billing_source"].(string); ok {
			ev.BillingSource = v
		}
		if v := fnum(oc["list_cost"]); v != nil {
			ev.ListCostUSD = v
		}
		if v := fnum(oc["allowance"]); v != nil {
			ev.AllowanceUSD = v
		}
		if t, ok := oc["tokens"].(map[string]any); ok {
			if v := num(t["reasoning"]); v != nil && ev.ReasoningTokens == nil {
				ev.ReasoningTokens = v
			}
			if v := num(t["cache_read"]); v != nil && ev.CachedReadTokens == nil {
				ev.CachedReadTokens = v
			}
			if v := num(t["cache_write_5m"]); v != nil {
				ev.CachedWriteTokens = v
			}
		}
	}
}

func relaySessionID(runID, agentID, taskID string) string {
	// Deterministic UUIDv4-shaped routing id derived from the trial identity.
	h := sha256.Sum256([]byte("opencode-session|" + runID + "|" + agentID + "|" + taskID))
	b := h[:16]
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:16])
}

// StartRelay launches one listener for one competitor trial and returns a
// shutdown function. bindAddr is 0.0.0.0:<port>.
func StartRelay(ctx context.Context, r *Relay, bindAddr string) (*http.Server, string, error) {
	ln, err := net.Listen("tcp", bindAddr)
	if err != nil {
		return nil, "", err
	}
	srv := &http.Server{
		Handler:           r.Handler(),
		ReadHeaderTimeout: 20 * time.Second,
		IdleTimeout:       300 * time.Second,
	}
	go srv.Serve(ln)
	go func() {
		<-ctx.Done()
		sh, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		srv.Shutdown(sh)
	}()
	return srv, ln.Addr().String(), nil
}
