/**
 * JavaScript SDK 使用示例 - LiuHao AI OS
 *
 * 本示例演示 JavaScript/Node.js SDK 的使用：
 * 1. 客户端初始化
 * 2. Agent 管理
 * 3. 记忆操作
 * 4. Promise 风格 API
 * 5. 错误处理
 *
 * 运行方式：
 *   node examples/sdk/javascript_usage.js
 *
 * 注意：这是 SDK 使用示例，展示 API 调用方式。
 * 实际使用需要安装 SDK 包并配置 API Key。
 */

// ======================================================================
// LiuHao JavaScript SDK 客户端
// ======================================================================

class LiuHaoClient {
  /**
   * LiuHao AI OS JavaScript SDK 客户端。
   *
   * @param {Object} options - 配置选项
   * @param {string} options.apiKey - API 密钥
   * @param {string} options.baseUrl - API 基础 URL
   * @param {number} options.timeout - 请求超时（毫秒）
   */
  constructor(options = {}) {
    this.apiKey = options.apiKey || process.env.LIUHAO_API_KEY || '';
    this.baseUrl = options.baseUrl || process.env.LIUHAO_BASE_URL || 'http://localhost:8000/api/v1';
    this.timeout = options.timeout || 30000;

    // 模拟模式
    this._mockMode = !this.apiKey;
    this._mockAgents = {};
    this._mockMemories = [];
    this._mockKnowledge = [];
    this._agentCounter = 0;

    if (this._mockMode) {
      console.log('  ⚠️  API Key 未配置，使用模拟模式');
    }
  }

  // ==================================================================
  // Agents API
  // ==================================================================

  /**
   * 创建 Agent。
   *
   * @param {Object} params
   * @param {string} params.name - Agent 名称
   * @param {string} params.role - 角色
   * @param {string} [params.systemPrompt] - 系统提示
   * @param {number} [params.temperature] - 温度
   * @returns {Promise<Object>} 创建的 Agent
   */
  async createAgent({ name, role = 'general', systemPrompt = '', temperature = 0.7 }) {
    if (this._mockMode) {
      this._agentCounter++;
      const agentId = `agent-${String(this._agentCounter).padStart(4, '0')}`;
      const agent = {
        id: agentId,
        name,
        role,
        status: 'idle',
        systemPrompt: systemPrompt || `你是一个${role}角色的 AI 助手。`,
        temperature,
        createdAt: new Date().toISOString(),
      };
      this._mockAgents[agentId] = agent;
      return agent;
    }

    return this._request('POST', '/agents', {
      name,
      role,
      system_prompt: systemPrompt,
      temperature,
    });
  }

  /**
   * 获取 Agent 详情。
   */
  async getAgent(agentId) {
    if (this._mockMode) {
      return this._mockAgents[agentId] || null;
    }
    return this._request('GET', `/agents/${agentId}`);
  }

  /**
   * 列出 Agent。
   *
   * @param {Object} [params]
   * @param {number} [params.limit=20]
   * @param {number} [params.offset=0]
   * @param {string} [params.role]
   */
  async listAgents({ limit = 20, offset = 0, role = null } = {}) {
    if (this._mockMode) {
      let items = Object.values(this._mockAgents);
      if (role) {
        items = items.filter(a => a.role === role);
      }
      const total = items.length;
      const paginated = items.slice(offset, offset + limit);
      return { items: paginated, total, limit, offset };
    }

    const params = new URLSearchParams({ limit, offset });
    if (role) params.set('role', role);
    return this._request('GET', `/agents?${params.toString()}`);
  }

  /**
   * 执行任务。
   *
   * @param {string} agentId
   * @param {string} task
   * @param {Object} [options]
   * @param {boolean} [options.stream] - 是否流式
   */
  async executeTask(agentId, task, { stream = false } = {}) {
    if (this._mockMode) {
      const agent = this._mockAgents[agentId];
      if (!agent) throw new Error(`Agent ${agentId} not found`);

      agent.status = 'busy';

      // 模拟延迟
      await new Promise(r => setTimeout(r, 100));

      agent.status = 'idle';

      return {
        output: `这是对任务 "${task.substring(0, 30)}..." 的模拟回答。\n\nAgent: ${agent.name}\n角色: ${agent.role}\n\n在真实环境中，这里会返回 AI 生成的实际回答。`,
        status: 'completed',
        latencyMs: 1234,
        tokensUsed: 150,
        taskId: `task-${Date.now()}`,
      };
    }

    return this._request('POST', `/agents/${agentId}/execute`, { task, stream });
  }

