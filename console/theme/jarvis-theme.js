/* Jarvis theme overlay v2 — Boss hub ("我的空间") for the real console.
   Builds on v1 (espresso/copper + right-click groups in the Bot rail).

   What v2 does, honestly scoped:
   - The global function sidebar (.os-sidebar) is hidden by CSS; its real
     nav buttons stay in the DOM and the Boss panel activates them with
     real DOM clicks, so every panel "function" item performs the real
     navigation of the real console. Nothing here is a fake link.
   - "Skill" items （本事） go back to the workbench chat and click the
     real quick-action capsules the console itself renders
     （帮我查一下今天的天气 / 帮我写一封邮件 / 记下来… / 建一个帮我管账的 Bot).
     If the capsules are not on screen (chat already started), the text
     is filled into the real composer instead and a toast says so.
   - The Boss badge and the 待办审批 tile badge show the REAL pending
     approval count from /v1/approvals/summary — the same number the
     待办审批 page itself shows. (Walkthrough adjudication 2026-11-18:
     badges must equal the real todo count. The old design mirrored the
     topbar bell badge, which counts synthesized status notifications —
     that made 待办审批 wear a notification count, e.g. badge 2 while
     the approvals page said 0. Bell mirroring is gone.)
   - The topbar bell itself is hygienically filtered here: the shell
     synthesizes a developer-class alert （审计证据链 evidence status)
     into the owner's bell from /v1/ready. That signal belongs on the
     系统状态与诊断 page (which reads /v1/ready itself, unaffected);
     this overlay hides that one item in the bell panel and subtracts
     it from the bell badge, so owner-visible numbers only count
     owner-facing items.
   - Page-title fallback repair: the shell's page-meta map has no
     `allowlist` entry, so the allowlist page header falls back to
     企业总览. The overlay rewrites that one header when the allowlist
     page is on screen (dist itself is never rebuilt).
   - Groups (v3, 补缺口轮): persisted in the real backend
     (/v1/workbench/groups + /bots, per-account SQLite). localStorage is
     only a cache/fallback: on first load, legacy localStorage groups are
     migrated once via POST /v1/workbench/migrate; if the backend is
     unreachable the rail silently keeps working on localStorage. */
