"""
业务爆炸地图 demo — 直接运行查看效果:
    python3 ~/luna/blast_map_demo.py
"""
from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text
from rich.rule import Rule
from rich.align import Align
from rich.table import Table
from rich.padding import Padding

console = Console()

# ── 模拟数据 ──────────────────────────────────────────────────────────────────
changed       = "tradeUserId"
changed_files = "request.ts  ·  user.ts  ·  Login.vue  ·  router.ts"

high = [
    ("请求上下文",   "X-Trade-UserId 首个请求头可能缺失，下单/撤单接口将继承错误账号上下文", "src/api/request.ts:42"),
    ("交易账号隔离", "不同账号的订单数据存在串读风险，切换账号后旧账号数据可能残留",       "src/api/tradeApi.ts:87"),
]
medium = [
    ("状态同步",   "store 更新时机影响账号上下文，登录成功初始化时机需确认",    "src/store/user.ts:28"),
    ("异常处理",   "loading 状态未恢复，用户无法再次点击登录按钮",              "src/views/Login.vue:74"),
    ("权限/登录态", "权限验证可能使用旧 userId，账号切换后上下文可能残留",      "src/store/user.ts:28"),
    ("组件通信",   "Header 账号下拉框与 userStore 的绑定可能在初始化前触发",   "src/components/Header.vue:33"),
]
low = [
    ("页面跳转", "账号切换后必须刷新并回首页，当前 router.push 可能不够",       "src/router/index.ts:61"),
    ("测试覆盖", "缺少请求头存在性断言，缺少账号切换后回首页回归测试",          "tests/api.spec.ts:-"),
]

# ── 条目行构建 ────────────────────────────────────────────────────────────────
def item_rows(items, icon, name_style):
    """每条风险渲染成一行：icon 名称 — 说明  (证据)"""
    rows = []
    for name, reason, evidence in items:
        t = Text()
        t.append(f"  {icon} ", style=name_style)
        t.append(name, style=f"bold {name_style}")
        t.append("  —  ")
        t.append(reason)
        t.append(f"  ({evidence})", style="dim")
        rows.append(t)
    return rows

# ── 内层：改动入口 ─────────────────────────────────────────────────────────────
center_text = Text(justify="center")
center_text.append(f"💥  {changed}", style="bold cyan")
center_text.append(f"\n{changed_files}", style="dim")
center_panel = Panel(
    Align.center(center_text),
    border_style="cyan",
    padding=(1, 6),
    subtitle="[dim cyan]改动入口[/dim cyan]",
)

# ── 第一圈：高风险（直接影响） ─────────────────────────────────────────────────
high_rows = item_rows(high, "🚨", "red")
high_inner = Group(
    center_panel,
    Text(""),
    *high_rows,
    Text(""),
)
high_panel = Panel(
    high_inner,
    title="[bold red]🚨  高风险  —  直接影响[/bold red]",
    border_style="red",
    padding=(0, 2),
    subtitle=f"[dim red]{len(high)} 处[/dim red]",
)

# ── 第二圈：中风险（间接影响） ─────────────────────────────────────────────────
medium_rows = item_rows(medium, "⚠️", "yellow")
medium_inner = Group(
    high_panel,
    Text(""),
    *medium_rows,
    Text(""),
)
medium_panel = Panel(
    medium_inner,
    title="[bold yellow]⚠️   中风险  —  间接影响[/bold yellow]",
    border_style="yellow",
    padding=(0, 2),
    subtitle=f"[dim yellow]{len(medium)} 处[/dim yellow]",
)

# ── 第三圈：低风险（远端影响） ─────────────────────────────────────────────────
low_rows = item_rows(low, "💡", "blue")
low_inner = Group(
    medium_panel,
    Text(""),
    *low_rows,
    Text(""),
)
low_panel = Panel(
    low_inner,
    title="[bold blue]💡  低风险  —  远端影响[/bold blue]",
    border_style="blue",
    padding=(0, 2),
    subtitle=f"[dim blue]{len(low)} 处[/dim blue]",
)

# ── 输出 ──────────────────────────────────────────────────────────────────────
console.print()
console.print(Rule("[bold cyan]💥  业务爆炸地图[/bold cyan]", style="cyan"))
console.print()
console.print(low_panel)
console.print()

total = len(high) + len(medium) + len(low)
console.print(Rule(
    f"[dim]共 {total} 个业务域受波及  ·  "
    f"[red]🚨 高 {len(high)}[/red]  "
    f"[yellow]⚠️  中 {len(medium)}[/yellow]  "
    f"[blue]💡 低 {len(low)}[/blue][/dim]",
    style="dim",
))
console.print()
