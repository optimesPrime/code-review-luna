from __future__ import annotations

_SPARK = " ▁▂▃▄▅▆▇█"
_RISK_STYLE = {"high": "bold red", "medium": "bold yellow", "low": "bold blue"}


def _spark_char(value: float, max_val: float) -> str:
    if max_val == 0:
        return _SPARK[0]
    idx = min(int(value / max_val * (len(_SPARK) - 1)), len(_SPARK) - 1)
    return _SPARK[idx]


def _sparkline(values: list[int]) -> str:
    if not values:
        return ""
    max_v = max(values) or 1
    return "".join(_spark_char(v, max_v) for v in values)


def _verdict_style(verdict: str) -> str:
    if "阻塞" in verdict:
        return "bold red"
    if "修复" in verdict:
        return "bold yellow"
    return "bold green"


def render_overview(reports: list[dict], console=None) -> None:
    try:
        from rich.console import Console
        from rich.table import Table
        from rich import box as rich_box
        from rich.text import Text
    except ImportError:
        _render_overview_plain(reports)
        return

    if console is None:
        console = Console(stderr=True)

    if not reports:
        console.print("[dim]还没有审查记录。先运行 [bold]luna --staged[/bold] 生成第一份报告。[/dim]")
        return

    tbl = Table(
        show_header=True, header_style="bold",
        box=rich_box.ROUNDED, padding=(0, 1),
    )
    tbl.add_column("日期", style="dim", min_width=16)
    tbl.add_column("提交", style="cyan", min_width=8)
    tbl.add_column("结论", min_width=12)
    tbl.add_column("🚨", justify="right", min_width=4)
    tbl.add_column("⚠️",  justify="right", min_width=4)
    tbl.add_column("💡", justify="right", min_width=4)
    tbl.add_column("耗时", justify="right", min_width=6)

    for r in reports:
        verdict = r.get("verdict", "—")
        tbl.add_row(
            r.get("timestamp", ""),
            r.get("commit", "—")[:8],
            Text(verdict, style=_verdict_style(verdict)),
            str(r.get("high", 0)),
            str(r.get("medium", 0)),
            str(r.get("low", 0)),
            f"{r.get('elapsed', 0):.1f}s",
        )

    console.print(tbl)


def render_hotspots(hotspots: list[dict], console=None) -> None:
    try:
        from rich.console import Console
        from rich.table import Table
        from rich import box as rich_box
        from rich.text import Text
    except ImportError:
        for h in hotspots:
            print(f"{h['file']}  {h['count']}次  {h['max_risk']}")
        return

    if console is None:
        console = Console(stderr=True)

    if not hotspots:
        console.print("[dim]暂无高频风险文件数据。[/dim]")
        return

    tbl = Table(
        show_header=True, header_style="bold",
        box=rich_box.ROUNDED, padding=(0, 1),
    )
    tbl.add_column("文件", min_width=30)
    tbl.add_column("出现次数", justify="right", min_width=8)
    tbl.add_column("最高风险", min_width=8)
    tbl.add_column("最近标记", style="dim", min_width=16)

    for h in hotspots:
        risk = h.get("max_risk", "low")
        tbl.add_row(
            h["file"],
            str(h["count"]),
            Text(risk, style=_RISK_STYLE.get(risk, "")),
            h.get("last_seen", ""),
        )

    console.print(tbl)


def render_trend(trend: dict, console=None) -> None:
    try:
        from rich.console import Console
        from rich.text import Text
    except ImportError:
        for level, vals in trend.items():
            print(f"{level}: {_sparkline(vals)}")
        return

    if console is None:
        console = Console(stderr=True)

    console.print("\n[bold]风险趋势[/bold]  (旧 → 新)\n")
    labels = [("高风险", "high", "red"), ("中风险", "medium", "yellow"), ("低风险", "low", "blue")]
    for label, key, color in labels:
        vals = trend.get(key, [])
        spark = _sparkline(vals)
        line = Text()
        line.append(f"  {label}  ", style="bold")
        line.append(spark, style=color)
        console.print(line)
    console.print()


def _render_overview_plain(reports: list[dict]) -> None:
    if not reports:
        print("还没有审查记录。先运行 luna --staged 生成第一份报告。")
        return
    for r in reports:
        print(f"{r.get('timestamp','')}  {r.get('commit','')[:8]}  "
              f"🚨{r.get('high',0)} ⚠️{r.get('medium',0)} 💡{r.get('low',0)}")
