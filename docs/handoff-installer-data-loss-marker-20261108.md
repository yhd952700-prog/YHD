# 交接单：区分「首次安装」与「数据目录被删」（给安装器线）

- 交接时间：2026-11-08
- 提交方：首启/数据目录回退线
- 接收方：安装器线（`installer/liuhao.iss` 与 `scripts/launch_installed.py` 由你们独占修改）
- 前置声明：**本单没有改这两个文件**。下面只是结论与建议，供你们落地。

---

## 1. 要解决的边界

当前行为：`data-dir.txt` 指向的数据目录不存在时，启动器会按"首次启动"建目录 + 建库。

- 盘符不在（D 盘拔掉/换机）：`mkdir` 失败，启动器已经 fail-closed，行为正确。
- **盘符还在、但数据目录被删/被改名**：`mkdir` 成功 → 安静地建出一个空库。
  用户看到的是"账号、员工、知识库全没了"，会以为数据被鎏灏清空；实际是
  程序把"目录不存在"误判成了"第一次安装"。

根因：`data-dir.txt` 只记录"数据在哪"，不记录"这个位置曾经有过数据/安装已完成"。
目录消失后，安装器与首启向导都无法区分"新装"和"数据丢了"。

## 2. 需要的标记

### 2.1 文件标记（主依据）

- 路径：`{app}\install-ok.marker`，**放在程序安装目录**，与 `data-dir.txt` 同级。
- 为什么不放数据目录：数据目录一旦被删，放在里面的标记会跟着消失，正好失去
  判断依据。程序目录随覆盖升级保留/迁移，是唯一可靠的"安装完成"证据。
- 内容（UTF-8，带 BOM，路径按 `data-dir.txt` 的同一编码规则）：

```json
{
  "schema": "liuhao.install-ok/1",
  "data_dir": "D:\\LiuHaoData",
  "data_dir_created_at": "2026-11-08T10:00:00+08:00",
  "install_completed_at": "2026-11-08T10:00:00+08:00",
  "first_run_completed": false
}
```

- `data_dir` 冗余记录一份当时的数据目录，用于校验 `data-dir.txt` 是否被手改。
- `first_run_completed` 由首启向导在 `commit()` 成功后更新为 `true`；安装器首次
  写入时是 `false`。启动器判断"数据丢了"只要求文件存在，不要求首启完成。

### 2.2 注册表冗余（兼容旧版本升级）

- 在现有卸载键旁增加：
  - `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\LiuHao_is1`
    - `DataDir`（现有逻辑已写）
    - 新增 `InstallCompleted`（DWORD/字符串 `1`）
- 作用：从"还没有 `install-ok.marker` 的旧版"覆盖升级上来时，注册表 `DataDir`
  存在 + 目录缺失，也要判定为"数据丢了"，避免只有新版安装才受保护。

## 3. 什么时候写

| 时机 | 谁写 | 写什么 |
| --- | --- | --- |
| `CurStepChanged(ssPostInstall)`，`[Dirs]` 已创建数据目录、`data-dir.txt` 已写成功 | 安装器 | `install-ok.marker`（`first_run_completed=false`）+ 注册表 `InstallCompleted=1` |
| 首启向导 `commit()` 成功（账号、密钥、`data-dir.txt` 已落盘） | 首启向导/启动器 | 更新 `install-ok.marker` 的 `first_run_completed=true` |
| 用户在设置里改数据目录并迁移成功 | 启动器/设置 | 更新 marker 的 `data_dir` 与 `data_dir_created_at`，**不要删除旧目录** |

只有在数据目录真实创建成功之后才写 marker：marker 存在本身就代表"安装时这个
目录确实创建过"。

## 4. 启动时如何区分

```
data_dir = data-dir.txt（无效再回退注册表 DataDir，再回退 %LOCALAPPDATA%\LiuHao\data）
installed = <app>\runtime\python.exe 存在
had_data  = install-ok.marker 存在
            或 注册表 InstallCompleted=1
            或 注册表 DataDir 有值（旧版升级兼容）

if not data_dir.exists():
    if installed and had_data:
        -> DATA_MISSING：拒绝启动，不建任何目录（见第 5 节）
    else:
        -> FRESH：按现有首启流程创建目录 + 向导
else:
    -> 正常启动（目录存在但为空，仍走首启向导，这是新装后的正常状态）
```

注意：便携版/开发树（无 `runtime/python.exe`）保持现有行为，不参与该判定。

