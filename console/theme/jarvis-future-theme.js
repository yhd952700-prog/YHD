/* Jarvis theme overlay — FUTURE theme (0.4.6-future-theme).
   Sits on top of the classic espresso overlay (jarvis-theme.js); loads after it.

   Design contract (owner-approved effect page, final version):
   - Deep blue-black + ice-blue hologram. Workbench becomes three columns:
     LEFT glass panel = the real chat column (real messages, real approval
     cards, real composer — nothing is moved in the DOM, CSS repositions
     the existing .wb-main / .wb-right inside .wb-shell);
     CENTER = holo 小铁 stage (HUD rings + progress arc + central voice
     button); RIGHT glass panel = "数据 / 我的空间" tabs — the data face is
     the console's own right rail content, the space face re-implements the
     classic Boss hub items and activates the REAL nav buttons / chat
     capsules, exactly like jarvis-theme.js does.
   - Theme switch （未来 / 经典） lives in the topbar in BOTH modes and is
     persisted in localStorage; switching reloads the page so the classic
     layout comes back untouched (all future CSS is scoped to body.jf-on).
   - Voice: the central button forwards press-and-hold to the console's
     real .wb-voice-btn （按住说话）. If that button is absent it falls back
     to the browser SpeechRecognition API; if neither exists it shows the
     listening UI and says so honestly in a toast.
   - Progress arc: a MutationObserver watches the chat list — while
     .wb-thinking is present the arc charges towards 90%; when a new bot
     message lands it completes, flashes and resets. Approximation of real
     progress (the backend exposes no percentage), driven by real DOM state.
   - Approval reminder: the space-tab / item number badges show the
     REAL pending-approval count (published by jarvis-theme.js as
     window.JARVIS_TODO from /v1/approvals/summary — the 待办审批 page's
     own number; walkthrough adjudication 2026-11-18). Real approval
     buttons inside the chat light the right panel's amber rim. The
     topbar bell badge is never used as a number source any more: it
     counts synthesized status notifications, not todos.
   - No slogan: the welcome line "我是贾维斯…" is hidden by CSS in this
     theme only (owner's call); the quick-action capsules stay. */
