# RAPID Code Analyzer

A Python tool + Tkinter GUI to analyze ABB RAPID code:

- Computes complexity and readability metrics per file
- Detects unreachable procedures from `Main`
- Rates variable naming using WordNet
- Shows all `WaitTime` calls with context
- Visualizes a call tree starting from `Main`
- Supports optional exclusion of `NOSTEPIN` modules

## Features

- **Code metrics**
  - Total lines / code lines / comment ratio
  - Simple + depth complexity
  - Max nesting and call-chain depth
  - Largest procedure and number of procedures
- **Naming quality**
  - Variable naming score (0–100)
  - List of “bad words” (vague/unknown tokens)
- **WaitTime overview**
  - All `WaitTime` calls with 2 lines of context before and after
- **Call tree**
  - Text-based call tree view starting from `Main`
- **GUI goodies**
  - Sortable file table
  - Color-coded summary bar (green/yellow/red based on average code score)
  - FAQ tab explaining all metrics
  - Option to exclude `NOSTEPIN` modules
  - “Copy summary” button (easy paste into Excel)

## Installation

```bash
git clone https://github.com/YOUR-USERNAME/rapid-code-analyzer.git
cd rapid-code-analyzer
python -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate
pip install -r requirements.txt

## Usage (GUI)
python rapid_analyzer_gui.py

Then:

Choose a folder with RAPID files (.mod, .prg, .sys, .cfg).

Click Analyze.

Explore the Files, Call tree, WaitTimes, and FAQ tabs.

Usage (CLI)

You can also run the analyzer directly:

python rapid_analyzer.py PATH/TO/FOLDER
