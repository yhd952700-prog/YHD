# 安装器 D 盘回退路径审计（2026-11-08）

> 审计对象：工作区当前版本的 [installer/liuhao.iss](../installer/liuhao.iss)、[scripts/launch_installed.py](../scripts/launch_installed.py)、[src/upgrade/manager.py](../src/upgrade/manager.py)、[scripts/first_run_gui.py](../scripts/first_run_gui.py)。
> 注意：`installer/liuhao.iss`、`scripts/launch_installed.py`、`src/upgrade/manager.py` 在工作区里**有未提交改动**（HEAD 仍是旧的"静默回退 C 盘"版本）。本文审计的是**工作区当前内容**，不是 HEAD。
> 审计方式：只读 + 临时目录探针。**未编译安装包、未执行 setup.exe、未改动 `dist/`、未提交 commit**。
> 标注约定：**实测** = 本机 Python 探针直接观测（探针目录已删除）；**代码** = 逐行读实现可确定；**推断** = 依赖 Inno 运行时行为、本机无法在不运行 setup.exe 的前提下复现。

---

## 0. 结论摘要

1. **没有 D 盘时，交互安装不会直接失败**：程序目录回退到 `%LOCALAPPDATA%\Programs\LiuHao`（普通用户）或 `%ProgramFiles%\LiuHao`（以管理员运行），欢迎页写明原因；数据目录**留空**，用户必须在「数据目录」页选一个可写目录才能继续。不会装到 `Program Files` 后再把数据也塞进去（除非用户自己选）。
2. **没有 D 盘且静默安装、又没给 `/DATA=` 时，安装会在复制文件前中止**，提示"没有可用的数据目录，安装已停止……静默安装请加参数 /DATA=…"。这是一次干净失败，不会留下"装完了但数据写不进去"的状态；但企业静默部署必须显式给 `/DATA`。
3. **D 盘存在但不可用时，程序目录一律回退、数据目录一律要求用户选择**；只读/写保护/BitLocker 锁/网络映射盘/可移动盘都会在写探针这一步被判为不可用。**安装阶段**没有任何一种情况会把数据静默写到 C 盘；静默安装会直接中止（见第 2 条）。
4. **现有 3 个真实缺口（必须改）**：
   - **数据目录所在盘的空间从未检查**：只查程序盘。用户把数据选到另一个快满的盘时，安装能成功，首次启动才失败。
   - **升级时对"旧数据目录"的判断会误判**：`DirWritable()` 对**不存在的目录**返回 True（只要最近父目录可写）。旧 D 盘被拔掉、盘符被新盘复用后，升级会静默把数据指到一个新建的空目录——账号"消失"，旧数据留在原盘。
   - **首启向导改数据目录后，注册表 `DataDir` 不更新**：覆盖升级和卸载优先读注册表，会回到**旧目录**；新目录的数据不丢，但会"失联"。
5. **没有 D 盘时的回退位置推荐 `%LOCALAPPDATA%\LiuHao\data`**（即 `{localappdata}`，不要手拼 `C:\Users\<名字>\...`）。它天然可写、路径可见、不在 Program Files、长度安全（实测中文+空格路径可无损往返）。建议把它**预填**进「数据目录」页而不是留空，用户仍可改选——这同时解决静默安装必须手写 `/DATA` 的问题。
6. **文档与当前实现已不一致**：[INSTALL-WINDOWS.md](INSTALL-WINDOWS.md) 第 35-38、65 行和 [BEFORE-INSTALL.txt](../installer/assets/BEFORE-INSTALL.txt) 第 64-66 行仍写"D 盘不可用时数据自动回退 `%LOCALAPPDATA%`"，而当前实现是"让用户选"，不会自动回退。

> 本文件涉及的代码在工作区里被其他改动占用：`installer/liuhao.iss`、`scripts/launch_installed.py`、`src/upgrade/manager.py` 显示 `M`，`scripts/first_run_gui.py` 是未跟踪新文件（`??`）。按共用工作树约定未直接覆盖；第 4 节给出可直接套用的最小补丁。

---

## 1. 场景 / 现状 / 会不会挡住安装 / 建议

