# ============================================================================
# test_all.ps1 - Esegue l'intera pipeline del portafoglio e tutti i controlli.
#
# Uso (PowerShell, da qualunque posizione):
#   .\test_all.ps1                     # tutto, inclusi i fondamentali
#   .\test_all.ps1 -SkipFondamentali   # salta il download fondamentali
#                                      # (risparmia quota Alpha Vantage, 25/giorno)
#   .\test_all.ps1 -NoBrowser          # non aprire il report alla fine
#
# Sequenza:
#   0. verifica dipendenze Python (installa quelle mancanti)
#   1. download fondamentali (Alpha Vantage, rotazione automatica quota)
#   2. pipeline serale: prezzi -> pulizia -> feature -> qualita' -> report HTML
#   3. controlli: check_prices, check_fundamentals, check_clean, check_features
#   4. apre data\reports\latest.html nel browser
# ============================================================================

param(
    [switch]$SkipFondamentali,
    [switch]$NoBrowser
)

# Lavora sempre dalla cartella dove sta questo script (portafoglio/)
Set-Location -Path $PSScriptRoot

$fallimenti = @()
$inizio = Get-Date

function Esegui-Passo {
    param([string]$Nome, [string[]]$Comando)
    Write-Host ""
    Write-Host ("=" * 70) -ForegroundColor DarkGray
    Write-Host ">> $Nome" -ForegroundColor Cyan
    Write-Host ("=" * 70) -ForegroundColor DarkGray
    & python @Comando
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FALLITO] $Nome (exit code $LASTEXITCODE)" -ForegroundColor Red
        $script:fallimenti += $Nome
    } else {
        Write-Host "[OK] $Nome" -ForegroundColor Green
    }
}

# --- 0. Dipendenze ----------------------------------------------------------
Write-Host ">> Verifica dipendenze Python..." -ForegroundColor Cyan
python -c "import pandas, yaml, pyarrow, requests, dotenv, yfinance" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "   Dipendenze mancanti: le installo..." -ForegroundColor Yellow
    python -m pip install --quiet pandas pyyaml pyarrow requests python-dotenv yfinance
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FALLITO] Installazione dipendenze: interrompo." -ForegroundColor Red
        exit 1
    }
}
Write-Host "[OK] Dipendenze pronte" -ForegroundColor Green

# --- 1. Fondamentali (opzionale) ---------------------------------------------
if (-not $SkipFondamentali) {
    Esegui-Passo "Fondamentali - Alpha Vantage (rotazione quota 25/giorno)" @("src\ingestion\fundamentals_av.py")
} else {
    Write-Host ""
    Write-Host ">> Fondamentali saltati (-SkipFondamentali)" -ForegroundColor Yellow
}

# --- 2. Pipeline serale: prezzi -> pulizia -> report --------------------------
Esegui-Passo "Pipeline serale (prezzi -> pulizia -> report)" @("scripts\run_daily_update.py")

# --- 3. Controlli -------------------------------------------------------------
Esegui-Passo "Controllo prezzi (raw)" @("scripts\check_prices.py")
Esegui-Passo "Controllo fondamentali (raw)" @("scripts\check_fundamentals.py")
Esegui-Passo "Controllo pulizia (raw vs clean)" @("scripts\check_clean.py")
Esegui-Passo "Controllo feature (rendimenti + volatilita')" @("scripts\check_features.py")

# --- Riepilogo ----------------------------------------------------------------
$durata = (Get-Date) - $inizio
Write-Host ""
Write-Host ("=" * 70) -ForegroundColor DarkGray
if ($fallimenti.Count -eq 0) {
    Write-Host ("TUTTO OK in {0:mm}m {0:ss}s" -f $durata) -ForegroundColor Green
} else {
    Write-Host ("COMPLETATO CON ERRORI in {0:mm}m {0:ss}s - passi falliti:" -f $durata) -ForegroundColor Red
    $fallimenti | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
}
Write-Host ("=" * 70) -ForegroundColor DarkGray

# --- 4. Apri il report ----------------------------------------------------------
$report = Join-Path $PSScriptRoot "data\reports\latest.html"
if ((-not $NoBrowser) -and (Test-Path $report)) {
    Write-Host "Apro il report: $report"
    Start-Process $report
}

exit $fallimenti.Count
