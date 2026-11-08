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
- **指向备份目录**：启动器需要知道 `backup_root`。
  - 现状风险：默认备份在 `<data_dir>/upgrade-backups`——数据目录整个被删时备份
    也一起没了，这时"打开备份目录"只会打开一个不存在的位置。
  - 建议安装器把 `backup_root` 默认放到数据目录**之外**，例如
    `%LOCALAPPDATA%\LiuHao\backups` 或 `D:\LiuHaoBackups`，并写入 marker /
    注册表；若用户已有外部备份，启动器应回退提示"最近一次导出/外部备份"。
- 只读打开旧目录做一次"是否可恢复"的诊断可以，但不得写入。

## 7. 验收清单（请安装器线落地后逐条跑）

1. 正常安装 → `{app}\install-ok.marker` 存在、注册表 `InstallCompleted=1`、数据目录存在。
2. 把数据目录改名（盘符仍在）→ 启动：必须弹 `DATA_MISSING`，目录**没有**被重新创建。
3. 恢复目录 → 启动正常，账号/员工/知识库都在。
4. 删除 `install-ok.marker` 与 `InstallCompleted`（模拟首次安装）→ 允许建目录 + 首启向导。
5. 旧版覆盖升级（无 marker，但注册表 `DataDir` 有值）→ 数据目录缺失时仍判 `DATA_MISSING`。
6. 便携版/开发树 → 行为不变。
7. 把数据目录放在已拔掉的盘符上 → 仍按现有 fail-closed 报错，不建目录。
