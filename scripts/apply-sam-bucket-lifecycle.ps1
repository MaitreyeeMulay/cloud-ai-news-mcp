<#
.SYNOPSIS
    Adds the lifecycle rule in infra\sam-bucket-lifecycle.json to SAM's managed upload bucket.

.DESCRIPTION
    `sam deploy` (resolve_s3 = true) uploads the code zip to a bucket that SAM creates in its own
    stack, aws-sam-cli-managed-default. That bucket has versioning on and no lifecycle rule, so
    old uploads would be kept (and billed) forever. This script, run once after the first deploy:
      - deletes uploads 90 days after they were written,
      - deletes old (noncurrent) versions 1 day after they are replaced or deleted,
      - aborts unfinished multipart uploads after 1 day,
      - removes leftover delete markers.
    It deletes nothing itself: S3 applies the rule in the background, usually within a day.
    Note: the bucket is shared by every SAM project in this account and region.

.EXAMPLE
    .\scripts\apply-sam-bucket-lifecycle.ps1
    .\scripts\apply-sam-bucket-lifecycle.ps1 -Yes   # skip the confirmation prompt
#>
[CmdletBinding()]
param(
    [string]$Region = "eu-west-1",
    [string]$ManagedStack = "aws-sam-cli-managed-default",
    [switch]$Yes
)

# Native commands (aws) report failure through $LASTEXITCODE, so check it after each call.
$ErrorActionPreference = "Continue"

# Step 1: locate the rules file next to this repo's infra\ folder.
$rulesFile = (Resolve-Path (Join-Path $PSScriptRoot "..\infra\sam-bucket-lifecycle.json")).Path
Write-Host "Rules file: $rulesFile"

# Step 2: find the bucket name from SAM's managed stack (it has a random suffix).
$bucket = aws cloudformation describe-stack-resource `
    --stack-name $ManagedStack `
    --logical-resource-id SamCliSourceBucket `
    --region $Region `
    --query "StackResourceDetail.PhysicalResourceId" `
    --output text
if ($LASTEXITCODE -ne 0 -or -not $bucket) {
    Write-Error "Could not find stack '$ManagedStack' in $Region. Run 'sam deploy' first."
    exit 1
}
Write-Host "Bucket:     $bucket"

# Step 3: show the current lifecycle config. "NoSuchLifecycleConfiguration" just means none yet.
# put-bucket-lifecycle-configuration REPLACES the whole config, so look before overwriting.
Write-Host "`nCurrent lifecycle configuration:"
aws s3api get-bucket-lifecycle-configuration --bucket $bucket --region $Region --output json
if ($LASTEXITCODE -ne 0) { Write-Host "(none, or not readable - see message above)" }

# Step 4: ask before changing anything, unless -Yes was given.
if (-not $Yes) {
    $answer = Read-Host "`nReplace it with the rules from $rulesFile? (y/N)"
    if ($answer -ne "y") { Write-Host "Nothing changed."; exit 0 }
}

# Step 5: apply the rules (file:// makes the CLI read the JSON from disk).
aws s3api put-bucket-lifecycle-configuration `
    --bucket $bucket `
    --region $Region `
    --lifecycle-configuration "file://$rulesFile"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Applying the lifecycle rule failed (see message above)."
    exit 1
}

# Step 6: read the config back to confirm it took effect.
Write-Host "`nApplied. Lifecycle configuration is now:"
aws s3api get-bucket-lifecycle-configuration --bucket $bucket --region $Region --output json
exit $LASTEXITCODE