| # | 场景 | 现状（程序装哪 / 数据写哪 / 用户看到什么） | 挡住安装？ | 半截目录 / 丢数据？ | 建议 |
|---|---|---|---|---|---|
| 1 | **无 D 盘**，交互全新安装，普通用户双击 | 程序 → `%LOCALAPPDATA%\Programs\LiuHao`；数据页初始为空，必须手选可写目录；欢迎页写明"D 盘不可用，程序目录已改到…"、"安装器不会自行回退到 C 盘" | 否，但在数据页被挡住直到选目录 | 校验在复制前，无 | 预填 `%LOCALAPPDATA%\LiuHao\data`（见 P1-3） |
| 2 | **无 D 盘**，交互安装，用户右键"以管理员身份运行" | 程序 → `%ProgramFiles%\LiuHao`；数据仍须手选 | 否 | 无 | 同 P1-3；数据目录加 Program Files 黑名单（P2-1） |
| 3 | **无 D 盘**，`/SILENT` 或 `/VERYSILENT`，未给 `/DATA=` | 数据解析为空 → `PrepareToInstall` 返回中文错误并在复制前中止；`{app}` 回退是否对静默生效见 §3 说明 | **是（预期失败）** | 无 | P1-3 预填后静默也能装；若坚持"必须显式指定"，保持现状 |
| 4 | **无 D 盘**，静默 + `/DATA="C:\...\LiuHao\data"` | 正常安装到回退程序目录，数据按参数落盘 | 否 | 无 | — |
| 5 | 无 D 盘，**覆盖升级**（上一版程序在 C 盘） | 程序沿用 `InstallLocation`；数据取注册表 `DataDir`，再取 `data-dir.txt`；两者都读不到才要求手选 | 否 | 无 | — |
| 6 | D 盘存在但**整盘只读 / ACL 拒绝写 / 写保护** | `DDriveUsable` 写探针失败 → 程序回退；数据为空、要求手选；欢迎页统一提示"D 盘不可用（不存在 / 不可写 / 可移动盘）" | 否 | 探针失败即不写，无 | 文案已可行动；可细分"只读"原因（P3） |
| 7 | D 盘根可写，但 `D:\LiuHaoData` 已存在且**不可写** | 程序仍在 `D:\LiuHao`；数据条件 `DDriveUsable and DirWritable('D:\LiuHaoData')` 为假 → 要求手选 | 否 | 无 | 已覆盖；错误文案点名 `D:\LiuHaoData` |
| 8 | D 盘**空间不足**（< 400 MB）但写探针能过 | 程序 + 数据都默认 D；`PrepareToInstall` 只检查**程序盘**，不足 400 MB 时中文报错挡住 | 程序同盘时挡住 | 若数据被选到**另一个快满的盘**：装完，首次启动失败 | **P1-2：数据目录所在盘也要查空间** |
| 9 | D 盘被 **BitLocker 锁定** | 盘类型仍可能是固定盘，写探针失败 → 视为不可用；程序回退、数据要求手选（是否弹解锁框属推断） | 否 | 无 | 无需改；启动阶段错误提示可补"解锁 D 盘"（P3） |
| 10 | D 盘是**网络映射盘** | `GetDriveTypeW = DRIVE_REMOTE(4)` → 不可用；程序回退、数据要求手选 | 否 | 无 | 数据页仍允许用户手填网络路径；断网后首启失败。建议对非固定盘给出明确警告（P2-3） |
| 11 | D 盘是**可移动 U 盘/移动硬盘** | `GetDriveTypeW = DRIVE_REMOVABLE(2)` → 不可用；同上 | 否 | 无 | 同上；数据若手选到 U 盘，拔盘后启动器 fail-closed（不会写错地方） |
| 12 | 安装时 D 正常，**首次启动前 D 被拔掉 / 锁定 / 写满** | 启动器读 `data-dir.txt` 指向 D → `_ensure_layout`/写探针失败 → `[FAIL] 无法创建数据目录 …`，退出码 3，中文提示；无降级写入 | **挡住启动（预期）** | 已建的空骨架目录可能残留，无用户数据 | 错误文案补"接回/解锁 D 盘后重试"（P3） |
| 13 | 首启向导里**改数据目录** | `data-dir.txt` 更新、`LIUHAO_DATA_DIR` 本次进程生效；**注册表 `DataDir` 仍是旧值** | 否 | 数据不丢，但下次覆盖升级会读旧目录 | **P1-4：同步注册表 DataDir** |
| 14 | 覆盖升级（未改过目录） | 注册表 `DataDir` → marker 顺序解析；与安装时一致 | 否 | 无 | — |
| 15 | 覆盖升级（**改过目录**） | 注册表旧值优先；旧目录仍存在且可写 → 静默回到旧目录，新目录账号"看不到" | 否 | 新数据成为孤儿（不删） | P1-4（+ P1-1 防"旧目录不存在"误判） |
| 16 | 旧 D 盘拔出、**盘符被新盘复用**，旧数据目录不存在 | `PathDriveUsable` 为真；`DirWritable(旧路径)` 因"最近存在父目录可写"返回 **True** → 静默采用不存在目录 | 否 | **是**：升级后账号为空，旧数据留在原盘 | **P1-1：加 `DirExists(Marker)`** |
| 17 | 卸载（未改目录） | `InitializeUninstall` 读注册表 `DataDir`；默认保留数据，只有用户两次确认才按该目录删除 | 否 | 无 | — |
| 18 | 卸载（改过目录） | 注册表旧路径被拿去询问删除；新目录数据留在原处、变成孤儿 | 否 | 不丢，但删错候选 | P1-4 |
| 19 | 卸载时数据盘不在 | `DirExists(UninstallDataDir)` 为假 → 跳过删除询问 → 数据保留 | 否 | 无（安全默认） | 可提示"数据盘未连接"（P3） |
| 20 | 旧程序目录已不存在（如老版装在 D，D 被拔） | `PreviousInstallLocation` 要求 `DirExists` → 判为"全新安装"，注册表里的旧 `DataDir` 被忽略；用户可能选 C 建一套新空库 | 否 | 旧数据留在原盘，两套并存 | P2-4：程序目录缺失时仍读旧 `DataDir` 并提示 |
| 21 | **中文 / 空格 / emoji / 超长路径** | marker 往返实测无损（含"张三 的电脑"）；回退路径 `%LOCALAPPDATA%\LiuHao\...` 很短，用户名再长也远低于 259；超长由现有长度预检挡住 | 否 | 无 | 不需要额外处理（详见 §3 实测 B） |

