# 仅对本次安装进程临时放开执行策略，不修改系统/用户级设置
Set-ExecutionPolicy Bypass -Scope Process -Force
$ErrorActionPreference = 'Stop'

function Ok   { param($msg) Write-Host "v $msg" -ForegroundColor Green }
function Warn  { param($msg) Write-Host "! $msg" -ForegroundColor Yellow }
function Fail  { param($msg) Write-Host "x $msg" -ForegroundColor Red; exit 1 }
function Info  { param($msg) Write-Host "> $msg" -ForegroundColor Cyan }

Write-Host ""
Write-Host "Luna Code Review" -ForegroundColor White -NoNewline
Write-Host " — 安装程序"
Write-Host "────────────────────────────────────"

# ── 1. 检查 Python 3.11+ ──────────────────────────────────────
$PYTHON = $null
foreach ($cmd in @("python", "python3")) {
    try {
        $ver = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($ver) {
            $parts = $ver -split '\.'
            if ([int]$parts[0] -gt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 11)) {
                $PYTHON = $cmd
                Ok "Python $ver"
                break
            }
        }
    } catch {}
}
if (-not $PYTHON) { Fail "需要 Python 3.11 或更高版本。请先安装：https://python.org" }

# ── 2. 检查/安装 pipx ─────────────────────────────────────────
$pipxOk = $false
try {
    $pipxVer = (pipx --version 2>$null)
    if ($pipxVer) { Ok "pipx $pipxVer"; $pipxOk = $true }
} catch {}

if (-not $pipxOk) {
    Warn "未找到 pipx，正在安装..."
    & $PYTHON -m pip install --user pipx
    & $PYTHON -m pipx ensurepath
    # 从注册表刷新当前进程的 PATH
    $userPath    = [System.Environment]::GetEnvironmentVariable("PATH", "User")
    $machinePath = [System.Environment]::GetEnvironmentVariable("PATH", "Machine")
    $pipxBin     = Join-Path $HOME ".local\bin"
    $env:PATH = "$userPath;$machinePath;$pipxBin"
    try {
        $pipxVer = (pipx --version 2>$null)
        if ($pipxVer) { Ok "pipx 安装完成" } else { Fail "pipx 安装失败，请手动执行：pip install pipx" }
    } catch { Fail "pipx 安装失败，请手动执行：pip install pipx" }
}

# ── 3. 安装 Luna ──────────────────────────────────────────────
$REPO = "https://github.com/optimesPrime/code-review-luna.git"
$pipxList = try { (& pipx list 2>&1) -join "`n" } catch { "" }
if ($pipxList -match "package luna") {
    Info "检测到已安装 Luna，正在升级..."
    pipx upgrade luna
    if ($LASTEXITCODE -ne 0) {
        pipx install --force "git+$REPO"
        if ($LASTEXITCODE -ne 0) { Fail "Luna 安装失败，请检查网络或 GitHub 访问" }
    }
} else {
    Info "正在从 GitHub 安装 Luna..."
    pipx install "git+$REPO"
    if ($LASTEXITCODE -ne 0) { Fail "Luna 安装失败，请检查网络或 GitHub 访问" }
}
Ok "Luna 安装完成"

# ── 4. 初始化配置文件 ─────────────────────────────────────────
$ConfigDir  = Join-Path $HOME ".luna"
$ConfigFile = Join-Path $ConfigDir "config.yaml"
$RawExample = "https://raw.githubusercontent.com/optimesPrime/code-review-luna/main/config.example.yaml"

if (-not (Test-Path $ConfigDir)) { New-Item -ItemType Directory -Path $ConfigDir | Out-Null }

if (Test-Path $ConfigFile) {
    Warn "配置文件已存在，跳过：$ConfigFile"
} else {
    Info "下载默认配置..."
    try {
        Invoke-WebRequest -Uri $RawExample -OutFile $ConfigFile -UseBasicParsing
    } catch {
        Fail "下载配置文件失败，请检查网络或手动下载：$RawExample"
    }
    Ok "配置文件已创建：$ConfigFile"
}

# ── 5. 完成提示 ───────────────────────────────────────────────
Write-Host ""
Write-Host "安装完成！" -ForegroundColor Green -NoNewline
Write-Host " 下一步："
Write-Host ""
Write-Host "  1. 配置 API Key（交互式引导）："
Write-Host "     luna switch"
Write-Host ""
Write-Host "  2. 在任意代码仓库中运行审查："
Write-Host "     luna"
Write-Host ""
Write-Host "  也可以直接用环境变量传入 Key，无需配置："
Write-Host '     $env:ANTHROPIC_API_KEY="sk-xxx"; luna'
Write-Host ""

$lunaOk = $false
try { Get-Command luna -ErrorAction Stop | Out-Null; $lunaOk = $true } catch {}
if (-not $lunaOk) {
    Warn "luna 命令暂不可用，请重启 PowerShell 后再试。"
    Write-Host ""
}