## 5. 两种情况的文案

### FRESH（首次安装，允许建空库）

沿用现有首启向导，不增加额外提示。

### DATA_MISSING（数据丢了，拒绝建空库）

窗口标题：

```
鎏灏的数据目录不见了
```

正文（原话建议）：

```
找不到数据目录：{data_dir}

这通常意味着数据被移动、删除，或原来的磁盘没有接上。
为保护数据，鎏灏不会在这里新建空数据库。

你可以：
1. 把备份恢复到上面的目录（备份目录：{backup_root}）；
2. 选择已有的数据目录；
3. 确认从零开始（会新建空库，原数据无法恢复）。
```

按钮：`打开备份目录`（默认焦点）、`选择已有数据目录`、`重新初始化（新建空库）`。
`重新初始化`必须二次确认：

```
确定要从零开始吗？这会新建一个空数据库，原数据无法通过鎏灏恢复。
```

命令行/计划任务版（无窗口）输出到 stderr 并返回非零：

```
[FAIL] 数据目录不见了：{data_dir}
       为避免覆盖/误导，本次不新建空库。
       备份目录：{backup_root}
       如确认从零开始，请显式设置 LIUHAO_ALLOW_FRESH_DATA=1 后重试。
```

## 6. 数据丢了的正确动作

- **绝不 `mkdir`**：不创建数据目录、不建库、不建 `config/`、不写锁文件、不把日志
  轮转进去。
- **指向备份目录**：启动器需要知道 `backup_root`，且这个位置必须是**数据目录被删
  之后仍然活着**的目录。
  - 第三轮已把升级备份的默认位置改到数据目录之外（实现细节见第 8 节），所以
    "打开备份目录"不再打开一个和数据一起消失的位置。
  - 启动器不要自己猜备份位置：应读升级状态里的真实落点
    （`GET /v1/upgrade/status` 返回体的 `backup.root` 与 `backup.data_dir`），
    并把该路径原样显示在 `DATA_MISSING` 文案里。`backup.same_volume=true` 时
    必须额外说明"备份与数据在同一块盘，盘坏了会一起丢"，不能只显示路径。
  - 若 `backup.root` 不存在（用户手删/换机没带走），回退提示"最近一次导出/外部
    备份"，不要声称"备份在这里"。
- 只读打开旧目录做一次"是否可恢复"的诊断可以，但不得写入。

## 7. 验收清单（请安装器线落地后逐条跑）

1. 正常安装 → `{app}\install-ok.marker` 存在、注册表 `InstallCompleted=1`、数据目录存在。
2. 把数据目录改名（盘符仍在）→ 启动：必须弹 `DATA_MISSING`，目录**没有**被重新创建。
3. 恢复目录 → 启动正常，账号/员工/知识库都在。
4. 删除 `install-ok.marker` 与 `InstallCompleted`（模拟首次安装）→ 允许建目录 + 首启向导。
5. 旧版覆盖升级（无 marker，但注册表 `DataDir` 有值）→ 数据目录缺失时仍判 `DATA_MISSING`。
6. 便携版/开发树 → 行为不变。
7. 把数据目录放在已拔掉的盘符上 → 仍按现有 fail-closed 报错，不建目录。

## 8. 第三轮：备份落点（现状与遗留）

> 本节记录**已落到代码的部分**和**还没有落地的部分**，不代表整条链路已经完成。
> 第 1–7 节的 `install-ok.marker` / `InstallCompleted` / `DATA_MISSING` 仍在等待
> 安装器线接手，本节只解决"备份本身不在数据目录里"这一条。

### 8.1 已改

前端三处已写入工作树但**未入库**；后端两处在提交 `26ff4456`（`feat(upgrade):
数据版本闸、文件清单校验与启动自检`）里。

- `src/upgrade/manager.py` 新增 `default_backup_root()`。安装版（数据目录在程序
  目录之外）按下列顺序选址，每一步都用真写探针验证可写：
  1. 另一块固定本地盘 `<X>:\LiuHaoBackups`（`GetDriveTypeW == DRIVE_FIXED`，
     排除可移动盘与网络盘；多块盘取剩余空间最大的一块）；
  2. `%LOCALAPPDATA%\LiuHao\backups`（没有第二块盘时的退路，会如实标记是否与
     数据同盘）；
  3. 数据目录同级 `<data 名>-backups`（用户目录也写不了时的退路）；
  4. 以上都写不了才退回 `<data>/upgrade-backups`：保证升级仍能完成备份，界面
     如实显示同盘风险，而不是等到第一次升级才失败。