---

## 2. 关键实现位置（现状依据）

| 行为 | 位置 | 说明 |
|---|---|---|
| 默认程序目录写死 D | [liuhao.iss:63](../installer/liuhao.iss#L63) | `DefaultDirName=D:\LiuHao` |
| D 盘可用性（固定盘 + 根目录写探针） | [liuhao.iss:311-324](../installer/liuhao.iss#L311-L324) | 可移动/网络/无根目录直接判假；探针文件写后即删 |
| 程序目录回退 | [liuhao.iss:521-540](../installer/liuhao.iss#L521-L540) | 管理员 → `{commonpf}`，否则 → `{autopf}`（= `%LOCALAPPDATA%\Programs`） |
| 数据目录解析（D / 升级 / 手选） | [liuhao.iss:542-601](../installer/liuhao.iss#L542-L601) | 升级优先注册表 `DataDir`，其次 ANSI marker；全新安装 D 可写才用 `D:\LiuHaoData` |
| "可写"的判定含不存在的目录 | [liuhao.iss:375-413](../installer/liuhao.iss#L375-L413) | `DirWritable` → `WritableAncestor`：目标不存在时只探测父目录 |
| 数据目录页 + 空值拦截 | [liuhao.iss:733-741](../installer/liuhao.iss#L733-L741)、[792-838](../installer/liuhao.iss#L792-L838) | 空值、放程序目录内、保留名、超长、不可写逐项中文拦截 |
| 安装前最终校验 | [liuhao.iss:843-955](../installer/liuhao.iss#L843-L955) | 路径/文件名/可写/长度；**空间只查 `Dir`（程序目录）**，见 941-950 |
| marker + 注册表写入 | [liuhao.iss:977-1013](../installer/liuhao.iss#L977-L1013) | UTF-8 BOM marker；HKCU `DataDir`，若存在 HKLM 键则同步写 |
| 卸载读取 | [liuhao.iss:1016-1032](../installer/liuhao.iss#L1016-L1032) | 注册表优先，其次 ANSI marker，最后 `%LOCALAPPDATA%\LiuHao\data` |
| 启动器 marker 解析与默认值 | [launch_installed.py:58](../scripts/launch_installed.py#L58)、[75-122](../scripts/launch_installed.py#L75-L122) | 环境变量 > marker > `%LOCALAPPDATA%\LiuHao\data` |
| 启动器 fail-closed | [launch_installed.py:650-748](../scripts/launch_installed.py#L650-L748) | 名字/长度/建目录/写探针失败都转中文并拒绝启动 |
| 升级管理器目录解析 | [manager.py:148-177](../src/upgrade/manager.py#L148-L177) | 安装树且无 `LIUHAO_DATA_DIR` 时 → `%LOCALAPPDATA%\LiuHao\data`，**不读 marker** |
| 首启向导默认/改目录 | [first_run_gui.py:75-88](../scripts/first_run_gui.py#L75-L88)、[175-181](../scripts/first_run_gui.py#L175-L181)、[290-335](../scripts/first_run_gui.py#L290-L335) | 默认 D 可写则 D，否则 LOCALAPPDATA；改目录只写 marker |

---

## 3. 实测（临时探针，目录已删除）

探针在 `%TEMP%` 下创建临时目录，结束后 `shutil.rmtree` 自清理（输出 `cleaned= True`）。

| 编号 | 验证内容 | 结果 |
|---|---|---|
| A | `data-dir.txt` **缺失**时启动器落点 | 返回 `DEFAULT_DATA_DIR`（即 `%LOCALAPPDATA%\LiuHao\data`）——**静默**，无任何提示 |
| B | 中文 + 空格路径的 UTF-8 BOM marker 往返 | `D:\...\张三 的电脑\AppData\Local\LiuHao\Data` 解析结果 `equal= True`；不存在 `C:\Users\张三 的电脑` 这类拼接问题（路径来自 marker 原值，不做字符串拼接） |
| C | marker 指向**不存在的盘符** `Q:\LiuHaoData` | `DataDirectoryUnavailable: 无法创建数据目录 Q:\LiuHaoData（FileNotFoundError: [WinError 3]…）。请确认磁盘没有写满、路径没有被只读挂载，或用环境变量 LIUHAO_DATA_DIR 指向另一个可写目录。` 未留下任何目录 |
| D | 安装树（有 `runtime/python.exe`）+ 无 `LIUHAO_DATA_DIR`，`manager.default_data_dir()` | 返回 `C:\Users\Administrator\AppData\Local\LiuHao\data`，与安装器默认 `D:\LiuHaoData` 不一致 |
| E | 尾随反斜杠 `D:\LiuHaoData\` 的 `Path.resolve()` | 归一化为 `D:\LiuHaoData`（注册表值带尾斜杠不会导致比较失败） |

补充说明（代码/推断）：

- 交互安装时 `InitializeWizard` 会把 `DirEdit` 改写成回退目录（[liuhao.iss:725-731](../installer/liuhao.iss#L725-L731)）；静默安装依赖 Inno"静默也使用向导字段值"的行为，未在本机运行 setup.exe 验证，属**推断**。若该推断不成立，场景 3/4 的无 `/DIR` 静默安装会尝试 `D:\LiuHao` 并在复制阶段失败——建议在打包冒烟里补一条"无 D 盘静默安装"实跑。
- 启动器只读 marker，不读注册表；因此 **P1-4 只有在 marker 可写时才完整**。装在 `Program Files` 的机器上，普通用户无法更新程序目录里的 marker（首启向导的 `write_data_dir_marker` 会 `OSError`），此时应给可行动提示，而不是假装成功。

---

## 4. 必须改的最小改动集

> 下列补丁按优先级排列；P1 是"不改就会出现装完写不进 / 升级找错目录"的项，P2 是加固项。
> 行号以工作区当前文件为准。

### P1-1 升级时不要信任"不存在的旧数据目录"

文件：[installer/liuhao.iss:561](../installer/liuhao.iss#L561)

```pascal
-      if PathDriveUsable(Marker) and DirWritable(Marker) then
+      if PathDriveUsable(Marker) and DirExists(Marker) and DirWritable(Marker) then
```

原因：`DirWritable()` 对不存在的路径会探测最近的已存在父目录；旧 D 盘拔掉、盘符被新盘复用后，它会返回 True，安装器会静默采用 `D:\LiuHaoData`（实际不存在）并在升级后建成空库，账号看起来"消失"。加 `DirExists` 后落入已有的 `NeedDataDirChoice` 分支，界面会显示上次记录的路径与原因。

### P1-2 数据目录所在盘也要检查剩余空间

文件：[installer/liuhao.iss:216-218](../installer/liuhao.iss#L216-L218)（常量）与 [939 行之后](../installer/liuhao.iss#L939)

```pascal
 const
   NEEDED_MB = 400;
+  { 数据目录首次运行要建库、写日志；用户可能把它选到另一个盘，
+    只查程序盘会漏掉"装完了但数据写不进去"。 }
+  DATA_NEEDED_MB = 300;
```

```pascal
   if Length(ResolvedDataDir) + 1 + DATA_REL_MAX > MAX_PATH_LIMIT then
   begin
     ...
     Exit;
   end;
+
+  { 数据目录常与程序目录不在同一个盘（用户可以改选）。与程序盘同盘时
+    重复检查无害；跨盘时这里是唯一能挡住"装完首启失败"的地方。 }
+  FreeMB := DriveFreeMB(ResolvedDataDir);
+  if (FreeMB >= 0) and (FreeMB < DATA_NEEDED_MB) then
+  begin
+    Result := '数据目录所在磁盘空间不足：' + ResolvedDataDir + #13#10#13#10 +
+      '首次运行至少需要 ' + IntToStr(DATA_NEEDED_MB) + ' MB 可用空间，' +
+      '该盘当前只剩 ' + IntToStr(FreeMB) + ' MB。' + #13#10 +
+      '请返回上一步改选其他目录（默认 D:\LiuHaoData）；' +
+      '静默安装请用 /DATA="D:\LiuHaoData" 指定。';
+    Exit;
+  end;
 
   FreeMB := DriveFreeMB(Dir);
```

### P1-3 无 D 盘时把推荐位置**预填**进数据目录页（而不是留空）

文件：[installer/liuhao.iss:594-601](../installer/liuhao.iss#L594-L601)

```pascal
   else
   begin
-    ResolvedDataDir := '';
-    NeedDataDirChoice := True;
-    DataFallback := True;
-    DataDirProblem := '这台机器上 D 盘不可用（不存在 / 是可移动盘），' +
-      '或 D:\LiuHaoData 当前不可写，不能默认把数据放在 D:\LiuHaoData。';
+    { 不留空：留空会让交互用户面对空输入框、让静默安装必然中止。
+      填入**可见、可改**的推荐位置（当前用户 LOCALAPPDATA），页面仍要求
+      用户确认；不写 Program Files，也不是"静默回退 C 盘"。 }
+    ResolvedDataDir := ExpandConstant('{localappdata}\LiuHao\data');
+    NeedDataDirChoice := True;
+    DataFallback := True;
+    DataDirProblem := '这台机器上 D 盘不可用（不存在 / 可移动盘 / 不可写）。' +
+      '已推荐当前用户目录 ' + ResolvedDataDir + '，你可以改选其他可写目录。';
   end;
```

同步改三处文案，避免"留空/不会回退"与实现不符：

1. 文件头 [liuhao.iss:8-10](../installer/liuhao.iss#L8-L10)："要求用户在这一页选择" → "把推荐位置填进这一页，由用户确认或改选"。
2. 数据页副标题 [liuhao.iss:738](../installer/liuhao.iss#L738)：`'默认 D:\LiuHaoData。请选择一个当前 Windows 账户可写的目录…'` → `'默认 D:\LiuHaoData；D 盘不可用时推荐当前用户目录。请选择（或确认）一个当前账户可写的目录…'`。
3. 空数据目录时的欢迎语 [liuhao.iss:751-756](../installer/liuhao.iss#L751-L756)：
   `'安装器不会自行回退到 C 盘：请在「数据目录」页选择一个可写目录，否则无法继续安装。'` → `'请在「数据目录」页确认这个推荐位置，或改选其他可写目录；安装器不会静默改写你的选择。'`

原因：`%LOCALAPPDATA%` 是唯一"必然存在且当前用户可写、又不在 Program Files"的稳定位置；预填而不是留空，既满足"路径要能显示在界面上"，也让静默安装无需强制 `/DATA`（仍可用 `/DATA` 覆盖）。升级分支（原数据目录不可用）**保持留空**：旧数据在别处，必须让用户有意识地选，不能替他猜。

### P1-4 首启向导改数据目录时同步注册表 `DataDir`

文件：[scripts/launch_installed.py](../scripts/launch_installed.py#L58)（新增常量与函数）

```python
#: 卸载键（与 installer/liuhao.iss 的 AppIdGuid 保持一致）。覆盖升级/卸载
#: 优先读这里的 DataDir；向导改目录只写 marker 会让升级回到旧目录。
UNINSTALL_KEY_PATH = (
    r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    r"\{8F2B9C1E-4C0D-4A57-9D2E-6B1C3A5E7F90}_is1"
)


def record_data_dir_to_uninstall_key(data_dir: Path) -> bool:
    """把当前数据目录写回卸载键（HKCU 优先，其次已存在的 HKLM 键）。

    只在键已存在时写，不制造孤立卸载键；返回是否写成功。
    """
    if os.name != "nt":
        return False
    try:
        import winreg
    except ImportError:
        return False
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(
                root, UNINSTALL_KEY_PATH, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE
            ) as key:
                winreg.SetValueEx(key, "DataDir", 0, winreg.REG_SZ, str(data_dir))
                return True
        except OSError:
            continue
    return False
```

文件：[scripts/first_run_gui.py:324-329](../scripts/first_run_gui.py#L324-L329)（调用）

```python
     try:
         # 程序目录可能只读（装在 Program Files）：目录没变就不要写 marker，
         # 否则一个不需要改动的首启也会因为 marker 写不进去而失败。
         if not same_dir:
             write_data_dir_marker(dir_value)
+            from scripts import launch_installed as installed
+            if not installed.record_data_dir_to_uninstall_key(dir_value):
+                _write_log(dir_value, "[warn] 数据目录已改，但注册表 DataDir 同步失败；"
+                                      "覆盖升级时可能提示旧目录。")
         prepare_store_env(dir_value)
```

原因：升级/卸载读 `DataDir` 的顺序是"注册表 → ANSI marker"（[liuhao.iss:350-362](../installer/liuhao.iss#L350-L362)、[1023-1031](../installer/liuhao.iss#L1023-L1031)）。只更新 marker 时，用户在首启向导里改过目录后，下一次覆盖升级会把数据指回旧目录。

限制：装在 `Program Files` 的机器上，普通用户跑首启向导时 `write_data_dir_marker` 本身就会 `OSError`，此时应停在 `_fail` 给中文提示（以管理员身份启动或改装用户目录），不能假装改目录成功；不要为了"能过"把 marker 写入失败吞掉。

---

### P2-1 禁止把数据目录放进 Program Files

文件：[installer/liuhao.iss:808](../installer/liuhao.iss#L808)（`NextButtonClick`，紧跟"不能在程序目录里"之后）与 [899 行之前](../installer/liuhao.iss#L899)（`PrepareToInstall`，覆盖静默 `/DATA`）

```pascal
   if IsSameOrChildDir(Dir, ExpandConstant('{commonpf}')) or
      IsSameOrChildDir(Dir, ExpandConstant('{commonpf32}')) then
   begin
     MsgBox('数据目录不能放在 Program Files：' + #13#10 + Dir + #13#10#13#10 +
       '程序升级/卸载会替换这个区域，普通用户运行时也未必可写。' +
       '请改选用户目录（例如 ' + ExpandConstant('{localappdata}') + '\LiuHao\data）。',
       mbError, MB_OK);
     Result := False;
     Exit;
   end;
```

`PrepareToInstall` 里把 `Dir` 换成 `ResolvedDataDir`、`MsgBox` 换成 `Result := ...; Exit` 即可（静默 `/DATA` 走这条路径）。

原因：以管理员安装时 `{commonpf}` 可写，当前代码只拦"数据在程序目录内"，用户仍可把数据选到 `C:\Program Files\LiuHaoData`，违背"不把数据写进 Program Files"。

### P2-2 升级管理器优先读 `data-dir.txt`

文件：[src/upgrade/manager.py:148-163](../src/upgrade/manager.py#L148-L163)

```python
+def _marker_data_dir(app_root: Path) -> Path | None:
+    """读安装器写的 data-dir.txt（UTF-8 带 BOM；兼容旧版 ANSI）。"""
+    marker = app_root / "data-dir.txt"
+    try:
+        raw = marker.read_bytes()
+    except OSError:
+        return None
+    if raw.startswith(b"\xef\xbb\xbf"):
+        raw = raw[3:]
+        try:
+            text = raw.decode("utf-8")
+        except UnicodeDecodeError:
+            return None
+    else:
+        try:
+            text = raw.decode("utf-8")
+        except UnicodeDecodeError:
+            try:
+                text = raw.decode("mbcs")
+            except (UnicodeDecodeError, LookupError):
+                return None
+    text = text.strip().strip('"').strip()
+    if not text or "\ufffd" in text:
+        return None
+    return Path(text).expanduser().resolve()
+
+
 def default_data_dir(app_root: str | Path) -> Path:
     root = Path(app_root).resolve()
     if (root / _INSTALL_MARKER).is_file():
+        marker = _marker_data_dir(root)
+        if marker is not None:
+            return marker
         base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
         return (base / "LiuHao" / "data").resolve()
     return root / "data"
```

原因：实测 D 显示安装树 + 无 `LIUHAO_DATA_DIR` 时默认 `%LOCALAPPDATA%\LiuHao\data`，与安装器默认 `D:\LiuHaoData` 不一致。正常启动由启动器注入 `LIUHAO_DATA_DIR`，所以在线升级不受影响；但手工/计划任务直接调 `src.upgrade.cli`（backup、升级检查）会读写错误目录。现有测试 `test_installed_tree_uses_localappdata` 构造的安装树没有 marker，仍会走 `%LOCALAPPDATA%` 分支，不需要改测试；建议补一条"安装树 + marker=D:\LiuHaoData"的断言。

同样的 marker 读取应同步到 [src/common/integrity_key.py:53-62](../src/common/integrity_key.py#L53-L62) 与 [src/logging_config.py:112-126](../src/logging_config.py#L112-L126)，否则 CLI 向导/网关直启仍会落到 C 盘。

### P2-3 数据目录落非固定盘时明确警告

文件：[installer/liuhao.iss:837](../installer/liuhao.iss#L837) 之后（`DirWritable` 通过、写回 `ResolvedDataDir` 之前）

```pascal
   if GetDriveTypeW(ExtractFileDrive(Dir) + '\') <> 3 then
   begin
     if MsgBox('这个数据目录不在本机固定磁盘上：' + #13#10 + Dir + #13#10#13#10 +
       'U 盘 / 移动硬盘 / 网络盘拔掉或断线后，鎏灏会因读不到数据而拒绝启动。' + #13#10 +
       '仍要使用这个位置吗？', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDNO then
     begin
       Result := False;
       Exit;
     end;
   end;
```

原因：当前数据页只验证"能写"，网络映射盘与 U 盘都能通过；这类位置一旦离线，启动器 fail-closed（不会写错数据，但用户会觉得"装完打不开"）。

### P2-4 程序目录不存在但注册表有旧 `DataDir` 时，不要当成全新安装

文件：[installer/liuhao.iss:518-519](../installer/liuhao.iss#L518-L519)、[552](../installer/liuhao.iss#L552)

最小做法：保留 `HasPrevInstall` 的语义（决定程序目录是否沿用），但数据解析分支改为"只要 `RegisteredDataDir <> ''` 就按升级处理，并在 `PrevInstallDir = ''` 时跳过读 `PrevInstallDir\data-dir.txt`"。

原因：`PreviousInstallLocation` 要求 `DirExists`，老版装在 D、D 被拔掉时会被判为全新安装，于是注册表里的旧数据目录被完全忽略，用户容易在 C 盘建一套新空库。旧数据不会丢，但会分裂成两套。

### P3 文案 / 文档（不影响逻辑）

- [liuhao.iss:889-892](../installer/liuhao.iss#L889-L892)：用户手动把程序目录改到 D 后仍报"如果没有 D 盘，安装器会自动改用…"，实际不会自动改（回退只在启动时决定一次）。改成"请改选 `%LOCALAPPDATA%\Programs\LiuHao` 或以管理员身份重试"。
- [INSTALL-WINDOWS.md:35-38](INSTALL-WINDOWS.md#L35-L38)、[65](INSTALL-WINDOWS.md#L65)：写"D 盘不可用时数据自动回退 `%LOCALAPPDATA%\LiuHao\data`"，与当前"必须确认/手选"不一致。
- [BEFORE-INSTALL.txt:64-66](../installer/assets/BEFORE-INSTALL.txt#L64-L66)：同上。
- 启动器 `DataDirectoryUnavailable` 文案（[launch_installed.py:695-713](../scripts/launch_installed.py#L695-L713)）可补一句："若数据目录在 D 盘，请先解锁 BitLocker / 接回移动硬盘"。

---

## 5. 回退策略结论（回答"没有 D 盘选哪里最稳"）

| 候选 | 可写性 | 路径可见 | 数据在 Program Files | 多用户/共享机器 | 结论 |
|---|---|---|---|---|---|
| `{localappdata}\LiuHao\data`（即 `%LOCALAPPDATA%\LiuHao\data`） | 当前用户必然可写 | 是，向导页直接显示 | 否 | 每用户一份 | **推荐**（与启动器默认、卸载默认一致） |
| 手拼 `C:\Users\<用户名>\...` | 取决于用户名/profile 重定向 | 是 | 否 | 可能指错 | 不推荐 |
| `{commonappdata}\LiuHao\data`（`%ProgramData%`） | 普通用户通常不可写 | 是 | 否（但属机级受保护区） | 一份 | 不推荐（需要安装器预先改 ACL） |
| `{commonpf}\LiuHaoData` | 仅管理员可写 | 是 | **是** | 一份 | 禁止（P2-1） |
| 让用户完全自由输入且不给默认 | 取决于用户 | 是 | 可能 | — | 次选；交互摩擦大，静默部署必填 `/DATA` |

**推荐做法**：无 D 盘时把 `{localappdata}\LiuHao\data` 预填到「数据目录」页（用户可改），保留 `PrepareToInstall` 的写探针与长度校验；Program Files 一律拒绝；静默安装可直接使用预填值，`/DATA` 仍可覆盖。

---

## 6. 未覆盖 / 限制

1. **未编译、未运行 setup.exe**（按要求）：Inno 静默模式下"`InitializeWizard` 改写 `DirEdit` 是否生效"属推断；建议出包后补一条无 D 盘的 `/SILENT` 冒烟（可用 `/DSetupOutputDir` 指到临时目录）。
2. 本机有 D 盘，无法物理复现"无 D 盘""BitLocker 锁定"；只读/网络/可移动盘结论来自 `GetDriveTypeW` 与写探针代码。
3. 中文/空格路径为 marker 往返实测；emoji、超长用户名未单独构造（另一条线在测特殊字符，此处不重复）。
4. 现有审计 [audit-data-layout-20261108.md](audit-data-layout-20261108.md) 记录的"本机实际数据在 C、`D:\LiuHaoData` 是空壳"与本文结论一致：那台机器是老版本安装 + 测试残留，不是当前工作区安装器的正常产物。
