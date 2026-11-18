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
   - The Boss badge mirrors the console's own topbar reminder badge
     (.os-icon-btn-badge) — the console exposes no separate approval
     counter in its shell, so the badge is that real number, not an
     approval-specific count.
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
    { label: "记提醒", type: "skill", target: CAP.remind },
    { label: "建 Bot", type: "skill", target: CAP.bot },
    { label: "知识中心", type: "nav", target: "知识中心" }
  ];
  /* 全部 · 27 = 真机导航 23 个页面 + 4 个聊天本事胶囊。
     合并掉的真机导航项：贾维斯（就是聊天本身）、旧版操作台（与操作台重复）、
     全局总览（并入企业总览）、组织架构（并入我的员工）。 */
  var ALL = [
    { group: "干活", items: [
      { label: "操作台", type: "nav", target: "操作台" },
      { label: "企业总览", type: "nav", target: "企业总览" },
      { label: "任务中心", type: "nav", target: "任务中心" },
      { label: "待办审批", type: "nav", target: "待办审批", badge: true },
      { label: "提醒", type: "nav", target: "提醒" },
      { label: "工作区文件", type: "nav", target: "工作区文件" },
      { label: "办公室", type: "nav", target: "办公室" },
      { label: "我的员工", type: "nav", target: "我的员工" },
      { label: "全部任务与审计", type: "nav", target: "全部任务与审计" }
    ]},
    { group: "本事", items: [
      { label: "能力中心", type: "nav", target: "能力中心" },
      { label: "查资料", type: "skill", target: CAP.search },
      { label: "写邮件", type: "skill", target: CAP.mail },
      { label: "记提醒", type: "skill", target: CAP.remind },
      { label: "建 Bot", type: "skill", target: CAP.bot }
    ]},
    { group: "管理", items: [
      { label: "知识中心", type: "nav", target: "知识中心" },
      { label: "我的数据", type: "nav", target: "我的数据" },
      { label: "成员与权限", type: "nav", target: "成员与权限" },
      { label: "系统设置", type: "nav", target: "系统设置" },
      { label: "我的账号与安全", type: "nav", target: "我的账号与安全" },
      { label: "审批签发", type: "nav", target: "审批签发" },
      { label: "备份与迁移", type: "nav", target: "备份与迁移" },
      { label: "主机命令白名单", type: "nav", target: "主机命令白名单" },
      { label: "通知中心", type: "nav", target: "通知中心" },
      { label: "生产预检", type: "nav", target: "生产预检" },
      { label: "关于与隐私", type: "nav", target: "关于与隐私" },
      { label: "系统状态与诊断", type: "nav", target: "系统状态与诊断" },
      { label: "自改确认", type: "nav", target: "自改确认" }
    ]}
  ];

  var panel = null, panelFace = "daily", panelOpen = false;

  function bossName() {
    var n = document.querySelector(".os-user-name");
    return (n && n.textContent.trim()) || "Boss";
  }
  function badgeCount() {
    var b = document.querySelector(".os-icon-btn-badge");
    if (!b) return 0;
    var v = parseInt(b.textContent.trim(), 10);
    return isNaN(v) ? 0 : v;
  }
  function syncBadges() {
    var c = badgeCount();
    document.querySelectorAll(".jt-boss-badge").forEach(function (el) {
      el.textContent = String(c);
      el.style.display = c > 0 ? "" : "none";
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
    var aBtn = document.createElement("button"); aBtn.type = "button"; aBtn.textContent = "全部 · 27";
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
    setTimeout(function () { pending = false; ensureGroups(); ensureBoss(); syncBadges(); }, 120);
  });
  function boot() {
    ensureGroups(); ensureBoss(); syncBadges();
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
    window.addEventListener("focus", syncFromBackend);
    if (document.body) obs.observe(document.body, { childList: true, subtree: true });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
