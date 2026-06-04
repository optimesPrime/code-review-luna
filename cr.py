from __future__ import annotations
import json
import sys
from datetime import datetime
from pathlib import Path

import click

from config import load_config
from diff_reader import get_diff, redact, DiffError
from skill_loader import load_skills
from confirmer import ask
import phases.blast_radius as blast
import phases.code_quality as quality
from test_importer import parse_test_file, find_related_tests
from reporter import ReviewReport, save, render


@click.group()
def cli():
    pass


@cli.command()
@click.option("--staged", is_flag=True, help="只审查已 git add 的内容")
@click.option("--since", default=None, help="审查相对某个 ref 的改动，如 main")
@click.option("--tests", default=None, help="测试文件或目录路径")
@click.option("--phase", default=None, type=click.Choice(["blast", "quality"]))
@click.option("--apply", "apply_mode", is_flag=True, help="开启可写入模式，仍需逐条确认")
@click.option("--output", default=None, help="自定义报告输出路径")
@click.option("--format", "fmt", default="markdown",
              type=click.Choice(["markdown", "json"]))
@click.option("--config", "config_path", default="config.yaml")
def run(staged, since, tests, phase, apply_mode, output, fmt, config_path):
    """对当前 git 改动执行 AI 代码审查"""
    cfg = load_config(config_path)
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
        blast_items = blast.analyze(diff, skill_context, cfg)
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


def main():
    cli()


if __name__ == "__main__":
    main()
