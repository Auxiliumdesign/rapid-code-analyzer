#!/usr/bin/env python3
"""
Tkinter GUI frontend for the RAPID Code Analyzer.

This GUI wraps the functionality in rapid_analyzer.py and presents:
  - A sortable per-file summary table
  - Detailed metrics for the selected file
  - A call-tree view
  - A WaitTime usage view
  - A small FAQ explaining the metrics
"""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path

import rapid_analyzer as ra  # backend analysis module

# Turn off noisy debug output in GUI mode
ra.DEBUG = False
ra.DEBUG_VARS = False


class RapidAnalyzerGUI:
    """Main application window for the RAPID Code Analyzer GUI."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("RAPID Code Analyzer")

        # Analysis state
        self.current_results = []
        self.current_project_vars = 0
        self.call_graph = {}
        self.proc_registry = {}

        # Checkbox state for excluding NOSTEPIN modules
        self.exclude_nostepin_var = tk.BooleanVar(value=True)

        # Per-column Treeview sort state (column -> bool for reverse)
        self.tree_sort_reverse = {}

        self._build_ui()

    # ---------------- UI BUILDING ----------------

    def _build_ui(self) -> None:
        """Create and lay out all widgets."""
        # Top frame: folder selector + analyze button + NOSTEPIN checkbox
        top = ttk.Frame(self.root, padding=10)
        top.pack(side=tk.TOP, fill=tk.X)

        self.folder_var = tk.StringVar()

        ttk.Label(top, text="Folder:").pack(side=tk.LEFT)
        self.folder_entry = ttk.Entry(top, textvariable=self.folder_var, width=60)
        self.folder_entry.pack(side=tk.LEFT, padx=5)

        ttk.Button(top, text="Browse...", command=self.browse_folder).pack(side=tk.LEFT)

        self.analyze_button = ttk.Button(top, text="Analyze", command=self.run_analysis)
        self.analyze_button.pack(side=tk.LEFT, padx=5)

        self.exclude_checkbox = ttk.Checkbutton(
            top,
            text="Exclude NOSTEPIN modules",
            variable=self.exclude_nostepin_var,
            onvalue=True,
            offvalue=False,
        )
        self.exclude_checkbox.pack(side=tk.LEFT, padx=10)

        # Middle: Notebook with "Files", "Call tree", "WaitTimes", "FAQ"
        middle = ttk.Notebook(self.root)
        middle.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        # -------------------------------------------------
        # Files tab
        # -------------------------------------------------
        files_tab = ttk.Frame(middle)
        middle.add(files_tab, text="Files")

        # PanedWindow with file list (left) and details (right)
        self.files_pane = ttk.PanedWindow(files_tab, orient=tk.HORIZONTAL)
        self.files_pane.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # Left pane: Treeview with sortable columns
        left_frame = ttk.Frame(self.files_pane)
        self.files_pane.add(left_frame)

        columns = (
            "file",
            "lines",
            "complexity",
            "score",
            "bad_words",
            "unreachable",
            "unused_vars",
        )

        self.tree = ttk.Treeview(
            left_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
        )

        headings = {
            "file": "File",
            "lines": "Lines",
            "complexity": "Complexity",
            "score": "Score",
            "bad_words": "Bad words",
            "unreachable": "Unused procs",
            "unused_vars": "Unused vars",
        }

        for col in columns:
            self.tree.heading(
                col,
                text=headings[col],
                command=lambda c=col: self.sort_by_column(c),
            )

        self.tree.column("file", width=220, anchor=tk.W)
        self.tree.column("lines", width=70, anchor=tk.E)
        self.tree.column("complexity", width=90, anchor=tk.E)
        self.tree.column("score", width=70, anchor=tk.E)
        self.tree.column("bad_words", width=90, anchor=tk.E)
        self.tree.column("unreachable", width=100, anchor=tk.E)
        self.tree.column("unused_vars", width=100, anchor=tk.E)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(left_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)

        # Right pane: details text
        right_frame = ttk.Frame(self.files_pane)
        self.files_pane.add(right_frame)

        ttk.Label(right_frame, text="File details:").pack(anchor=tk.W)

        self.details_text = tk.Text(right_frame, wrap=tk.WORD, height=20)
        self.details_text.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        details_y = ttk.Scrollbar(
            right_frame, orient=tk.VERTICAL, command=self.details_text.yview
        )
        self.details_text.configure(yscrollcommand=details_y.set)
        details_y.pack(side=tk.RIGHT, fill=tk.Y)

        # -------------------------------------------------
        # Call tree tab
        # -------------------------------------------------
        call_tab = ttk.Frame(middle)
        middle.add(call_tab, text="Call tree")

        call_top = ttk.Frame(call_tab)
        call_top.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.call_tree_text = tk.Text(call_top, wrap=tk.NONE)
        self.call_tree_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        call_scroll_y = ttk.Scrollbar(
            call_top, orient=tk.VERTICAL, command=self.call_tree_text.yview
        )
        self.call_tree_text.configure(yscrollcommand=call_scroll_y.set)
        call_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)

        # -------------------------------------------------
        # WaitTimes tab
        # -------------------------------------------------
        wait_tab = ttk.Frame(middle)
        middle.add(wait_tab, text="WaitTimes")

        self.wait_text = tk.Text(wait_tab, wrap=tk.WORD)
        self.wait_text.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=5, pady=5)

        wait_scroll_y = ttk.Scrollbar(
            wait_tab, orient=tk.VERTICAL, command=self.wait_text.yview
        )
        self.wait_text.configure(yscrollcommand=wait_scroll_y.set)
        wait_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)

        # -------------------------------------------------
        # FAQ tab
        # -------------------------------------------------
        faq_tab = ttk.Frame(middle)
        middle.add(faq_tab, text="FAQ")

        faq_text = tk.Text(faq_tab, wrap=tk.WORD)
        faq_text.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=5, pady=5)

        faq_content = """RAPID Code Analyzer – FAQ