  /**
   * 删除 Agent。
   */
  async deleteAgent(agentId) {
    if (this._mockMode) {
      if (agentId in this._mockAgents) {
        delete this._mockAgents[agentId];
        return true;
      }
      return false;
    }
    await this._request('DELETE', `/agents/${agentId}`);
    return true;
  }

  // ==================================================================
  // Memory API
  // ==================================================================

  /**
   * 添加记忆。
   */
  async addMemory({ content, type = 'semantic', tags = [], metadata = {}, agentId = null }) {
    if (this._mockMode) {
      const id = `mem-${String(this._mockMemories.length + 1).padStart(4, '0')}`;
      const memory = {
        id,
        content,
        type,
        tags,
        metadata,
        agentId,
        createdAt: new Date().toISOString(),
      };
      this._mockMemories.push(memory);
      return memory;
    }

    return this._request('POST', '/memory', {
      content, type, tags, metadata, agent_id: agentId,
    });
  }

  /**
   * 搜索记忆。
   */
  async searchMemory(query, { limit = 10, type = null } = {}) {
    if (this._mockMode) {
      const queryLower = query.toLowerCase();
      const results = [];

      for (const mem of this._mockMemories) {
        if (type && mem.type !== type) continue;

        const contentLower = mem.content.toLowerCase();
        let score = 0;

        if (queryLower === contentLower) {
          score = 1.0;
        } else if (contentLower.includes(queryLower)) {
          score = 0.8;
        } else {
          const queryWords = new Set(queryLower.split(' '));
          const contentWords = new Set(contentLower.split(' '));
          const intersection = [...queryWords].filter(w => contentWords.has(w));
          score = intersection.length / queryWords.size;
        }

        if (score > 0) {
          results.push({ ...mem, score });
        }
      }

      results.sort((a, b) => b.score - a.score);
      return { results: results.slice(0, limit), total: results.length, query };
    }

    const params = new URLSearchParams({ q: query, limit });
    if (type) params.set('type', type);
    return this._request('GET', `/memory/search?${params.toString()}`);
  }

  /**
   * 列出记忆。
   */
  async listMemories({ limit = 20, offset = 0, agentId = null } = {}) {
    if (this._mockMode) {
      let items = this._mockMemories;
      if (agentId) {
        items = items.filter(m => m.agentId === agentId);
      }
      return {
        items: items.slice(offset, offset + limit),
        total: items.length,
      };
    }

    const params = new URLSearchParams({ limit, offset });
    if (agentId) params.set('agent_id', agentId);
    return this._request('GET', `/memory?${params.toString()}`);
  }

  // ==================================================================
  // Knowledge API
  // ==================================================================

  /**
   * 添加文档到知识库。
   */
  async addDocument({ title, content, source = '', tags = [] }) {
    if (this._mockMode) {
      const id = `doc-${String(this._mockKnowledge.length + 1).padStart(4, '0')}`;
      const doc = {
        id,
        title,
        content,
        source,
        tags,
        chunkCount: Math.ceil(content.length / 500),
        createdAt: new Date().toISOString(),
      };
      this._mockKnowledge.push(doc);
      return doc;
    }

    return this._request('POST', '/knowledge/documents', { title, content, source, tags });
  }

  /**
   * 语义搜索知识库。
   */
  async searchKnowledge(query, { limit = 5, minScore = 0.5 } = {}) {
    if (this._mockMode) {
      const results = this._mockKnowledge.map(doc => {
        // 模拟相似度
        const seed = query + doc.id;
        let hash = 0;
        for (let i = 0; i < seed.length; i++) {
          hash = ((hash << 5) - hash) + seed.charCodeAt(i);
          hash |= 0;
        }
        const score = minScore + Math.abs(hash % 100) / 100 * (1 - minScore);
        return { document: doc, score, snippet: doc.content.substring(0, 200) + '...' };
      });

      results.sort((a, b) => b.score - a.score);
      return { results: results.slice(0, limit), total: results.length, query };
    }

    const params = new URLSearchParams({ q: query, limit, min_score: minScore });
    return this._request('GET', `/knowledge/search?${params.toString()}`);
  }

  // ==================================================================
  // 内部方法
  // ==================================================================

  async _request(method, path, data = null, params = null) {
    // 实际实现使用 fetch 或 axios
    throw new Error('Real API calls require API key configuration');
  }
}


// ======================================================================
// 示例 1：客户端初始化
// ======================================================================

