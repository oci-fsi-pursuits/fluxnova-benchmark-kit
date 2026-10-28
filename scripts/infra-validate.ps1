param([string]$Terraform = (Join-Path $PSScriptRoot '../.tools/terraform/terraform.exe'), [switch]$SkipInit)
$ErrorActionPreference = 'Stop'
$infraRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../infra'))
if (-not (Test-Path -LiteralPath $Terraform)) { throw 'Terraform binary missing; install the reviewed pinned binary or pass -Terraform.' }
& $Terraform fmt -check -recursive $infraRoot
if ($LASTEXITCODE -ne 0) { throw 'Terraform formatting or syntax check failed.' }
foreach ($stackName in @('oci-reference', 'oci-evidence')) {
    $stackPath = Join-Path $infraRoot $stackName
    if (-not $SkipInit) {
        & $Terraform "-chdir=$stackPath" init -backend=false -input=false -no-color
        if ($LASTEXITCODE -ne 0) { throw "Provider initialization failed for $stackName; no cloud changes were applied." }
    }
    & $Terraform "-chdir=$stackPath" validate -no-color
    if ($LASTEXITCODE -ne 0) { throw "Provider schema validation failed for $stackName." }
}
Write-Output 'Both Terraform stacks validated. This does not validate tenancy IAM, regional capacity, image boot, or runtime readiness.'