Code score
  A single 0–100 score that combines several aspects:
  - Complexity (IF/WHILE/TEST, nesting)
  - Call-chain depth from MAIN
  - Procedure size
  - Comment coverage
  - Variable naming quality
  Higher is better. Values above ~80 are usually very good.

Comments %
  Percentage of lines that are comments.
  Ideal range is roughly 15–25%. Too few comments can hurt
  readability, but too many may indicate noise or commented-out code.

Simple complexity
  Rough count of decision points in the file (IF, WHILE, FOR,
  TEST, CASE, ELSEIF, etc). Higher means more branching logic.

Depth complexity
  Takes nesting into account, not just the number of decisions.
  Deeply nested code is harder to understand and maintain.

Max nesting depth
  Maximum number of nested control blocks (IF/WHILE/FOR/TEST).
  Large values mean “pyramid” code that is hard to read.

Max call-chain depth
  Longest call chain starting from MAIN (or similar entry point).
  Deep call stacks can make behavior harder to trace.

Procedures / Biggest procedure
  - Procedures: number of PROC/FUNC blocks in the file.
  - Biggest procedure: size of the largest one, and how big that
    is as a percentage of the file’s code.
  Very large procedures are often worth splitting.

Unreachable procs
  Procedures that are not called from MAIN (and not detected as
  dynamically called by prefix). They might be dead code, helpers
  called from other projects, or entry points not wired up yet.

Unique variables
  Distinct variable names in the file. High counts often go with
  complex modules.

Variable naming score
  0–100 rating of how “dictionary-like” the variable names are.
  It uses WordNet and a whitelist of allowed short tokens.
  Low scores usually indicate very short or cryptic names.

Bad words
  Tokens in variable names that:
    - Are very short and not whitelisted, or
    - Are not found in the dictionary.
  These are good candidates for renaming to clearer words.
