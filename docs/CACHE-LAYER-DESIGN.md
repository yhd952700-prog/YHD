# Phase 8 — 缓存层诚实化 + 真实性能基线

> 状态：**已实现并验证**（2026-09-14）。范围是 Phase 7 之后的「性能与安全收口」第一片：
> `src/performance/cache.py` 的多层缓存。
> 纪律同前：**先实测再判断**、**附加式改动**、**默认零行为变化**、**禁止静默降级**。

## 1. 实测发现：`MultiTierCache` 是又一处"宣称三层、实际零层"的静默失败

`src/performance/` 只有 3 个文件，`MultiTierCache` **零调用方**（实测：全仓库仅
`src/performance/benchmarks.py:190,218` 用到 `LRUCache`，没有任何地方用 `MultiTierCache`；
`tests/framework/framework.py` 里的 `create_mock_performance_cache` 是个 Mock）。

| # | 缺陷 | 证据 | 后果 |
|---|------|------|------|
| D1 | **docstring 宣称 `memory -> redis -> disk`，实际只有 memory** | `cache.py:119-124` 类文档；`_ensure_initialized:131-135` 只翻一个 flag（注释自认 placeholder） | 文档即谎言 |
| D2 | **`put(tier=REDIS/DISK)` 静默什么都不做** | `put:147-152` 只有 `if tier == MEMORY:` 分支，**没有 else**；注释 `# TODO: Add Redis and Disk tier support` | 写缓存"成功"返回，实际没存 —— 读回时才发现，且无从定位 |
| D3 | **`get()` 永不查 redis/disk** | `get:137-146` memory 未命中就 `return None`；注释 `# TODO: Try Redis, then Disk` | 多层的全部价值（容量/持久性）不存在 |
| D4 | **`stats()` 宣称"all tiers"，只返回 memory** | `stats:154-157` 返回 `{"memory": ...}` | 容量规划基于假数据 |
| D5 | **`initialized` 标志无意义** | `_ensure_initialized` 立刻置 `True`，没有任何真实副作用 | 与 Phase 7b 的 `msg_bus.initialize()` 同型 |
| **D6** | **`BatchCache.put_batch` 每次调用都死锁**（写测试时才撞上；实测 `faulthandler` 抓到 `cache.py:76 in put` ← `cache.py:650 in put_batch`） | `LRUCache._lock` 是**非重入** `threading.Lock`，而 `put_batch` 先 `with self.cache._lock` 再调 `put()`（重复取同一把锁） | 批写**永久挂死**；因 `BatchCache` 零调用方 + 零测试而长期潜伏，与 D1–D5 同源 |

**修法选择**：这个模块**零调用方** ⇒ 无运行时爆炸半径，可以**放手做真**。而且两个"真"层里
**disk 层零依赖、可真机验证**（真写文件、真读回），redis 层可沿用 Phase 7b 的
「guarded import + 可注入 client」模式离线验证。所以本片**把三层都做实**，而不是把文档改小 ——
"能真做就真做"优先于"删掉承诺"。

## 2. 交付物

### 2.1 扩展 `src/performance/cache.py`

| 符号 | 职责 |
|------|------|
| `CacheTierUnavailableError(RuntimeError)` | 层不可用时的**诚实失败** |
| `DiskCache` | 真文件型层：每 key 一个 JSON 文件（文件名 = sha256(key)），带 TTL；`get/put/delete/contains/cleanup/stats/close` |
| `RedisCacheTier` | 真 redis 层：`GET`/`SETEX`（TTL）/`DEL`/`EXISTS`；**guarded import + 可注入 client**；连不上抛 `CacheTierUnavailableError` |
| `MultiTierCache`（重写） | 真级联 `memory → redis → disk`；未启用的层**抛错**而不是静默丢弃；per-tier `stats()` |
| `cache_tiers_available(...)` | 能力探针，如实报告哪几层真的可用 |

**默认零行为变化**：
- `MultiTierCache(memory_max_size=1000)` 仍可用，`get/put(key, value, ttl)`（默认 `tier=MEMORY`）语义不变。
- disk / redis 层**默认不启用**（disk 默认关是为了不在 cwd 乱建文件；redis 默认关是因为要真连服务端）。
  未启用时 `put(tier=DISK/REDIS)` **抛错并说明如何启用** —— 这是本轮唯一的行为改变，且是**修 bug**：
  旧行为是"假装写入成功"。

**级联语义（写清，不含糊）**：`get(key)` 依次查 memory → redis（若启用）→ disk（若启用）；
**命中低层时回填（promote）到 memory**，并把命中记到该层 stats。
`put(key, value, ttl, tier)` 只写指定层（不做写穿）—— 与旧签名一致，不擅自改变语义。

### 2.2 测试 `tests/performance/test_multitier_cache.py`

- **disk 层真机验证**：真写文件 → 真读回 → 真过期 → 真删除 → 真清理；断言文件确实出现在磁盘上。
- **级联**：memory 未命中 → disk 命中并**回填 memory**（第二次读应命中 memory）。
- **诚实失败**：未启用 disk/redis 时 `put(tier=...)` **必抛**；redis 不可达时抛且原因可操作。
- **redis 层真实代码路径**：注入 fake client，断言 `SETEX`/`GET`/`DEL` 的调用与 TTL 参数。
- **stats 真实性**：三层各自的 hit/miss 计数分别可查（修 D4）。
- **元验证（反橡皮图章）**：逐字复刻旧 `put`，断言它对 `tier=REDIS` **确实什么都没做**；新实现则抛错。
- **零回归**：`LRUCache` 全部既有语义（LRU 淘汰、TTL 过期、`BatchCache`）保持不变。
- **新增（测试期发现 D6）**：`BatchCache.put_batch` 死锁回归测试 —— 用「子线程 + join
  超时」让"再次死锁"表现为**失败**，而不是挂死整个 CI。

### 2.3 真实性能基线（同片交付）

`tests/performance/` 已有 `benchmark.py`（BenchmarkRunner / PerformanceAnalyzer / LoadGenerator）
与 `targets.py`。本片补一个**真跑**的微基准：LRU 命中路径与 MultiTier 命中路径的 p50/p95
吞吐量，并设**宽松但非空转**的阈值（防止"性能回退到不可用"这种沉默事故）。

⚠️ **诚实前提**：CI 机器性能波动大，阈值必须宽松到不会随机变红，只拦**数量级**退化
（例如慢了 10 倍）。基线值写死在测试里并在报告里注明**它来自本机实测**，不是承诺。

## 3. 风险 / 回滚

- `LRUCache` / `CacheEntry` / `CacheStats` / `BatchCache` 语义**不动**；唯一改动是
  `LRUCache._lock` 由 `threading.Lock` 改 `threading.RLock` —— 这是修 D6 死锁的**最小
  正确改动**（`put_batch` 的嵌套取锁在 RLock 下成立，其余路径语义不变）。
- `MultiTierCache` 零调用方 ⇒ 回归面为零；但仍写测试锁死 memory 路径的旧语义。
- 回滚 = `git checkout -- src/performance/cache.py` + 删新增测试文件。

## 4. 诚实边界（明确不做）

- **不做** cache stampede 防护 / 分布式锁 / 一致性哈希。
- **不做** redis 的 pub/sub 失效广播（那是失效协议，不是缓存本体）。
- disk 层**不做**并发多进程安全保证（只保证同进程线程安全 + 原子替换写）。
- 性能基线是**本机数字**，不是生产 SLA；本片不引入新的性能 SLA 承诺。
