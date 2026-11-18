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
   - Approval reminder: watches the console topbar badge (.os-icon-btn-badge)
     and real approval buttons inside the chat; lights the right panel's
     amber rim + the space-tab badge.
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
    { label: "记提醒", type: "skill", target: CAP.remind },
    { label: "建 Bot", type: "skill", target: CAP.bot },
    { label: "知识中心", type: "nav", target: "知识中心" }
  ];
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
  function setListening(onState) {
    var st = document.getElementById("jf-stage");
    if (st) st.classList.toggle("is-listening", onState);
    var hint = document.getElementById("jf-voice-hint");
    if (hint) hint.textContent = onState ? "正 在 聆 听" : "按 住 说 话";
  }
  function wireVoice(st) {
    var btn = st.querySelector("#jf-voice");
    var holding = false;
    function down(ev) {
      ev.preventDefault();
      if (holding) return; holding = true;
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
      if (!holding) return; holding = false;
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

  /* ================= right panel: tabs + my space ================= */
  var rightTab = "data", spaceFace = "daily";
  function badgeCount() {
    var b = document.querySelector(".os-icon-btn-badge");
    if (!b) return 0;
    var v = parseInt(b.textContent.trim(), 10);
    return isNaN(v) ? 0 : v;
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
    var aBtn = document.createElement("button"); aBtn.type = "button"; aBtn.textContent = "全部 · 27";
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
    var count = badgeCount();
    var btns = document.querySelectorAll(
      ".wb-chat button, .wb-inbox-item button, .wb-main button");
    var pending = 0;
    btns.forEach(function (b) {
      var t = b.textContent.trim();
      if (t === "批准" || t === "同意" || t === "确认执行" || t === "通过") pending++;
    });
    return { count: Math.max(count, pending), pendingButtons: pending };
  }
  function syncApproval() {
    var sig = approvalSignal();
    /* the amber rim means "an approval is waiting on screen" — real
       approval buttons only. The topbar badge also counts notifications,
       so it feeds the number badge but never lights the rim by itself. */
    document.body.classList.toggle("jf-has-approval", sig.pendingButtons > 0);
    var bd = document.getElementById("jf-tab-badge");
    if (bd) {
      bd.textContent = String(sig.count);
      bd.style.display = sig.count > 0 ? "" : "none";
    }
    document.querySelectorAll(".jf-badge-slot").forEach(function (el) {
      el.textContent = String(sig.count);
      el.style.display = sig.count > 0 ? "" : "none";
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
