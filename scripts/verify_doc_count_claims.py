#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_doc_count_claims.py — make COUNTABLE claims in authoritative docs re-derivable.

WHY THIS EXISTS
---------------
The project's worst recurring documentation defect is not staleness, it is
**numbers that cannot be reproduced** -- and the flagship instance was found in
`docs/spec/KERNEL-CANON.md`, the file that literally declares itself "the single
authoritative source" (v1.0):

  * all **14/14** per-kernel line counts were wrong (e.g. `audit` claimed 423,
    actually 2,288 in `__init__.py` / 6,694 across the package);
  * the stated total, "合计 6,391 行", did not even match **its own table**
    (the table sums to 6,581) -- a 190-line self-contradiction on one page;
  * it argued "14 = the directory count of `src/kernels/`", which is false --
    there are **15** directories (`retention/` is a non-registered HD-06
    subsystem), so the *argument type* was invalid even though the number 14 was
    right.

A count that no third party can recompute is indistinguishable from a guess.
This gate turns the countable subset of those claims into a mechanical check.

WHAT IT CHECKS
--------------
1. **Kernel count** — the registered-kernel count is read from the runtime
   (`src.kernels._registry.KERNEL_ENTRIES`) and from the directory tree, and any
   document that states a kernel count must agree with BOTH.
2. **Kernel line counts** — any `| kernel | <n> |`-shaped row in KERNEL-CANON
   must match `git show HEAD:<file> | wc -l`, i.e. the *committed* code, not the
   working tree (a working-tree count drifts with unrelated WIP and would make
   the check un-reproducible after the fact).
3. **Self-consistency** — the stated total must equal the sum of the table.

HONESTY BOUNDARY
----------------
PASS means "the counts this gate knows how to re-derive do match". It does NOT
mean the documents are accurate in general -- most prose claims are not
machine-checkable and remain a human obligation. This gate is a *ratchet* on the
specific defect class that actually bit us, not a general truth-checking system.
It deliberately does NOT try to be one: a linter that claimed to verify claims
would be the same failure mode in a new place.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANON = os.path.join(REPO_ROOT, "docs", "spec", "KERNEL-CANON.md")

# The order in KERNEL-CANON §1, and the docstring-clause each kernel cites.
KERNEL_ORDER = [
    "identity", "memory", "context", "capability", "policy", "execution",
    "resource", "event", "network", "trust", "evaluation", "security", "audit",
    "plugin",
]

# "src/kernels/retention/" is an HD-06 retention subsystem, NOT a registered
# kernel: absent from KERNEL_ENTRIES, no `lifecycle` field, not in GET /v1/kernels.
# Its exclusion is asserted here so that if someone ever registers it, this file
# fails loudly instead of the count silently becoming 15.
NON_KERNEL_DIRS = {"retention"}


class _SingleColumnTable(Exception):
    """KERNEL-CANON §1 still uses the legacy one-bare-number-per-row format.

    Raised as its own type (not a bare AssertionError) so `main()` can print a
    diagnosis a human can act on. The v1.0 table looked like this:

        | 13 | **audit** | `src/kernels/audit/` | 423 | **§113** | ...

    One number, no column meaning. It matched neither the `__init__.py` count nor
    the whole-package count (2,288 / 6,694 at the time of measurement), and the
    total stated beneath the table (6,391) did not even match the sum of those
    meaningless numbers (6,581). The ambiguity is the defect -- an ambiguous
    number cannot be verified, so it cannot be allowed to stand.
    """

    def __init__(self, rows):
        self.rows = rows
        super().__init__(
            f"KERNEL-CANON §1 uses the legacy single-number-per-row format "
            f"({len(rows)} rows, first: line {rows[0][0]} '{rows[0][1]}' = {rows[0][2]})"
        )


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout


def _registered_kernels():
    sys.path.insert(0, REPO_ROOT)
    from src.kernels._registry import KERNEL_ENTRIES

    return [e.name for e in KERNEL_ENTRIES]


