#!/usr/bin/env bash
set -e

BOLD='\033[1m'
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}!${NC} $*"; }
fail() { echo -e "${RED}✗${NC} $*"; exit 1; }
info() { echo -e "${BLUE}→${NC} $*"; }

echo ""
echo -e "${BOLD}Luna Code Review${NC} — 安装程序"
echo "────────────────────────────────────"

# ── 1. 检查 Python 3.11+ ──────────────────────────────────────
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        _maj=$("$cmd" -c "import sys; print(sys.version_info.major)" 2>/dev/null)
        _min=$("$cmd" -c "import sys; print(sys.version_info.minor)" 2>/dev/null)
        if [ "$_maj" -ge 3 ] && [ "$_min" -ge 11 ]; then
            PYTHON="$cmd"
            ok "Python $("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")"
            break
        fi
    fi
done
[ -z "$PYTHON" ] && fail "需要 Python 3.11 或更高版本。请先安装：https://python.org"

# ── 2. 检查/安装 pipx ─────────────────────────────────────────
if command -v pipx &>/dev/null; then
    ok "pipx $(pipx --version)"
else
    warn "未找到 pipx，正在安装..."
    if command -v brew &>/dev/null; then
        brew install pipx
    else
        "$PYTHON" -m pip install --user pipx
    fi
    "$PYTHON" -m pipx ensurepath
    export PATH="$HOME/.local/bin:$PATH"
    command -v pipx &>/dev/null || fail "pipx 安装失败，请手动执行：pip install pipx"
    ok "pipx 安装完成"
fi

# ── 3. 安装 Luna ──────────────────────────────────────────────
REPO="https://github.com/optimesPrime/code-review-luna.git"

if pipx list 2>/dev/null | grep -q "package luna"; then
    info "检测到已安装 Luna，正在升级..."
    pipx upgrade luna 2>/dev/null || pipx install --force "git+$REPO"
else
    info "正在从 GitHub 安装 Luna..."
    pipx install "git+$REPO"
fi
ok "Luna 安装完成"

# ── 4. 初始化配置文件 ─────────────────────────────────────────
CONFIG_DIR="$HOME/.luna"
CONFIG_FILE="$CONFIG_DIR/config.yaml"
RAW_EXAMPLE="https://raw.githubusercontent.com/optimesPrime/code-review-luna/main/config.example.yaml"

mkdir -p "$CONFIG_DIR"

if [ -f "$CONFIG_FILE" ]; then
    warn "配置文件已存在，跳过：$CONFIG_FILE"
else
    info "下载默认配置..."
    if command -v curl &>/dev/null; then
        curl -sSL "$RAW_EXAMPLE" -o "$CONFIG_FILE"
    elif command -v wget &>/dev/null; then
        wget -qO "$CONFIG_FILE" "$RAW_EXAMPLE"
    else
        fail "需要 curl 或 wget 来下载配置文件"
    fi
    ok "配置文件已创建：$CONFIG_FILE"
fi

# ── 5. 完成提示 ───────────────────────────────────────────────
echo ""
echo -e "${BOLD}安装完成！${NC} 下一步："
echo ""
echo -e "  1. 编辑配置，填入你的 API Key："
echo -e "     ${BOLD}${CONFIG_FILE}${NC}"
echo ""
echo -e "  2. 在任意代码仓库中运行审查："
echo -e "     ${BOLD}luna review${NC}"
echo ""
echo -e "  也可以直接用环境变量传入 Key，无需改配置："
echo -e "     ${BOLD}ANTHROPIC_API_KEY=sk-xxx luna review${NC}"
echo ""

if ! command -v luna &>/dev/null; then
    warn "luna 命令暂不可用，请重启终端后再试。"
    warn "或手动执行：source ~/.bashrc  /  source ~/.zshrc"
    echo ""
fi
