// Go SDK 使用示例 - LiuHao AI OS
//
// 本示例演示 Go SDK 的使用：
// 1. 客户端创建与选项
// 2. Agent 操作
// 3. 记忆操作
// 4. Context 用法
// 5. 错误处理
//
// 运行方式：
//   go run examples/sdk/go_usage.go
//
// 注意：这是 SDK 使用示例，展示 API 调用方式。
// 实际使用需要安装 SDK 包并配置 API Key。

package main

import (
	"context"
	"fmt"
	"os"
	"time"
)

// ======================================================================
// 数据结构
// ======================================================================

// Agent 表示一个 AI Agent。
type Agent struct {
	ID            string    `json:"id"`
	Name          string    `json:"name"`
	Role          string    `json:"role"`
	Status        string    `json:"status"`
	SystemPrompt  string    `json:"system_prompt"`
	Temperature   float64   `json:"temperature"`
	CreatedAt     time.Time `json:"created_at"`
}

// Memory 表示一条记忆。
type Memory struct {
	ID        string            `json:"id"`
	Content   string            `json:"content"`
	Type      string            `json:"type"`
	Tags      []string          `json:"tags"`
	Metadata  map[string]string `json:"metadata"`
	AgentID   string            `json:"agent_id"`
	CreatedAt time.Time         `json:"created_at"`
}

// Document 表示知识库文档。
type Document struct {
	ID         string    `json:"id"`
	Title      string    `json:"title"`
	Content    string    `json:"content"`
	Source     string    `json:"source"`
	Tags       []string  `json:"tags"`
	ChunkCount int       `json:"chunk_count"`
	CreatedAt  time.Time `json:"created_at"`
}

// ListResponse 是通用的列表响应。
type ListResponse[T any] struct {
	Items  []T `json:"items"`
	Total  int `json:"total"`
	Limit  int `json:"limit"`
	Offset int `json:"offset"`
}

// ExecuteResult 是任务执行结果。
type ExecuteResult struct {
	Output      string `json:"output"`
	Status      string `json:"status"`
	LatencyMs   int    `json:"latency_ms"`
	TokensUsed  int    `json:"tokens_used"`
	TaskID      string `json:"task_id"`
}

// SearchResult 是搜索结果。
type SearchResult[T any] struct {
	Results []ScoredResult[T] `json:"results"`
	Total   int               `json:"total"`
	Query   string            `json:"query"`
}

type ScoredResult[T any] struct {
	Item    T       `json:"item"`
	Score   float64 `json:"score"`
	Snippet string  `json:"snippet"`
}

// ======================================================================
// 错误类型
// ======================================================================

// APIError 表示 API 错误。
type APIError struct {
	StatusCode int
	Code       string
	Message    string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("[%d] %s: %s", e.StatusCode, e.Code, e.Message)
}

// ======================================================================
// LiuHao 客户端
// ======================================================================

// ClientOption 是客户端配置选项。
type ClientOption func(*LiuHaoClient)

// WithAPIKey 设置 API Key。
func WithAPIKey(apiKey string) ClientOption {
	return func(c *LiuHaoClient) {
		c.apiKey = apiKey
	}
}

// WithBaseURL 设置基础 URL。
func WithBaseURL(baseURL string) ClientOption {
	return func(c *LiuHaoClient) {
		c.baseURL = baseURL
	}
}

// WithTimeout 设置超时时间。
func WithTimeout(timeout time.Duration) ClientOption {
	return func(c *LiuHaoClient) {
		c.timeout = timeout
	}
}

// WithHTTPClient 设置自定义 HTTP 客户端。
// （此处简化，仅作演示）
func WithHTTPClient(client interface{}) ClientOption {
	return func(c *LiuHaoClient) {
		// c.httpClient = client
	}
}

// LiuHaoClient 是 LiuHao AI OS 的 Go SDK 客户端。
type LiuHaoClient struct {
	apiKey  string
	baseURL string
	timeout time.Duration

	// 模拟模式
	mockMode      bool
	mockAgents    map[string]*Agent
	mockMemories  []*Memory
	mockKnowledge []*Document
	agentCounter  int
}

// NewClient 创建一个新的客户端。
func NewClient(opts ...ClientOption) *LiuHaoClient {
	client := &LiuHaoClient{
		baseURL:       "http://localhost:8000/api/v1",
		timeout:       30 * time.Second,
		mockAgents:    make(map[string]*Agent),
		mockMemories:  make([]*Memory, 0),
		mockKnowledge: make([]*Document, 0),
	}

	// 应用选项
	for _, opt := range opts {
		opt(client)
	}

	// 检查 API Key
	client.mockMode = client.apiKey == ""
	if client.mockMode {
		fmt.Println("  ⚠️  API Key 未配置，使用模拟模式")
	}

	return client
}