def _kernel_dirs() -> set:
    base = os.path.join(REPO_ROOT, "src", "kernels")
    return {
        d
        for d in os.listdir(base)
        if os.path.isdir(os.path.join(base, d)) and d != "__pycache__"
    }


def _head_line_count(rel_path: str) -> int:
    """Lines of a file AS COMMITTED. Working-tree counts drift with unrelated WIP."""
    return _git("show", f"HEAD:{rel_path}").count("\n")


def _head_kernel_lines(kernel: str):
    """(init_lines, package_lines) for a kernel, measured at HEAD."""
    tree = [
        p
        for p in _git("ls-tree", "-r", "--name-only", "HEAD", f"src/kernels/{kernel}/").split()
        if p.endswith(".py")
    ]
    pkg = sum(_head_line_count(p) for p in tree)
    init_path = f"src/kernels/{kernel}/__init__.py"
    init = _head_line_count(init_path) if init_path in tree else 0
    return init, pkg


def _parse_canon_rows(text: str):
    """Pull (kernel, init_claim, pkg_claim) out of the KERNEL-CANON §1 table.

    Tolerates the row shape
        | 13 | **audit** | `src/kernels/audit/` | 2288 | 6694 | **§113** | ...
    and the italic non-registered row
        | — | *(非注册)* **retention** | `src/kernels/retention/` | *134* | *1131* | ... |

    The kernel name is taken from the PATH column and cross-checked against the
    label column: a row whose label and path disagree is itself a doc defect and
    must not be silently attributed to the path's kernel.

    Parsed CELL BY CELL rather than with one big regex. An earlier single-regex
    version matched **0 rows** (the label cell contains `**` and the path cell is
    several cells away), which would have left this gate a permanent no-op -- a
    fake green, the exact failure mode this repo keeps catching. A parser that
    cannot parse its own table is worse than no gate, so every row is counted.

    A row with fewer than two numeric cells is the SIGNATURE of the legacy
    single-column format v1.0 shipped. That is reported as a readable diagnosis
    (`_SingleColumnTable`) rather than a traceback, because the point is to tell
    the reader "this table gives one bare number per row with no stated column
    meaning" -- which IS the defect -- not to crash on it.
    """
    rows = {}
    single_col = []
    conflicts = []
    for lineno, raw in enumerate(text.split("\n"), 1):
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        path_idx = next(
            (
                i
                for i, c in enumerate(cells)
                if re.fullmatch(r"`src/kernels/[a-z_0-9]+/`", c)
            ),
            None,
        )
        if path_idx is None:
            continue
        path = cells[path_idx].strip("`").split("/")[2]
        nums = []
        for c in cells[path_idx + 1:]:
            m = re.fullmatch(r"\**([\d,]+)\**", c)
            if m:
                nums.append(int(m.group(1).replace(",", "")))
            elif nums:
                break  # first non-numeric cell after the numbers closes the columns
        if len(nums) < 2:
            single_col.append((lineno, path, nums[0] if nums else None))
            continue
        if path_idx > 0:
            label = cells[path_idx - 1]
            for token in re.findall(r"[a-z_]{3,}", label):
                if token in set(KERNEL_ORDER) | NON_KERNEL_DIRS and token != path:
                    conflicts.append(f"line {lineno}: path '{path}' but label says '{token}'")
        rows[path] = (nums[0], nums[1])
    if single_col:
        raise _SingleColumnTable(single_col)
    if conflicts:
        raise AssertionError("KERNEL-CANON label/path mismatch: " + "; ".join(conflicts))
    return rows