function demoInitialization() {
  console.log('=' .repeat(60));
  console.log('  示例 1：客户端初始化');
  console.log('='.repeat(60));
  console.log();

  // 方式 1：直接传参
  console.log('方式 1：直接传入参数');
  const client1 = new LiuHaoClient({
    apiKey: 'your-api-key-here',
    baseUrl: 'https://api.liuhao.example.com/v1',
    timeout: 30000,
  });
  console.log(`  Base URL: ${client1.baseUrl}`);
  console.log(`  超时: ${client1.timeout}ms`);
  console.log();

  // 方式 2：环境变量
  console.log('方式 2：使用环境变量');
  console.log('  设置环境变量:');
  console.log('    LIUHAO_API_KEY=your-api-key');
  console.log('    LIUHAO_BASE_URL=https://api.liuhao.example.com/v1');
  console.log();

  const client2 = new LiuHaoClient();
  console.log(`  模式: ${client2._mockMode ? '模拟' : '真实API'}`);
  console.log();

  return client2;
}


// ======================================================================
// 示例 2：Agent 操作
// ======================================================================

async function demoAgentOperations(client) {
  console.log('='.repeat(60));
  console.log('  示例 2：Agent 操作');
  console.log('='.repeat(60));
  console.log();

  // 创建 Agent
  console.log('[1/4] 创建 Agent...');
  const agent = await client.createAgent({
    name: '我的 JS 助手',
    role: 'general',
    systemPrompt: '你是一个友好的 AI 助手。',
    temperature: 0.7,
  });
  console.log(`  ✓ 已创建: ${agent.name} (ID: ${agent.id})`);
  console.log(`    角色: ${agent.role}`);
  console.log();

  // 创建更多 Agent
  console.log('[2/4] 创建不同角色的 Agent...');
  const researcher = await client.createAgent({ name: '研究助手', role: 'researcher' });
  const coder = await client.createAgent({ name: '代码助手', role: 'coder' });
  console.log(`  ✓ 已创建 2 个 Agent`);
  console.log();

  // 列出 Agent
  console.log('[3/4] 列出所有 Agent...');
  const listResult = await client.listAgents({ limit: 10 });
  console.log(`  总数: ${listResult.total}`);
  for (const a of listResult.items) {
    console.log(`    • ${a.name} (${a.role}) - ${a.status}`);
  }
  console.log();

  // 执行任务
  console.log('[4/4] 执行任务...');
  const task = '介绍一下 JavaScript 的异步编程';
  console.log(`  任务: ${task}`);
  console.log('  执行中...');

  const result = await client.executeTask(agent.id, task);
  console.log(`  状态: ${result.status}`);
  console.log(`  耗时: ${result.latencyMs}ms`);
  console.log(`  Token: ${result.tokensUsed}`);
  console.log();
  console.log('  回答:');
  const lines = result.output.split('\n').slice(0, 5);
  for (const line of lines) {
    console.log(`    ${line}`);
  }
  console.log();
}


// ======================================================================
// 示例 3：记忆操作
// ======================================================================

async function demoMemoryOperations(client) {
  console.log('='.repeat(60));
  console.log('  示例 3：记忆操作');
  console.log('='.repeat(60));
  console.log();

  // 添加记忆
  console.log('[1/3] 添加记忆...');
  const memories = [
    { content: '用户喜欢深色主题', tags: ['偏好', 'UI'] },
    { content: '项目截止日期是本周五', tags: ['项目', '截止日期'] },
    { content: 'JavaScript 是一门动态类型语言', tags: ['技术', 'JS'] },
    { content: 'React 使用虚拟 DOM', tags: ['技术', '前端', 'React'] },
    { content: 'Node.js 基于 V8 引擎', tags: ['技术', '后端', 'Node.js'] },
  ];

  for (const mem of memories) {
    const result = await client.addMemory(mem);
    console.log(`  + ${mem.content.substring(0, 40)}... (ID: ${result.id})`);
  }
  console.log();

  // 搜索记忆
  console.log('[2/3] 搜索记忆...');
  const query = 'JavaScript 技术';
  console.log(`  查询: "${query}"`);

  const searchResult = await client.searchMemory(query);
  console.log(`  找到 ${searchResult.total} 条相关记忆`);
  console.log();

  for (const item of searchResult.results) {
    const bar = '█'.repeat(Math.floor(item.score * 20)) + '░'.repeat(20 - Math.floor(item.score * 20));
    console.log(`    [${bar}] ${item.score.toFixed(2)} - ${item.content.substring(0, 50)}...`);
  }
  console.log();

  // 列出记忆
  console.log('[3/3] 列出记忆...');
  const listResult = await client.listMemories({ limit: 10 });
  console.log(`  总数: ${listResult.total}`);
  console.log();
}


// ======================================================================
// 示例 4：知识库操作
// ======================================================================

