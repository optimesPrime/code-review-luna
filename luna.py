from __future__ import annotations
import json
import sys
from datetime import datetime
from pathlib import Path

import click
import yaml

from config import load_config
from diff_reader import get_diff, redact, DiffError
from skill_loader import load_skills
from confirmer import ask
import phases.blast_radius as blast
import phases.code_quality as quality
from test_importer import parse_test_file, find_related_tests
from reporter import ReviewReport, save
from phases.symbol_locator import extract_changed_symbols_from_diff
from phases.context_graph import build_graph, load_graph, save_graph
from phases.risk_propagation import propagate_risk
from phases.context_pack import build_context_pack


DEFAULT_CONFIG = Path.home() / ".luna" / "config.yaml"

PROVIDERS = {
    "claude": {
        "provider": "anthropic",
        "models": ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
        "default_model": "claude-sonnet-4-6",
        "api_key_env": "ANTHROPIC_API_KEY",
    },
    "gpt": {
        "provider": "openai",
        "models": ["gpt-5.5", "gpt-4o", "gpt-4o-mini"],
        "default_model": "gpt-5.5",
        "api_key_env": "OPENAI_API_KEY",
    },
}


@click.group(invoke_without_command=True)
@click.pass_context
@click.option("--staged", is_flag=True, help="只审查已 git add 的内容")
@click.option("--since", default=None, help="审查相对某个 ref 的改动，如 main")
@click.option("--tests", default=None, help="测试文件或目录路径")
@click.option("--phase", default=None, type=click.Choice(["blast", "quality"]))
@click.option("--apply", "apply_mode", is_flag=True, help="开启可写入模式，仍需逐条确认")
@click.option("--output", default=None, help="自定义报告输出路径")
@click.option("--format", "fmt", default="markdown", type=click.Choice(["markdown", "json"]))
@click.option("--config", "config_path", default=None, help="配置文件路径，默认 ~/.luna/config.yaml")
def cli(ctx, staged, since, tests, phase, apply_mode, output, fmt, config_path):
    """Luna — AI 代码审查工具

    直接运行 `luna` 即可审查当前 git 改动。
    使用 `luna switch` 切换 AI 提供商。
    """
    if ctx.invoked_subcommand is not None:
        return

    cfg = load_config(str(config_path or DEFAULT_CONFIG))
    if apply_mode:
        cfg.review.apply_enabled = True

    try:
        diff = get_diff(staged=staged, since=since)
    except DiffError as e:
        click.echo(f"错误: {e}", err=True)
        sys.exit(1)

    if not diff.strip():
        click.echo("无改动，退出审查。")
        return

    if len(diff) > cfg.review.max_diff_chars:
        click.echo(
            f"diff 过大（{len(diff)} 字符），超过限制 {cfg.review.max_diff_chars}。\n"
            "建议使用 --staged 或 --since 缩小范围。",
            err=True,
        )
        sys.exit(1)

    diff = redact(diff, cfg.privacy.redact_patterns)

    skill_context, skill_errors = load_skills(cfg.skills)
    for err in skill_errors:
        click.echo(f"[Skill 加载失败] {err.name}: {err.reason}", err=True)

    test_cases = []
    if tests:
        tp = Path(tests)
        if tp.is_file():
            test_cases = parse_test_file(str(tp))
        elif tp.is_dir():
            for f in list(tp.rglob("*.spec.*")) + list(tp.rglob("*.test.*")):
                test_cases.extend(parse_test_file(str(f)))

    related_tests = find_related_tests(test_cases, diff)

    report = ReviewReport(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
        diff_summary=f"共 {diff.count(chr(10))} 行改动",
        skill_errors=skill_errors,
        related_tests=related_tests,
    )

    if phase in (None, "blast"):
        click.echo("\n[阶段1] 爆炸范围分析中...\n")

        cache_path = Path(".luna") / "cache" / "context-graph.json"
        graph = load_graph(str(cache_path))
        if graph is None:
            click.echo("  构建代码关系图...", err=True)
            graph = build_graph(".")
            save_graph(graph, str(cache_path))

        symbols = extract_changed_symbols_from_diff(diff, project_root=".")
        impact_paths = propagate_risk(symbols, graph)
        context_pack = build_context_pack(
            symbols,
            impact_paths,
            related_rules=[],
            related_tests=[f"{r.describe}: {r.it}" for r in related_tests],
        )

        blast_items = blast.analyze(diff, skill_context, cfg, context_pack=context_pack)
        report.blast_radius_items = blast_items

        for item in sorted(blast_items, key=lambda x: {"high": 0, "medium": 1, "low": 2}[x.risk]):
            note = " [需人工确认]" if item.needs_human_review else ""
            click.echo(f"[爆炸范围·{item.risk}] {item.file}:{item.line} — {item.reason}{note}")
            if item.suggestion and ask("  查看修复建议？"):
                click.echo(f"  建议: {item.suggestion}")
                if cfg.review.apply_enabled:
                    if ask("  应用此修改？"):
                        report.applied_fixes.append(f"blast:{item.file}:{item.line}")
                        click.echo("  [已记录，请按建议手动应用]")
                    else:
                        report.skipped_items.append(f"blast:{item.file}:{item.line}")
                else:
                    if ask("  生成 patch 供人工复制？"):
                        click.echo(f"\n--- patch ---\n{item.suggestion}\n--- end ---\n")

    if phase in (None, "quality"):
        click.echo("\n[阶段2] 代码质量审查中...\n")
        quality_items = quality.analyze(diff, skill_context, cfg)
        report.code_quality_items = quality_items

        for item in quality_items:
            click.echo(f"[代码质量·{item.risk}] {item.file}:{item.line} — {item.description}")
            click.echo(f"  依据: {item.evidence}")
            if item.suggestion and ask("  查看修复建议？"):
                click.echo(f"  建议: {item.suggestion}")
                if cfg.review.apply_enabled:
                    if ask("  应用此修改？"):
                        report.applied_fixes.append(f"quality:{item.file}:{item.line}")
                    else:
                        report.skipped_items.append(f"quality:{item.file}:{item.line}")

    if fmt == "json":
        click.echo(json.dumps({
            "blast_radius": [vars(i) for i in report.blast_radius_items],
            "code_quality": [vars(i) for i in report.code_quality_items],
        }, ensure_ascii=False, indent=2))
        return

    out_dir = output or cfg.reports.output_dir
    path = save(report, out_dir)
    click.echo(f"\n报告已保存：{path}")

    high_count = sum(1 for i in report.blast_radius_items if i.risk == "high")
    if high_count:
        click.echo(f"注意：发现 {high_count} 处高风险爆炸范围，请重点复审。")