"""
        faq_text.insert("1.0", faq_content)
        faq_text.config(state=tk.DISABLED)

        # -------------------------------------------------
        # Bottom: summary bar with color background
        # -------------------------------------------------
        bottom = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        bottom.pack(side=tk.BOTTOM, fill=tk.X)

        self.summary_var = tk.StringVar(value="Ready.")
        self.summary_label = tk.Label(
            bottom,
            textvariable=self.summary_var,
            anchor="w",
            padx=5,
            pady=3,
        )
        self.summary_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.copy_summary_button = ttk.Button(
            bottom,
            text="Copy summary",
            command=self.copy_summary_to_clipboard,
        )
        self.copy_summary_button.pack(side=tk.RIGHT, padx=5)

        # After layout is done, move the sash so the left pane is wider
        self.root.after(50, self._set_initial_sash)

    def _set_initial_sash(self) -> None:
        """Place the sash so the left panel gets most of the width."""
        total_width = self.files_pane.winfo_width()
        if total_width <= 1:
            # If the widget isn't fully laid out yet, try again shortly.
            self.root.after(50, self._set_initial_sash)
            return

        # Give about 80% of the width to the left pane (table), 20% to details
        self.files_pane.sashpos(0, int(total_width * 0.5))

    # ---------------- HELPERS ----------------

    def wrap_list_for_display(
        self, items, prefix: str, max_per_line: int = 5
    ) -> list[str]:
        """
        Break a list of items into multiple nicely indented lines:

          prefix + item1, item2, ..., item5
                   item6, item7, ...

        to avoid very long lines in the details panel.
        """
        lines = []
        if not items:
            return lines

        indent = " " * len(prefix)

        for i in range(0, len(items), max_per_line):
            chunk = ", ".join(items[i : i + max_per_line])
            if i == 0:
                lines.append(prefix + chunk)
            else:
                lines.append(indent + chunk)

        return lines

    def copy_summary_to_clipboard(self) -> None:
        """
        Copy the bottom summary (files, total lines, total complexity,
        avg code score, unique variables) as tab-separated values.
        """
        if not self.current_results:
            header = (
                "Files\tTotal lines\tTotal complexity\t"
                "Avg code score\tUnique variables\n"
            )
            self.root.clipboard_clear()
            self.root.clipboard_append(header)
            self.root.update()
            return

        total_files = len(self.current_results)
        total_lines = sum(res["total_lines"] for res in self.current_results)

        total_complexity = sum(
            (res["simple_complexity"] + res["depth_complexity"])
            for res in self.current_results
        )

        avg_score = (
            sum(res["readability_score"] for res in self.current_results)
            / total_files
            if total_files > 0
            else 0.0
        )

        unique_vars = self.current_project_vars

        header = (
            "Files\tTotal lines\tTotal complexity\t"
            "Avg code score\tUnique variables\n"
        )
        row = (
            f"{total_files}\t{total_lines}\t{total_complexity:.0f}\t"
            f"{avg_score:.1f}\t{unique_vars}\n"
        )
        tsv = header + row

        self.root.clipboard_clear()
        self.root.clipboard_append(tsv)
        self.root.update()

    def update_waittime_tab(self) -> None:
        """Fill the WaitTimes tab with all WaitTime calls and 2 lines of context."""
        if not hasattr(self, "wait_text"):
            return

        self.wait_text.config(state=tk.NORMAL)
        self.wait_text.delete("1.0", tk.END)

        if not self.current_results:
            self.wait_text.insert(tk.END, "No analysis yet.\n")
            self.wait_text.config(state=tk.DISABLED)
            return

        any_wait = False

        for res in self.current_results:
            path = Path(res["file_path"])
            wait_lines = res.get("waittime_lines", [])
            if not wait_lines:
                continue

            any_wait = True

            try:
                with path.open("r", encoding="utf-8") as f:
                    lines = f.readlines()
            except UnicodeDecodeError:
                with path.open("r", encoding="cp1252") as f:
                    lines = f.readlines()

            self.wait_text.insert(tk.END, f"File: {path}\n")

            for lineno in wait_lines:
                start = max(1, lineno - 2)
                end = min(len(lines), lineno + 2)

                self.wait_text.insert(tk.END, f"  WaitTime at line {lineno}:\n")
                for i in range(start, end + 1):
                    prefix = ">" if i == lineno else " "
                    line_text = lines[i - 1].rstrip("\n")
                    self.wait_text.insert(
                        tk.END,
                        f"   {prefix} {i:5d}: {line_text}\n",
                    )
                self.wait_text.insert(tk.END, "\n")

            self.wait_text.insert(tk.END, "\n")

        if not any_wait:
            self.wait_text.insert(
                tk.END, "No WaitTime calls found in any analyzed file.\n"
            )

        self.wait_text.config(state=tk.DISABLED)

    # ---------------- FOLDER / ANALYSIS ----------------

    def sort_by_column(self, col: str) -> None:
        """Sort the treeview by the given column. Click again to reverse the order."""
        items = list(self.tree.get_children(""))

        data = [(self.tree.set(item, col), item) for item in items]

        numeric_cols = {
            "lines",
            "complexity",
            "score",
            "bad_words",
            "unreachable",
            "unused_vars",
        }

        def to_number(value: str):
            if not isinstance(value, str):
                return value
            v = value.strip()
            if v.endswith("%"):
                v = v[:-1]
            try:
                return float(v)
            except ValueError:
                return v

        if col in numeric_cols:
            data = [(to_number(v), item) for (v, item) in data]

        reverse = self.tree_sort_reverse.get(col, False)
        data.sort(reverse=reverse)

        for index, (_, item) in enumerate(data):
            self.tree.move(item, "", index)

        self.tree_sort_reverse[col] = not reverse

    def browse_folder(self) -> None:
        """Open a folder chooser and put the selected path into the entry."""
        folder = filedialog.askdirectory()
        if folder:
            self.folder_var.set(folder)

    def run_analysis(self) -> None:
        """Run the backend analysis and refresh all views."""
        folder = self.folder_var.get().strip()
        if not folder:
            messagebox.showerror("Error", "Please select a folder.")
            return

        path = Path(folder)
        if not path.exists() or not path.is_dir():
            messagebox.showerror("Error", f"'{path}' is not a valid directory.")
            return

        self.analyze_button.config(state=tk.DISABLED)
        self.summary_var.set("Analyzing... this may take a moment.")
        self.root.update_idletasks()

        try:
            ra.ensure_wordnet()
            (
                results,
                project_unique_var_count,
                call_graph,
                proc_registry,
            ) = ra.analyze_folder(
                path,
                exclude_nostepin_modules=self.exclude_nostepin_var.get(),
            )
        except Exception as e:  # simple GUI app
            messagebox.showerror("Error during analysis", str(e))
            self.analyze_button.config(state=tk.NORMAL)
            self.summary_var.set("Error.")
            return

        self.current_results = results
        self.current_project_vars = project_unique_var_count
        self.call_graph = call_graph
        self.proc_registry = proc_registry

        self.populate_tree()
        self.update_summary()
        self.update_call_tree()
        self.update_waittime_tab()

        self.analyze_button.config(state=tk.NORMAL)

    # ---------------- POPULATE UI WITH RESULTS ----------------

    def populate_tree(self) -> None:
        """Fill the summary table with one row per analyzed file."""
        for item in self.tree.get_children():
            self.tree.delete(item)

        for idx, res in enumerate(self.current_results):
            file_name = Path(res["file_path"]).name
            bad_words = res.get("bad_words", [])
            bad_words_count = len(bad_words)
            unreachable_count = res.get("unreachable_count", 0)
            unused_count = res.get(
                "unused_var_count", len(res.get("unused_vars", []))
            )

            self.tree.insert(
                "",
                tk.END,
                iid=str(idx),
                values=(
                    file_name,
                    res["total_lines"],
                    res["simple_complexity"],
                    f"{res['readability_score']:.0f}",
                    bad_words_count,
                    unreachable_count,
                    unused_count,
                ),
            )

        if not self.current_results:
            self.details_text.delete("1.0", tk.END)
        else:
            first_id = self.tree.get_children()[0]
            self.tree.selection_set(first_id)
            self.tree.focus(first_id)
            self.show_details_for_index(0)

    def update_summary(self) -> None:
        """Update the bottom summary text and background color based on avg score."""
        if not self.current_results:
            self.summary_var.set("No RAPID files found.")
            self.summary_label.config(bg=self.root.cget("bg"))
            return

        total_files = len(self.current_results)
        total_lines = sum(res["total_lines"] for res in self.current_results)

        total_complexity = sum(
            (res["simple_complexity"] + res["depth_complexity"])
            for res in self.current_results
        )

        avg_score = (
            sum(res["readability_score"] for res in self.current_results)
            / total_files
        )

        self.summary_var.set(
            f"Files: {total_files} | Total lines: {total_lines} | "
            f"Total complexity: {total_complexity:.0f} | "
            f"Avg code score: {avg_score:.0f}/100 | "
            f"Unique variables (project): {self.current_project_vars}"
        )

        if avg_score >= 80:
            bg = "#ccffcc"  # light green
        elif avg_score >= 50:
            bg = "#fff5cc"  # light yellow
        else:
            bg = "#ffd6d6"  # light red

        self.summary_label.config(bg=bg)

    def on_tree_select(self, event) -> None:
        """Handle selection change in the summary table."""
        selection = self.tree.selection()
        if not selection:
            return
        idx = int(selection[0])
        self.show_details_for_index(idx)

    def show_details_for_index(self, idx: int) -> None:
        """Show the detailed metrics for the file at the given index."""
        if idx < 0 or idx >= len(self.current_results):
            return
        res = self.current_results[idx]
        text = self.format_file_details(res)
        self.details_text.delete("1.0", tk.END)
        self.details_text.insert(tk.END, text)

    def format_file_details(self, res) -> str:
        """Format a single file's metrics into human-readable text."""
        lines = []
        lines.append(f"File: {res['file_path']}")
        lines.append("")
        lines.append(f"Total lines:          {res['total_lines']}")
        lines.append(f"Code lines:           {res['code_lines']}")
        lines.append(f"Comment lines:        {res['comment_lines']}")
        lines.append(f"Comment ratio:        {res['comment_ratio']:.0f} %")
        lines.append(f"Simple complexity:    {res['simple_complexity']}")
        lines.append(f"Depth complexity:     {res['depth_complexity']}")

        if res.get("max_nesting_line") is not None:
            lines.append(
                "Max nesting depth:    "
                f"{res['max_nesting']} "
                f"(at line {res['max_nesting_line']} in {res['max_nesting_proc']})"
            )
        else:
            lines.append(f"Max nesting depth:    {res['max_nesting']}")

        lines.append(f"Max call-chain depth: {res['max_call_depth']}")
        lines.append("")
        lines.append(f"Procedures:           {res['proc_count']}")
        lines.append(
            "Biggest procedure:    "
            f"{res['biggest_proc_lines']} "
            f"({res['biggest_proc_ratio']*100:.1f}% of code)"
        )

        lines.append(f"Unreachable procs:    {res['unreachable_count']}")
        if res.get("unreachable_count", 0) > 0:
            names = res.get("unreachable_procs", [])
            lines.extend(
                self.wrap_list_for_display(
                    names,
                    prefix="  Names:              ",
                    max_per_line=5,
                )
            )

        unused_vars = res.get("unused_vars", [])
        lines.append("")
        lines.append(f"Unused variables:     {len(unused_vars)}")
        if unused_vars:
            lines.extend(
                self.wrap_list_for_display(
                    unused_vars,
                    prefix="  Names:              ",
                    max_per_line=5,
                )
            )

        lines.append("")
        lines.append(f"Unique variables:     {res['variable_count']}")
        lines.append(
            f"Variable naming score:{res['variable_name_score']:.0f} / 100"
        )

        bad_words = res.get("bad_words", [])
        lines.append(f"Bad words:            {len(bad_words)}")
        if bad_words:
            lines.extend(
                self.wrap_list_for_display(
                    bad_words,
                    prefix="  Words:              ",
                    max_per_line=5,
                )
            )

        wait_count = res.get("waittime_count", 0)
        wait_lines = res.get("waittime_lines", [])
        lines.append("")
        lines.append(f"WaitTime calls:       {wait_count}")
        if wait_lines:
            wait_line_strs = [str(n) for n in wait_lines]
            lines.extend(
                self.wrap_list_for_display(
                    wait_line_strs,
                    prefix="  At lines:           ",
                    max_per_line=5,
                )
            )

        lines.append("")
        lines.append(f"Overall code score:   {res['readability_score']:.0f} / 100")

        return "\n".join(lines)

    # ---------------- CALL TREE TAB ----------------

    def update_call_tree(self) -> None:
        """Refresh the call tree tab text."""
        if not self.call_graph:
            self.call_tree_text.delete("1.0", tk.END)
            self.call_tree_text.insert(tk.END, "No calls detected.")
            return

        text = self.build_call_tree_text()
        self.call_tree_text.delete("1.0", tk.END)
        self.call_tree_text.insert(tk.END, text)

    def build_call_tree_text(self) -> str:
        """Build a textual representation of the call graph."""
        cg = self.call_graph
        lines = []

        if not cg:
            lines.append("No calls detected.")
            return "\n".join(lines)

        main_candidates = [
            p
            for p in cg.keys()
            if p.lower().endswith("::main") or p.lower() == "main"
        ]

        if main_candidates:
            lines.append("=== Call tree from MAIN ===")
        else:
            lines.append("=== Call tree (no MAIN found; showing all roots) ===")

        lines.append("")

        if not main_candidates:
            main_candidates = sorted(cg.keys())

        def dfs(node, depth, visiting):
            indent = "  " * depth
            lines.append(f"{depth} {indent}{node}")
            if node in visiting:
                lines.append(f"{depth} {indent}(cycle detected, stopping here)")
                return
            visiting.add(node)
            for callee in sorted(cg.get(node, [])):
                dfs(callee, depth + 1, visiting)
            visiting.remove(node)

        for m in sorted(main_candidates):
            dfs(m, 0, set())
            lines.append("")

        return "\n".join(lines)


def main() -> None:
    root = tk.Tk()
    app = RapidAnalyzerGUI(root)
    # A sensible default window size; user can resize as they like.
    root.geometry("1200x800")
    root.mainloop()


if __name__ == "__main__":
    main()