async function demoKnowledgeOperations(client) {
  console.log('='.repeat(60));
  console.log('  示例 4：知识库操作');
  console.log('='.repeat(60));
  console.log();

  // 添加文档
  console.log('[1/2] 添加文档...');
  const docs = [
    { title: '产品介绍', content: 'LiuHao AI OS 是一个多智能体操作系统...', source: '官网' },
    { title: '快速入门', content: '第一步：安装 SDK。第二步：配置 API Key...', source: '文档' },
    { title: '常见问题', content: 'Q: 如何获取 API Key？A: 在控制台的设置页面...', source: 'FAQ' },
  ];

  for (const doc of docs) {
    const result = await client.addDocument(doc);
    console.log(`  + ${doc.title} (${result.chunkCount} 个分块)`);
  }
  console.log();

  // 语义搜索
  console.log('[2/2] 语义搜索...');
  const query = '怎么开始使用这个产品？';
  console.log(`  查询: "${query}"`);

  const result = await client.searchKnowledge(query, { limit: 3 });
  console.log(`  找到 ${result.total} 个相关文档`);
  console.log();

  for (let i = 0; i < result.results.length; i++) {
    const item = result.results[i];
    const bar = '█'.repeat(Math.floor(item.score * 20));
    console.log(`  ${i + 1}. [${bar}] ${item.score.toFixed(2)} - ${item.document.title}`);
    console.log(`     ${item.snippet.substring(0, 60)}...`);
  }
  console.log();
}


// ======================================================================
// 示例 5：Promise 链式调用与错误处理
// ======================================================================

async function demoPromiseAndErrorHandling(client) {
  console.log('='.repeat(60));
  console.log('  示例 5：Promise 链式调用与错误处理');
  console.log('='.repeat(60));
  console.log();

  // Promise 链式调用
  console.log('Promise 链式调用:');
  console.log();

  const agent = await client.createAgent({ name: '链式测试', role: 'general' });
  console.log(`  1. 创建 Agent: ${agent.id}`);

  const taskResult = await client.executeTask(agent.id, 'Hello');
  console.log(`  2. 执行任务: ${taskResult.status}`);

  await client.deleteAgent(agent.id);
  console.log(`  3. 删除 Agent: 完成`);
  console.log();

  // async/await 错误处理
  console.log('错误处理 (try/catch):');
  console.log();

  try {
    await client.getAgent('non-existent-id');
  } catch (error) {
    console.log(`  ✓ 捕获到错误: ${error.message}`);
    console.log(`    类型: ${error.constructor.name}`);
  }
  console.log();

  // Promise.all 并发
  console.log('Promise.all 并发操作:');
  console.log();

  const start = Date.now();
  const createPromises = [
    client.createAgent({ name: '并发-1', role: 'general' }),
    client.createAgent({ name: '并发-2', role: 'general' }),
    client.createAgent({ name: '并发-3', role: 'general' }),
  ];

  const agents = await Promise.all(createPromises);
  const duration = Date.now() - start;

  console.log(`  并发创建 ${agents.length} 个 Agent`);
  console.log(`  耗时: ${duration}ms`);
  console.log();
}


// ======================================================================
// 主函数
// ======================================================================

async function main() {
  console.log('\n' + '█'.repeat(60));
  console.log('  LiuHao AI OS - JavaScript SDK 使用示例');
  console.log('█'.repeat(60));
  console.log();

  try {
    // 示例 1：初始化
    const client = demoInitialization();

    // 示例 2：Agent 操作
    await demoAgentOperations(client);

    // 示例 3：记忆操作
    await demoMemoryOperations(client);

    // 示例 4：知识库操作
    await demoKnowledgeOperations(client);

    // 示例 5：Promise 和错误处理
    await demoPromiseAndErrorHandling(client);

    console.log('='.repeat(60));
    console.log('  JavaScript SDK 功能总结');
    console.log('='.repeat(60));
    console.log();
    console.log('  ✓ 客户端初始化');
    console.log('  ✓ Agent 管理 (创建/列表/执行/删除)');
    console.log('  ✓ 记忆操作 (添加/搜索/列表)');
    console.log('  ✓ 知识库 (添加文档/语义搜索)');
    console.log('  ✓ Promise 风格 API');
    console.log('  ✓ async/await 语法');
    console.log('  ✓ 错误处理');
    console.log('  ✓ 并发操作 (Promise.all)');
    console.log();
  } catch (error) {
    console.error('\n❌ 运行出错:', error.message);
    console.error(error.stack);
    process.exit(1);
  }
}

// 运行主函数
if (require.main === module) {
  main();
}

module.exports = { LiuHaoClient };
