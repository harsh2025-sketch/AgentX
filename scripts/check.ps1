<#
.SYNOPSIS
    Runs the full AgentX validation suite: tests, lint, format check, type check.

.DESCRIPTION
    Equivalent to running, in order, from the repository root:

        python -m pytest
        python -m ruff check .
        python -m ruff format --check .
        python -m mypy

    Every tool is invoked through "python -m" so all steps use the interpreter
    of the active virtual environment. Stops at the first failing step and
    exits with that step's exit code. Requires an activated virtual environment
    with the "dev" extra installed (see README.md).

    Compatible with Windows PowerShell 5.1 and PowerShell 7+.

.EXAMPLE
    .\scripts\check.ps1
#>
[CmdletBinding()]
param()

$repoRoot = Split-Path -Parent $PSScriptRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "python was not found on PATH. Activate the virtual environment first:" -ForegroundColor Red
    Write-Host "    .\.venv\Scripts\Activate.ps1"
    exit 1
}

$steps = @(
    @{ Name = "Tests";        Command = @("python", "-m", "pytest") },
    @{ Name = "Lint";         Command = @("python", "-m", "ruff", "check", ".") },
    @{ Name = "Format check"; Command = @("python", "-m", "ruff", "format", "--check", ".") },
    @{ Name = "Type check";   Command = @("python", "-m", "mypy") }
)

$exitCode = 0
Push-Location $repoRoot
foreach ($step in $steps) {
    $exe = $step.Command[0]
    $arguments = @($step.Command | Select-Object -Skip 1)

    Write-Host ""
    Write-Host "==> $($step.Name): $($step.Command -join ' ')" -ForegroundColor Cyan

    & $exe @arguments
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $($step.Name) (exit code $LASTEXITCODE)" -ForegroundColor Red
        $exitCode = $LASTEXITCODE
        break
    }
}
Pop-Location

if ($exitCode -eq 0) {
    Write-Host ""
    Write-Host "All checks passed." -ForegroundColor Green
}
exit $exitCode