// ==================================================================
// Agents API
// ==================================================================

// CreateAgent 创建一个新的 Agent。
func (c *LiuHaoClient) CreateAgent(ctx context.Context, name, role string, opts ...AgentOption) (*Agent, error) {
	config := &agentConfig{
		role:        role,
		temperature: 0.7,
	}
	for _, opt := range opts {
		opt(config)
	}

	if c.mockMode {
		c.agentCounter++
		id := fmt.Sprintf("agent-%04d", c.agentCounter)
		agent := &Agent{
			ID:           id,
			Name:         name,
			Role:         config.role,
			Status:       "idle",
			SystemPrompt: config.systemPrompt,
			Temperature:  config.temperature,
			CreatedAt:    time.Now(),
		}
		c.mockAgents[id] = agent
		return agent, nil
	}

	// 真实实现：发送 HTTP 请求
	// ...
	return nil, &APIError{StatusCode: 501, Code: "not_implemented", Message: "mock mode"}
}

type agentConfig struct {
	role         string
	systemPrompt string
	temperature  float64
}

// AgentOption 是 Agent 创建选项。
type AgentOption func(*agentConfig)

// WithSystemPrompt 设置系统提示。
func WithSystemPrompt(prompt string) AgentOption {
	return func(c *agentConfig) {
		c.systemPrompt = prompt
	}
}

// WithTemperature 设置温度参数。
func WithTemperature(temp float64) AgentOption {
	return func(c *agentConfig) {
		c.temperature = temp
	}
}

// GetAgent 获取 Agent 详情。
func (c *LiuHaoClient) GetAgent(ctx context.Context, agentID string) (*Agent, error) {
	if c.mockMode {
		agent, ok := c.mockAgents[agentID]
		if !ok {
			return nil, &APIError{
				StatusCode: 404,
				Code:       "not_found",
				Message:    fmt.Sprintf("agent %s not found", agentID),
			}
		}
		return agent, nil
	}
	return nil, &APIError{StatusCode: 501, Code: "not_implemented", Message: "mock mode"}
}

// ListAgents 列出 Agent。
func (c *LiuHaoClient) ListAgents(ctx context.Context, limit, offset int, role string) (*ListResponse[*Agent], error) {
	if c.mockMode {
		items := make([]*Agent, 0, len(c.mockAgents))
		for _, agent := range c.mockAgents {
			if role == "" || agent.Role == role {
				items = append(items, agent)
			}
		}
		total := len(items)
		end := offset + limit
		if end > total {
			end = total
		}
		if offset > total {
			offset = total
		}
		return &ListResponse[*Agent]{
			Items:  items[offset:end],
			Total:  total,
			Limit:  limit,
			Offset: offset,
		}, nil
	}
	return nil, &APIError{StatusCode: 501, Code: "not_implemented", Message: "mock mode"}
}

// ExecuteTask 执行任务。
func (c *LiuHaoClient) ExecuteTask(ctx context.Context, agentID, task string) (*ExecuteResult, error) {
	// 检查 context 是否已取消
	select {
	case <-ctx.Done():
		return nil, ctx.Err()
	default:
	}

	if c.mockMode {
		agent, ok := c.mockAgents[agentID]
		if !ok {
			return nil, &APIError{
				StatusCode: 404,
				Code:       "not_found",
				Message:    fmt.Sprintf("agent %s not found", agentID),
			}
		}

		agent.Status = "busy"
		defer func() { agent.Status = "idle" }()

		// 模拟计算时间
		time.Sleep(100 * time.Millisecond)

		// 检查 context 再次
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		default:
		}

		return &ExecuteResult{
			Output: fmt.Sprintf(
				"这是对任务 %q 的模拟回答。\n\nAgent: %s\n角色: %s\n\n在真实环境中，这里会返回 AI 生成的实际回答。",
				task[:30], agent.Name, agent.Role,
			),
			Status:     "completed",
			LatencyMs:  1234,
			TokensUsed: 150,
			TaskID:     fmt.Sprintf("task-%d", time.Now().Unix()),
		}, nil
	}

	return nil, &APIError{StatusCode: 501, Code: "not_implemented", Message: "mock mode"}
}

