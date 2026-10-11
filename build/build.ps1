# 打包脚本：在 PowerShell 里执行 .\build\build.ps1
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$vpy = Join-Path $root ".venv\Scripts\python.exe"
Write-Host "== 1/4 安装/确认 PyInstaller ==" -ForegroundColor Cyan
& $vpy -m pip install --quiet --upgrade pyinstaller
Write-Host "== 2/4 打包 ==" -ForegroundColor Cyan
$pyArgs = @("-m", "PyInstaller", "--noconfirm", "--clean",
  "--distpath", (Join-Path $root "dist"),
  "--workpath", (Join-Path $root "build\_work"),
  (Join-Path $root "build\app.spec"))
& $vpy @pyArgs
$appDir = Join-Path $root "dist\证件照换底"
if (-not (Test-Path $appDir)) { throw "打包失败：找不到 $appDir" }
Write-Host "== 3/4 放模型 + VC++ 运行库 ==" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path (Join-Path $appDir "models") | Out-Null
Copy-Item (Join-Path $root "models\*.onnx") (Join-Path $appDir "models") -Force
foreach ($dll in @("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")) {
  $src = Join-Path $env:SystemRoot "System32\$dll"
  if (Test-Path $src) { Copy-Item $src $appDir -Force }
}
Write-Host "== 4/5 瘦身：删掉用不到的大文件 ==" -ForegroundColor Cyan
# 只处理静态图片，用不到视频解码（opencv 的 ffmpeg 后端有 27MB）
Get-ChildItem $appDir -Recurse -Filter "opencv_videoio_ffmpeg*.dll" | ForEach-Object { Write-Host ("  删 " + $_.Name); Remove-Item $_.FullName -Force }
# 用不到 AVIF 图片格式（7.5MB）
Get-ChildItem $appDir -Recurse -Filter "_avif*.pyd" | ForEach-Object { Write-Host ("  删 " + $_.Name); Remove-Item $_.FullName -Force }
# tkdnd 只留标准版，Tcl9 版用不上
$tcl9 = Join-Path $appDir "_internal\tkinterdnd2\tkdnd\win-x64-tcl9"
if (Test-Path $tcl9) { Remove-Item $tcl9 -Recurse -Force; Write-Host "  删 tkdnd/win-x64-tcl9" }
# 注意：顶层那三个 VC++ 运行库 DLL 与 _internal 里的确实重复（约 0.8MB），
# 但 exe 同目录是 Windows 加载 DLL 时最优先搜索的位置，对「没装 VC++ 运行库的电脑」
# 是最稳的一道保险。0.8MB 不值得拿兼容性去换，所以这里刻意保留。

Write-Host "== 5/5 打包成 zip ==" -ForegroundColor Cyan
$zip = Join-Path $root "dist\证件照换底.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path "$appDir\*" -DestinationPath $zip -CompressionLevel Optimal
$sizeMB = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host "完成：$zip（$sizeMB MB）" -ForegroundColor Green