def main() -> int:
    failures: list = []

    # ---- 1. the registered-kernel count ------------------------------
    registered = _registered_kernels()
    dirs = _kernel_dirs()
    n_reg = len(registered)

    print(f"registered kernels (runtime KERNEL_ENTRIES) = {n_reg}: {sorted(registered)}")
    print(f"directories under src/kernels/             = {len(dirs)}: {sorted(dirs)}")

    expected_dirs = set(registered) | NON_KERNEL_DIRS
    if dirs != expected_dirs:
        failures.append(
            f"src/kernels/ directories changed: {sorted(dirs)} != expected {sorted(expected_dirs)}. "
            "If a kernel was added/removed/registered, KERNEL-CANON §1 and this gate's "
            "KERNEL_ORDER + NON_KERNEL_DIRS must be updated in the same commit."
        )

    for k in NON_KERNEL_DIRS:
        if k in registered:
            failures.append(
                f"'{k}' is listed in NON_KERNEL_DIRS but is now a REGISTERED kernel. "
                "Remove it from NON_KERNEL_DIRS and add it to KERNEL_ORDER."
            )

    # ---- 2. every doc that states a kernel count must agree ----------
    with open(CANON, encoding="utf-8") as fh:
        canon = fh.read()

    try:
        rows = _parse_canon_rows(canon)
    except _SingleColumnTable as exc:
        print()
        print(f"RESULT: FAIL — {exc}")
        print()
        print("  KERNEL-CANON §1 gives ONE bare number per kernel row with no stated")
        print("  column meaning, so no number in it can be verified. This is not a")
        print("  formatting nit: the v1.0 table's own rows summed to 6,581 while the")
        print("  total printed beneath it said 6,391, and the values matched neither the")
        print("  `__init__.py` count nor the whole-package count.")
        print()
        print("  Fix: publish two labelled columns — `__init__.py` lines and whole-package")
        print("  `.py` lines — measured from the COMMITTED code (`git show HEAD:<path> | wc -l`),")
        print("  and make the stated total equal the sum of the registered rows.")
        print("  This gate re-checks all of it automatically once the columns exist.")
        return 1
    # The retention row is deliberately present in the table (it must stayvisible
    # so nobody re-derives "14" by counting directories), but it is NOT a
    # registered kernel and must not enter the registered-kernel total.
    kernel_rows = {k: v for k, v in rows.items() if k in set(KERNEL_ORDER)}
    extra_rows = sorted(set(rows) - set(KERNEL_ORDER))
    if set(kernel_rows) != set(KERNEL_ORDER):
        failures.append(
            f"KERNEL-CANON §1 table lists registered kernels {sorted(kernel_rows)}, "
            f"expected {sorted(KERNEL_ORDER)}."
        )
    for name in registered:
        if name not in rows:
            failures.append(f"runtime kernel '{name}' is missing from KERNEL-CANON §1.")

    # ---- 3. self-consistency of the stated totals -------------------
    claimed_totals = re.findall(r"合计[：:]\s*\**([\d,]+)\s*行", canon)
    claimed_pkg_totals = re.findall(r"全包\s*`?\.py`?\s*[=:：]?\s*\**([\d,]+)", canon)

    real_init = {}
    real_pkg = {}
    for name in KERNEL_ORDER:
        i, p = _head_kernel_lines(name)
        real_init[name] = i
        real_pkg[name] = p

    sum_init = sum(real_init[k] for k in KERNEL_ORDER)
    sum_pkg = sum(real_pkg[k] for k in KERNEL_ORDER)
    print(f"\nHEAD-measured totals for the {len(KERNEL_ORDER)} registered kernels:")
    print(f"  sum(__init__.py) = {sum_init}   sum(all .py) = {sum_pkg}")

    # ---- 4. per-kernel rows must match HEAD exactly -----------------
    for name in KERNEL_ORDER:
        if name not in rows:
            continue
        c_init, c_pkg = rows[name]
        if c_init != real_init[name]:
            failures.append(
                f"KERNEL-CANON §1 row '{name}': __init__.py claimed {c_init}, "
                f"HEAD actually {real_init[name]}."
            )
        if c_pkg != real_pkg[name]:
            failures.append(
                f"KERNEL-CANON §1 row '{name}': package claimed {c_pkg}, "
                f"HEAD actually {real_pkg[name]}."
            )

    # ---- 5. the stated total must equal the sum of the stated rows ----
    # This is the check that would have caught the 6,391-vs-6,581 self-contradiction
    # on the very first run, without needing to know the true numbers at all.
    #
    # Only the REGISTERED rows are summed: the table also carries a clearly-marked
    # non-registered row (retention), and the sentence it belongs to is explicitly
    # scoped to the registered kernels. Summing every row would flag a correct
    # document -- which is how gates learn to be ignored.
    if kernel_rows:
        row_init_sum = sum(v[0] for v in kernel_rows.values())
        row_pkg_sum = sum(v[1] for v in kernel_rows.values())
        if extra_rows:
            print(
                f"note: table also carries non-registered row(s) {extra_rows}, "
                f"excluded from the registered-kernel total"
            )
        if claimed_totals:
            claimed = int(claimed_totals[0].replace(",", ""))
            if claimed != row_init_sum:
                failures.append(
                    f"KERNEL-CANON states total {claimed} __init__.py lines, but its own "
                    f"registered rows sum to {row_init_sum} (measured HEAD sum is {sum_init}). "
                    "This is the exact class of self-contradiction v1.0 shipped."
                )
            elif claimed != sum_init:
                failures.append(
                    f"KERNEL-CANON total {claimed} is self-consistent with its table but does "
                    f"not match the committed code ({sum_init} at HEAD)."
                )
            else:
                print(
                    f"stated total {claimed} == registered row sum {row_init_sum} "
                    f"== HEAD measurement  [consistent]"
                )
        if claimed_pkg_totals:
            claimed_pkg = int(claimed_pkg_totals[0].replace(",", ""))
            if claimed_pkg != row_pkg_sum:
                failures.append(
                    f"KERNEL-CANON states package total {claimed_pkg}, but its own registered "
                    f"rows sum to {row_pkg_sum} (measured HEAD sum is {sum_pkg})."
                )
            elif claimed_pkg != sum_pkg:
                failures.append(
                    f"KERNEL-CANON package total {claimed_pkg} is self-consistent with its "
                    f"table but does not match the committed code ({sum_pkg} at HEAD)."
                )
            else:
                print(f"stated package total {claimed_pkg} == HEAD measurement  [consistent]")

    # ---- 6. the 'directory count == kernel count' argument ----------
    # v1.0 argued "14 == the directory count of src/kernels/". The directories are
    # 15, so the ARGUMENT is invalid even though 14 is the right answer.
    #
    # CAREFUL: a doc that *retracts* the argument must be allowed to say so, and
    # otherwise this check would punish the very act of correcting the defect --
    # a false-positive generator dressed as a guard. So only UNQUOTED prose counts:
    # blockquote lines ("> ") are where retractions live, and are skipped.
    bad_lines = []
    for lineno, line in enumerate(canon.split("\n"), 1):
        stripped = line.strip()
        if stripped.startswith(">"):
            continue  # quoted / retraction context
        if "目录数完全一致" in stripped or re.search(r"=\s*14\s*[←<-]{1,2}", stripped):
            bad_lines.append((lineno, stripped[:100]))
    if bad_lines:
        failures.append(
            "KERNEL-CANON re-asserts the 'kernel count == src/kernels/ directory count' "
            f"argument in unquoted prose (line(s) {[b[0] for b in bad_lines]}), but the "
            f"directory count is {len(dirs)} and the registered count is {n_reg}. Counting "
            "directories cannot establish the number of kernels (retention/ is a directory "
            "but not a registered kernel). State the count as len(KERNEL_ENTRIES) instead. "
            "Retracting the claim inside a '>' blockquote is fine and is ignored here."
        )

    # ---- report ------------------------------------------------------
    print()
    if failures:
        print(f"RESULT: FAIL ({len(failures)} problem(s))")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("RESULT: PASS")
    print("  - registered-kernel count matches the runtime registry")
    print("  - every KERNEL-CANON line-count row matches the committed code at HEAD")
    print("  - the stated total is consistent with the table it summarises")
    print()
    print("  BOUNDARY: this checks only the counts it can re-derive. Prose claims,")
    print("  behavioural guarantees, and non-countable statements remain a human")
    print("  obligation -- a PASS here is not a statement that the docs are correct.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