// DeleteAgent 删除 Agent。
func (c *LiuHaoClient) DeleteAgent(ctx context.Context, agentID string) error {
	if c.mockMode {
		if _, ok := c.mockAgents[agentID]; !ok {
			return &APIError{
				StatusCode: 404,
				Code:       "not_found",
				Message:    fmt.Sprintf("agent %s not found", agentID),
			}
		}
		delete(c.mockAgents, agentID)
		return nil
	}
	return &APIError{StatusCode: 501, Code: "not_implemented", Message: "mock mode"}
}

// ==================================================================
// Memory API
// ==================================================================

// AddMemory 添加记忆。
func (c *LiuHaoClient) AddMemory(ctx context.Context, content, memType string, tags []string) (*Memory, error) {
	if c.mockMode {
		id := fmt.Sprintf("mem-%04d", len(c.mockMemories)+1)
		mem := &Memory{
			ID:        id,
			Content:   content,
			Type:      memType,
			Tags:      tags,
			Metadata:  make(map[string]string),
			CreatedAt: time.Now(),
		}
		c.mockMemories = append(c.mockMemories, mem)
		return mem, nil
	}
	return nil, &APIError{StatusCode: 501, Code: "not_implemented", Message: "mock mode"}
}

// SearchMemory 搜索记忆。
func (c *LiuHaoClient) SearchMemory(ctx context.Context, query string, limit int) (*SearchResult[*Memory], error) {
	if c.mockMode {
		results := make([]ScoredResult[*Memory], 0)

		for _, mem := range c.mockMemories {
			// 简单的模拟相似度
			score := 0.5 + float64(len(mem.Content)%50)/100.0
			results = append(results, ScoredResult[*Memory]{
				Item:    mem,
				Score:   score,
				Snippet: mem.Content[:min(200, len(mem.Content))] + "...",
			})
		}

		if limit > len(results) {
			limit = len(results)
		}

		return &SearchResult[*Memory]{
			Results: results[:limit],
			Total:   len(results),
			Query:   query,
		}, nil
	}
	return nil, &APIError{StatusCode: 501, Code: "not_implemented", Message: "mock mode"}
}

// ListMemories 列出记忆。
func (c *LiuHaoClient) ListMemories(ctx context.Context, limit, offset int) (*ListResponse[*Memory], error) {
	if c.mockMode {
		total := len(c.mockMemories)
		end := offset + limit
		if end > total {
			end = total
		}
		return &ListResponse[*Memory]{
			Items:  c.mockMemories[offset:end],
			Total:  total,
			Limit:  limit,
			Offset: offset,
		}, nil
	}
	return nil, &APIError{StatusCode: 501, Code: "not_implemented", Message: "mock mode"}
}

// ==================================================================
// Knowledge API
// ==================================================================

// AddDocument 添加文档。
func (c *LiuHaoClient) AddDocument(ctx context.Context, title, content, source string, tags []string) (*Document, error) {
	if c.mockMode {
		id := fmt.Sprintf("doc-%04d", len(c.mockKnowledge)+1)
		doc := &Document{
			ID:         id,
			Title:      title,
			Content:    content,
			Source:     source,
			Tags:       tags,
			ChunkCount: len(content)/500 + 1,
			CreatedAt:  time.Now(),
		}
		c.mockKnowledge = append(c.mockKnowledge, doc)
		return doc, nil
	}
	return nil, &APIError{StatusCode: 501, Code: "not_implemented", Message: "mock mode"}
}

// SearchKnowledge 语义搜索知识库。
func (c *LiuHaoClient) SearchKnowledge(ctx context.Context, query string, limit int) (*SearchResult[*Document], error) {
	if c.mockMode {
		results := make([]ScoredResult[*Document], 0)

		for _, doc := range c.mockKnowledge {
			score := 0.6 + float64(len(doc.Title)%40)/100.0
			snippet := doc.Content[:min(200, len(doc.Content))] + "..."
			results = append(results, ScoredResult[*Document]{
				Item:    doc,
				Score:   score,
				Snippet: snippet,
			})
		}

		if limit > len(results) {
			limit = len(results)
		}

		return &SearchResult[*Document]{
			Results: results[:limit],
			Total:   len(results),
			Query:   query,
		}, nil
	}
	return nil, &APIError{StatusCode: 501, Code: "not_implemented", Message: "mock mode"}
}

// ======================================================================
// 工具函数
// ======================================================================

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// ======================================================================
// 示例 1：客户端创建
// ======================================================================

