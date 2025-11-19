#!/usr/bin/env python3
"""
RAPID code analyzer: compute complexity and readability metrics
for ABB RAPID (.mod, .prg, .sys, .cfg) files.

Can be used as:
  - a CLI tool (python rapid_analyzer.py PATH)
  - a library (import rapid_analyzer and call analyze_folder)
  - a backend for the Tkinter GUI (see rapid_analyzer_gui.py)
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

import nltk
from nltk.corpus import wordnet as wn

# -------------------------------------------------
# DEBUG FLAGS
# -------------------------------------------------
# Set to True when you want verbose debugging in CLI mode.
DEBUG = False
DEBUG_VARS = False

# -------------------------------------------------
# CONSTANTS
# -------------------------------------------------

RAPID_EXTENSIONS = [".mod", ".prg", ".sys", ".cfg"]

ALLOWED_SHORT_TOKENS: Set[str] = {
    "di", "do", "gi", "go", "ai", "ao",
    "in", "on", "p", "t", "w", "l", "n", "s",
    "via", "Via", "m", "bool", "plc", "pre", "off",
    "with", "from", "x", "y", "z", "ry", "rz", "rx",
    "dir", "calc", "prog", "pers", "i", "j", "k", "a",
    "b", "ok", "at","for","over","under","front","back","cc","ct","v",
}

# -------------------------------------------------
# WORDNET / NLP HELPERS
# -------------------------------------------------

_dictionary_cache: Dict[str, bool] = {}


def ensure_wordnet() -> None:
    """Ensure NLTK WordNet data is available (downloads on first run if needed)."""
    try:
        wn.synsets("test")
    except LookupError:
        nltk.download("wordnet")
        nltk.download("omw-1.4")


def is_dictionary_word(word: str) -> bool:
    """
    Check if a token is a dictionary word using WordNet.
    Uses a cache so we don't hit WordNet repeatedly.
    """
    w = word.lower()
    if w in _dictionary_cache:
        return _dictionary_cache[w]
    _dictionary_cache[w] = bool(wn.synsets(w))
    return _dictionary_cache[w]


def camel_split(s: str) -> List[str]:
    """
    Split CamelCase words, including cases like:
      YAxis -> Y + Axis
      XMLParser -> XML + Parser
    """
    if not s:
        return []

    result: List[str] = []
    current = s[0]

    for ch in s[1:]:
        if ch.isupper():
            # Case 1: current ends lowercase -> split (e.g. myVar)
            if not current[-1].isupper():
                result.append(current)
                current = ch
            # Case 2: acronym ended and now starts normal word (XMLParser)
            elif len(current) > 1 and current[-1].isupper() and ch.isupper():
                # stay in acronym (e.g. ML in XML)
                current += ch
            else:
                current += ch
        else:
            # Lowercase letter: break acronym
            if current[-1].isupper() and len(current) > 1:
                # Split acronym minus last capital
                result.append(current[:-1])
                current = current[-1] + ch
            else:
                current += ch

    result.append(current)
    return result


def split_identifier(name: str) -> List[str]:
    """
    Full identifier splitter with underscore, digit breaks, improved camel splitting,
    and axis/Y/X special handling.
    """
    parts = re.split(r"_+", name)
    tokens: List[str] = []

    for part in parts:
        if not part:
            continue

        # Treat digits as separators: point2On -> point_On
        no_digits = re.sub(r"\d+", "_", part)
        for chunk in no_digits.split("_"):
            if not chunk:
                continue

            # Apply improved camel-case split
            subs = camel_split(chunk)

            for s in subs:
                s = s.lower()
                if s in ("xaxis", "yaxis", "zaxis"):
                    tokens.extend([s[0], "axis"])
                else:
                    tokens.append(s)

    if DEBUG and DEBUG_VARS:
        print(f"    split_identifier('{name}') -> {tokens}")

    return tokens


def variable_goodness(name: str, bad_tokens: Set[str]) -> float:
    """
    Returns a score 0–1 for a single variable name based on dictionary words.
    1.0 = all tokens are dictionary words.
    0.0 = no tokens are dictionary words OR no meaningful tokens.

    Also fills 'bad_tokens' with any tokens that:
      - are < 3 chars and not whitelisted, or
      - are not found in the dictionary.
    """
    tokens = split_identifier(name)
    if not tokens:
        if DEBUG:
            print(f"  Var '{name}': no meaningful tokens -> score 0.0")
        return 0.0

    good = 0
    for t in tokens:
        t_norm = t.lower()

        # 1) Whitelist wins, regardless of length
        if t_norm in ALLOWED_SHORT_TOKENS:
            is_word = True
            if DEBUG and DEBUG_VARS:
                print(f"      word '{t}' -> whitelisted -> GOOD")

        # 2) Too short and not whitelisted → bad
        elif len(t_norm) < 3:
            is_word = False
            bad_tokens.add(t_norm)
            if DEBUG:
                print(f"      The word '{t}' is vague and not recommended")

        # 3) Normal dictionary check
        else:
            is_word = is_dictionary_word(t_norm)
            if not is_word:
                bad_tokens.add(t_norm)
                if DEBUG:
                    print(f"      The word '{t}' not found in dictionary.")

        if is_word:
            good += 1

    score = good / len(tokens)
    if DEBUG and DEBUG_VARS:
        print(f"  Var '{name}': good={good}/{len(tokens)} -> score={score:.3f}")
    return score


def variable_name_score(all_variables: Iterable[str]) -> Tuple[float, Set[str]]:
    """
    Overall naming quality for a file/module, 0–1, plus the set of "bad" tokens.
    Returns: (avg_score, bad_tokens_set)
    """
    all_variables = list(all_variables)
    bad_tokens: Set[str] = set()

    if not all_variables:
        if DEBUG:
            print("  No variables found in this file -> variable_name_score = 1.0 (neutral)")
        return 1.0, bad_tokens  # nothing to judge = neutral/good

    if DEBUG and DEBUG_VARS:
        print("\n--- Variable naming debug for this file ---")
        print("Variables to score:", ", ".join(sorted(all_variables)))

    scores = [variable_goodness(v, bad_tokens) for v in all_variables]
    avg = sum(scores) / len(scores)
    if DEBUG:
        print(f"  Average variable name score for file = {avg:.3f}")
        if bad_tokens:
            print(f"  Bad tokens detected: {', '.join(sorted(bad_tokens))}")
    return avg, bad_tokens


# -------------------------------------------------
# COMMENT METRICS
# -------------------------------------------------

def comment_score(comment_ratio: float) -> float:
    """
    Returns a score 0–1 based on how healthy the comment ratio is.

    comment_ratio is expected in [0, 1] (0–100%).

    Behaviour:
      - 0% comments          -> score 0
      - 6% comments          -> score 1  (linear ramp 0 → 1)
      - 6%–25% comments      -> score 1  (ideal plateau)
      - 25%–60% comments     -> linearly drops 1 → 0
      - >= 60% comments      -> score 0
    """
    if comment_ratio <= 0.0:
        return 0.0

    # 0–6%: ramp from 0 to 1
    if comment_ratio < 0.06:
        return comment_ratio / 0.06

    # 6–25%: optimal range, full score
    if comment_ratio <= 0.25:
        return 1.0

    # >25%: linearly decrease from 1 at 25% to 0 at 60%
    if comment_ratio >= 0.60:
        return 0.0

    # Linear interpolation between 25% and 60%
    return 1.0 - (comment_ratio - 0.25) / (0.60 - 0.25)



# -------------------------------------------------
# PROCEDURE INFO HOLDER
# -------------------------------------------------

class ProcInfo:
    """Information about a single PROC/FUNC in a RAPID file."""

    def __init__(self, module_name: str, file_path: Path, proc_name: str) -> None:
        self.module_name = module_name
        self.file_path = file_path
        self.proc_name = proc_name
        self.fqname = f"{module_name}::{proc_name}" if module_name else proc_name
        self.body_lines: List[str] = []  # code lines inside proc
        self.code_lines: int = 0         # count of code lines (non-comment) in this proc

    def add_line(self, line: str) -> None:
        self.body_lines.append(line)
        self.code_lines += 1


# -------------------------------------------------
# FIRST PASS: PER-FILE ANALYSIS
# -------------------------------------------------

def analyze_file_first_pass(
    path: Path,
    proc_registry: Dict[str, ProcInfo],
    proc_name_to_fq: Dict[str, Set[str]],
    exclude_nostepin_modules: bool = True,
) -> Dict:
    """
    First pass over a file:
      - compute basic line/complexity metrics (excluding NOSTEPIN modules)
      - collect procedures and their bodies
      - collect variable names (including IO signals Set/Reset/SetDO/ResetDO)
    """
    if DEBUG:
        print(f"\n=== Analyzing file: {path} ===")

    try:
        with path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        with path.open("r", encoding="cp1252") as f:
            lines = f.readlines()

    total_lines = 0
    code_lines = 0
    comment_lines = 0

    simple_complexity = 1
    depth_complexity = 1
    nesting_level = 0
    max_nesting = 0
    max_nesting_line = None
    max_nesting_proc = None
    indent_pattern_score = 0

    CONTROL_START = ["IF", "FOR", "WHILE", "TEST"]
    CONTROL_END = ["ENDIF", "ENDFOR", "ENDWHILE", "ENDTEST"]
    EXTRA_DECISION = ["ELSEIF", "CASE"]

    current_module = None
    in_nostepin_module = False

    current_proc: str | None = None  # fqname of current proc
    local_proc_infos: Dict[str, ProcInfo] = {}

    variable_names: Set[str] = set()

    var_declared: Set[str] = set()  # names declared in this file
    var_uses: Set[str] = set()      # names used in this file (identifiers)

    # Unicode-safe identifier start: any word char except digit
    name_regex = r"[^\W\d_]\w*"

    # WaitTime tracking
    waittime_lines: List[int] = []

    proc_pattern = re.compile(rf"^\s*PROC\s+({name_regex})", re.IGNORECASE)
    func_pattern = re.compile(rf"^\s*FUNC\s+\w+\s+({name_regex})", re.IGNORECASE)
    endproc_pattern = re.compile(r"^\s*ENDPROC\b", re.IGNORECASE)
    endfunc_pattern = re.compile(r"^\s*ENDFUNC\b", re.IGNORECASE)
    callbyvar_pattern = re.compile(
        r'\bCallByVar\b[^\n"]*"([^"]+)"',
        re.IGNORECASE,
    )
    dynamic_call_prefixes: Set[str] = set()
    ident_pattern = re.compile(r"\b[^\W\d_]\w*\b", re.UNICODE)

    var_decl_pattern = re.compile(
        rf"^\s*(PERS|VAR|CONST|LOCAL)\s+\w+\s+({name_regex})",
        re.IGNORECASE,
    )

    waittime_pattern = re.compile(r"\bWaitTime\b", re.IGNORECASE)

    # IO-related: Set x, Reset x, Switch x, SetDO x, ResetDO x
    set_reset_pattern = re.compile(rf"\b(Set|Reset|Switch)\s+({name_regex})", re.IGNORECASE)
    setdo_pattern = re.compile(rf"\b(SetDO|ResetDO)\s+({name_regex})", re.IGNORECASE)

    for lineno, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip("\n")
        stripped = line.strip()
        upper = stripped.upper()

        # --- Detect start of (possibly) NOSTEPIN module ---
        if not in_nostepin_module and "MODULE" in upper:
            m = re.match(rf"^\s*MODULE\s+({name_regex})", line, re.IGNORECASE)
            if m:
                current_module = m.group(1)
                has_nostepin = "NOSTEPIN" in upper
                if DEBUG:
                    print(f"  MODULE detected: {current_module} (NOSTEPIN={has_nostepin})")
                if exclude_nostepin_modules and has_nostepin:
                    in_nostepin_module = True
                    # Skip this line entirely
                    continue

        # --- If we are inside a NOSTEPIN module, skip everything until ENDMODULE ---
        if in_nostepin_module:
            if "ENDMODULE" in upper:
                if DEBUG:
                    print("  Leaving NOSTEPIN module.")
                in_nostepin_module = False
                current_module = None
            continue

        # --- Handle ENDMODULE for normal modules ---
        if "ENDMODULE" in upper:
            if DEBUG and current_module:
                print(f"  ENDMODULE for {current_module}")
            current_module = None

        # --- Ignore decorative comments starting with !**** ---
        if stripped.startswith("!**") or stripped.startswith("!--"):
            if DEBUG and DEBUG_VARS:
                print(f"  Ignoring decorative comment line: {line}")
            continue

        # --- Now count this as a physical line in the file ---
        total_lines += 1

        # --- Comment line (other than !****) ---
        if upper.startswith("!"):
            comment_lines += 1
            if DEBUG and DEBUG_VARS:
                print(f"  Comment line: {line}")
            continue

        # --- Blank line ---
        if stripped == "":
            continue

        # --- It's a code line ---
        code_lines += 1

        # WaitTime usage
        if waittime_pattern.search(line):
            waittime_lines.append(lineno)
            if DEBUG and DEBUG_VARS:
                print(f"  WaitTime at line {lineno}: {line}")

        # Is this line a declaration line?
        decl_match = var_decl_pattern.match(line)

        # Remove inline comments and strings for usage scan
        line_no_comment = stripped.split("!", 1)[0]
        line_no_strings = re.sub(r'"[^"]*"', "", line_no_comment)

        # Track variable uses (gross: by name only)
        if not decl_match:  # don't treat declarations themselves as "uses"
            for ident in ident_pattern.findall(line_no_strings):
                name = ident
                var_uses.add(name)
                if DEBUG and DEBUG_VARS and name in var_declared:
                    print(f"  Variable use: {name} at line {lineno}")

        # Indentation tracking
        indent_level = len(line) - len(line.lstrip())
        indent_pattern_score += indent_level

        # Variable declarations
        vmatch = var_decl_pattern.match(line)
        if vmatch:
            var_name = vmatch.group(2)
            variable_names.add(var_name)
            var_declared.add(var_name)
            if DEBUG and DEBUG_VARS:
                print(f"  Declared variable found: {var_name}")

        # IO usage as "variables": Set/Reset/SetDO/ResetDO
        m = set_reset_pattern.search(line)
        if m:
            var_name = m.group(2)
            variable_names.add(var_name)
            if DEBUG and DEBUG_VARS:
                print(f"  IO variable (Set/Reset/Switch) found: {var_name} in line: {line}")

        m = callbyvar_pattern.search(line)
        if m:
            prefix = m.group(1).strip()
            if prefix:
                dynamic_call_prefixes.add(prefix.lower())


        m = setdo_pattern.search(line)
        if m:
            var_name = m.group(2)
            variable_names.add(var_name)
            if DEBUG and DEBUG_VARS:
                print(f"  IO variable (SetDO/ResetDO) found: {var_name} in line: {line}")

        # Procedure/function definitions
        new_proc_name = None
        pm = proc_pattern.match(line)
        if pm:
            new_proc_name = pm.group(1)
        else:
            fm = func_pattern.match(line)
            if fm:
                new_proc_name = fm.group(1)

        if new_proc_name:
            pinfo = ProcInfo(current_module or path.stem, path, new_proc_name)
            fqname = pinfo.fqname
            current_proc = fqname
            local_proc_infos[fqname] = pinfo
            proc_registry[fqname] = pinfo
            proc_name_to_fq[new_proc_name.lower()].add(fqname)
            if DEBUG and DEBUG_VARS:
                print(f"  PROC/FUNC detected: {fqname}")
            # Don't treat the PROC/FUNC line as body code
            continue

        # End of procedure/function
        if endproc_pattern.match(line) or endfunc_pattern.match(line):
            if DEBUG and DEBUG_VARS and current_proc is not None:
                print(f"  ENDPROC/ENDFUNC for {current_proc}")
            current_proc = None

        # --- Complexity / nesting ---

        # Handle block-closing keywords first
        if any(upper.startswith(k) for k in CONTROL_END):
            nesting_level = max(nesting_level - 1, 0)

        # New nesting blocks
        if any(upper.startswith(k) for k in CONTROL_START):
            simple_complexity += 1
            nesting_level += 1

            if nesting_level > max_nesting:
                max_nesting = nesting_level
                max_nesting_line = lineno
                max_nesting_proc = current_proc  # fqname of the current proc

            depth_complexity += nesting_level

        # Other decision structures that add branches but not nesting
        if any(upper.startswith(k) for k in EXTRA_DECISION):
            simple_complexity += 1
            depth_complexity += nesting_level

        # --- Inside a procedure: record body lines for later call graph analysis ---
        if current_proc is not None:
            local_proc_infos[current_proc].add_line(line)

    avg_indent = indent_pattern_score / total_lines if total_lines > 0 else 0.0
    depth_complexity = max(depth_complexity, simple_complexity)

    if DEBUG:
        print(
            f"  File stats: total_lines={total_lines}, code_lines={code_lines}, "
            f"comment_lines={comment_lines}, simple_complexity={simple_complexity}, "
            f"max_nesting={max_nesting}"
        )

    return {
        "file_path": path,
        "total_lines": total_lines,
        "code_lines": code_lines,
        "comment_lines": comment_lines,
        "simple_complexity": simple_complexity,
        "depth_complexity": depth_complexity,
        "max_nesting": max_nesting,
        "max_nesting_line": max_nesting_line,
        "max_nesting_proc": max_nesting_proc,
        "avg_indent": avg_indent,
        "variable_names": variable_names,
        "proc_ids": list(local_proc_infos.keys()),
        "dynamic_prefixes": dynamic_call_prefixes,
        "waittime_lines": waittime_lines,
        "var_declared": var_declared,
        "var_uses": var_uses,
    }


# -------------------------------------------------
# CALL GRAPH
# -------------------------------------------------

def print_call_tree_from_main(call_graph: Dict[str, Set[str]], main_candidates: List[str]) -> None:
    """
    Print a tree-like view of the call graph starting from main candidates.
    Avoids infinite loops on cycles.
    """
    if not main_candidates:
        print("\n(No MAIN found, cannot print call tree from MAIN.)")
        return

    print("\n=== Call tree from MAIN ===")

    def dfs(node: str, depth: int, visiting: Set[str]) -> None:
        indent = "  " * depth
        print(f"{indent}- {node}")
        if node in visiting:
            print(f"{indent}  (cycle detected, stopping here)")
            return
        visiting.add(node)
        for callee in sorted(call_graph.get(node, [])):
            dfs(callee, depth + 1, visiting)
        visiting.remove(node)

    for m in main_candidates:
        dfs(m, 0, set())


def build_call_graph(
    proc_registry: Dict[str, ProcInfo],
    proc_name_to_fq: Dict[str, Set[str]],
    dynamic_all_variants: bool = True,
) -> Dict[str, Set[str]]:
    """
    Second pass: build call graph between procedures across all files.
    Case-insensitive, Unicode-safe.

    Skips:
      - lines starting with '!'  (comments)
      - lines starting with 'TPWrite' (pure HMI text)

    In addition to normal static calls, it also treats CallByVar as a
    real call:

        CallByVar "CycleModel_M", nModel;

    will be treated as calls to:
      - ALL procedures whose names start with "CycleModel_M"
        if dynamic_all_variants == True
      - ONLY the first such procedure (lexicographically)
        if dynamic_all_variants == False
    """
    if DEBUG:
        print("\n=== Building call graph ===")

    call_graph: Dict[str, Set[str]] = defaultdict(set)

    # known names stored in lowercase (exact proc names)
    known_proc_names = set(proc_name_to_fq.keys())

    ident_pattern = re.compile(r"\b[^\W\d_]\w*\b", re.UNICODE)
    # Matches:
    #   CallByVar "CycleModel_M", nModel;
    #   CallByVar \Task:="T_ROB2","CycleModel_M",nModel;
    #   CallByVar \Something, "Leave130_P", Recept;
    callbyvar_pattern = re.compile(
        r"\bCallByVar\b[^\n\"]*\"([^\"]+)\"",
        re.IGNORECASE,
    )

    for fqname, pinfo in proc_registry.items():
        if DEBUG and DEBUG_VARS:
            print(f"  Scanning body of proc: {fqname}")

        for line in pinfo.body_lines:
            stripped = line.lstrip()
            upper = stripped.upper()

            # Skip full-line comments and TPWrite lines
            if upper.startswith("!") or upper.startswith("TPWRITE"):
                if DEBUG and DEBUG_VARS:
                    print(f"    Skipping line (comment/TPWrite): {line.rstrip()}")
                continue

            # Strip inline comments and strings for *static* call scanning
            line_no_comment = stripped.split("!", 1)[0]
            line_no_strings = re.sub(r'"[^"]*"', "", line_no_comment)

            # ---------- Static calls (as before) ----------
            for ident in ident_pattern.findall(line_no_strings):
                ident_l = ident.lower()
                if ident_l in known_proc_names:
                    for callee_fq in proc_name_to_fq[ident_l]:
                        call_graph[fqname].add(callee_fq)
                        if DEBUG and DEBUG_VARS:
                            print(
                                f"    Static call: {fqname} -> {callee_fq} "
                                f"(from ident '{ident}')"
                            )

            # ---------- Dynamic calls via CallByVar ----------
            m_dyn = callbyvar_pattern.search(stripped)
            if not m_dyn:
                continue

            prefix_raw = m_dyn.group(1).strip()
            if not prefix_raw:
                continue

            prefix_lc = prefix_raw.lower()

            # Find *all* procedures whose names start with this prefix
            matching_proc_names = [
                name for name in proc_name_to_fq.keys()
                if name.startswith(prefix_lc)
            ]

            if not matching_proc_names:
                if DEBUG and DEBUG_VARS:
                    print(
                        f"    CallByVar '{prefix_raw}' in {fqname} "
                        f"has no matching procedures."
                    )
                continue

            # If we only want the first variant, pick exactly ONE proc name
            if dynamic_all_variants:
                selected_proc_names = matching_proc_names
            else:
                # One representative per prefix family, e.g. only 'cyclemodel_m1'
                selected_proc_names = [sorted(matching_proc_names)[0]]

            for proc_name in selected_proc_names:
                fq_list = sorted(proc_name_to_fq[proc_name])

                if dynamic_all_variants:
                    targets = fq_list          # all modules that define that proc
                else:
                    targets = [fq_list[0]]     # first module only, if multiple

                for callee_fq in targets:
                    call_graph[fqname].add(callee_fq)
                    if DEBUG and DEBUG_VARS:
                        print(
                            f"    CallByVar '{prefix_raw}' treated as "
                            f"call: {fqname} -> {callee_fq}"
                        )

    if DEBUG and DEBUG_VARS:
        print("\nCall graph edges:")
        if not call_graph:
            print("  (no calls detected)")
        else:
            for caller, callees in call_graph.items():
                print(f"  {caller} calls: {', '.join(sorted(callees))}")

    return call_graph


def compute_call_depths_from_main(
    call_graph: Dict[str, Set[str]],
) -> Dict[str, int]:
    """
    Compute call-chain depth starting ONLY from 'Main' or 'main'.

    Depth(proc) = distance from MAIN in call graph.
    Procedures not reachable from MAIN get no entry in the dict.

    Dynamic calls (CallByVar) are already encoded as normal edges
    in the call graph.
    """
    main_candidates = [
        p for p in call_graph.keys()
        if p.lower().endswith("::main") or p.lower() == "main"
    ]

    depth: Dict[str, int] = defaultdict(int)

    if not main_candidates:
        if DEBUG:
            print("No MAIN procedure found. Treating all call depths as 0.")
        return depth

    if DEBUG:
        print("\n=== Computing call depth from MAIN ===")
        print_call_tree_from_main(call_graph, main_candidates)

    from collections import deque

    queue = deque()

    for m in main_candidates:
        depth[m] = 0
        queue.append(m)

    while queue:
        current = queue.popleft()
        current_depth = depth[current]

        for callee in call_graph.get(current, []):
            # Only set depth the first time we see a node, so recursion /
            # mutual recursion does not grow depth forever.
            if callee not in depth:
                depth[callee] = current_depth + 1
                queue.append(callee)

    if DEBUG and DEBUG_VARS:
        print("Depth from MAIN:")
        for proc, d in depth.items():
            print(f"  {proc} -> depth {d}")

    return depth




# -------------------------------------------------
# FILE SCORES
# -------------------------------------------------

def compute_file_scores(
    file_stats_list: List[Dict],
    proc_registry: Dict[str, ProcInfo],
    call_graph: Dict[str, Set[str]],
    call_depths: Dict[str, int],
    dynamic_prefixes: Set[str],
    project_used_vars: Set[str],
) -> List[Dict]:
    """
    Combine all metrics per file into readability/maintainability scores.
    """
    results: List[Dict] = []

    total_files = len(file_stats_list)
    # Map file -> list of procs
    file_to_procs: Dict[Path, List[ProcInfo]] = defaultdict(list)
    for fqname, pinfo in proc_registry.items():
        file_to_procs[pinfo.file_path].append(pinfo)

    for fs in file_stats_list:
        path: Path = fs["file_path"]
        total_lines = fs["total_lines"]
        code_lines = fs["code_lines"]
        comment_lines = fs["comment_lines"]
        simple_complexity = fs["simple_complexity"]
        depth_complexity = fs["depth_complexity"]
        max_nesting = fs["max_nesting"]
        avg_indent = fs["avg_indent"]
        variable_names = fs["variable_names"]
        max_nesting_line = fs.get("max_nesting_line")
        max_nesting_proc = fs.get("max_nesting_proc")
        var_declared = fs.get("var_declared", set())
        waittime_lines = fs.get("waittime_lines", [])

        if DEBUG:
            print(f"\n=== Computing scores for file: {path} ===")

        unused_vars = sorted(
            name for name in var_declared
            if name not in project_used_vars
        )
        unused_var_count = len(unused_vars)

        comment_ratio_pct = 100 * (comment_lines / total_lines) if total_lines > 0 else 0.0
        cscore = comment_score(comment_ratio_pct / 100.0) * 100

        # variable_name_score returns (0–1 score, set of bad tokens)
        vscore_raw, bad_tokens = variable_name_score(variable_names)
        vscore = vscore_raw * 100
        bad_word_count = len(bad_tokens)
        # Procedure stats
        procs = file_to_procs.get(path, [])
        proc_count = len(procs)
        biggest_proc_lines = 0
        proc_line_sum = 0
        file_max_call_depth = 0

        unreachable_count = 0
        unreachable_names: List[str] = []

        for p in procs:
            proc_line_sum += p.code_lines
            if p.code_lines > biggest_proc_lines:
                biggest_proc_lines = p.code_lines

            d = call_depths.get(p.fqname, None)
            proc_lower = p.proc_name.lower()

            # Reachable from MAIN
            if d is not None:
                if d > file_max_call_depth:
                    file_max_call_depth = d
                continue

            # MAIN is never counted as unreachable
            if proc_lower == "main":
                continue

            # Dynamic calls — match prefixes
            is_dynamic_called = any(
                proc_lower.startswith(prefix) for prefix in dynamic_prefixes
            )
            if is_dynamic_called:
                continue

            unreachable_count += 1
            unreachable_names.append(p.proc_name)

        # Use code_lines as denominator; fallback to proc_line_sum
        denom_code = code_lines if code_lines > 0 else max(proc_line_sum, 1)
        biggest_proc_ratio = (biggest_proc_lines / denom_code) if denom_code > 0 else 0.0

        # ---- Penalties & boosts ----

        complexity_penalty = min(30, max(0.0,(simple_complexity - 50) * 1))
        nesting_penalty = min(30, max(0.0,(max_nesting - 8) * 5))
        call_depth_penalty = min(50.0, max(0.0, (file_max_call_depth - 3) * 15.0))

        if proc_count > 20:
            proc_count_penalty = min(20.0, (proc_count - 20) * 1)
        else:
            proc_count_penalty = 0.0

        if biggest_proc_ratio > 0.60 and total_lines > 300:
            proc_size_penalty = (biggest_proc_ratio - 0.60) * 50.0
        else:
            proc_size_penalty = 0.0


        # Penalty for very large files (more than 600 total lines)
        if total_lines > 600 and proc_count > 1:
            total_line_penalty = min(20,(total_lines - 600) * 0.05)
        else:
            total_line_penalty = 0.0

        
        unused_var_penalty = min(20, unused_var_count*0.5)
        bad_word_penalty = min(20, bad_word_count*0.5)
        
        comment_penalty = 5-cscore * 0.05 

        base = 100.0
        total_penalty = (
            complexity_penalty
            + nesting_penalty
            + call_depth_penalty
            + proc_count_penalty
            + proc_size_penalty
            + total_line_penalty
            + bad_word_penalty
            + unused_var_penalty
            + comment_penalty
        )

        readability_score = base - total_penalty
        readability_score = max(0.0, min(100.0, readability_score))

        if DEBUG:
            print(f"  comment_ratio={comment_ratio_pct:.3f}%, cscore={cscore:.3f}")
            print(f"  variable_name_score={vscore:.3f}")
            print(f"  simple_complexity={simple_complexity}, depth_complexity={depth_complexity}")
            print(f"  max_nesting={max_nesting}, file_max_call_depth={file_max_call_depth}")
            print(f"  proc_count={proc_count}, biggest_proc_ratio={biggest_proc_ratio:.3f}")
            print(
                "  penalties: "
                f"complexity={complexity_penalty:.2f}, "
                f"nesting={nesting_penalty:.2f}, "
                f"calldepth={call_depth_penalty:.2f}, "
                f"proccount={proc_count_penalty:.2f}, "
                f"procsize={proc_size_penalty:.2f}, "
                f"totallines={total_line_penalty:.2f}"
                f"comments={comment_penalty:.2f}"
            )
            print(f"  => readability_score={readability_score:.2f}")

        results.append({
            "file_path": path,
            "total_lines": total_lines,
            "code_lines": code_lines,
            "comment_lines": comment_lines,
            "comment_ratio": comment_ratio_pct,
            "simple_complexity": simple_complexity,
            "depth_complexity": depth_complexity,
            "max_nesting": max_nesting,
            "max_nesting_line": max_nesting_line,
            "max_nesting_proc": max_nesting_proc,
            "avg_indent": avg_indent,
            "proc_count": proc_count,
            "biggest_proc_lines": biggest_proc_lines,
            "biggest_proc_ratio": biggest_proc_ratio,
            "max_call_depth": file_max_call_depth,
            "variable_count": len(variable_names),
            "variable_name_score": vscore,
            "readability_score": readability_score,
            "unreachable_count": unreachable_count,
            "unreachable_procs": unreachable_names,
            "bad_words": sorted(bad_tokens),
            "waittime_lines": waittime_lines,
            "waittime_count": len(waittime_lines),
            "unused_vars": unused_vars,
            "unused_var_count": unused_var_count,
            "complexity_penalty": complexity_penalty,
            "nesting_penalty": nesting_penalty,
            "call_depth_penalty": call_depth_penalty,
            "proc_count_penalty": proc_count_penalty,
            "proc_size_penalty": proc_size_penalty,
            "total_line_penalty": total_line_penalty,
            "bad_word_penalty": float(bad_word_penalty),
            "unused_var_penalty": float(unused_var_penalty),
            "comment_penalty": comment_penalty,
            "total_penalty": total_penalty,
        })

    return results


# -------------------------------------------------
# PROJECT-LEVEL ORCHESTRATION
# -------------------------------------------------

def analyze_folder(
    root_folder: Path,
    exclude_nostepin_modules: bool = True,
    dynamic_all_variants: bool = True,
):

    """
    Main orchestration:
      - First pass: parse files, gather stats & procedures
      - Second pass: build call graph, compute call depths
      - Third: aggregate and compute readability scores

    Returns:
      file_results: list of per-file result dicts
      project_unique_var_count: int
      call_graph: dict {caller_fqname -> set(callee_fqname)}
      proc_registry: dict {fqname -> ProcInfo}
    """
    proc_registry: Dict[str, ProcInfo] = {}
    proc_name_to_fq: Dict[str, Set[str]] = defaultdict(set)
    file_stats_list: List[Dict] = []

    # Collect dynamic prefixes from all files
    all_dynamic_prefixes: Set[str] = set()

    # FIRST PASS: files
    for file in root_folder.rglob("*"):
        if file.suffix.lower() in RAPID_EXTENSIONS and file.is_file():
            fs = analyze_file_first_pass(
                file,
                proc_registry,
                proc_name_to_fq,
                exclude_nostepin_modules=exclude_nostepin_modules,
            )
            file_stats_list.append(fs)
            all_dynamic_prefixes.update(fs.get("dynamic_prefixes", set()))

    # Project-level variable count
    all_variables: Set[str] = set()
    project_declared_vars: Set[str] = set()
    project_used_vars: Set[str] = set()

    for fs in file_stats_list:
        project_declared_vars.update(fs.get("var_declared", set()))
        project_used_vars.update(fs.get("var_uses", set()))

    for fs in file_stats_list:
        all_variables.update(fs.get("variable_names", set()))
    project_unique_var_count = len(all_variables)

    # SECOND PASS: call graph + depths
    call_graph = build_call_graph(
        proc_registry,
        proc_name_to_fq,
        dynamic_all_variants=dynamic_all_variants,
    )
    call_depths = compute_call_depths_from_main(call_graph)

    # THIRD PASS: per-file scores
    file_results = compute_file_scores(
        file_stats_list,
        proc_registry,
        call_graph,
        call_depths,
        all_dynamic_prefixes,
        project_used_vars,
    )


    return file_results, project_unique_var_count, call_graph, proc_registry


def print_results(file_results: List[Dict], project_unique_var_count: int) -> None:
    """Pretty-print the analysis results in a CLI-friendly format."""
    if not file_results:
        print("No RAPID files found.")
        return

    print("\n=== RAPID CODE ANALYSIS ===")
    total_project_lines = 0
    total_files = len(file_results)
    avg_complexity = 0.0
    avg_readability = 0.0

    for res in file_results:
        path = res["file_path"]
        total_project_lines += res["total_lines"]
        avg_complexity += res["simple_complexity"]
        avg_readability += res["readability_score"]

        print(f"\nFile: {path}")
        print(f"  Total lines:           {res['total_lines']}")
        print(f"  Code lines:            {res['code_lines']}")
        print(f"  Comment lines:         {res['comment_lines']}")
        print(f"  Comment ratio:         {res['comment_ratio']:.0f} %")
        print(f"  Simple complexity:     {res['simple_complexity']}")
        print(f"  Depth complexity:      {res['depth_complexity']}")
        if res.get("max_nesting_line") is not None:
            print(
                "  Max nesting depth:     "
                f"{res['max_nesting']} "
                f"(at line {res['max_nesting_line']} in {res['max_nesting_proc']})"
            )
        else:
            print(f"  Max nesting depth:     {res['max_nesting']}")

        print(f"  Max call-chain depth:  {res['max_call_depth']}")
        print(f"  Unreachable procs:     {res['unreachable_count']}")
        if res["unreachable_count"] > 0:
            print(f"    Names:               {', '.join(res['unreachable_procs'])}")
        print(f"  Procedures:            {res['proc_count']}")
        print(
            "  Biggest procedure:     "
            f"{res['biggest_proc_lines']} "
            f"({res['biggest_proc_ratio']*100:.1f}% of code)"
        )
        print(f"  Unique variables:      {res['variable_count']}")
        print(
            f"  Variable naming score: {res['variable_name_score']:.0f} / 100"
        )

        bad_words = res.get("bad_words", [])
        print(f"  Bad words:             {len(bad_words)}")
        if bad_words:
            print(f"    Words:               {', '.join(bad_words)}")

        # WaitTime usage
        wait_count = res.get("waittime_count", 0)
        wait_lines = res.get("waittime_lines", [])
        print(f"  WaitTime calls:        {wait_count}")
        if wait_lines:
            print(f"    Lines:               {', '.join(str(n) for n in wait_lines)}")

        # Unused variables
        unused_vars = res.get("unused_vars", [])
        print(f"  Unused variables:      {len(unused_vars)}")
        if unused_vars:
            print(f"    Names:               {', '.join(unused_vars)}")

        print(f"  Overall code score:    {res['readability_score']:.0f} / 100")

    avg_complexity /= max(total_files, 1)

    # Raw average of all file scores
    raw_avg_score = avg_readability / max(total_files, 1)

    # Cap: average of the three worst modules + 40
    all_scores = [res["readability_score"] for res in file_results]
    all_scores.sort()
    if all_scores:
        worst_scores = all_scores[:3]  # if <3 files, this will just be all of them
        worst_avg = sum(worst_scores) / len(worst_scores)
        capped_avg_score = min(raw_avg_score, worst_avg + 40.0)
    else:
        capped_avg_score = raw_avg_score

    print("\n=== PROJECT SUMMARY ===")
    print(f"Files analyzed:          {total_files}")
    print(f"Total project lines:     {total_project_lines}")
    print(f"Average complexity:      {avg_complexity:.0f}")
    print(f"Average Code Score:      {capped_avg_score:.0f} / 100")
    print(f"Unique variables:        {project_unique_var_count}\n")



# -------------------------------------------------
# CLI ENTRY POINT
# -------------------------------------------------

def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze ABB RAPID code complexity and readability."
    )
    parser.add_argument(
        "folder",
        nargs="?",
        help="Folder containing RAPID files (.mod, .prg, .sys, .cfg)",
    )
    parser.add_argument(
        "--include-nostepin",
        action="store_true",
        help="Include modules marked NOSTEPIN in the analysis.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug output.",
    )
    parser.add_argument(
        "--debug-vars",
        action="store_true",
        help="Enable detailed variable naming debug output.",
    )
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    global DEBUG, DEBUG_VARS

    args = parse_args(argv)

    if args.debug:
        DEBUG = True
    if args.debug_vars:
        DEBUG_VARS = True

    if args.folder:
        root_folder = Path(args.folder)
    else:
        folder_input = input("Enter folder path containing RAPID files: ").strip()
        root_folder = Path(folder_input) if folder_input else Path(".")

    if not root_folder.exists() or not root_folder.is_dir():
        print(f"Error: '{root_folder}' is not a valid directory.")
        return 1

    ensure_wordnet()
    results, project_unique_var_count, call_graph, proc_registry = analyze_folder(
        root_folder,
        exclude_nostepin_modules=not args.include_nostepin,
    )
    print_results(results, project_unique_var_count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
