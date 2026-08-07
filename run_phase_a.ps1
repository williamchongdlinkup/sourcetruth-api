# Phase A ingestion + eval master script
# Run from: api/ directory
# Usage: .\run_phase_a.ps1

$PY = "python\\.venv\\Scripts\\python.exe"
$PYDIR = "python"

function Run-Step($label, $cmd) {
    Write-Host "`n========================================" -ForegroundColor Cyan
    Write-Host "STEP: $label" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    Invoke-Expression $cmd
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[WARN] Step exited with code $LASTEXITCODE — continuing" -ForegroundColor Yellow
    }
}

Set-Location $PSScriptRoot

# ── INGESTION — already complete; scripts will skip already-ingested texts ───

# Run-Step "Chinese expansion (Zhuangzi + Xunzi)" "& $PY $PYDIR/ingestion/chinese_expansion.py"
# Run-Step "Rumi Masnavi (Nicholson Vol 1-2)" "& $PY $PYDIR/ingestion/rumi.py"
# Run-Step "Christian theology (Augustine + Apostolic Fathers)" "& $PY $PYDIR/ingestion/christian_theology.py"

# ── IVFFlat REBUILD ──────────────────────────────────────────────────────────

Run-Step "Rebuild IVFFlat index" `
    "& $PY $PYDIR/rebuild_ivfflat.py"

# ── EVAL: HINDU (Bhagavad Gita + Upanishads + Yoga Sutras) ──────────────────

Run-Step "Hindu eval: generate queries" `
    "& $PY $PYDIR/../eval_hindu/generate_queries.py"

Run-Step "Hindu eval: score" `
    "& $PY $PYDIR/../eval_hindu/score.py"

Run-Step "Hindu eval: judge" `
    "& $PY $PYDIR/../eval_hindu/judge.py"

# ── EVAL: GREEK ──────────────────────────────────────────────────────────────

Run-Step "Greek eval: generate queries" `
    "& $PY $PYDIR/../eval_greek/generate_queries.py"

Run-Step "Greek eval: score" `
    "& $PY $PYDIR/../eval_greek/score.py"

Run-Step "Greek eval: judge" `
    "& $PY $PYDIR/../eval_greek/judge.py"

# ── EVAL: ISLAMIC ─────────────────────────────────────────────────────────────

Run-Step "Islamic eval: generate queries" `
    "& $PY $PYDIR/../eval_islamic/generate_queries.py"

Run-Step "Islamic eval: score" `
    "& $PY $PYDIR/../eval_islamic/score.py"

Run-Step "Islamic eval: judge" `
    "& $PY $PYDIR/../eval_islamic/judge.py"

# ── EVAL: CHINESE ─────────────────────────────────────────────────────────────

Run-Step "Chinese eval: generate queries" `
    "& $PY $PYDIR/../eval_chinese/generate_queries.py"

Run-Step "Chinese eval: score" `
    "& $PY $PYDIR/../eval_chinese/score.py"

Run-Step "Chinese eval: judge" `
    "& $PY $PYDIR/../eval_chinese/judge.py"

# ── EVAL: CHRISTIANITY ────────────────────────────────────────────────────────

Run-Step "Christianity eval: generate queries" `
    "& $PY $PYDIR/../eval_christianity/generate_queries.py"

Run-Step "Christianity eval: score" `
    "& $PY $PYDIR/../eval_christianity/score.py"

Run-Step "Christianity eval: judge" `
    "& $PY $PYDIR/../eval_christianity/judge.py"

Write-Host "`n========================================" -ForegroundColor Green
Write-Host "Phase A ingestion + eval pipeline DONE" -ForegroundColor Green
Write-Host "Next: review judge_report.md files, update hybrid_search.py, push to GitHub" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
