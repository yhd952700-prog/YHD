# Shell Adapter — Threat Model

**Component:** `src/ai/world_interface.py` → `ShellAdapter` + `WorldInterface`
**Owner authorization (binding):** *"If ShellAdapter has security risk, finish the
threat model, sandbox/capability boundary, tests and safety gate, then decide when
to enable."*
**Decision (this increment):** Shell is **NOT enabled** in the autonomous goal
path by default. The safety gate in `src/ai/world_interface_shell_gate.py` is the
only sanctioned escape hatch, and it is fail-closed.

---

## 1. Command injection (CWE-78)

`ShellAdapter.execute` runs `subprocess.run(argv, shell=use_shell)`.

* **Default (`shell=False`):** the command string is first passed through
  `shlex.split` and executed as an **argv list**. Shell metacharacters
  (`;`, `|`, `&&`, `>`, `$()`) are *not* interpreted — they become ordinary
  string arguments to the first token. This **closes the command-injection
  surface** for the common case: an attacker-supplied `echo hi; rm -rf /`
  is tokenized literally and `rm` never executes as a separate command.
* **`params={"shell": True}` re-opens a real shell.** The entire string is handed
  to `/bin/sh -c`. Any shell grammar is now live → injection is possible. This is
  an explicit opt-in that must be paired with human authorization (see §4).

> Note: `shell=False` removes *shell grammar* but is **not** a sandbox — argv
> `[["rm", "-rf", "/"]]` would still execute. Containment must come from policy,
> not from shlex.

## 2. Privilege & blast radius (CWE-250 / CWE-732)

The subprocess runs as the **LIUHAO OS process user** with that user's full
ambient authority. Consequences:

* **Whole-filesystem reach.** Workspace path-containment is **insufficient** for
  shell: a shell can `cd` elsewhere or use absolute paths; `shlex` does nothing
  to constrain the executable or its arguments. Path-containment only bounds the
  *filesystem adapter*, not a shell.
* **Network egress.** The command can reach the network (exfiltration,
  C2 callbacks, SSRF-style pivots) with no kernel-level egress control here.
* **Exfiltration / data destruction.** Reading secrets, tampering with the host,
  or destroying data are all in scope. The action inherits the OS process's
  trust, not the (narrower) workspace trust.

The blast radius is therefore the **entire host**, not the workspace.

## 3. Why shell must stay out of the autonomous default path

* The autonomous actor model is **default-deny**; human is default-allow
  (human sovereignty). A shell in autonomous mode is a host-command capability
  with host-wide blast radius and (under `shell=True`) an open injection surface.
* The goal-execution `ToolRegistry` (`register_default_local_tools`) registers
  only `python_compute` + `file_write` — `ShellAdapter` is **not** registered
  there, so the autonomous GOAL path is already fail-closed **by absence**.
* `WorldInterface` reinforces this: for `actor="autonomous"`, `shell=True` is
  blocked unless the injected `authorize` policy explicitly permits it, and a
  missing policy is default-deny. The safety gate in §4 hardens construction and
  per-request arming on top of that.

## 4. Recommended gating if shell is ever armed

Enforced by `make_world_interface_with_shell` / `build_shell_world_request`:

1. **Explicit, per-request, logged human authorization.** `shell=True` requires a
   `human_arm_token` on every request; it is recorded (masked) and logged. No
   blanket-allow; no standing enablement.
2. **Deny-by-default.** An autonomous interface refuses to even build without an
   explicit `authorize` policy. Shell requests are default-deny; only an
   authorize policy that *explicitly* permits the request, plus a valid token,
   allow it.
3. **Construction fail-closed.** `make_world_interface_with_shell(actor="autonomous")`
   with no `authorize` → `ValueError`. `build_shell_world_request(shell=True)`
   with no token → `ValueError`.
4. **Prefer `shell=False`** (shlex) whenever the command needs no shell grammar;
   reserve `shell=True` for the rare, human-armed case and keep the command
   string constrained/minimal.
5. **Audit every arm.** Each armed shell use is logged with a masked token so it
   is traceable to a human decision.

## 5. Residual risks even when armed

* `shell=False` still runs arbitrary executables chosen by the (authorized)
  command — policy must whitelist intent, not just gate the shell flag.
* No resource/network isolation at this layer; if ever armed, combine with the
  executor fence (`src/kernels/execution/fence`) and host-level egress controls.
* Token theft → unauthorized arming; tokens must be short-lived, one-shot, and
  issued by a human-sovereign path.
