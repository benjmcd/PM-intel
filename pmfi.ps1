param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = "Stop"
python "$PSScriptRoot\scripts\task.py" @Arguments
exit $LASTEXITCODE