func demoClientCreation() *LiuHaoClient {
	fmt.Println("=" + stringsRepeat("=", 58))
	fmt.Println("  示例 1：客户端创建与选项模式")
	fmt.Println("=" + stringsRepeat("=", 58))
	fmt.Println()

	// 方式 1：使用默认配置
	fmt.Println("方式 1：默认配置")
	client1 := NewClient()
	fmt.Printf("  Base URL: %s\n", client1.baseURL)
	fmt.Printf("  超时: %v\n", client1.timeout)
	fmt.Println()

	// 方式 2：使用选项模式 (Functional Options)
	fmt.Println("方式 2：选项模式配置")
	client2 := NewClient(
		WithAPIKey("your-api-key-here"),
		WithBaseURL("https://api.liuhao.example.com/v1"),
		WithTimeout(60*time.Second),
	)
	fmt.Printf("  Base URL: %s\n", client2.baseURL)
	fmt.Printf("  超时: %v\n", client2.timeout)
	fmt.Println()

	// 方式 3：从环境变量读取
	fmt.Println("方式 3：环境变量配置")
	apiKey := os.Getenv("LIUHAO_API_KEY")
	baseURL := os.Getenv("LIUHAO_BASE_URL")
	if apiKey != "" {
		opts := []ClientOption{WithAPIKey(apiKey)}
		if baseURL != "" {
			opts = append(opts, WithBaseURL(baseURL))
		}
		_ = NewClient(opts...)
	}
	fmt.Println("  环境变量: LIUHAO_API_KEY, LIUHAO_BASE_URL")
	fmt.Println()

	return client1
}

// ======================================================================
// 示例 2：Context 使用
// ======================================================================

func demoContextUsage(client *LiuHaoClient) {
	fmt.Println("=" + stringsRepeat("=", 58))
	fmt.Println("  示例 2：Context 上下文使用")
	fmt.Println("=" + stringsRepeat("=", 58))
	fmt.Println()

	ctx := context.Background()

	// 1. 基础 context
	fmt.Println("1. 基础 Context (context.Background)")
	agent, err := client.CreateAgent(ctx, "测试 Agent", "general")
	if err != nil {
		fmt.Printf("  错误: %v\n", err)
		return
	}
	fmt.Printf("  ✓ 创建成功: %s\n", agent.ID)
	fmt.Println()

	// 2. 带超时的 context
	fmt.Println("2. 带超时的 Context (WithTimeout)")
	ctxTimeout, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()

	result, err := client.ExecuteTask(ctxTimeout, agent.ID, "你好，介绍一下你自己")
	if err != nil {
		fmt.Printf("  错误: %v\n", err)
	} else {
		fmt.Printf("  ✓ 执行成功: %s\n", result.Status)
		fmt.Printf("    耗时: %dms\n", result.LatencyMs)
	}
	fmt.Println()

	// 3. 可取消的 context
	fmt.Println("3. 可取消的 Context (WithCancel)")
	ctxCancel, cancelFn := context.WithCancel(ctx)

	// 启动一个 goroutine 并在需要时取消
	done := make(chan error, 1)
	go func() {
		_, err := client.ExecuteTask(ctxCancel, agent.ID, "长时间任务")
		done <- err
	}()

	// 模拟立即取消
	time.Sleep(10 * time.Millisecond)
	cancelFn()

	err = <-done
	if err != nil {
		fmt.Printf("  ✓ 已取消: %v\n", err)
	}
	fmt.Println()

	// 4. 带值的 context
	fmt.Println("4. 带值的 Context (WithValue)")
	type ctxKey string
	ctxValue := context.WithValue(ctx, ctxKey("request_id"), "req-12345")
	fmt.Printf("  Context 值: request_id = %v\n", ctxValue.Value(ctxKey("request_id")))
	fmt.Println()
}

// ======================================================================
// 示例 3：Agent 操作
// ======================================================================

