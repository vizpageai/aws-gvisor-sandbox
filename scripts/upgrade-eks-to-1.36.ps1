[CmdletBinding()]
param(
    [string]$ClusterName = "gvisor-eks",
    [string]$Region = "us-east-1",
    [string]$PublicAccessCidr = ""
)

$ErrorActionPreference = "Stop"
$terraformDirectory = Join-Path (Split-Path -Parent $PSScriptRoot) "terraform"
$upgradePath = @("1.32", "1.33", "1.34", "1.35", "1.36")

$awsCredentials = aws configure export-credentials | Out-String | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Unable to export the active AWS CLI credentials. Run 'aws login' and retry." }
$env:AWS_ACCESS_KEY_ID = $awsCredentials.AccessKeyId
$env:AWS_SECRET_ACCESS_KEY = $awsCredentials.SecretAccessKey
$env:AWS_SESSION_TOKEN = $awsCredentials.SessionToken

if ([string]::IsNullOrWhiteSpace($PublicAccessCidr)) {
    $publicIp = (Invoke-RestMethod -Uri "https://checkip.amazonaws.com" -TimeoutSec 15).Trim()
    $parsedIp = $null
    if (-not [System.Net.IPAddress]::TryParse($publicIp, [ref]$parsedIp)) {
        throw "Unable to discover a valid public IP. Pass -PublicAccessCidr explicitly."
    }
    $PublicAccessCidr = "$publicIp/32"
}
$env:TF_VAR_cluster_endpoint_public_access_cidrs = ConvertTo-Json -Compress @($PublicAccessCidr)

$currentVersion = aws eks describe-cluster `
    --name $ClusterName `
    --region $Region `
    --query "cluster.version" `
    --output text

if ($LASTEXITCODE -ne 0) {
    throw "Unable to read EKS cluster $ClusterName. Authenticate with 'aws login' and try again."
}

$currentIndex = [Array]::IndexOf($upgradePath, $currentVersion.Trim())
if ($currentIndex -lt 0) {
    throw "Cluster version $currentVersion is outside the supported 1.32-to-1.36 upgrade path."
}

terraform "-chdir=$terraformDirectory" init
if ($LASTEXITCODE -ne 0) {
    throw "terraform init failed."
}

# Reconcile the current control-plane minor first so workers are not left
# behind before advancing to the next minor.
$versionsToApply = $upgradePath[$currentIndex..($upgradePath.Count - 1)]

foreach ($version in $versionsToApply) {
    Write-Host "Reconciling EKS and Ubuntu nodes at Kubernetes $version"

    $commonArguments = @(
        "-chdir=$terraformDirectory",
        "apply",
        "-var=cluster_version=$version",
        "-var=enable_gpu_node_group=true"
    )

    if ($version -ne "1.36") {
        # The repository pins a 1.36 autoscaler image. Do not run it against an
        # intermediate control-plane version during the staged upgrade.
        $commonArguments += "-var=enable_cluster_autoscaler=false"
    }

    # Apply the EKS module first. Root Kubernetes/Helm providers depend on
    # values that Terraform marks unknown while the control plane is changing.
    $controlPlaneArguments = $commonArguments + "-target=module.eks"
    terraform @controlPlaneArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Upgrade stopped while reconciling the EKS control plane at Kubernetes $version."
    }

    terraform @commonArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Upgrade stopped while reconciling nodes and add-ons at Kubernetes $version. Resolve the Terraform error before continuing."
    }
}

Write-Host "EKS upgrade complete. The cluster, Ubuntu nodes, autoscaler, and GPU operator target Kubernetes 1.36."