- 便携/开发形态（数据目录在程序目录内）保持 `<data>/upgrade-backups`：该形态
  语义是整目录自包含，且开发/测试不得往用户目录或其他盘写数据。
- 老备份迁移：`UpgradeManager.__init__` 在目录创建后调用
  `_migrate_legacy_backups()`，把 `<data>/upgrade-backups` 里每份含
  `backup.json` 的旧备份**自动搬走**到新位置（先复制为 `.<id>.migrating` 再
  改名，校验通过后删旧副本；中断重跑会跳过已迁移项；任一步失败只告警保留原样）。
  迁移结果写入 `<state_dir>/backup-migration.json`。
  - 选择"自动搬走"而不是"保留提示/双写"的理由：留着旧副本等于保留"数据目录一删
    备份一起没"的原缺陷；双写要维护两处、回滚还可能挑旧目录里那份，出错面更大；
    搬走之后全局只有一个备份位置可指向。
- `src/upgrade/api.py` 的 `_status_payload` 增加 `backup` 字段，来源是
  `manager.backup_summary()`：`root`、`data_dir`、`same_volume`、`data_drive`、
  `backup_drive`、`backup_count`、`last_backup_at`、`last_backup_id`、
  `migration`。
- 设置页「软件更新」卡（`apps/console/console/src/components/UpgradePanel.tsx`）
  增加备份信息块，说清三件事：备份位置、为什么放在数据目录外、换机时带走哪个
  目录；并给出是否与数据同盘、最近一次备份时间与份数、旧位置迁移提示。同盘时
  整块用 `role="alert"` 的警示样式，文案明确"数据和备份会一起丢"。
- 前端测试新增 2 个用例（非同盘三要素、同盘且无备份时的警告），共 6 个用例。
  本仓库既有测试基建直接跑到 `UpgradePanel.test.tsx` 会报 `React is not defined`
  （未改动的 `GoalStatus.test.tsx` 同样失败），属既有问题；本次用一份临时
  `esbuild.jsx='automatic'` 配置单独跑，6/6 通过后已删除该临时配置。

### 8.2 遗留（未完成）

- **安装器未写入备份路径**：`installer/liuhao.iss` 与 `scripts/launch_installed.py`
  仍不知道 `backup_root`，`install-ok.marker` / `InstallCompleted` 也没落地
  （第 2、3 节仍是待接手的方案）。`DATA_MISSING` 分支现在拿不到"该指向哪个
  备份目录"这一项。
- **启动器尚未读 `backup.root`**：第 6 节要求的"读真实落点并原样显示、同盘时
  额外警告"还没实现，需要安装器线接入。
- **只有升级备份，不是全量数据备份**：`LiuHaoBackups` 里是**升级前的代码/数据
  快照**，不是日常数据的持续备份。数据目录被删时能恢复到"上一次升级前"的状态；
  从未升级过的用户在这个位置没有可用备份。文案不能把它说成"数据备份"，否则
  用户会误以为有持续保护。
- **`same_volume` 只按盘符判断**：UNC 共享路径按共享名比较；同一物理盘的不同
  分区（例如 `D:` 与 `E:` 同盘）会被判为"不同盘"。这是已知近似，界面文案不要
  升级成"一定安全"。
- **迁移不覆盖冲突**：新位置已有同 id 备份时不重复搬（删旧副本），不做内容
  比对。旧目录里缺少 `backup.json` 的半截目录既不搬也不删，会在旧位置残留。
- **迁移失败无界面提示**：占用中/权限不足时只写日志并保留原样，用户看不到提示
  （`backup-migration.json` 只记成功搬走的数量）。
- **前端改动未入库**：`lib/upgrade.ts`、`UpgradePanel.tsx`、
  `UpgradePanel.test.tsx` 三处仍是工作树改动，本轮按约束不 `git add/commit`。

### 8.3 本轮取证（模拟，不是真机证据）

在安装版布局上（数据目录位于程序目录之外），用 `UpgradeManager` 真实创建一份
升级备份，确认落点不在数据目录内；随后清空数据目录，确认备份目录与备份文件
仍在、可枚举、可校验。实测两条路径与文件数见交接回报。该实验只验证"备份不随
数据目录一起消失"，不验证安装器/启动器的 `DATA_MISSING` 分支。