func demoAgentOperations(client *LiuHaoClient) {
	fmt.Println("=" + stringsRepeat("=", 58))
	fmt.Println("  示例 3：Agent 操作")
	fmt.Println("=" + stringsRepeat("=", 58))
	fmt.Println()

	ctx := context.Background()

	// 创建多个 Agent
	fmt.Println("[1/4] 创建不同角色的 Agent...")
	roles := []struct {
		name string
		role string
	}{
		{"通用助手", "general"},
		{"研究助手", "researcher"},
		{"代码助手", "coder"},
		{"分析师", "analyst"},
	}

	agents := make([]*Agent, 0, len(roles))
	for _, r := range roles {
		agent, err := client.CreateAgent(ctx, r.name, r.role,
			WithTemperature(0.7),
		)
		if err != nil {
			fmt.Printf("  ✗ 创建失败: %v\n", err)
			continue
		}
		agents = append(agents, agent)
		fmt.Printf("  ✓ %s (%s) - %s\n", agent.Name, agent.Role, agent.ID)
	}
	fmt.Println()

	// 列出 Agent
	fmt.Println("[2/4] 列出所有 Agent...")
	listResult, err := client.ListAgents(ctx, 10, 0, "")
	if err != nil {
		fmt.Printf("  错误: %v\n", err)
		return
	}
	fmt.Printf("  总数: %d\n", listResult.Total)
	for _, agent := range listResult.Items {
		fmt.Printf("    • %s (%s) - %s\n", agent.Name, agent.Role, agent.Status)
	}
	fmt.Println()

	// 执行任务
	fmt.Println("[3/4] 执行任务...")
	if len(agents) > 0 {
		task := "用 Go 写一个 Hello World 程序"
		fmt.Printf("  任务: %s\n", task)

		result, err := client.ExecuteTask(ctx, agents[0].ID, task)
		if err != nil {
			fmt.Printf("  错误: %v\n", err)
		} else {
			fmt.Printf("  状态: %s\n", result.Status)
			fmt.Printf("  耗时: %dms\n", result.LatencyMs)
			fmt.Printf("  Token: %d\n", result.TokensUsed)
			fmt.Println()
			fmt.Println("  输出:")
			lines := splitLines(result.Output)
			for i, line := range lines {
				if i >= 5 {
					fmt.Println("    ...")
					break
				}
				fmt.Printf("    %s\n", line)
			}
		}
	}
	fmt.Println()

	// 获取详情
	fmt.Println("[4/4] 获取 Agent 详情...")
	if len(agents) > 0 {
		detail, err := client.GetAgent(ctx, agents[0].ID)
		if err != nil {
			fmt.Printf("  错误: %v\n", err)
		} else {
			fmt.Printf("  名称: %s\n", detail.Name)
			fmt.Printf("  角色: %s\n", detail.Role)
			fmt.Printf("  状态: %s\n", detail.Status)
			fmt.Printf("  温度: %.1f\n", detail.Temperature)
			fmt.Printf("  创建时间: %s\n", detail.CreatedAt.Format(time.RFC3339))
		}
	}
	fmt.Println()
}

// ======================================================================
// 示例 4：记忆操作
// ======================================================================

func demoMemoryOperations(client *LiuHaoClient) {
	fmt.Println("=" + stringsRepeat("=", 58))
	fmt.Println("  示例 4：记忆操作")
	fmt.Println("=" + stringsRepeat("=", 58))
	fmt.Println()

	ctx := context.Background()

	// 添加记忆
	fmt.Println("[1/3] 添加记忆...")
	memories := []struct {
		content string
		mtype   string
		tags    []string
	}{
		{"Go 是一种静态类型的编译语言", "semantic", []string{"技术", "Go"}},
		{"Goroutine 是 Go 的并发执行单元", "semantic", []string{"技术", "Go", "并发"}},
		{"Channel 用于 Goroutine 间通信", "semantic", []string{"技术", "Go", "并发"}},
		{"项目使用 Go 1.21 版本", "semantic", []string{"项目", "技术"}},
	}

	for _, m := range memories {
		mem, err := client.AddMemory(ctx, m.content, m.mtype, m.tags)
		if err != nil {
			fmt.Printf("  ✗ 错误: %v\n", err)
			continue
		}
		fmt.Printf("  + %s... (%s)\n", m.content[:30], mem.ID)
	}
	fmt.Println()

	// 搜索记忆
	fmt.Println("[2/3] 搜索记忆...")
	query := "Go 并发"
	fmt.Printf("  查询: %q\n", query)

	result, err := client.SearchMemory(ctx, query, 5)
	if err != nil {
		fmt.Printf("  错误: %v\n", err)
		return
	}

	fmt.Printf("  找到 %d 条\n", result.Total)
	fmt.Println()
	for i, r := range result.Results {
		barLen := int(r.Score * 20)
		bar := stringsRepeat("█", barLen) + stringsRepeat("░", 20-barLen)
		fmt.Printf("  %d. [%s] %.2f - %s\n",
			i+1, bar, r.Score, r.Item.Content[:40])
	}
	fmt.Println()

	// 列出记忆
	fmt.Println("[3/3] 列出记忆...")
	listResult, _ := client.ListMemories(ctx, 10, 0)
	fmt.Printf("  总数: %d\n", listResult.Total)
	fmt.Println()
}

