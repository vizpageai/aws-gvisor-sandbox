[CmdletBinding()]
param(
    [string]$PublicIp,
    [switch]$AutoApprove
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$terraformDirectory = Join-Path $repositoryRoot "terraform"

foreach ($command in @("aws", "terraform")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Required command '$command' is not installed or is not on PATH."
    }
}

aws sts get-caller-identity | Out-Null
if ($LASTEXITCODE -ne 0) { throw "AWS authentication is unavailable. Run 'aws login' and retry." }

$awsCredentials = aws configure export-credentials | Out-String | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Unable to export the active AWS CLI credentials." }
$env:AWS_ACCESS_KEY_ID = $awsCredentials.AccessKeyId
$env:AWS_SECRET_ACCESS_KEY = $awsCredentials.SecretAccessKey
$env:AWS_SESSION_TOKEN = $awsCredentials.SessionToken

if (-not $PublicIp) {
    $PublicIp = (Invoke-RestMethod -Uri "https://checkip.amazonaws.com" -TimeoutSec 15).Trim()
}
$parsedPublicIp = $null
if (-not [System.Net.IPAddress]::TryParse($PublicIp, [ref]$parsedPublicIp) -or
    $parsedPublicIp.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
    throw "PublicIp must be an IPv4 address without a CIDR suffix."
}
$env:TF_VAR_cluster_endpoint_public_access_cidrs = ConvertTo-Json -Compress @("$PublicIp/32")

Write-Warning "Terraform will remove the project infrastructure. Back up required S3 and EBS data before approving."
terraform "-chdir=$terraformDirectory" init -lockfile=readonly
if ($LASTEXITCODE -ne 0) { throw "terraform init failed." }

$destroyArguments = @("-chdir=$terraformDirectory", "destroy")
if ($AutoApprove) { $destroyArguments += "-auto-approve" }
terraform @destroyArguments
if ($LASTEXITCODE -ne 0) { throw "terraform destroy failed; inspect remaining resources before retrying." }
