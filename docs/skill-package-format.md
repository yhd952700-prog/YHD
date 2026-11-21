# 技能包格式与生命周期（大升级第二季 V7）

本文是技能包生态的正式规格：包长什么样、怎么装/升/卸、权限怎么算。
实现分布：解析 `src/gateway/skill_import.py`、安全落盘
`src/gateway/skill_install.py`、注册表 `src/gateway/skill_store.py`、
运行 `src/gateway/skill_runtime.py`、生命周期服务
`src/gateway/skill_packages.py`、HTTP 面 `src/gateway/skills.py`、
聊天工具 `src/ai/tools/kernel_tools.py`（`liuhao.skill.*`）。

## 1. 包的形态

一个技能包是以下三者之一：

| 形态 | 内容 | 说明 |
|---|---|---|
| `.zip` | 根目录（或一层同名目录）含 `SKILL.md`，可带 `scripts/` 等文件 | 正式分发形态 |
| 目录 | 同上，未压缩 | 本机安装用（Windows 无法用浏览器上传目录，走本机路径安装） |
| 单文件 | 一份 `SKILL.md` 或一份 `.json` 清单 | 无脚本或脚本不在包内时可用 |

落盘安全（既有 `skill_install` 硬约束，不因 V7 放宽）：路径穿越/绝对路径/
符号链接/盘符一律拒绝整包；条目数、单文件、解压总量有上限；先暂存校验、
后整体提交；覆盖安装失败自动回滚到旧版；文件扩展名白名单。

## 2. 清单字段

`SKILL.md` 用 YAML frontmatter；`.json` 用同名字段。V7 起正式字段如下
（★ = V7 新增）：

| 字段 | 必填 | 语义 |
|---|---|---|
| `name` | 是 | 技能名；同名不可重复安装，升级必须同名 |
| `description` | 否 | 一句话说明，列表与聊天目录展示用 |
| `version` | 否 | 版本号，建议 `x.y.z`；升级后注册表同步为新值 |
| `author` ★ | 否 | 作者声明（顶层键，或 `metadata.author`）；只展示，不做身份核验 |
| `dependencies` ★ | 否 | 依赖的**已装技能**，见 §3 |
| `license` | 否 | 许可证声明，只展示 |
| `capabilities` | 否 | 需要的既有执行器能力（JSON 清单）；风险由服务端按能力目录推导，不采信包自报 |
| `permissions` | 否 | 包**声明**的权限面；只展示与登记，不产生实际权限 |
| `allowed-tools` | 否 | 同上，只存原文 |
| `runtime.entrypoints` | 否 | 可执行入口：`id` / `interpreter`（python/node/powershell）/ `script`（相对包根路径） |
| `runtime.limits` | 否 | 资源上限，只能在服务端硬上限内收紧，不能放大 |
| `metadata` | 否 | 其余扩展键值，原样保存，不参与任何判定 |

未知字段一律进导入报告（`import_report`）标 `unsupported`，不假装支持。
MCP server 配置不是技能包，会被识别并拒绝（正确入口是工具中心）；
`runtime.mode: service`（常驻进程）明确不支持并报错。

## 3. 依赖声明 ★

`dependencies` 是列表，每条三种写法等价：

```yaml
dependencies:
  - 基础技能名              # 任意版本
  - 基础技能名>=1.2.0       # 最低版本
  - name: 基础技能名        # 对象写法
    version: ">=1.2.0"
```

规则：

- 只支持 `>=最低版本` 一种约束；解析不了的声明直接报错，不静默丢弃。
- 安装/升级时逐条核对**已登记**技能（启用与否不论）：缺失 → 拒绝并点名；
  版本不足或版本无法比较 → 拒绝并说明。绝不半装、绝不先装上再说。
- 卸载守卫：被其它已登记技能声明为依赖的技能，卸载/删除会被拒绝并点名
  依赖方（与"仍绑定员工时 409 点名"同一口径）。
- 上限 20 条。

## 4. 生命周期

| 动作 | HTTP | 聊天（一句话） | 语义 |
|---|---|---|---|
| 预览 | `POST /v1/skills/inspect` | `inspect_skill_package(path)` | 只解析：摊开名称/版本/作者/入口/声明权限/依赖核对/文件清单/包哈希。不落盘、不建记录、不产生授权 |
| 安装 | `POST /v1/skills/import`、`/install/local` | `install_skill_package(path, confirm)` | 依赖核对 → 建记录（**默认未启用**）→ 包安全落盘 → 登记运行包；失败回滚 |
| 升级 | `POST /v1/skills/{id}/upgrade` | `upgrade_skill_package(id, path, confirm)` | 必须同名；新版依赖先核对；覆盖落盘失败回滚旧版；注册表版本/作者/依赖/说明同步新值；包哈希变化使已记住的运行授权自动失效 |
| 卸载 | `DELETE /v1/skills/{id}` | `uninstall_skill_package(id, confirm)` | 守卫：仍绑定员工 → 拒；被声明为依赖 → 拒；通过后删记录、清绑定残留、删包目录、撤销全部已记住授权 |
| 列表 | `GET /v1/skills`、`GET /v1/skills/{id}/files` | `list_skill_packages()` | 注册表 + 包哈希/文件清单 |
| 导出 | `GET /v1/skills/{id}/export` | —（技能页操作） | 已装包重新打包为 zip 分发 |
| 运行 | `POST /v1/skills/{id}/run` | `run_skill(id, entrypoint)` | 既有语义不变，见 §5 |

聊天写操作（装/升/卸）带 `confirm` 二次校验：不带 `confirm=true` 时工具
只返回待确认状态 + 权限声明，模型必须把声明转告用户、等用户亲口确认后
才许带 `confirm=true` 执行（与 `apply_update` 同口径）。聊天工具受技能页
注册闸管理：列表/预览 A 档默认开，装/升/卸 B 档默认关，Owner 在技能页
开启后才进聊天工具集。

## 5. 安全模型（不因生态开放而降级）

- **声明 ≠ 授权**：包声明的权限只用于展示与登记。脚本能不能跑，只由
  `runtime.entrypoints` 声明 + Boss 首次运行审批决定；审批按
  （技能, 入口, 包哈希, 脚本哈希）绑定，包一变自动失效。
- **没有静默提权路径**：安装/升级不启用技能、不产生审批、不改能力目录；
  风险档位由服务端按能力目录推导，高危/未知风险启用时另有显式确认。
- **运行期隔离**沿用 `skill_runtime` 既有受限执行（低完整性 + Job Object），
  本版不宣称沙箱；脚本执行平台边界以运行时说明为准。
- **凭据纪律**：包内文件、清单、日志都不应出现 Key/令牌；生命周期代码
  不接触凭据，日志只记技能 id、来源与操作者。
- **默认不预装**任何第三方包；新机制默认不改变既有技能行为（回归测试
  覆盖：`tests/gateway/test_skill_packages_v7.py` + 既有技能测试集）。

## 6. 版本与兼容

- 包格式向后兼容：V7 之前安装的技能没有 `author`/`dependencies` 字段，
  读取时按空处理，行为不变。
- 注册表记录新增字段同理：旧记录缺键按默认值兜底，不需要迁移脚本。