@cli.command()
@click.option("--config", "config_path", default=None, help="配置文件路径，默认 ~/.luna/config.yaml")
def switch(config_path):
    """切换 AI 提供商（claude / gpt）"""
    cfg_path = Path(config_path) if config_path else DEFAULT_CONFIG
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}

    current_provider = raw.get("api", {}).get("provider", "anthropic")
    current_model = raw.get("api", {}).get("model", "")
    current_base_url = raw.get("api", {}).get("base_url", "")
    click.echo(f"当前配置：provider={current_provider}  model={current_model}")
    click.echo("")

    provider_choice = click.prompt(
        "选择 provider",
        type=click.Choice(["claude", "gpt"]),
        default="claude" if current_provider == "anthropic" else "gpt",
    )
    info = PROVIDERS[provider_choice]

    model_list = "  /  ".join(info["models"])
    click.echo(f"可用模型：{model_list}")
    model = click.prompt("选择模型", default=info["default_model"])

    base_url = click.prompt(
        "中转地址（无需代理可直接回车留空）",
        default=current_base_url if current_provider == info["provider"] else "",
    )

    if "api" not in raw:
        raw["api"] = {}
    raw["api"]["provider"] = info["provider"]
    raw["api"]["model"] = model
    raw["api"]["api_key_env"] = info["api_key_env"]
    raw["api"]["base_url"] = base_url

    cfg_path.write_text(yaml.dump(raw, allow_unicode=True, default_flow_style=False), encoding="utf-8")

    click.echo("")
    click.echo(f"已切换：{provider_choice}  ({model})")
    if base_url:
        click.echo(f"中转地址：{base_url}")
    click.echo(f"API Key 环境变量：{info['api_key_env']}")


def main():
    cli()


if __name__ == "__main__":
    main()
