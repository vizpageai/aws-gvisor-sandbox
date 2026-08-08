[CmdletBinding()]
param(
    [string]$PublicIp,
    [switch]$AutoApprove,
    [switch]$SkipGpuVerification
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$terraformDirectory = Join-Path $repositoryRoot "terraform"

foreach ($command in @("aws", "terraform", "kubectl", "python")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Required command '$command' is not installed or is not on PATH."
    }
}

aws sts get-caller-identity | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "AWS authentication is unavailable. Run 'aws login' and retry."
}

# `aws login` credentials are understood by the AWS CLI but not by every
# Terraform AWS provider release. Export them only into this process so
# Terraform can use the same short-lived session without writing secrets.
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
terraform "-chdir=$terraformDirectory" init
if ($LASTEXITCODE -ne 0) { throw "terraform init failed." }

$applyArguments = @("-chdir=$terraformDirectory", "apply")
if ($AutoApprove) { $applyArguments += "-auto-approve" }
terraform @applyArguments
if ($LASTEXITCODE -ne 0) { throw "terraform apply failed." }

$region = terraform "-chdir=$terraformDirectory" output -raw region
$clusterName = terraform "-chdir=$terraformDirectory" output -raw cluster_name
$bucket = terraform "-chdir=$terraformDirectory" output -raw sandbox_s3_bucket_name
aws eks update-kubeconfig --region $region --name $clusterName
if ($LASTEXITCODE -ne 0) { throw "Unable to configure kubectl." }

python -m pip install $repositoryRoot
if ($LASTEXITCODE -ne 0) { throw "Python package installation failed." }

$verifyArguments = @(
    (Join-Path $repositoryRoot "scripts\verify_deployment.py"),
    "--region", $region,
    "--s3-bucket", $bucket
)
if ($SkipGpuVerification) { $verifyArguments += "--skip-gpu" }
python @verifyArguments
if ($LASTEXITCODE -ne 0) { throw "Deployment verification failed." }

Write-Host "Sandbox platform is ready. Run: gvisor-sandbox create my-agent --ebs-size 8"
