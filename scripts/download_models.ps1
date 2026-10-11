# Download the model weights this tool needs into models/.
# 下载本工具需要的模型权重到 models/ 目录。
#
# Usage / 用法:  powershell -ExecutionPolicy Bypass -File scripts\download_models.ps1
#
# The weights are NOT stored in this git repository (they are ~114 MB together and
# each has its own license). They are published as GitHub Release assets instead,
# so cloning the code stays fast.
# 权重不放在 git 仓库里（合计约 114MB，且各有各的许可证），而是作为 Release 附件发布，
# 这样 clone 代码依然很快。

$ErrorActionPreference = "Continue"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$dir = Join-Path $root "models"
New-Item -ItemType Directory -Force -Path $dir | Out-Null

$tag = "v1.0.0"
$base = "https://github.com/Cheng1933/id-photo-bg/releases/download/$tag"

$files = @(
    @{ name = "birefnet-v1-lite.onnx"; mb = 89; note = "high-quality tier (BiRefNet, int8, MIT)" },
    @{ name = "hivision_modnet.onnx";  mb = 25; note = "fast tier (MODNet, Apache-2.0)" }
)

# GitHub 的大文件下载在国内经常被重置，所以失败时自动换镜像重试。
# GitHub release downloads are frequently reset from mainland China, so we retry
# through mirrors. The mirror simply prepends its own prefix to the original URL.
$prefixes = @("", "https://ghfast.top/", "https://gh-proxy.com/", "https://ghproxy.net/")

foreach ($f in $files) {
    $out = Join-Path $dir $f.name
    if ((Test-Path $out) -and ((Get-Item $out).Length -gt 1MB)) {
        Write-Host "[skip] $($f.name) already exists / 已存在" -ForegroundColor Yellow
        continue
    }
    $ok = $false
    foreach ($p in $prefixes) {
        Write-Host "[get ] $($f.name)  (~$($f.mb) MB, $($f.note))" -ForegroundColor Cyan
        if ($p -ne "") { Write-Host "       via mirror $p" }
        & curl.exe -L --fail --connect-timeout 20 -m 1800 -o $out "$p$base/$($f.name)" 2>$null
        if ($LASTEXITCODE -eq 0 -and (Test-Path $out) -and ((Get-Item $out).Length -gt 1MB)) {
            $ok = $true
            break
        }
        if (Test-Path $out) { Remove-Item $out -Force -ErrorAction SilentlyContinue }
    }
    if ($ok) {
        Write-Host ("       OK: {0:N1} MB" -f ((Get-Item $out).Length / 1MB)) -ForegroundColor Green
    } else {
        Write-Host "       失败。请手动下载后放进 models/ ：$base/$($f.name)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "Now run:  python run_app.py" -ForegroundColor Green
Write-Host "然后运行：python run_app.py"