(function () {
  "use strict";

  var MODE_KEY = "jarvis.theme.mode.v1";
  var QUIET_KEY = "jarvis.future.quiet.v1";
  function mode() {
    try { return localStorage.getItem(MODE_KEY) || "future"; } catch (e) { return "future"; }
  }
  var FUTURE = mode() !== "classic";
  if (FUTURE) document.body.classList.add("jf-on");
  if (FUTURE && localStorage.getItem(QUIET_KEY) === "1") document.body.classList.add("jf-quiet");

  /* ============ PWA 状态栏配色（多端线 0.4.6-devices） ============
     内置 mode.ts 按它自己的深/浅主题写 meta theme-color（深色 #050912），
     与经典 overlay 的深咖底 #171009 不一致——装到手机主屏后状态栏会露出一道
     藏蓝。这里按 overlay 的实际底色纠正，并用 MutationObserver 守住 mode.ts
     在主题切换时的改写（它只写 meta、不监听 meta，无循环风险）。 */
  var EXPECTED_TC = FUTURE ? "#050912" : "#171009";
  function fixThemeColor() {
    var m = document.querySelector('meta[name="theme-color"]');
    if (m && m.getAttribute("content") !== EXPECTED_TC) m.setAttribute("content", EXPECTED_TC);
  }
  fixThemeColor();
  if (document.head && window.MutationObserver) {
    new MutationObserver(fixThemeColor).observe(document.head, {
      attributes: true, attributeFilter: ["content"], subtree: true,
    });
  }

  function toast(msg) {
    var old = document.querySelector(".jt-toast"); if (old) old.remove();
    var t = document.createElement("div"); t.className = "jt-toast"; t.textContent = msg;
    t.style.zIndex = "400";
    document.body.appendChild(t); setTimeout(function () { t.remove(); }, 2800);
  }

  /* ================= theme switch (both modes) ================= */
  function ensureSwitch() {
    var host = document.querySelector(".os-topbar-actions") || document.querySelector(".os-topbar");
    if (!host || document.getElementById("jf-switch")) return;
    var wrap = document.createElement("span"); wrap.className = "jf-switch"; wrap.id = "jf-switch";
    var bf = document.createElement("button"); bf.type = "button"; bf.textContent = "未来";
    bf.className = FUTURE ? "jf-on-btn" : "jf-future-btn";
    var bc = document.createElement("button"); bc.type = "button"; bc.textContent = "经典";
    bc.className = FUTURE ? "jf-classic-btn" : "jf-on-btn";
    bf.addEventListener("click", function () {
      if (FUTURE) return;
      try { localStorage.setItem(MODE_KEY, "future"); } catch (e) {}
      location.reload();
    });
    bc.addEventListener("click", function () {
      if (!FUTURE) return;
      try { localStorage.setItem(MODE_KEY, "classic"); } catch (e) {}
      location.reload();
    });
    wrap.appendChild(bf); wrap.appendChild(bc);
    host.appendChild(wrap);
  }

  if (!FUTURE) {
    /* classic mode: only the switch is injected, nothing else changes */
    var ivC = setInterval(function () { ensureSwitch(); if (document.getElementById("jf-switch")) clearInterval(ivC); }, 400);
    setTimeout(function () { clearInterval(ivC); }, 20000);
    return;
  }

  /* ================= shared real-action helpers (same contract as classic hub) ================= */
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
  /* 27 项裁决（机主走查 §4，2026-11-18 并入 V1.5）：留 13 / 改名 3 /
     合并 4 / 藏深 6 / 降级 1 → 全部 22 项。label = 显示名（改名后），
     target = 壳侧栏原名（导航锚点，不许改）。藏深 6 进「高级」折叠组；
     自改确认撤下清单（有提案时由聊天卡顶出，壳侧行为）。 */
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
  function findNavItem(label) {
    var items = document.querySelectorAll(".os-sidebar .os-nav-item");
    for (var i = 0; i < items.length; i++) {
      if (items[i].textContent.trim().replace(/\s+/g, "") === label.replace(/\s+/g, "")) return items[i];
    }
    return null;
  }
  function ensureAllScope(cb) {
    if (findNavItem("能力中心") || findNavItem("自改确认")) { cb(); return; }
    var allBtn = null;
    document.querySelectorAll(".os-nav-scope-btn").forEach(function (b) {
      if (b.textContent.indexOf("全部") >= 0) allBtn = b;
    });
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
      if (el) el.click(); else toast("没找到真机入口「" + label + "」");
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
    if (document.querySelector(".wb-shell")) {
      if (attempt()) return;
      /* already on the workbench with a live composer: fill immediately
         instead of the classic hub's nav-and-poll detour (~3.2s) */
      if (fillComposer(text)) { toast("已把「" + text + "」填进输入框，发送即可"); return; }
    }
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

  /* ================= topbar status + quiet ================= */
  var clockTimer = null;
  function ensureStatus() {
    var host = document.querySelector(".os-topbar-actions");
    if (!host || document.getElementById("jf-status")) return;
    var s = document.createElement("span"); s.className = "jf-status"; s.id = "jf-status";
    var sec = document.createElement("span"); sec.className = "jf-secure"; sec.textContent = "SECURE LINK · ENCRYPTED";
    var on = document.createElement("span"); on.className = "jf-online"; on.textContent = "ONLINE";
    var ck = document.createElement("span"); ck.className = "jf-clock"; ck.id = "jf-clock"; ck.textContent = "--:--:--";
    var qb = document.createElement("button"); qb.type = "button"; qb.className = "jf-quiet-btn"; qb.id = "jf-quiet-btn";
    qb.textContent = document.body.classList.contains("jf-quiet") ? "安静 · 开" : "安静";
    qb.addEventListener("click", function () {
      var on2 = document.body.classList.toggle("jf-quiet");
      try { localStorage.setItem(QUIET_KEY, on2 ? "1" : "0"); } catch (e) {}
      qb.textContent = on2 ? "安静 · 开" : "安静";
    });
    s.appendChild(sec); s.appendChild(on); s.appendChild(ck); s.appendChild(qb);
    host.appendChild(s);
    function tick() {
      var d = new Date();
      function p(n) { return (n < 10 ? "0" : "") + n; }
      ck.textContent = p(d.getUTCHours()) + ":" + p(d.getUTCMinutes()) + ":" + p(d.getUTCSeconds()) + " UTC";
    }
    tick();
    if (clockTimer) clearInterval(clockTimer);
    clockTimer = setInterval(tick, 1000);
  }

  /* ================= center stage ================= */
  var ARC_C = 2 * Math.PI * 46; /* r=46 in a 120 viewBox */
  function buildStage() {
    var shell = document.querySelector(".wb-shell");
    if (!shell || document.getElementById("jf-stage")) return;
    var st = document.createElement("div"); st.id = "jf-stage";
    st.innerHTML =
      '<div class="jf-hud">' +
        '<svg class="jf-rings" viewBox="0 0 120 130" aria-hidden="true">' +
          '<g class="jf-ring-a" fill="none" stroke="rgba(111,211,255,.5)" stroke-width=".7">' +
            '<circle cx="60" cy="65" r="57" stroke-dasharray="4 5"/>' +
            '<circle cx="60" cy="65" r="52" stroke-dasharray="1 7" stroke="rgba(111,211,255,.35)"/>' +
          '</g>' +
          '<g class="jf-ring-b" fill="none" stroke="rgba(56,182,232,.4)" stroke-width=".7">' +
            '<circle cx="60" cy="65" r="49" stroke-dasharray="14 9"/>' +
          '</g>' +
          '<circle class="jf-arc" id="jf-arc" cx="60" cy="65" r="46" fill="none" stroke="#6fd3ff" stroke-width="2.2" stroke-linecap="round" transform="rotate(-90 60 65)" stroke-dasharray="' + ARC_C.toFixed(1) + '" stroke-dashoffset="' + ARC_C.toFixed(1) + '"/>' +
        '</svg>' +
        '<div class="jf-mascot-wrap"><img src="/theme/assets/holo-mascot.webp" alt="小铁 · 贾维斯全息投影"></div>' +
        '<div class="jf-mood">?</div>' +
      '</div>' +
      '<div class="jf-base"></div>' +
      '<div class="jf-voice-zone">' +
        '<button type="button" id="jf-voice" aria-label="按住说话">' +
          '<span class="jf-bar"></span><span class="jf-bar"></span><span class="jf-bar"></span><span class="jf-bar"></span><span class="jf-bar"></span>' +
        '</button>' +
        '<div class="jf-voice-hint" id="jf-voice-hint">按 住 说 话</div>' +
      '</div>';
    shell.appendChild(st);
    wireVoice(st);
  }

  /* ================= voice: forward to the real voice button ================= */
  var recog = null;
  var voiceHold = false;      /* user is holding the central voice button */
  var voiceRemote = "";       /* last state mirrored from the backend session */
  function setListening(onState) {
    var st = document.getElementById("jf-stage");
    if (st) st.classList.toggle("is-listening", onState);
    var hint = document.getElementById("jf-voice-hint");
    if (hint && !voiceRemote) hint.textContent = onState ? "正 在 聆 听" : "按 住 说 话";
  }
  /* V1 语音状态联动：后端会话状态机（/v1/voice/status 的 session.state）
     映射到中央语音钮的四态——聆听（listening/awake）/ 转写（transcribing）
     / 思考（thinking）/ 播报（speaking）。轮询失败静默跳过，按住说话的
     本地镜像优先（voiceHold 期间不让远端状态抢显示）。 */
  function applyVoiceState(state) {
    var st = document.getElementById("jf-stage");
    if (!st) return;
    var hint = document.getElementById("jf-voice-hint");
    st.classList.toggle("is-speaking", state === "speaking");
    if (voiceHold) return;
    voiceRemote = state || "";
    if (state === "listening" || state === "awake") {
      st.classList.add("is-listening");
      if (hint) hint.textContent = state === "awake" ? "我 在 呢，请 说" : "正 在 聆 听";
    } else if (state === "transcribing" || state === "thinking") {
      st.classList.remove("is-listening");
      if (hint) hint.textContent = state === "transcribing" ? "正 在 识 别" : "思 考 中";
    } else if (state === "speaking") {
      st.classList.remove("is-listening");
      if (hint) hint.textContent = "正 在 播 报";
    } else {
      st.classList.remove("is-listening");
      voiceRemote = "";
      if (hint) hint.textContent = "按 住 说 话";
    }
  }
  setInterval(function () {
    if (!document.body || !document.body.classList.contains("jf-on")) return;
    fetch("/v1/voice/status", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (data && data.session) applyVoiceState(data.session.state || "");
      })
      .catch(function () { /* 状态拿不到就保持本地镜像，不报错 */ });
  }, 3000);
  function wireVoice(st) {
    var btn = st.querySelector("#jf-voice");
    var holding = false;
    function down(ev) {
      ev.preventDefault();
      if (holding) return; holding = true; voiceHold = true;
      var real = document.querySelector(".wb-voice-btn");
      if (real) {
        setListening(true);
        ["pointerdown", "mousedown"].forEach(function (type) {
          real.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true }));
        });
        return;
      }
      var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (SR) {
        try {
          recog = new SR(); recog.lang = "zh-CN"; recog.interimResults = false;
          recog.onresult = function (e) {
            var text = e.results[0][0].transcript;
            if (fillComposer(text)) toast("语音识别：「" + text + "」已填入输入框");
          };
          recog.onend = function () { setListening(false); recog = null; };
          recog.onerror = function () { setListening(false); recog = null; };
          recog.start(); setListening(true); return;
        } catch (e) { /* fall through */ }
      }
      setListening(true);
      setTimeout(function () {
        setListening(false);
        toast("这台机器上没找到可用的语音入口（真机语音钮未渲染、浏览器也不支持语音识别）");
      }, 2200);
    }
    function up() {
      if (!holding) return; holding = false; voiceHold = false;
      var real = document.querySelector(".wb-voice-btn");
      if (real) {
        ["pointerup", "mouseup", "click"].forEach(function (type) {
          real.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true }));
        });
        setTimeout(function () { setListening(false); }, 600);
      }
      if (recog) { try { recog.stop(); } catch (e) {} }
    }
    btn.addEventListener("pointerdown", down);
    window.addEventListener("pointerup", up);
    btn.addEventListener("pointerleave", function () { if (holding && recog) up(); });
  }

  /* 待办角标跟随 jarvis-theme.js 的 JARVIS_TODO 轮询（15s）刷新 */
  setInterval(function () {
    if (document.body && document.body.classList.contains("jf-on")) syncApproval();
  }, 5000);

  /* ================= right panel: tabs + my space ================= */
  var rightTab = "data", spaceFace = "daily";
  function todoCount() {
    var t = window.JARVIS_TODO;
    return (t && typeof t.count === "number") ? t.count : null;
  }
  function makeSpaceItem(it) {
    var b = document.createElement("button");
    b.type = "button"; b.className = "jf-space-item";
    var s = document.createElement("span"); s.textContent = it.label; b.appendChild(s);
    if (it.badge) {
      var bd = document.createElement("span"); bd.className = "jf-item-badge jf-badge-slot";
      bd.style.display = "none"; b.appendChild(bd);
    }
    b.addEventListener("click", function () {
      showTab("data");
      if (it.type === "nav") navTo(it.target); else skillTo(it.target);
    });
    return b;
  }
  function buildSpace(host) {
    var box = document.createElement("div"); box.className = "jf-space"; box.id = "jf-space"; box.style.display = "none";
    var seg = document.createElement("div"); seg.className = "jf-space-seg";
    var dBtn = document.createElement("button"); dBtn.type = "button"; dBtn.textContent = "日常 · 8";
    var aBtn = document.createElement("button"); aBtn.type = "button"; aBtn.textContent = "全部 · 22";
    dBtn.addEventListener("click", function () { setSpaceFace("daily"); });
    aBtn.addEventListener("click", function () { setSpaceFace("all"); });
    seg.appendChild(dBtn); seg.appendChild(aBtn); box.appendChild(seg);
    var daily = document.createElement("div"); daily.className = "jf-space-grid"; daily.id = "jf-space-daily";
    DAILY.forEach(function (it) { daily.appendChild(makeSpaceItem(it)); });
    box.appendChild(daily);
    var all = document.createElement("div"); all.id = "jf-space-all"; all.style.display = "none";
    ALL.forEach(function (g) {
      var lab = document.createElement("div"); lab.className = "jf-space-label"; lab.textContent = g.group;
      all.appendChild(lab);
      var list = document.createElement("div"); list.className = "jf-space-list";
      g.items.forEach(function (it) { list.appendChild(makeSpaceItem(it)); });
      if (g.collapsed) {
        list.style.display = "none";
        lab.classList.add("jf-space-label-toggle");
        lab.textContent = g.group + " · 点开";
        lab.addEventListener("click", function () {
          var open = list.style.display !== "none";
          list.style.display = open ? "none" : "";
          lab.textContent = g.group + (open ? " · 点开" : " · 收起");
        });
      }
      all.appendChild(list);
    });
    box.appendChild(all);
    box._dBtn = dBtn; box._aBtn = aBtn;
    host.appendChild(box);
    setSpaceFace("daily");
    return box;
  }
  function setSpaceFace(f) {
    spaceFace = f;
    var box = document.getElementById("jf-space"); if (!box) return;
    var d = document.getElementById("jf-space-daily"), a = document.getElementById("jf-space-all");
    if (d) d.style.display = f === "daily" ? "" : "none";
    if (a) a.style.display = f === "all" ? "" : "none";
    box._dBtn.classList.toggle("jf-seg-on", f === "daily");
    box._aBtn.classList.toggle("jf-seg-on", f === "all");
  }
  function showTab(which) {
    rightTab = which;
    var body = document.querySelector(".wb-right-body");
    var space = document.getElementById("jf-space");
    var tD = document.getElementById("jf-tab-data"), tS = document.getElementById("jf-tab-space");
    if (body) body.style.display = which === "data" ? "" : "none";
    if (space) space.style.display = which === "space" ? "" : "none";
    if (tD) tD.classList.toggle("jf-tab-on", which === "data");
    if (tS) tS.classList.toggle("jf-tab-on", which === "space");
  }
  function buildTabs() {
    var right = document.querySelector(".wb-right");
    if (!right || document.getElementById("jf-tabs")) return;
    var tabs = document.createElement("div"); tabs.className = "jf-tabs"; tabs.id = "jf-tabs";
    var tD = document.createElement("button"); tD.type = "button"; tD.id = "jf-tab-data"; tD.textContent = "数据"; tD.className = "jf-tab-on";
    var tS = document.createElement("button"); tS.type = "button"; tS.id = "jf-tab-space"; tS.textContent = "我的空间";
    var bd = document.createElement("span"); bd.className = "jf-tab-badge"; bd.id = "jf-tab-badge"; bd.style.display = "none";
    tS.appendChild(bd);
    tD.addEventListener("click", function () { showTab("data"); });
    tS.addEventListener("click", function () { showTab("space"); });
    tabs.appendChild(tD); tabs.appendChild(tS);
    right.insertBefore(tabs, right.firstChild);
    buildSpace(right);
  }

  /* ================= approval reminder + badges ================= */
  function approvalSignal() {
    var count = todoCount();
    var btns = document.querySelectorAll(
      ".wb-chat button, .wb-inbox-item button, .wb-main button");
    var pending = 0;
    btns.forEach(function (b) {
      var t = b.textContent.trim();
      if (t === "批准" || t === "同意" || t === "确认执行" || t === "通过") pending++;
    });
    return { count: count, pendingButtons: pending };
  }
  function syncApproval() {
    var sig = approvalSignal();
    /* the amber rim means "an approval is waiting on screen" — real
       approval buttons only. The number badges show the real pending
       count from /v1/approvals/summary (window.JARVIS_TODO); until the
       first poll lands they stay hidden rather than show a wrong
       number. */
    document.body.classList.toggle("jf-has-approval", sig.pendingButtons > 0);
    var show = sig.count !== null && sig.count > 0;
    var bd = document.getElementById("jf-tab-badge");
    if (bd) {
      bd.textContent = show ? String(sig.count) : "";
      bd.style.display = show ? "" : "none";
    }
    document.querySelectorAll(".jf-badge-slot").forEach(function (el) {
      el.textContent = show ? String(sig.count) : "";
      el.style.display = show ? "" : "none";
    });
  }

  /* ================= progress arc + mascot moods ================= */
  var arcTimer = null, arcVal = 0, wasBusy = false, lastBotCount = 0;
  function setArc(v) {
    arcVal = v;
    var arc = document.getElementById("jf-arc");
    if (arc) arc.style.strokeDashoffset = String(ARC_C * (1 - v));
  }
  function startArc() {
    stopArc();
    setArc(0.02);
    var started = Date.now();
    arcTimer = setInterval(function () {
      var t = (Date.now() - started) / 7000;
      setArc(Math.min(0.9, 0.02 + 0.88 * (1 - Math.pow(1 - Math.min(t, 1), 2))));
    }, 120);
  }
  function stopArc() { if (arcTimer) { clearInterval(arcTimer); arcTimer = null; } }
  function finishArc() {
    stopArc();
    setArc(1);
    var st = document.getElementById("jf-stage");
    if (st) {
      st.classList.add("is-flash", "is-done");
      setTimeout(function () { st.classList.remove("is-flash", "is-done"); setArc(0); }, 1100);
    } else setArc(0);
  }
  function confuse() {
    var st = document.getElementById("jf-stage");
    if (!st) return;
    st.classList.add("is-confused");
    setTimeout(function () { st.classList.remove("is-confused"); }, 2600);
  }
  function watchChat() {
    var list = document.querySelector(".wb-chat-list");
    if (!list || list.dataset.jfWatch) return;
    list.dataset.jfWatch = "1";
    lastBotCount = list.querySelectorAll(".wb-msg-bot").length;
    var pending = false;
    var obs = new MutationObserver(function () {
      if (pending) return; pending = true;
      setTimeout(function () {
        pending = false;
        var busy = !!list.querySelector(".wb-thinking");
        var st = document.getElementById("jf-stage");
        if (st) st.classList.toggle("is-busy", busy);
        if (busy && !wasBusy) startArc();
        if (!busy && wasBusy) {
          var bots = list.querySelectorAll(".wb-msg-bot").length;
          if (bots > lastBotCount) {
            finishArc();
            var last = list.querySelectorAll(".wb-msg-bot");
            var txt = last.length ? last[last.length - 1].innerText : "";
            if (/没太听懂|没听懂|无法理解|换个说法/.test(txt)) confuse();
          } else stopArc(), setArc(0);
          lastBotCount = bots;
        }
        wasBusy = busy;
        /* native voice confirm bar mirrors listening state */
        if (document.querySelector(".wb-voice-confirm")) setListening(true);
        syncApproval();
      }, 150);
    });
    obs.observe(list, { childList: true, subtree: true });
  }

  /* ================= idle dimming ================= */
  var dimTimer = null;
  function armDim() {
    document.body.classList.remove("jf-dim");
    if (dimTimer) clearTimeout(dimTimer);
    dimTimer = setTimeout(function () {
      if (!document.body.classList.contains("jf-quiet")) document.body.classList.add("jf-dim");
    }, 6000);
  }
  ["pointermove", "pointerdown", "keydown"].forEach(function (ev) {
    document.addEventListener(ev, function () { if (document.body.classList.contains("jf-on")) armDim(); }, { passive: true });
  });

  /* ================= boot / keep-alive under SPA re-renders ================= */
  var tickPending = false;
  function ensureAll() {
    ensureSwitch(); ensureStatus(); buildStage(); buildTabs(); watchChat(); syncApproval();
  }
  var obs = new MutationObserver(function () {
    if (tickPending) return; tickPending = true;
    setTimeout(function () { tickPending = false; ensureAll(); }, 200);
  });
  function boot() {
    ensureAll(); armDim();
    if (document.body) obs.observe(document.body, { childList: true, subtree: true });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
