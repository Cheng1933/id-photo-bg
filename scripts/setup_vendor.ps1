# Fetch the upstream matting kernel and apply this project's patches.
# 拉取上游抠图内核并打上本项目的补丁。
#
# Usage / 用法:  powershell -ExecutionPolicy Bypass -File scripts\setup_vendor.ps1

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$vendor = Join-Path $root "vendor\HivisionIDPhotos"
$patchFile = Join-Path $root "patches\hivision-idphotos.patch"
$commitFile = Join-Path $root "patches\UPSTREAM_COMMIT.txt"

if (Test-Path (Join-Path $vendor "hivision")) {
    Write-Host "vendor already exists, skipping. / vendor 已存在，跳过。" -ForegroundColor Yellow
    exit 0
}

Write-Host "== 1/3 clone HivisionIDPhotos ==" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path (Split-Path $vendor) | Out-Null
git clone https://github.com/Zeyi-Lin/HivisionIDPhotos.git $vendor

if (Test-Path $commitFile) {
    $commit = (Get-Content $commitFile -Raw).Trim()
    if ($commit) {
        Write-Host "== 2/3 checkout patch baseline $commit ==" -ForegroundColor Cyan
        git -C $vendor fetch --depth 1 origin $commit 2>$null
        git -C $vendor checkout $commit
    }
}

Write-Host "== 3/3 apply patches ==" -ForegroundColor Cyan
git -C $vendor apply $patchFile

Write-Host "Done. Next steps:" -ForegroundColor Green
Write-Host "  python -m pip install -r requirements.txt"
Write-Host "  python run_app.py"