// ======================================================================
// 示例 5：错误处理
// ======================================================================

func demoErrorHandling(client *LiuHaoClient) {
	fmt.Println("=" + stringsRepeat("=", 58))
	fmt.Println("  示例 5：错误处理")
	fmt.Println("=" + stringsRepeat("=", 58))
	fmt.Println()

	ctx := context.Background()

	fmt.Println("错误类型:")
	fmt.Println()

	// 1. 资源不存在错误
	fmt.Println("1. 资源不存在 (404)")
	_, err := client.GetAgent(ctx, "non-existent-id")
	if err != nil {
		if apiErr, ok := err.(*APIError); ok {
			fmt.Printf("   状态码: %d\n", apiErr.StatusCode)
			fmt.Printf("   错误码: %s\n", apiErr.Code)
			fmt.Printf("   消息: %s\n", apiErr.Message)
		} else {
			fmt.Printf("   错误: %v\n", err)
		}
	}
	fmt.Println()

	// 2. 类型断言判断错误类型
	fmt.Println("2. 错误类型判断")
	fmt.Println("   使用 errors.As 进行类型断言:")
	fmt.Println(`   var apiErr *APIError`)
	fmt.Println(`   if errors.As(err, &apiErr) {`)
	fmt.Println(`     switch apiErr.StatusCode {`)
	fmt.Println(`       case 401: // 认证失败`)
	fmt.Println(`       case 404: // 资源不存在`)
	fmt.Println(`       case 429: // 速率限制`)
	fmt.Println(`       case 500: // 服务器错误`)
	fmt.Println(`     }`)
	fmt.Println(`   }`)
	fmt.Println()

	// 3. Context 相关错误
	fmt.Println("3. Context 错误")
	fmt.Println("   - context.DeadlineExceeded: 超时")
	fmt.Println("   - context.Canceled: 已取消")
	fmt.Println()

	fmt.Println("错误处理最佳实践:")
	fmt.Println("  ✓ 总是检查错误返回")
	fmt.Println("  ✓ 使用 errors.Is / errors.As 进行错误判断")
	fmt.Println("  ✓ 适当包装错误信息")
	fmt.Println("  ✓ 资源清理使用 defer")
	fmt.Println("  ✓ Context 超时和取消要处理")
	fmt.Println()
}

// ======================================================================
// 辅助函数
// ======================================================================

func stringsRepeat(s string, count int) string {
	result := ""
	for i := 0; i < count; i++ {
		result += s
	}
	return result
}

func splitLines(s string) []string {
	var lines []string
	current := ""
	for _, c := range s {
		if c == '\n' {
			lines = append(lines, current)
			current = ""
		} else {
			current += string(c)
		}
	}
	if current != "" {
		lines = append(lines, current)
	}
	return lines
}

// ======================================================================
// 主函数
// ======================================================================

func main() {
	fmt.Println()
	fmt.Println("█" + stringsRepeat("█", 58) + "█")
	fmt.Println("  LiuHao AI OS - Go SDK 使用示例")
	fmt.Println("█" + stringsRepeat("█", 58) + "█")
	fmt.Println()

	// 示例 1：客户端创建
	client := demoClientCreation()

	// 示例 2：Context 使用
	demoContextUsage(client)

	// 示例 3：Agent 操作
	demoAgentOperations(client)

	// 示例 4：记忆操作
	demoMemoryOperations(client)

	// 示例 5：错误处理
	demoErrorHandling(client)

	fmt.Println("=" + stringsRepeat("=", 58))
	fmt.Println("  Go SDK 功能总结")
	fmt.Println("=" + stringsRepeat("=", 58))
	fmt.Println()
	fmt.Println("  ✓ 选项模式 (Functional Options)")
	fmt.Println("  ✓ Context 支持（超时、取消、传值）")
	fmt.Println("  ✓ Agent 操作（创建/列表/执行/删除）")
	fmt.Println("  ✓ 记忆操作（添加/搜索/列表）")
	fmt.Println("  ✓ 知识库操作")
	fmt.Println("  ✓ 结构化错误类型")
	fmt.Println("  ✓ 类型安全的泛型响应")
	fmt.Println("  ✓ 并发安全设计")
	fmt.Println()
}