(function () {
  "use strict";

  /* ================= groups: backend-first, localStorage fallback ===== */
  var KEY = "jarvis.theme.groups.v1";
  var MIGRATED_KEY = "jarvis.theme.groups.migrated.v1";
  var cache = null;      /* last known group list (backend shape) */
  var botsCache = [];    /* last known bot list (for group membership) */
  var backendOk = false;
  function load() {
    if (cache) return cache;
    try { var v = JSON.parse(localStorage.getItem(KEY) || "[]"); return Array.isArray(v) ? v : []; }
    catch (e) { return []; }
  }
  function save(groups) {
    cache = groups;
    try { localStorage.setItem(KEY, JSON.stringify(groups)); } catch (e) {}
  }
  function sessionToken() {
    try {
      var s = JSON.parse(localStorage.getItem("liuhao.session") || "{}");
      return s.token || s.access_token || "";
    } catch (e) { return ""; }
  }
  function api(path, opts) {
    var t = sessionToken();
    if (!t) return Promise.resolve(null);
    var o = opts || {};
    o.headers = { "Content-Type": "application/json",
                  "X-Liuhao-Token": "Bearer " + t,
                  "Authorization": "Bearer " + t };
    return fetch(path, o).then(function (r) {
      if (!r.ok) throw new Error("http " + r.status);
      return r.status === 204 ? {} : r.json();
    }).catch(function () { return null; });
  }
  var syncInFlight = false;
  var lastSyncSig = "";
  function syncFromBackend() {
    if (syncInFlight) return Promise.resolve();
    syncInFlight = true;
    return api("/v1/workbench/groups").then(function (res) {
      if (!res || !Array.isArray(res.groups)) return;
      backendOk = true;
      var local = load();
      if (res.groups.length === 0 && local.length > 0 &&
          !localStorage.getItem(MIGRATED_KEY)) {
        /* one-time migration of legacy localStorage groups */
        return api("/v1/workbench/migrate", {
          method: "POST",
          body: JSON.stringify({ groups: local.map(function (g) {
            return { id: g.id, name: g.name, folded: !!g.folded };
          }) })
        }).then(function () {
          try { localStorage.setItem(MIGRATED_KEY, "1"); } catch (e) {}
          return api("/v1/workbench/groups").then(function (res2) {
            if (res2 && Array.isArray(res2.groups)) { save(res2.groups); renderGroups(); }
          });
        });
      }
      try { localStorage.setItem(MIGRATED_KEY, "1"); } catch (e) {}
      return api("/v1/workbench/bots").then(function (bres) {
        if (bres && Array.isArray(bres.bots)) botsCache = bres.bots;
        /* Only touch storage/DOM when something actually changed;
           re-rendering on every sync used to retrigger the mutation
           observer and sustain a request loop. */
        var sig = JSON.stringify(res.groups) + "|" + JSON.stringify(botsCache);
        if (sig !== lastSyncSig) {
          lastSyncSig = sig;
          save(res.groups); renderGroups();
        }
      });
    }).then(function () { syncInFlight = false; },
            function () { syncInFlight = false; });
  }
  function toast(msg) {
    var old = document.querySelector(".jt-toast"); if (old) old.remove();
    var t = document.createElement("div"); t.className = "jt-toast"; t.textContent = msg;
    document.body.appendChild(t); setTimeout(function () { t.remove(); }, 2600);
  }
  function closeMenu() { var m = document.querySelector(".jt-menu"); if (m) m.remove(); }

  function renderGroups() {
    var host = document.getElementById("jt-groups"); if (!host) return;
    var groups = load(); host.innerHTML = "";
    groups.forEach(function (g) {
      var wrap = document.createElement("div"); wrap.className = "jt-group";
      var head = document.createElement("button");
      head.type = "button"; head.className = "jt-group-head"; head.dataset.gid = g.id;
      head.textContent = (g.folded ? "▸ " : "▾ ") + g.name;
      head.addEventListener("click", function () {
        var gs = load(); var it = gs.find(function (x) { return x.id === g.id; });
        if (it) {
          it.folded = !it.folded; save(gs); renderGroups();
          if (backendOk) {
            api("/v1/workbench/groups/" + encodeURIComponent(g.id), {
              method: "PATCH", body: JSON.stringify({ folded: it.folded })
            });
          }
        }
      });
      wrap.appendChild(head);
      if (!g.folded) {
        var body = document.createElement("div"); body.className = "jt-group-body";
        var members = botsCache.filter(function (b) { return b.group_id === g.id; });
        body.textContent = members.length
          ? members.map(function (b) { return "· " + (b.name || b.id); }).join("\n")
          : "（空分组 — 右键分组可重命名 / 删除）";
        wrap.appendChild(body);
      }
      host.appendChild(wrap);
    });
  }
  function createGroup() {
    var name = window.prompt("新建分组 — 输入分组名", "新分组");
    if (name === null) return;
    name = (name || "").trim() || "新分组";
    if (backendOk) {
      api("/v1/workbench/groups", {
        method: "POST", body: JSON.stringify({ name: name })
      }).then(function (res) {
        if (res && res.group) {
          var gs = load(); gs.push(res.group); save(gs); renderGroups();
          toast("已新建分组「" + name + "」（已存后端）");
        } else { localCreate(); }
      });
    } else { localCreate(); }
    function localCreate() {
      var gs = load();
      gs.push({ id: "g" + Date.now().toString(36), name: name, folded: false });
      save(gs); renderGroups(); toast("已新建分组「" + name + "」");
    }
  }
  function renameGroup(id) {
    var gs = load(); var it = gs.find(function (x) { return x.id === id; }); if (!it) return;
    var name = window.prompt("重命名分组", it.name);
    if (name === null) return;
    it.name = (name || "").trim() || it.name; save(gs); renderGroups();
    if (backendOk) {
      api("/v1/workbench/groups/" + encodeURIComponent(id), {
        method: "PATCH", body: JSON.stringify({ name: it.name })
      });
    }
  }
  function deleteGroup(id) {
    save(load().filter(function (x) { return x.id !== id; }));
    botsCache.forEach(function (b) { if (b.group_id === id) b.group_id = ""; });
    renderGroups(); toast("分组已删除");
    if (backendOk) {
      api("/v1/workbench/groups/" + encodeURIComponent(id), { method: "DELETE" });
    }
  }
  function openMenu(x, y, items) {
    closeMenu();
    var m = document.createElement("div"); m.className = "jt-menu"; m.setAttribute("role", "menu");
    items.forEach(function (it) {
      var b = document.createElement("button"); b.type = "button";
      b.textContent = it.label; if (it.danger) b.className = "jt-danger";
      b.addEventListener("click", function () { closeMenu(); it.fn(); });
      m.appendChild(b);
    });
    document.body.appendChild(m);
    m.style.left = Math.min(x, window.innerWidth - 210) + "px";
    m.style.top = Math.min(y, window.innerHeight - items.length * 44 - 20) + "px";
  }
  function ensureGroups() {
    var scroll = document.querySelector(".wb-rail-scroll");
    if (!scroll) return;
    if (!document.getElementById("jt-hint")) {
      var hint = document.createElement("div"); hint.id = "jt-hint";
      hint.textContent = "右键此处 · 新建分组";
      scroll.appendChild(hint);
    }
    if (!document.getElementById("jt-groups")) {
      var host = document.createElement("div"); host.id = "jt-groups";
      scroll.appendChild(host);
    }
    renderGroups();
    var rail = document.querySelector(".wb-rail");
    if (rail && !rail.dataset.jtBound) {
      rail.dataset.jtBound = "1";
      rail.addEventListener("contextmenu", function (ev) {
        ev.preventDefault();
        var head = ev.target.closest ? ev.target.closest(".jt-group-head") : null;
        if (head) {
          var id = head.dataset.gid;
          openMenu(ev.clientX, ev.clientY, [
            { label: "新建分组", fn: createGroup },
            { label: "重命名分组", fn: function () { renameGroup(id); } },
            { label: "删除分组", danger: true, fn: function () { deleteGroup(id); } }
          ]);
        } else {
          openMenu(ev.clientX, ev.clientY, [
            { label: "新建分组", fn: createGroup },
            { label: "新建 Bot（走原生创建，主题层未接管）", fn: function () {
                toast("Bot 创建仍用原生流程 — 本主题只接管分组的页面层展示");
              } }
          ]);
        }
      });
    }
  }

  /* ================= v2: Boss hub ================= */
  var CAP = {
    search: "帮我查一下今天的天气",
    mail: "帮我写一封邮件",
    remind: "记下来：明天早上提醒我开会",
    bot: "建一个帮我管账的 Bot"
  };
  var DAILY = [
    { label: "操作台", type: "nav", target: "操作台" },
    { label: "待办审批", type: "nav", target: "待办审批", badge: true },
    { label: "任务中心", type: "nav", target: "任务中心" },
    { label: "查资料", type: "skill", target: CAP.search },
    { label: "写邮件", type: "skill", target: CAP.mail },
    { label: "提醒", type: "nav", target: "提醒" },
    { label: "建 Bot", type: "skill", target: CAP.bot },
    { label: "知识中心", type: "nav", target: "知识中心" }
  ];
  /* 全部 · 22（27 项裁决后，机主走查 §4，2026-11-18 并入 V1.5）：
     留 13 / 改名 3 / 合并 4 / 藏深 6 / 降级 1。label = 显示名（改名后），
     target = 壳侧栏原名（导航锚点，不许改）。合并去向：记提醒→提醒、
     我的员工→办公室、我的数据→数据与备份、通知中心→铃铛；藏深 5 项
     进「高级」折叠组，自改确认撤下清单（有提案时由聊天卡顶出，壳侧
     行为）；企业总览改名运行总览并降级到管理组。另有 4 个真机导航项
     早已合并（贾维斯=聊天本身、旧版操作台与操作台重复、全局总览并入
     企业总览、组织架构并入办公室）。 */
  var ALL = [
    { group: "干活", items: [
      { label: "操作台", type: "nav", target: "操作台" },
      { label: "任务中心", type: "nav", target: "任务中心" },
      { label: "待办审批", type: "nav", target: "待办审批", badge: true },
      { label: "提醒", type: "nav", target: "提醒" },
      { label: "我的文件", type: "nav", target: "工作区文件" },
      { label: "办公室", type: "nav", target: "办公室" }
    ]},
    { group: "本事", items: [
      { label: "能力中心", type: "nav", target: "能力中心" },
      { label: "查资料", type: "skill", target: CAP.search },
      { label: "写邮件", type: "skill", target: CAP.mail },
      { label: "建 Bot", type: "skill", target: CAP.bot }
    ]},
    { group: "管理", items: [
      { label: "知识中心", type: "nav", target: "知识中心" },
      { label: "运行总览", type: "nav", target: "企业总览" },
      { label: "系统设置", type: "nav", target: "系统设置" },
      { label: "我的账号与安全", type: "nav", target: "我的账号与安全" },
      { label: "数据与备份", type: "nav", target: "备份与迁移" },
      { label: "关于与隐私", type: "nav", target: "关于与隐私" },
      { label: "系统状态与诊断", type: "nav", target: "系统状态与诊断" }
    ]},
    { group: "高级", collapsed: true, items: [
      { label: "全部任务与审计", type: "nav", target: "全部任务与审计" },
      { label: "成员与权限", type: "nav", target: "成员与权限" },
      { label: "审批授权", type: "nav", target: "审批签发" },
      { label: "主机命令白名单", type: "nav", target: "主机命令白名单" },
      { label: "安装自检", type: "nav", target: "生产预检" }
    ]}
  ];

  var panel = null, panelFace = "daily", panelOpen = false;

  function bossName() {
    var n = document.querySelector(".os-user-name");
    return (n && n.textContent.trim()) || "Boss";
  }
  /* ============ owner numbers: real todo count + bell hygiene ======
     Single source of truth for every owner-visible count this overlay
     renders. todoCount comes from /v1/approvals/summary (the 待办审批
     page's own source). auditDev is 1 while /v1/ready reports the audit
     evidence chain as unverified/broken — the shell turns that into a
     bell item, which this overlay filters back out (see header note).
     Published as window.JARVIS_TODO for jarvis-future-theme.js. */
  var JARVIS_TODO = { count: null, auditDev: 0 };
  window.JARVIS_TODO = JARVIS_TODO;
  function pollTodoCount() {
    api("/v1/approvals/summary").then(function (d) {
      if (d && typeof d.pending === "number") {
        JARVIS_TODO.count = d.pending;
        syncBadges();
      }
    });
  }
  function pollAuditDev() {
    api("/v1/ready").then(function (d) {
      var es = d && d.checks && d.checks.audit_store &&
               d.checks.audit_store.evidence_status;
      JARVIS_TODO.auditDev = (es === "unverified" || es === "broken") ? 1 : 0;
      fixBell();
    });
  }
  /* Bell badge: React writes the raw synthesized-item count; whenever
     it changes we re-apply raw-minus-dev so the visible number only
     counts owner-facing items. bellShown guards against our own
     writes being mistaken for a new raw value. */
  var bellRaw = null, bellShown = null;
  function fixBell() {
    var b = document.querySelector(".os-icon-btn-badge");
    if (!b) { bellRaw = null; bellShown = null; fixBellPanel(); return; }
    var cur = parseInt(b.textContent.trim(), 10);
    if (isNaN(cur)) return;
    if (cur !== bellShown) bellRaw = cur;
    var want = Math.max(0, (bellRaw || 0) - JARVIS_TODO.auditDev);
    if (want <= 0) {
      b.style.display = "none";
      bellShown = 0;
    } else {
      b.style.display = "";
      if (cur !== want) b.textContent = String(want);
      bellShown = want;
    }
    var btn = b.closest ? b.closest("button") : null;
    if (btn) btn.setAttribute("aria-label", "通知，" + want + " 条");
    fixBellPanel();
  }
  function fixBellPanel() {
    var panel = document.getElementById("os-notifications-panel");
    if (!panel) return;
    /* hide the developer-class audit item: from its title leaf, climb
       to the item container (the first ancestor that also carries the
       detail text, which mentions "checkpoint" in both variants) */
    panel.querySelectorAll("*").forEach(function (el) {
      if (el.children.length) return;
      var tx = (el.textContent || "").trim();
      if (tx.indexOf("审计证据链") !== 0) return;
      var item = el;
      for (var up = 0; up < 5 && item.parentElement && item.parentElement !== panel; up++) {
        item = item.parentElement;
        if ((item.textContent || "").indexOf("checkpoint") >= 0) break;
      }
      if (item !== panel) {
        item.style.display = "none";
        item.dataset.jfDevHidden = "1";
      }
    });
    /* header count: a leaf element whose whole text is the raw count
       (badge/count class, or one we already corrected) becomes the
       corrected count */
    if (bellRaw !== null) {
      var want = Math.max(0, bellRaw - JARVIS_TODO.auditDev);
      panel.querySelectorAll("*").forEach(function (el) {
        if (el.children.length) return;
        if ((el.textContent || "").trim() !== String(bellRaw)) return;
        var cls = (el.className && el.className.indexOf) ? el.className : "";
        if (cls.indexOf("badge") >= 0 || cls.indexOf("count") >= 0 || el.dataset.jfCount === "1") {
          el.textContent = String(want);
          el.dataset.jfCount = "1";
        }
      });
    }
  }
  /* Page-title fallback repair (allowlist) + 企业总览→运行总览 rename
     (walkthrough §4 verdict; the enterprise page keeps its own sub). */
  function fixPageTitle() {
    /* the workbench page sub is design-speak （窄左栏、Bot 单聊…), not
       owner copy — hide it */
    document.querySelectorAll(".os-page-title, h1").forEach(function (el) {
      if ((el.textContent || "").trim() === "工作台") {
        var head = el.closest(".os-page-head") || el.parentElement;
        var sub = head && head.querySelector(".os-page-sub");
        if (sub) sub.style.display = "none";
      }
    });
    var t = null;
    document.querySelectorAll(".os-page-title, h1").forEach(function (el) {
      if (!t && (el.textContent || "").trim() === "企业总览") t = el;
    });
    if (!t) return;
    var onAllow = false;
    document.querySelectorAll("h1, h2, h3, .os-card-title").forEach(function (c) {
      if ((c.textContent || "").trim() === "主机命令白名单") onAllow = true;
    });
    if (onAllow) {
      t.textContent = "主机命令白名单";
      var sub = document.querySelector(".os-page-head .os-page-sub");
      if (sub) sub.textContent = "贾维斯能直接跑哪些主机命令（Owner 专属）";
    } else {
      t.textContent = "运行总览";
    }
  }
  /* W1 (cont.): the enterprise page prints a raw API line
     (GET /v1/org/tree 返回 0 个节点) — not owner copy. The cards
     render after the title, so this sweep keys off the RENAMED title
     and runs on every tick while the page is open. */
  function fixEnterpriseApiLine() {
    var onPage = false;
    document.querySelectorAll(".os-page-title, h1").forEach(function (el) {
      if ((el.textContent || "").trim() === "运行总览") onPage = true;
    });
    if (!onPage) return;
    document.querySelectorAll("span, div, p, code").forEach(function (el) {
      if (el.children.length) return;
      if ((el.textContent || "").indexOf("/v1/org/tree") >= 0) {
        el.style.display = "none";
      }
    });
  }

  /* ============ V1.5: login declutter (L-01) + topbar slimming (T-01)
     + copy swaps (spec cards' visible-copy lists) ================== */
  function fixLogin() {
    var login = document.querySelector(".os-login");
    if (!login) return;
    var sub = login.querySelector("p.os-login-sub");
    if (sub && sub.textContent.indexOf("全栈") >= 0) {
      sub.textContent = "你的 AI 工作台，随身带着走。";
    }
    /* first-run wizard (§1-2/§1-3): the aside opens with a warning —
       swap in the welcome line; the step-1 explanation drops the
       管理范围 jargon for the spec card's plain copy */
    login.querySelectorAll("p.os-login-sub").forEach(function (p) {
      if (p.textContent.indexOf("管理范围") >= 0) {
        p.textContent = "这台机器还没有账号。先创建你的账号，创建好会给你一份恢复材料，请收好。";
      }
    });
    var note = login.querySelector(".os-login-aside-note");
    if (note && note.textContent.indexOf("任何人都无法登录") >= 0) {
      note.textContent = "我是贾维斯，查资料、写东西、记个事、建个 Bot，一句话就行。先花一分钟把这台机器设置好。";
      note.classList.add("jt-note-welcome");
    }
    login.querySelectorAll(".os-login-aside-mark, .os-login-mark").forEach(function (mark) {
      if (mark.querySelector("img.jt-login-mascot")) return;
      var img = document.createElement("img");
      img.className = "jt-login-mascot";
      img.src = "/theme/assets/holo-mascot.webp";
      img.alt = "小铁 · 贾维斯";
      mark.appendChild(img);
    });
  }
  function fixTopbar() {
    document.querySelectorAll(".os-topbar span, header span").forEach(function (sp) {
      if ((sp.textContent || "").trim() === "下载桌面版") {
        var btn = sp.closest("button, a");
        if (btn) btn.style.display = "none";
      }
    });
  }
  var TEXT_SWAPS = [
    ["记一句", "存下来"]
  ];
  function fixTextSwaps() {
    document.querySelectorAll("button, a").forEach(function (el) {
      if (el.children.length) return;
      var tx = (el.textContent || "").trim();
      for (var i = 0; i < TEXT_SWAPS.length; i++) {
        if (tx === TEXT_SWAPS[i][0]) el.textContent = TEXT_SWAPS[i][1];
      }
    });
    /* W10: 「Key 归属 · liuhao2 次」缺分隔符 → 「liuhao · 2 次」 */
    document.querySelectorAll("span, div, td, li, p").forEach(function (el) {
      if (el.children.length) return;
      var tx = el.textContent || "";
      if (tx.indexOf("Key 归属") < 0) return;
      var fixed = tx.replace(/(Key 归属\s*·\s*)([A-Za-z][A-Za-z0-9_-]*?)(\d+)(\s*次)/, "$1$2 · $3$4");
      if (fixed !== tx) el.textContent = fixed;
    });
  }

  /* ============ V1.5 batch 2: chat / settings / capability / account == */
  function lastUserQuestion() {
    var rows = document.querySelectorAll(".wb-msg-user");
    if (!rows.length) return "";
    var row = rows[rows.length - 1];
    var body = row.querySelector(".wb-msg-body, .wb-msg-text, .wb-msg-content") || row;
    var tx = (body.textContent || "").trim();
    /* strip the action-bar labels + timestamp glued onto the row text */
    ["引用", "撤回", "编辑", "☆ 收藏", "复制", "存下来", "记一句"].forEach(function (w) {
      tx = tx.replace(new RegExp(w + "$"), "").trim();
    });
    tx = tx.replace(/\d{1,2}:\d{2}(:\d{2})?$/, "").trim();
    return tx.slice(0, 500);
  }
  /* W5: no-Key notice rows get a direct 去填 Key entry; the question is
     stashed and prefilled back into the composer afterwards, so the
     owner never retypes it. §2-3: voice/mic system rows render as
     light inline notes, not chat bubbles. */
  function fixChatKey() {
    document.querySelectorAll(".wb-msg").forEach(function (row) {
      var tx = row.textContent || "";
      if (/麦克风|录音权限|语音输入/.test(tx) && !row.classList.contains("wb-msg-user")) {
        row.classList.add("jt-inline-note");
      }
      if (row.dataset.jtKey || row.classList.contains("wb-msg-user")) return;
      if (tx.indexOf("还没有配置模型") < 0 && tx.indexOf("请先配置模型密钥") < 0 &&
          tx.indexOf("未配置模型") < 0) return;
      row.dataset.jtKey = "1";
      var btn = document.createElement("button");
      btn.type = "button"; btn.className = "jt-key-cta"; btn.textContent = "去填 Key";
      btn.addEventListener("click", function () {
        var q = lastUserQuestion();
        if (q) sessionStorage.setItem("jt.pendingQuestion", q);
        navTo("系统设置");
      });
      row.appendChild(btn);
    });
    var pq = sessionStorage.getItem("jt.pendingQuestion");
    if (pq && document.querySelector(".wb-chat")) {
      var ta = document.querySelector(".wb-composer textarea");
      if (ta && !ta.value) {
        sessionStorage.removeItem("jt.pendingQuestion");
        fillComposer(pq);
      }
    }
  }
  /* W6/W7: settings — hide the unverified aggregator preset chip, the
     timeout field loses 必填 and gains a visible default, pip-install
     instructions never reach the owner (package users get the human
     line instead). */
  function fixSettings() {
    var title = document.querySelector(".os-page-title");
    if (!title || title.textContent.trim() !== "系统设置") return;
    document.querySelectorAll("button").forEach(function (b) {
      if ((b.textContent || "").indexOf("杰夫") >= 0) b.style.display = "none";
    });
    document.querySelectorAll("label, span, div").forEach(function (el) {
      if (el.children.length) return;
      var tx = (el.textContent || "").trim();
      if (tx === "超时（秒，必填）") {
        el.textContent = "超时（秒）";
        var row = el.closest("div, li, tr") || el.parentElement;
        var input = row && row.querySelector("input");
        if (input && !input.value && !input.dataset.jtDefaulted) {
          input.dataset.jtDefaulted = "1";
          var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
          setter.call(input, "120");
          input.dispatchEvent(new Event("input", { bubbles: true }));
          input.dispatchEvent(new Event("change", { bubbles: true }));
        }
      }
      if (tx.indexOf("pip install") >= 0) {
        el.textContent = tx.replace(/pip install[^\s，。；]*/g, "安装包自带组件");
      }
    });
  }
  /* W8/W9: capability centre — the long 说明 folds to one line, the
     empty state grows a real first-skill entry, tool tiers speak human
     and source file paths leave the table. */
  function fixCapability() {
    var title = document.querySelector(".os-page-title");
    if (!title || title.textContent.trim() !== "能力中心") return;
    document.querySelectorAll("*").forEach(function (el) {
      if (el.children.length) return;
      var tx = (el.textContent || "").trim();
      if (/^src\/[\w./]+\.py:\w+$/.test(tx)) {
        el.style.display = "none";
        if (el.parentElement) el.parentElement.title = tx;
        return;
      }
      if (/^[ABC] 档 · /.test(tx)) { el.textContent = tx.replace(/^[ABC] 档 · /, ""); return; }
      if (tx === "C 档") { el.textContent = "能操控电脑"; return; }
      if (tx.indexOf("C 档（操控电脑 / 看屏幕）") >= 0) {
        el.textContent = tx.replace("C 档（操控电脑 / 看屏幕）", "能操控电脑的工具");
      }
    });
    /* 说明 fold */
    document.querySelectorAll("h1, h2, h3, h4, div, span").forEach(function (h) {
      if ((h.textContent || "").trim() !== "说明" || h.dataset.jtFoldBound) return;
      var card = h.parentElement;
      if (!card || card.dataset.jtFolded) return;
      card.dataset.jtFolded = "1"; h.dataset.jtFoldBound = "1";
      var bodyEls = Array.from(card.children).filter(function (c) { return c !== h; });
      bodyEls.forEach(function (c) { c.style.display = "none"; });
      h.classList.add("jt-fold-toggle");
      h.textContent = "说明 · 点开看";
      h.addEventListener("click", function () {
        var open = bodyEls.length && bodyEls[0].style.display !== "none";
        bodyEls.forEach(function (c) { c.style.display = open ? "none" : ""; });
        h.textContent = open ? "说明 · 点开看" : "说明 · 收起";
      });
    });
    /* empty-state first-skill entry: attach to the SMALLEST matching
       box only (the empty-state card itself, not its ancestors) */
    var candidates = Array.from(document.querySelectorAll("div, section"))
      .filter(function (box) { return (box.textContent || "").includes("还没有技能"); });
    if (candidates.length && !document.querySelector(".jt-big-cta")) {
      var box = candidates.reduce(function (a, b) {
        return (a.textContent.length <= b.textContent.length) ? a : b;
      });
      var btn = document.createElement("button");
      btn.type = "button"; btn.className = "jt-big-cta"; btn.textContent = "装第一个技能";
      btn.addEventListener("click", function () {
        var nb = Array.from(document.querySelectorAll("button"))
          .find(function (b) { return (b.textContent || "").trim() === "新建技能" && b.offsetParent !== null; });
        if (nb) nb.click();
        else toast("到右侧「新建技能」就能装第一个技能");
      });
      box.appendChild(btn);
    }
  }
  /* W4: the post-logout notice is a normal state, not an error — the
     shell renders it with the red .os-alert skin; re-skin just that
     notice (real error alerts keep the red). */
  function fixAlerts() {
    document.querySelectorAll(".os-alert").forEach(function (el) {
      if ((el.textContent || "").indexOf("你已退出登录") >= 0) {
        el.classList.add("jt-alert-neutral");
      }
    });
  }
  /* §5-8: the account & security page carries a resident plain-language
     recovery-code explanation. */
  function fixAccount() {
    var title = document.querySelector(".os-page-title");
    if (!title || title.textContent.trim() !== "我的账号与安全") return;
    if (document.getElementById("jt-recovery-card")) return;
    var host = title.closest(".os-page") || title.parentElement;
    if (!host) return;
    var card = document.createElement("div");
    card.id = "jt-recovery-card"; card.className = "jt-recovery-card";
    card.innerHTML = "<h3>恢复码是什么</h3><p>恢复码是你忘了密码时把账号找回来的唯一凭证，" +
      "创建账号时只显示一次。请把它抄在纸上，或存进手机备忘录收好；不要截图发微信——" +
      "截图会同步到云端，别人拿到就能找回你的账号。</p>";
    host.appendChild(card);
  }
  /* §1-7: on the one-time recovery display, the keep-it-safe guidance
     sits with the material and 下载恢复材料 comes before 我已保存. */
  function fixRecoveryDialog() {
    var save = Array.from(document.querySelectorAll("button"))
      .find(function (b) { return (b.textContent || "").trim() === "我已保存" && b.offsetParent !== null; });
    if (!save) return;
    var box = save.parentElement;
    for (var i = 0; i < 4 && box; i++) {
      if ((box.textContent || "").indexOf("恢复") >= 0) break;
      box = box.parentElement;
    }
    if (!box || box.dataset.jtRecoveryTip) return;
    box.dataset.jtRecoveryTip = "1";
    var dl = Array.from(box.querySelectorAll("button, a"))
      .find(function (b) { return (b.textContent || "").indexOf("下载恢复材料") >= 0; });
    if (dl && save.parentElement === dl.parentElement) {
      dl.parentElement.insertBefore(dl, save);
    }
    var tip = document.createElement("p");
    tip.className = "jt-recovery-tip";
    tip.textContent = "请把恢复材料抄在纸上或存进手机备忘录收好，不要截图发微信。";
    box.appendChild(tip);
  }

  /* ============ V1.5 batch 3: mobile (M-01, spec card 手机端) =======
     ≤480px: slim topbar (小铁 + 贾维斯 + bell), Bot rail becomes a
     drawer (brand tap opens, shade / pick closes), bottom nav
     converges to 贾维斯 / 任务 / 空间 / 设置, long-press on the rail
     replaces right-click. Desktop layout is untouched. */
  function syncMobileMode() {
    var m = window.matchMedia("(max-width: 480px)").matches;
    document.body.classList.toggle("jt-mobile", m);
    if (m) {
      var topbar = document.querySelector(".os-topbar");
      if (topbar) {
        Array.from(topbar.children).forEach(function (ch) {
          var keep = ch.id === "jt-mobile-brand" ||
            (ch.querySelector && (ch.querySelector(".jt-keep") || ch.classList.contains("jt-keep")));
          ch.classList.toggle("jt-m-keep", !!keep);
        });
      }
    }
    syncMobileHint();
  }
  function syncMobileHint() {
    var hint = document.getElementById("jt-hint");
    if (!hint) return;
    hint.textContent = window.matchMedia("(max-width: 480px)").matches
      ? "长按新建分组" : "右键此处 · 新建分组";
  }
  function ensureMobile() {
    var topbar = document.querySelector(".os-topbar");
    if (topbar && !document.getElementById("jt-mobile-brand")) {
      var brand = document.createElement("button");
      brand.type = "button"; brand.id = "jt-mobile-brand"; brand.className = "jt-mobile-brand";
      brand.innerHTML = '<img src="/theme/assets/holo-mascot.webp" alt="小铁"><span>贾维斯</span>';
      brand.addEventListener("click", function () {
        document.body.classList.toggle("jt-drawer-open");
      });
      topbar.insertBefore(brand, topbar.firstChild);
    }
    document.querySelectorAll(".os-topbar .os-icon-btn").forEach(function (b) {
      if ((b.getAttribute("aria-label") || "").indexOf("通知") === 0) {
        b.classList.add("jt-keep");
        /* the bell sits inside an unnamed wrapper in the actions
           cluster — keep its ancestors too, or the cluster slimming
           hides the wrapper and the bell with it */
        var p = b.parentElement;
        while (p && !p.classList.contains("os-topbar")) {
          p.classList.add("jt-keep");
          if (p.classList.contains("os-topbar-actions")) break;
          p = p.parentElement;
        }
      }
    });
    if (!document.getElementById("jt-shade")) {
      var shade = document.createElement("div"); shade.id = "jt-shade";
      shade.addEventListener("click", function () {
        document.body.classList.remove("jt-drawer-open");
      });
      document.body.appendChild(shade);
    }
    var nav = document.querySelector(".os-bottom-nav");
    if (nav && !nav.dataset.jtMobile) {
      nav.dataset.jtMobile = "1";
      var hideLabels = ["企业总览", "办公室", "知识", "能力中心"];
      var taskItem = null, settingsItem = null;
      Array.from(nav.children).forEach(function (item) {
        var tx = (item.textContent || "").trim();
        if (hideLabels.indexOf(tx) >= 0) item.classList.add("jt-nav-hide");
        if (tx === "任务") taskItem = item;
        if (tx === "设置") settingsItem = item;
      });
      if (taskItem && !taskItem.querySelector(".jt-boss-badge")) {
        var bd = document.createElement("span");
        bd.className = "jt-boss-badge jt-nav-badge";
        taskItem.appendChild(bd);
        syncBadges();
      }
      var space = document.createElement(settingsItem ? settingsItem.tagName : "button");
      space.className = (settingsItem ? settingsItem.className : "") + " jt-nav-space";
      space.textContent = "空间";
      space.addEventListener("click", function (ev) {
        /* stop the document-level "click outside closes the panel"
           handler from instantly re-closing what this tap opens */
        ev.stopPropagation();
        var tab = document.getElementById("jf-tab-space");
        if (document.body.classList.contains("jf-on") && tab) {
          document.body.classList.add("jf-space-open");
          tab.click();
        } else {
          openPanel();
          fixMobilePanel();
        }
      });
      if (settingsItem) nav.insertBefore(space, settingsItem); else nav.appendChild(space);
      document.addEventListener("click", function (ev) {
        if (ev.target.closest && ev.target.closest(".jf-space-item")) {
          document.body.classList.remove("jf-space-open");
        }
      });
    }
    var rail = document.querySelector(".wb-rail");
    if (rail && !rail.dataset.jtMobileBound) {
      rail.dataset.jtMobileBound = "1";
      rail.addEventListener("click", function (ev) {
        if (ev.target.closest && ev.target.closest(".wb-bot, .wb-bots button")) {
          document.body.classList.remove("jt-drawer-open");
        }
      });
      var lpTimer = null, lpX = 0, lpY = 0;
      rail.addEventListener("touchstart", function (ev) {
        if (!window.matchMedia("(max-width: 480px)").matches) return;
        if (ev.target.closest && ev.target.closest("button, .wb-bot, input, textarea, a")) return;
        var t = ev.touches[0]; lpX = t.clientX; lpY = t.clientY;
        lpTimer = setTimeout(function () {
          rail.dispatchEvent(new MouseEvent("contextmenu", {
            bubbles: true, cancelable: true, clientX: lpX, clientY: lpY
          }));
        }, 550);
      }, { passive: true });
      ["touchend", "touchmove", "touchcancel"].forEach(function (evt) {
        rail.addEventListener(evt, function () {
          if (lpTimer) { clearTimeout(lpTimer); lpTimer = null; }
        }, { passive: true });
      });
    }
    syncMobileMode();
  }

  /* On phones the shell drops the future theme's right glass screen
     (no .wb-right, no jf space), so the hub panel is the space UI in
     the future theme too — but jarvis-future-theme.css force-hides it
     with !important (desktop design). Mirror panelOpen into the
     inline display so the panel can open on mobile jf. */
  function fixMobilePanel() {
    if (!panel) return;
    if (!document.body.classList.contains("jf-on")) return;
    if (!window.matchMedia("(max-width: 480px)").matches) return;
    if (panelOpen) panel.style.setProperty("display", "block", "important");
    else panel.style.display = "none";
  }

  function syncBadges() {
    var c = JARVIS_TODO.count;
    document.querySelectorAll(".jt-boss-badge").forEach(function (el) {
      el.textContent = c === null ? "" : String(c);
      el.style.display = (c !== null && c > 0) ? "" : "none";
    });
  }

  function findNavItem(label) {
    var items = document.querySelectorAll(".os-sidebar .os-nav-item");
    for (var i = 0; i < items.length; i++) {
      if (items[i].textContent.trim().replace(/\s+/g, "") === label.replace(/\s+/g, "")) return items[i];
    }
    return null;
  }
  function ensureAllScope(cb) {
    if (findNavItem("能力中心") || findNavItem("自改确认")) { cb(); return; }
    var btns = document.querySelectorAll(".os-nav-scope-btn");
    var allBtn = null;
    btns.forEach(function (b) { if (b.textContent.indexOf("全部") >= 0) allBtn = b; });
    if (allBtn) allBtn.click();
    var tries = 0;
    var iv = setInterval(function () {
      tries++;
      if (findNavItem("能力中心") || tries > 12) { clearInterval(iv); cb(); }
    }, 150);
  }
  function navTo(label) {
    ensureAllScope(function () {
      var el = findNavItem(label);
      if (el) { el.click(); }
      else { toast("没找到真机入口「" + label + "」"); }
    });
  }
  function fillComposer(text) {
    var ta = document.querySelector(".wb-composer textarea");
    if (!ta) return false;
    var setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
    setter.call(ta, text);
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    ta.focus();
    return true;
  }
  function skillTo(text) {
    function attempt() {
      var btns = document.querySelectorAll(".wb-tries button, .wb-first-open button, .wb-intro button");
      for (var i = 0; i < btns.length; i++) {
        if (btns[i].textContent.trim() === text) { btns[i].click(); return true; }
      }
      return false;
    }
    if (document.querySelector(".wb-shell") && attempt()) return;
    navTo("操作台");
    var tries = 0;
    var iv = setInterval(function () {
      tries++;
      if (attempt()) { clearInterval(iv); return; }
      if (tries > 16) {
        clearInterval(iv);
        if (fillComposer(text)) toast("已把「" + text + "」填进输入框，发送即可");
        else toast("没找到快捷入口「" + text + "」");
      }
    }, 200);
  }

  function makeItem(it) {
    var b = document.createElement("button");
    b.type = "button"; b.className = "jt-hub-item";
    var s = document.createElement("span"); s.textContent = it.label; b.appendChild(s);
    if (it.badge) {
      var bd = document.createElement("span"); bd.className = "jt-boss-badge jt-item-badge";
      b.appendChild(bd);
    }
    b.addEventListener("click", function () {
      closePanel();
      if (it.type === "nav") navTo(it.target); else skillTo(it.target);
    });
    return b;
  }
  function buildPanel() {
    if (panel) return panel;
    panel = document.createElement("div");
    panel.id = "jt-boss-panel"; panel.className = "jt-boss-panel"; panel.style.display = "none";

    var head = document.createElement("div"); head.className = "jt-hub-head";
    var av = document.createElement("div"); av.className = "jt-boss-avatar"; av.textContent = "B";
    var who = document.createElement("div"); who.className = "jt-hub-who";
    var nm = document.createElement("div"); nm.className = "jt-hub-name"; nm.textContent = bossName();
    var rl = document.createElement("div"); rl.className = "jt-hub-role"; rl.textContent = "我的工作空间";
    who.appendChild(nm); who.appendChild(rl);
    var bd = document.createElement("span"); bd.className = "jt-boss-badge";
    head.appendChild(av); head.appendChild(who); head.appendChild(bd);
    panel.appendChild(head);

    var seg = document.createElement("div"); seg.className = "jt-seg";
    var dBtn = document.createElement("button"); dBtn.type = "button"; dBtn.textContent = "日常 · 8";
    var aBtn = document.createElement("button"); aBtn.type = "button"; aBtn.textContent = "全部 · 22";
    dBtn.addEventListener("click", function () { setFace("daily"); });
    aBtn.addEventListener("click", function () { setFace("all"); });
    seg.appendChild(dBtn); seg.appendChild(aBtn);
    panel.appendChild(seg);
    panel._dBtn = dBtn; panel._aBtn = aBtn;

    var daily = document.createElement("div"); daily.className = "jt-face jt-face-daily";
    var grid = document.createElement("div"); grid.className = "jt-daily-grid";
    DAILY.forEach(function (it) { grid.appendChild(makeItem(it)); });
    daily.appendChild(grid);
    panel.appendChild(daily);
    panel._daily = daily;

    var all = document.createElement("div"); all.className = "jt-face jt-face-all";
    ALL.forEach(function (g) {
      var lab = document.createElement("div"); lab.className = "jt-group-label"; lab.textContent = g.group;
      all.appendChild(lab);
      var list = document.createElement("div"); list.className = "jt-all-list";
      g.items.forEach(function (it) { list.appendChild(makeItem(it)); });
      if (g.collapsed) {
        list.style.display = "none";
        lab.classList.add("jt-group-label-toggle");
        lab.textContent = g.group + " · 点开";
        lab.addEventListener("click", function () {
          var open = list.style.display !== "none";
          list.style.display = open ? "none" : "";
          lab.textContent = g.group + (open ? " · 点开" : " · 收起");
        });
      }
      all.appendChild(list);
    });
    panel.appendChild(all);
    panel._all = all;

    var foot = document.createElement("div"); foot.className = "jt-hub-foot";
    var lo = document.createElement("button"); lo.type = "button"; lo.className = "jt-hub-logout";
    lo.textContent = "退出登录";
    lo.addEventListener("click", function () {
      closePanel();
      var real = document.querySelector(".os-logout");
      if (real) real.click(); else toast("没找到真机退出按钮");
    });
    foot.appendChild(lo);
    panel.appendChild(foot);

    document.body.appendChild(panel);
    setFace("daily");
    syncBadges();
    return panel;
  }
  function setFace(f) {
    panelFace = f;
    if (!panel) return;
    panel._daily.style.display = f === "daily" ? "" : "none";
    panel._all.style.display = f === "all" ? "" : "none";
    panel._dBtn.classList.toggle("jt-seg-on", f === "daily");
    panel._aBtn.classList.toggle("jt-seg-on", f === "all");
  }
  function openPanel() {
    buildPanel();
    panel.style.display = "";
    panelOpen = true;
    syncBadges();
    var nm = panel.querySelector(".jt-hub-name"); if (nm) nm.textContent = bossName();
    /* D-R5：键盘可达——打开面板把焦点送进面板第一个可聚焦元素，
       不让焦点留在面板外的卡片上（屏幕阅读器/Tab 用户找不到入口）。 */
    var first = panel.querySelector(".jt-seg button, .jt-hub-item, button");
    if (first) first.focus();
  }
  function closePanel() {
    if (panel) {
      /* 焦点还在面板里时先还给 Boss 卡，再隐藏，避免焦点掉回 body。 */
      if (panel.contains(document.activeElement)) {
        var card = document.getElementById("jt-boss-card") ||
                   document.getElementById("jt-boss-float");
        if (card) card.focus();
      }
      panel.style.display = "none";
    }
    panelOpen = false;
  }

  function makeBossCard(extraClass) {
    var card = document.createElement("button");
    card.type = "button"; card.className = "jt-boss-card " + (extraClass || "");
    var av = document.createElement("span"); av.className = "jt-boss-avatar"; av.textContent = "B";
    var tx = document.createElement("span"); tx.className = "jt-boss-text";
    var n1 = document.createElement("span"); n1.className = "jt-boss-name"; n1.textContent = bossName();
    var n2 = document.createElement("span"); n2.className = "jt-boss-role"; n2.textContent = "我的工作空间";
    tx.appendChild(n1); tx.appendChild(n2);
    var bd = document.createElement("span"); bd.className = "jt-boss-badge";
    var ch = document.createElement("span"); ch.className = "jt-boss-chev"; ch.textContent = "⌃";
    card.appendChild(av); card.appendChild(tx); card.appendChild(bd); card.appendChild(ch);
    card.addEventListener("click", function (ev) {
      ev.stopPropagation();
      if (panelOpen) closePanel(); else openPanel();
    });
    return card;
  }
  function ensureBoss() {
    var rail = document.querySelector(".wb-rail");
    var floatCard = document.getElementById("jt-boss-float");
    if (rail) {
      if (floatCard) floatCard.remove();
      if (!document.getElementById("jt-boss-card")) {
        var card = makeBossCard(""); card.id = "jt-boss-card";
        rail.appendChild(card);
        syncBadges();
      }
    } else if (document.querySelector(".os-app")) {
      if (!floatCard && !document.getElementById("jt-boss-card")) {
        var f = makeBossCard("jt-boss-float"); f.id = "jt-boss-float";
        document.body.appendChild(f);
        syncBadges();
      }
    }
  }

  document.addEventListener("click", function (ev) {
    if (!ev.target.closest || !ev.target.closest(".jt-menu")) closeMenu();
    if (panelOpen && panel && !panel.contains(ev.target) &&
        !ev.target.closest(".jt-boss-card")) closePanel();
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") { closeMenu(); closePanel(); }
  });

  var pending = false;
  var obs = new MutationObserver(function () {
    if (pending) return; pending = true;
    /* DOM-local upkeep only. Backend sync must NOT ride the mutation
       observer: the console mutates constantly, and each sync re-render
       mutated the DOM again — a self-sustaining ~13 req/s loop that
       tripped the gateway's global rate limit (429s in normal use). */
    setTimeout(function () { pending = false; ensureGroups(); ensureBoss(); syncBadges(); fixBell(); fixPageTitle(); fixEnterpriseApiLine(); fixLogin(); fixTopbar(); fixTextSwaps(); fixChatKey(); fixSettings(); fixCapability(); fixAccount(); fixRecoveryDialog(); fixAlerts(); ensureMobile(); fixMobilePanel(); }, 120);
  });
  /* ---------- V2(b) 简报播报播放器 ----------
   * 聊天里说「播报简报」后，后端合成语音并置 ready；这里轮询待播，
   * 逐段拉 wav 播放（浏览器出声）。停止：点小条上的「停止」或聊天
   * 里说「停」——服务端状态变 stopped，段间检查即停。浏览器若拦
   * 自动播放，小条变成「点这里听简报」，点一下继续。 */
  var bcState = { playingId: "", doneId: "", audio: null, localStop: false };
  function bcChip() {
    var chip = document.getElementById("jt-broadcast-chip");
    if (chip) return chip;
    chip = document.createElement("div");
    chip.id = "jt-broadcast-chip";
    chip.setAttribute("style",
      "position:fixed;left:50%;bottom:84px;transform:translateX(-50%);z-index:9999;" +
      "display:none;align-items:center;gap:10px;padding:8px 14px;border-radius:999px;" +
      "background:rgba(23,16,9,.92);color:#f3e9dc;border:1px solid rgba(200,130,60,.55);" +
      "font-size:13px;box-shadow:0 6px 24px rgba(0,0,0,.35)");
    chip.innerHTML = "<span id=\"jt-broadcast-label\">正在播报简报…</span>" +
      "<button type=\"button\" id=\"jt-broadcast-stop\" style=\"background:#c8823c;" +
      "color:#171009;border:0;border-radius:999px;padding:4px 12px;cursor:pointer;" +
      "font-size:13px\">停止</button>";
    document.body.appendChild(chip);
    chip.querySelector("#jt-broadcast-stop").addEventListener("click", function () {
      if (bcState.playingId) bcStop();
    });
    return chip;
  }
  function bcShow(label, btnText) {
    var chip = bcChip();
    chip.style.display = "flex";
    chip.querySelector("#jt-broadcast-label").textContent = label;
    chip.querySelector("#jt-broadcast-stop").textContent = btnText || "停止";
  }
  function bcHide() {
    var chip = document.getElementById("jt-broadcast-chip");
    if (chip) chip.style.display = "none";
  }
  function bcStop() {
    bcState.localStop = true;
    if (bcState.audio) { try { bcState.audio.pause(); } catch (e) {} }
    api("/v1/briefing/broadcast/stop", { method: "POST" });
    bcHide();
  }
  function bcFetchAudio(id, index) {
    var t = sessionToken();
    return fetch("/v1/briefing/broadcast/" + id + "/audio/" + index, {
      headers: { "X-Liuhao-Token": "Bearer " + t, "Authorization": "Bearer " + t }
    }).then(function (r) {
      if (!r.ok) throw new Error("http " + r.status);
      return r.blob();
    });
  }
  function bcPlayChunk(id, index) {
    return bcFetchAudio(id, index).then(function (blob) {
      return new Promise(function (resolve) {
        var url = URL.createObjectURL(blob);
        var audio = new Audio(url);
        bcState.audio = audio;
        var done = false;
        function finish(ok) {
          if (done) return;
          done = true;
          URL.revokeObjectURL(url);
          resolve(ok);
        }
        audio.addEventListener("ended", function () { finish(true); });
        audio.addEventListener("error", function () { finish(false); });
        var p = audio.play();
        if (p && p.catch) p.catch(function () {
          bcShow("简报已备好，点右边按钮开始听", "听简报");
          var btn = document.querySelector("#jt-broadcast-stop");
          function resume() {
            btn.removeEventListener("click", resume);
            bcShow("正在播报简报…", "停止");
            audio.play().catch(function () { finish(false); });
          }
          btn.addEventListener("click", resume);
        });
      });
    }).catch(function () { return false; });
  }
  function bcPlayAll(pending) {
    var id = pending.broadcast_id;
    bcState.playingId = id;
    bcState.localStop = false;
    bcShow("正在播报简报…", "停止");
    var chain = Promise.resolve(true);
    var makeStep = function (idx) {
      return function () {
        if (bcState.localStop || bcState.playingId !== id) return false;
        return api("/v1/briefing/broadcast/status").then(function (st) {
          if (st && st.status === "stopped") { bcState.localStop = true; return false; }
          return bcPlayChunk(id, idx);
        });
      };
    };
    for (var i = 0; i < pending.chunks; i++) chain = chain.then(makeStep(i));
    chain.then(function () {
      if (bcState.playingId === id) {
        bcState.playingId = "";
        bcState.doneId = id;
        bcHide();
        api("/v1/briefing/broadcast/" + id + "/played", { method: "POST" });
      }
    });
  }
  function pollBroadcast() {
    if (bcState.playingId) return;
    api("/v1/briefing/broadcast/pending").then(function (r) {
      var p = r && r.pending;
      if (!p || !p.broadcast_id) return;
      if (p.broadcast_id === bcState.doneId) return;
      bcPlayAll(p);
    });
  }

  /* ---------- V2(c) 手机推送：本机订阅助手 ----------
   * 总开关在服务端（聊天一句话开/关）。开关开着、且这台设备的浏览器
   * 还没订阅时，右下角出一条「开启本机推送」——点它才走浏览器授权
   * （权限请求必须由用户手势触发，系统不许代劳）。订阅完成后小条
   * 消失；关了总开关也不再出现。 */
  function urlB64ToUint8(b64) {
    var pad = "=".repeat((4 - (b64.length % 4)) % 4);
    var raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
    var arr = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
    return arr;
  }
  function pushChipShow() {
    if (document.getElementById("jt-push-chip")) return;
    var chip = document.createElement("div");
    chip.id = "jt-push-chip";
    chip.setAttribute("style",
      "position:fixed;right:14px;bottom:84px;z-index:9999;display:flex;" +
      "align-items:center;gap:10px;padding:8px 14px;border-radius:999px;" +
      "background:rgba(23,16,9,.92);color:#f3e9dc;border:1px solid rgba(200,130,60,.55);" +
      "font-size:13px;box-shadow:0 6px 24px rgba(0,0,0,.35)");
    chip.innerHTML = "<span>想在这台设备上收提醒推送？</span>" +
      "<button type=\"button\" style=\"background:#c8823c;color:#171009;border:0;" +
      "border-radius:999px;padding:4px 12px;cursor:pointer;font-size:13px\">开启本机推送</button>";
    chip.querySelector("button").addEventListener("click", function () {
      chip.remove();
      subscribePushHere();
    });
    document.body.appendChild(chip);
  }
  function subscribePushHere() {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) return;
    api("/v1/push/vapid-key").then(function (r) {
      if (!r || !r.public_key) return;
      navigator.serviceWorker.register("/v1/push/sw.js", { scope: "/push/" })
        .then(function (reg) {
          return Notification.requestPermission().then(function (perm) {
            if (perm !== "granted") return null;
            return reg.pushManager.subscribe({
              userVisibleOnly: true,
              applicationServerKey: urlB64ToUint8(r.public_key)
            });
          });
        })
        .then(function (sub) {
          if (!sub) return;
          api("/v1/push/subscriptions", {
            method: "POST", body: JSON.stringify(sub.toJSON())
          });
        })
        .catch(function () {});
    });
  }
  function pollPushOffer() {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) return;
    if (typeof Notification === "undefined" || Notification.permission === "denied") return;
    api("/v1/push/status").then(function (st) {
      if (!st || !st.enabled) return;
      if (st.subscriptions_here > 0) return;
      navigator.serviceWorker.getRegistration("/push/")
        .then(function (reg) {
          if (!reg) { pushChipShow(); return; }
          reg.pushManager.getSubscription().then(function (sub) {
            if (!sub) pushChipShow();
          });
        })
        .catch(function () {});
    });
  }

  function boot() {
    ensureGroups(); ensureBoss(); syncBadges(); fixLogin(); fixTopbar(); ensureMobile();
    window.addEventListener("resize", syncMobileMode);
    pollTodoCount(); pollAuditDev();
    [4000, 12000].forEach(function (delay) {
      setTimeout(function () { pollTodoCount(); pollAuditDev(); }, delay);
    });
    setInterval(pollTodoCount, 15000);
    setInterval(pollAuditDev, 30000);
    syncFromBackend();
    /* The console logs in SPA-style (no page reload), so at boot there is
       usually no session token yet and the first sync no-ops, leaving the
       rail in local-fallback mode (backendOk=false) until the 60s tick:
       groups invisible after login, and creates/deletes silently local
       for up to a minute. Retry briefly until the first sync lands;
       the signature check in syncFromBackend makes retries that find
       no change cost one GET and no re-render. */
    [1500, 4000, 10000, 25000].forEach(function (delay) {
      setTimeout(function () { if (!backendOk) syncFromBackend(); }, delay);
    });
    setInterval(syncFromBackend, 60000);
    setInterval(pollBroadcast, 4000);
    setInterval(pollPushOffer, 60000);
    setTimeout(pollPushOffer, 5000);
    window.addEventListener("focus", syncFromBackend);
    if (document.body) obs.observe(document.body, { childList: true, subtree: true });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
