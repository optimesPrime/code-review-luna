from __future__ import annotations
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import click
import yaml

from config import load_config
from runtime_context import RuntimeContext
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
from phases.surprise_analyzer import (
    find_surprising_edges,
    find_untested_hotspots,
    find_bridge_nodes_in_impact,
    generate_review_questions,
)
from phases.backend_graph_engine import (
    find_symbols_from_diff as _engine_find_symbols,
    build_graph as _engine_build_graph,
    save_graph as _engine_save_graph,
    load_graph as _engine_load_graph,
)
from phases.backend_risk_propagation import propagate_backend_risk
from phases.backend_context_pack import build_backend_context_pack
from phases.backend_adapter_registry import should_run_backend_review, get_adapter
import phases.backend_review as backend_review


DEFAULT_CONFIG = Path.home() / ".luna" / "config.yaml"

_NON_CHAT_KEYWORDS = (
    "embedding", "whisper", "tts-", "dall-e", "moderation",
    "babbage", "davinci-002", "ada-", "curie-",
)

_CLAUDE_MODELS = [
    ("claude-haiku-4-5-20251001", "低档 · 快速"),
    ("claude-sonnet-4-6",         "中档 · 均衡"),
    ("claude-opus-4-8",           "高档 · 强力"),
]


def _fetch_models(base_url: str, api_key: str) -> list[str]:
    from openai import OpenAI
    kwargs: dict = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    try:
        client = OpenAI(**kwargs)
        all_models = [m.id for m in client.models.list().data]
        chat_models = [
            m for m in all_models
            if not any(kw in m.lower() for kw in _NON_CHAT_KEYWORDS)
        ]
        return sorted(chat_models)
    except Exception:
        return []


def _pick_model_interactive(models: list[str]) -> str:
    """展示编号列表，返回用户选择的模型名。"""
    for i, m in enumerate(models, 1):
        click.echo(f"  {i:>3}. {m}")
    click.echo("")
    while True:
        choice = click.prompt("输入编号").strip()
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(models):
                return models[idx]
        except ValueError:
            pass
        click.echo("  请输入列表中的数字")


def _analyze_diff(diff: str, cfg) -> "ReviewReport":
    """Blast radius + adversarial verify + quality on an arbitrary diff string."""
    import datetime
    from reporter import ReviewReport
    from phases import blast_radius as _blast, code_quality as _quality
    from phases.context_pack import build_context_pack
    from phases.symbol_locator import extract_changed_symbols_from_diff

    report = ReviewReport(
        timestamp=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        diff_summary=diff[:200],
    )

    context_pack = None
    try:
        symbols = extract_changed_symbols_from_diff(diff)
        context_pack = build_context_pack(symbols, [], related_rules=[], related_tests=[])
        report.changed_symbols = [{"name": s.symbol, "file": s.file} for s in symbols]
    except Exception:
        pass

    blast_items = []
    try:
        blast_items, _ = _blast.analyze(diff, "", cfg, context_pack=context_pack, project_root=".", detail_level="standard")
    except Exception:
        pass

    if context_pack and blast_items:
        try:
            from phases.adversarial_verifier import adversarial_verify, build_adversarial_context
            uncertain = [i for i in blast_items if i.risk == "high" and i.confidence != "high"]
            certain   = [i for i in blast_items if not (i.risk == "high" and i.confidence != "high")]
            if uncertain:
                files = {i.file for i in uncertain}
                ctx = build_adversarial_context(diff, files, context_pack)
                uncertain, refuted = adversarial_verify(uncertain, ctx, cfg)
                report.adversarial_refuted = refuted
            blast_items = certain + uncertain
        except Exception:
            pass

    report.blast_radius_items = blast_items

    try:
        quality_items, _ = _quality.analyze(diff, "", cfg, project_root=".", detail_level="standard")
        report.code_quality_items = quality_items
    except Exception:
        pass

    return report


_FRONTEND_EXTS = {".js", ".ts", ".jsx", ".tsx", ".vue", ".mjs", ".cjs"}


def _has_frontend_files(diff: str) -> bool:
    import re
    exts = set(re.findall(r" b/[^ \n]+(\.[A-Za-z0-9]+)", diff))
    return bool(exts & _FRONTEND_EXTS)


def _should_run_frontend_pipeline(diff: str, cfg) -> bool:
    if cfg.review.project_type == "auto":
        return _has_frontend_files(diff)
    return cfg.review.project_type in ("frontend", "fullstack")


def _should_run_backend_review(diff: str, cfg) -> bool:
    if not cfg.backend.enabled:
        return False
    if cfg.review.project_type == "auto":
        from phases.backend_adapter_registry import detect_backend_languages_from_diff, is_frontend_only_diff
        if is_frontend_only_diff(diff):
            return False
        enabled = {l.lower() for l in cfg.backend.languages}
        return bool(set(detect_backend_languages_from_diff(diff)) & enabled)
    return should_run_backend_review(
        diff,
        project_type=cfg.review.project_type,
        languages=cfg.backend.languages,
    )


@click.group(invoke_without_command=True)
@click.pass_context
@click.option("--staged", is_flag=True, help="只审查已 git add 的内容")
@click.option("--since", default=None, help="审查相对某个 ref 的改动，如 main")
@click.option("--tests", default=None, help="测试文件或目录路径")
@click.option("--phase", default=None, type=click.Choice(["blast", "quality"]))
@click.option("--apply", "apply_mode", is_flag=True, help="开启可写入模式，仍需逐条确认")
@click.option("--interactive", "interactive", is_flag=True, help="逐条确认修复建议（默认跳过）")
@click.option("--type", "project_type", default=None, type=click.Choice(["frontend", "backend", "fullstack"]), help="项目类型，覆盖自动检测")
@click.option("--output", default=None, help="自定义报告输出路径")
@click.option("--format", "fmt", default="markdown", type=click.Choice(["markdown", "json"]))
@click.option("--config", "config_path", default=None, help="配置文件路径，默认 ~/.luna/config.yaml")
@click.option("--quiet", "quiet", is_flag=True, help="只输出摘要，不展开详情")
@click.option("--details", "details", is_flag=True, help="详细模式：传完整 diff 给 LLM（token 消耗更多）")
def cli(ctx, staged, since, tests, phase, apply_mode, interactive, project_type, output, fmt, config_path, quiet, details):
    """Luna — AI 代码审查工具

    直接运行 `luna` 即可审查当前 git 改动。
    使用 `luna switch` 切换 AI 提供商。
    """
    start_time = time.time()
    try:
        from rich.console import Console as _RCon
        _rcon = _RCon(stderr=True)
    except ImportError:
        _rcon = None
    if ctx.invoked_subcommand is not None:
        return

    cfg = load_config(str(config_path or DEFAULT_CONFIG))
    detail_level = "minimal" if quiet else ("verbose" if details else "standard")
    if project_type:
        cfg.review.project_type = project_type
    if apply_mode and not interactive:
        click.echo("错误：--apply 仅在 --interactive 模式下有效，或使用 luna fix 命令。", err=True)
        sys.exit(1)
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

    # ── 进度显示 ──────────────────────────────────────────────────────────────
    _run_backend = phase in (None, "blast") and _should_run_backend_review(diff, cfg)
    _run_frontend = phase in (None, "blast") and _should_run_frontend_pipeline(diff, cfg)
    _run_quality = phase in (None, "quality")

    _phase_list = []
    if _run_backend:
        _phase_list += [("backend_graph", "构建后端代码图谱"), ("backend_review", "后端专项审查")]
    if _run_frontend:
        _phase_list += [("frontend_graph", "构建前端代码图谱"), ("hybrid_ctx", "混合语义检索"), ("blast", "爆炸范围分析"), ("adversarial", "反驳验证")]
    elif phase in (None, "blast"):
        _phase_list += [("blast", "爆炸范围分析"), ("adversarial", "反驳验证")]
    if _run_quality:
        _phase_list += [("quality", "代码质量检查")]
    if cfg.migration.enabled:
        _phase_list += [("migration", "数据库迁移审查")]
    if cfg.api_change.enabled:
        _phase_list += [("api_change", "API 契约检查")]

    _prog = None
    _task_ids: dict = {}
    if _rcon and not quiet and _phase_list:
        try:
            from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
            _prog = Progress(
                SpinnerColumn(finished_text="[green]✓[/green]"),
                TextColumn("{task.description}"),
                TimeElapsedColumn(),
                console=_rcon,
                transient=False,
            )
            for key, label in _phase_list:
                tid = _prog.add_task(f"[dim]{label}[/dim]", total=1, start=False)
                _task_ids[key] = (tid, label)
            _prog.start()
        except Exception:
            _prog = None

    def _begin(key: str) -> None:
        if _prog and key in _task_ids:
            tid, label = _task_ids[key]
            _prog.start_task(tid)
            _prog.update(tid, description=f"[cyan]{label}[/cyan]")

    def _finish(key: str) -> None:
        if _prog and key in _task_ids:
            tid, label = _task_ids[key]
            _prog.update(tid, completed=1, description=f"[bold green]{label}[/bold green]")

    if phase in (None, "blast") and _run_backend:
        from phases.backend_adapter_registry import detect_backend_languages_from_diff as _detect
        detected_langs = _detect(diff)
        enabled = {l.lower() for l in cfg.backend.languages}
        backend_symbols = []
        backend_edges = []

        _begin("backend_graph")
        for lang in detected_langs:
            if lang not in enabled:
                continue
            try:
                adapter = get_adapter(lang)
            except ValueError:
                continue

            backend_cache = Path(".luna") / "cache" / f"{lang}-graph.json"
            graph = _engine_load_graph(str(backend_cache))
            if graph is None:
                graph = _engine_build_graph(adapter, project_root=".")
                _engine_save_graph(graph, str(backend_cache))

            lang_symbols = _engine_find_symbols(diff, adapter, project_root=".")
            backend_symbols.extend(lang_symbols)
            backend_edges.extend(graph.edges)

        if backend_symbols:
            from phases.backend_models import BackendContextGraph as _BCG
            combined_graph = _BCG()
            for e in backend_edges:
                combined_graph.add_edge(e)

            backend_paths = propagate_backend_risk(
                backend_symbols, combined_graph, max_depth=cfg.backend.max_depth
            )
            backend_pack = build_backend_context_pack(backend_symbols, backend_edges, backend_paths)
            _finish("backend_graph")
            _begin("backend_review")
            backend_items, _backend_savings = backend_review.analyze_backend(
                backend_pack, diff, skill_context, cfg, detail_level=detail_level
            )
            _finish("backend_review")
            report.backend_review_items = backend_items
            report.token_savings["backend"] = _backend_savings

            if interactive:
                for item in sorted(backend_items, key=lambda x: {"high": 0, "medium": 1, "low": 2}[x.risk]):
                    note = " [需人工确认]" if item.needs_human_review else ""
                    click.echo(f"[后端·{item.risk}] {item.file}:{item.line} — {item.reason}{note}")
                    click.echo(f"  证据: {item.evidence}")
                    if item.suggestion and ask("  查看修复建议？"):
                        click.echo(f"  建议: {item.suggestion}")

    if phase in (None, "blast"):
        if _run_frontend:
            _begin("frontend_graph")
            cache_path = Path(".luna") / "cache" / "context-graph.json"
            graph = load_graph(str(cache_path))
            if graph is None:
                graph = build_graph(".")
                save_graph(graph, str(cache_path))
            _finish("frontend_graph")

            symbols = extract_changed_symbols_from_diff(diff, project_root=".")
            impact_paths = propagate_risk(symbols, graph)
            context_pack = build_context_pack(
                symbols,
                impact_paths,
                related_rules=[],
                related_tests=[f"{r.describe}: {r.it}" for r in related_tests],
            )
            report.changed_symbols = [vars(s) if hasattr(s, '__dict__') else str(s) for s in symbols]
            report.impact_paths = [vars(p) if hasattr(p, '__dict__') else str(p) for p in impact_paths]

            # 注入历史问题记录，让 LLM 知道哪些文件是"慢性病"
            try:
                from phases.history_reader import load_reports, get_file_history
                _changed_files = [s.file for s in symbols]
                _hist_reports = load_reports(cfg.reports.output_dir, limit=30)
                if _hist_reports and _changed_files:
                    context_pack.file_history = get_file_history(_hist_reports, _changed_files)
            except Exception:
                pass

            # Surprise scoring + review question generation
            from collections import Counter as _Counter
            import os as _os
            _file_degree: dict[str, int] = _Counter()
            for _e in graph.edges:
                _file_degree[_e.source] += 1
                _file_degree[_e.target] += 1
            _graph_ctx = {
                _f: {
                    "community": _os.path.dirname(_f),
                    "language": _f.rsplit(".", 1)[-1] if "." in _f else "",
                    "degree": _file_degree.get(_f, 0),
                    "is_test": "test" in _f.lower(),
                }
                for _f in _file_degree
            }
            _path_lists = [p.path for p in impact_paths]
            _sym_dicts = [
                {
                    "symbol": s.symbol,
                    "degree": _file_degree.get(s.file, 0),
                    "is_test": "test" in s.file.lower(),
                }
                for s in symbols
            ]
            _surprise_edges = find_surprising_edges(_path_lists, _graph_ctx)
            _hotspots = find_untested_hotspots(
                _sym_dicts,
                [f"{r.describe}: {r.it}" for r in related_tests],
            )
            _bridges = find_bridge_nodes_in_impact(_path_lists)
            _questions = generate_review_questions(_surprise_edges, _hotspots, _bridges)
            context_pack.review_questions = _questions
            report.review_questions = _questions

            _begin("hybrid_ctx")
            try:
                from phases.sqlite_graph import GraphDB
                from phases.hybrid_search import augment_impact_paths
                _db_path = str(Path(".luna") / "cache" / "context-graph.db")
                if Path(_db_path).exists():
                    _hdb = GraphDB(_db_path)
                    try:
                        context_pack.impact_paths = augment_impact_paths(
                            context_pack.impact_paths, _hdb, symbols,
                            project_root=".",
                        )
                    finally:
                        _hdb.close()
            except Exception as _hybrid_err:
                if _rcon:
                    _rcon.print(f"[dim yellow]⚠ hybrid_ctx 降级：{_hybrid_err}[/dim yellow]")
            finally:
                _finish("hybrid_ctx")

            try:
                from phases.caller_context import build_caller_contexts as _build_cc
                context_pack.caller_contexts = _build_cc(
                    symbols, ".", cfg.privacy.ignore
                )
            except Exception:
                pass
        else:
            context_pack = None

        _begin("blast")
        blast_items, _blast_savings = blast.analyze(
            diff, skill_context, cfg,
            context_pack=context_pack,
            project_root=".",
            detail_level=detail_level,
        )
        _finish("blast")

        if context_pack is not None and blast_items:
            _begin("adversarial")
            try:
                from phases.adversarial_verifier import (
                    adversarial_verify as _adv_verify,
                    build_adversarial_context as _build_ctx,
                )
                _uncertain = [i for i in blast_items if i.risk == "high" and i.confidence != "high"]
                _certain = [i for i in blast_items if not (i.risk == "high" and i.confidence != "high")]
                if _uncertain:
                    _files = {i.file for i in _uncertain}
                    _ctx = _build_ctx(diff, _files, context_pack)
                    _uncertain, _refuted = _adv_verify(_uncertain, _ctx, cfg)
                    report.adversarial_refuted = _refuted
                blast_items = _certain + _uncertain
            except Exception as _adv_err:
                if _rcon:
                    _rcon.print(f"[dim yellow]⚠ adversarial_verify 降级：{_adv_err}[/dim yellow]")
            finally:
                _finish("adversarial")

        report.blast_radius_items = blast_items
        report.token_savings["blast"] = _blast_savings

        if interactive:
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
        _begin("quality")
        _quality_syms = symbols if "symbols" in dir() else None
        quality_items, _quality_savings = quality.analyze(
            diff, skill_context, cfg,
            symbols=_quality_syms,
            project_root=".",
            detail_level=detail_level,
        )
        _finish("quality")
        report.code_quality_items = quality_items
        report.token_savings["quality"] = _quality_savings

        if interactive:
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

    # ── 数据库迁移审查 ────────────────────────────────────────────────────────
    if cfg.migration.enabled:
        from phases.migration_analyzer import analyze as _migration_analyze
        _begin("migration")
        report.migration_items = _migration_analyze(diff, ".")
        _finish("migration")

    # ── API 契约检查 ──────────────────────────────────────────────────────────
    if cfg.api_change.enabled:
        from phases.api_change_detector import analyze as _api_analyze
        _begin("api_change")
        report.api_change_items = _api_analyze(diff, ".")
        _finish("api_change")

    if _prog:
        _prog.stop()

    if fmt == "json":
        import dataclasses as _dc
        from terminal_renderer import build_fix_queue as _build_fq_json
        click.echo(json.dumps({
            "blast_radius": [vars(i) for i in report.blast_radius_items],
            "code_quality": [vars(i) for i in report.code_quality_items],
            "fix_candidates": [_dc.asdict(fc) for fc in _build_fq_json(report)],
        }, ensure_ascii=False, indent=2))
        return

    import re as _re
    _file_matches = _re.findall(r"^diff --git a/\S+", diff, _re.MULTILINE)
    _line_count = sum(1 for l in diff.splitlines() if l.startswith(("+", "-")) and not l.startswith(("+++", "---")))

    _commit_hash = ""
    try:
        import subprocess as _sp
        _commit_hash = _sp.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip()
    except Exception:
        pass

    runtime = RuntimeContext(
        project_name=Path(".").resolve().name,
        project_root=str(Path(".").resolve()),
        project_type=project_type or (
            "frontend" if _should_run_frontend_pipeline(diff, cfg)
            else ("backend" if report.backend_review_items else "auto")
        ),
        diff_scope="staged" if staged else (f"since {since}" if since else "working tree"),
        changed_files=len(_file_matches),
        changed_lines=_line_count,
        backend_review_status="ran" if report.backend_review_items else "skipped",
        elapsed_seconds=round(time.time() - start_time, 1),
        report_path="",
        commit_hash=_commit_hash,
    )

    out_dir = output or cfg.reports.output_dir
    from terminal_renderer import render_review, build_fix_queue as _build_fq
    report.fix_candidates = _build_fq(report)
    path = save(report, out_dir, runtime_ctx=runtime)
    runtime.report_path = str(path)
    render_review(report, runtime, fmt=fmt, quiet=quiet)


@cli.command("gitlab")
@click.option("--config", "config_path", default=None, help="配置文件路径，默认 ~/.luna/config.yaml")
def gitlab_cmd(config_path):
    """配置 GitLab 地址、Token 和项目，一次设置永久生效。"""
    import os as _os
    cfg_path = Path(config_path) if config_path else DEFAULT_CONFIG
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}

    gl = raw.get("gitlab", {})
    cur_url     = gl.get("url", "https://gitlab.com")
    cur_token   = gl.get("token", "")
    cur_project = gl.get("project_id", "")
    cur_min     = gl.get("min_risk", "medium")

    click.echo("\n🦊 Luna GitLab 配置\n")

    url = click.prompt(
        "GitLab 地址",
        default=cur_url,
    ).strip()

    token_hint  = f"{cur_token[:8]}..." if len(cur_token) > 8 else ""
    token_label = f"Token（当前：{token_hint}，直接回车保留）" if token_hint else "Token（Personal Access Token）"
    new_token   = click.prompt(token_label, default="", show_default=False).strip()
    token       = new_token or cur_token
    if not token:
        click.echo("❌ Token 不能为空，已取消")
        return

    project_id = click.prompt(
        "项目路径或数字 ID",
        default=cur_project,
        show_default=bool(cur_project),
    ).strip()
    if not project_id:
        click.echo("❌ 项目 ID 不能为空，已取消")
        return

    min_risk = click.prompt(
        "最低发评论风险等级",
        type=click.Choice(["high", "medium", "low"]),
        default=cur_min,
    )

    raw["gitlab"] = {
        "url": url,
        "token": token,
        "project_id": project_id,
        "min_risk": min_risk,
        "post_inline": gl.get("post_inline", True),
        "bot_note_prefix": gl.get("bot_note_prefix", "🌙 Luna Review"),
        "token_env": "GITLAB_TOKEN",
    }

    tmp = cfg_path.with_suffix(".tmp")
    tmp.write_text(yaml.dump(raw, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    _os.replace(tmp, cfg_path)

    click.echo(f"\n✅ 已保存")
    click.echo(f"   地址：{url}")
    click.echo(f"   项目：{project_id}")
    click.echo(f"   最低风险：{min_risk}")


@cli.command("review")
@click.option("--mr", "mr_iid", type=int, required=True, help="GitLab MR IID")
@click.option("--project", default=None, help="覆盖配置中的 project_id（格式：group/repo）")
@click.option("--dry-run", is_flag=True, help="只审查，不发评论")
@click.option("--config", "config_path", default=None, help="配置文件路径，默认 ~/.luna/config.yaml")
def review_cmd(mr_iid, project, dry_run, config_path):
    """从 GitLab MR 拉取 diff 并发布审查评论。"""
    import os as _os
    cfg = load_config(str(config_path or DEFAULT_CONFIG))

    token = cfg.gitlab.token.strip() or _os.environ.get(cfg.gitlab.token_env, "")
    if not token:
        click.echo(f"❌ 未配置 GitLab Token，请运行 luna gitlab 或设置环境变量 {cfg.gitlab.token_env}", err=True)
        raise SystemExit(1)

    project_id = project or cfg.gitlab.project_id
    if not project_id:
        click.echo("❌ 未配置 gitlab.project_id，请在 config.yaml 设置或用 --project", err=True)
        raise SystemExit(1)

    from phases.gitlab_client import GitLabClient
    from phases.mr_reviewer import map_items_to_positions, build_summary_comment, build_inline_comment

    client = GitLabClient(cfg.gitlab.url, token, project_id)

    click.echo(f"拉取 MR !{mr_iid} diff...", err=True)
    try:
        meta      = client.get_mr_meta(mr_iid)
        diff      = client.get_mr_diff(mr_iid)
        diff_refs = meta.get("diff_refs") or {}
    except ValueError as e:
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(1)

    click.echo("运行审查...", err=True)
    report = _analyze_diff(diff, cfg)

    from terminal_renderer import render_review, build_fix_queue as _bfq
    from runtime_context import RuntimeContext
    runtime = RuntimeContext()
    runtime.project_name = project_id
    runtime.diff_scope   = f"MR !{mr_iid}"
    report.fix_candidates = _bfq(report)
    render_review(report, runtime, quiet=dry_run)

    if dry_run:
        click.echo("\n(dry-run：不发评论)", err=True)
        return

    summary = build_summary_comment(report, prefix=cfg.gitlab.bot_note_prefix)
    try:
        client.post_summary_comment(mr_iid, summary)
        click.echo("✅ 总结评论已发布", err=True)
    except ValueError as e:
        click.echo(f"⚠️ 总结评论发布失败：{e}", err=True)

    if cfg.gitlab.post_inline:
        _risk_val = {"high": 2, "medium": 1, "low": 0}
        _min      = _risk_val.get(cfg.gitlab.min_risk, 1)
        all_items = list(report.blast_radius_items) + list(report.code_quality_items)
        eligible  = [i for i in all_items if _risk_val.get(i.risk, 0) >= _min]
        mapped    = map_items_to_positions(eligible, diff_refs)
        posted    = 0
        for item, refs in mapped:
            body = build_inline_comment(item, prefix=cfg.gitlab.bot_note_prefix)
            try:
                client.post_inline_comment(mr_iid, body, item.file, item.line, refs)
                posted += 1
            except ValueError:
                pass
        click.echo(f"✅ 已发布 {posted} 条 inline 评论", err=True)


@cli.command()
@click.option("--config", "config_path", default=None, help="配置文件路径，默认 ~/.luna/config.yaml")
def switch(config_path):
    """配置 AI 接入方式、API Key 和模型。"""
    import os as _os
    cfg_path = Path(config_path) if config_path else DEFAULT_CONFIG
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}

    api_raw = raw.get("api", {})
    current_provider = api_raw.get("provider", "")
    current_base_url = api_raw.get("base_url", "")
    current_key = api_raw.get("key", "")
    current_model = api_raw.get("model", "")

    # 显示当前状态
    if current_base_url:
        current_desc = f"中转站 {current_base_url}"
    elif current_provider == "anthropic":
        current_desc = "直连 Claude"
    else:
        current_desc = "直连 OpenAI"
    click.echo(f"\n当前配置：{current_desc}  model={current_model}\n")

    # 选择接入方式
    click.echo("接入方式：")
    click.echo("  1. 直连 Claude（Anthropic 官方）")
    click.echo("  2. 直连 OpenAI（OpenAI 官方）")
    click.echo("  3. 中转站（OpenAI 兼容接口）")
    click.echo("")
    while True:
        mode = click.prompt("输入编号").strip()
        if mode in ("1", "2", "3"):
            break
        click.echo("  请输入 1、2 或 3")

    # 询问 Key
    key_hint = f"{current_key[:8]}..." if len(current_key) > 8 else ""
    key_label = f"API Key（当前：{key_hint}，直接回车保留）" if key_hint else "API Key"
    new_key = click.prompt(key_label, default="", show_default=False).strip()
    api_key = new_key or current_key
    if not api_key:
        click.echo("❌ 未填写 API Key，已取消")
        return

    # 模式特定逻辑
    if mode == "1":
        # 直连 Claude：静态模型列表
        provider = "anthropic"
        base_url = ""
        click.echo("")
        for i, (m, label) in enumerate(_CLAUDE_MODELS, 1):
            click.echo(f"  {i}. {m}  （{label}）")
        click.echo("")
        model = _pick_model_interactive([m for m, _ in _CLAUDE_MODELS])

    elif mode == "2":
        # 直连 OpenAI：动态拉取
        provider = "openai"
        base_url = ""
        click.echo("\n正在拉取模型列表...", nl=False)
        models = _fetch_models("", api_key)
        if not models:
            click.echo(" 失败（检查 Key）")
            model = click.prompt("手动输入模型名称", default=current_model).strip()
        else:
            click.echo(f" {len(models)} 个模型\n")
            model = _pick_model_interactive(models)

    else:
        # 中转站
        provider = "proxy"
        base_url = click.prompt(
            "中转站地址",
            default=current_base_url,
            show_default=bool(current_base_url),
        ).strip()
        if not base_url:
            click.echo("❌ 中转站地址不能为空，已取消")
            return
        click.echo("\n正在拉取模型列表...", nl=False)
        models = _fetch_models(base_url, api_key)
        if not models:
            click.echo(" 失败（检查地址和 Key）")
            model = click.prompt("手动输入模型名称", default=current_model).strip()
        else:
            click.echo(f" {len(models)} 个模型\n")
            model = _pick_model_interactive(models)

    # 写入配置
    if "api" not in raw:
        raw["api"] = {}
    raw["api"]["provider"] = provider
    raw["api"]["base_url"] = base_url
    raw["api"]["key"] = api_key
    raw["api"]["model"] = model

    tmp = cfg_path.with_suffix(".tmp")
    tmp.write_text(yaml.dump(raw, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    _os.replace(tmp, cfg_path)

    click.echo(f"\n✅ 已保存  model={model}")
    if base_url:
        click.echo(f"   中转站：{base_url}")


@cli.command("detail")
@click.argument("detail_id", type=int)
@click.option("--reports-dir", default=None, help="报告目录（默认读配置）")
@click.option("--config", "config_path", default=None)
def detail_cmd(detail_id, reports_dir, config_path):
    """查看审查发现 #N 的完整详情：文件位置、证据、修复建议。"""
    from luna_fix import load_latest_report

    try:
        cfg = load_config(str(config_path or DEFAULT_CONFIG))
    except Exception:
        cfg = None
    rdir = reports_dir or (cfg.reports.output_dir if cfg else ".luna-reports")

    candidates = load_latest_report(rdir)
    if candidates is None:
        click.echo("未找到审查报告，请先运行 luna 生成报告。", err=True)
        raise SystemExit(1)

    candidate = next((c for c in candidates if c.id == detail_id), None)
    if candidate is None:
        click.echo(f"未找到发现项 #{detail_id}，运行 luna 查看列表。", err=True)
        raise SystemExit(1)

    _MODE_LABEL = {"auto": "🤖 可自动修复", "assist": "🔧 辅助修复", "manual": "👤 需人工处理"}
    _RISK_STYLE = {"high": "bold red", "medium": "bold yellow", "low": "cyan"}

    try:
        from rich.console import Console as _Con
        from rich.rule import Rule as _Rule
        from rich.text import Text as _Text
        from rich.padding import Padding as _Pad
        _con = _Con()
    except ImportError:
        _con = None

    if _con:
        _con.print()
        _con.print(_Rule(f"[bold]#{detail_id}  {candidate.title[:70]}[/bold]", style="cyan"))
        _con.print()

        parts = candidate.file.rsplit("/", 1)
        fdir  = parts[0] + "/" if len(parts) == 2 else ""
        fname = parts[-1]
        loc = _Text()
        loc.append("  文件  ", style="dim")
        if fdir:
            loc.append(fdir, style="dim")
        loc.append(fname, style="bold")
        loc.append(f"  L{candidate.line}", style="dim")
        _con.print(loc)

        mode_line = _Text()
        mode_line.append("  类型  ", style="dim")
        mode_line.append(_MODE_LABEL.get(candidate.mode, candidate.mode))
        _con.print(mode_line)

        if candidate.impact:
            imp = _Text()
            imp.append("  优先级  ", style="dim")
            imp.append(candidate.impact, style=_RISK_STYLE.get(candidate.impact.lower(), ""))
            _con.print(imp)

        _con.print()

        if candidate.evidence:
            _con.print(_Text("  证据", style="bold"))
            _con.print(_Pad(_Text(candidate.evidence, style="dim"), (0, 4)))
            _con.print()

        if candidate.suggestion:
            _con.print(_Text("  建议", style="bold"))
            _con.print(_Pad(_Text(candidate.suggestion), (0, 4)))
            _con.print()

        if candidate.mode != "manual":
            _con.print(_Pad(_Text(f"$ {candidate.command_hint}", style="bold green"), (0, 2)))

        _con.print()
    else:
        click.echo(f"\n#{detail_id}  {candidate.title}")
        click.echo(f"文件：{candidate.file}  L{candidate.line}")
        click.echo(f"类型：{_MODE_LABEL.get(candidate.mode, candidate.mode)}")
        if candidate.evidence:
            click.echo(f"\n证据：\n{candidate.evidence}")
        if candidate.suggestion:
            click.echo(f"\n建议：\n{candidate.suggestion}")


@cli.command("fix")
@click.argument("fix_id", type=int)
@click.option("--preview", is_flag=True, help="只展示 diff，不写入文件")
@click.option("--reports-dir", default=None, help="报告目录（默认读配置）")
@click.option("--config", "config_path", default=None)
def fix_cmd(fix_id, preview, reports_dir, config_path):
    """应用修复队列中的第 N 条建议。"""
    from luna_fix import load_latest_report, generate_fix, apply_patch

    try:
        cfg = load_config(str(config_path or DEFAULT_CONFIG))
    except Exception:
        cfg = None
    rdir = reports_dir or (cfg.reports.output_dir if cfg else ".luna-reports")

    candidates = load_latest_report(rdir)
    if candidates is None:
        click.echo("未找到审查报告。请先运行 luna --staged 生成报告。", err=True)
        raise SystemExit(1)

    candidate = next((c for c in candidates if c.id == fix_id), None)
    if candidate is None:
        click.echo(f"未找到修复项 #{fix_id}。运行 luna 查看修复队列。", err=True)
        raise SystemExit(1)

    if candidate.mode == "manual":
        click.echo(f"👤 #{fix_id} 需人工处理 — {candidate.title}")
        click.echo(f"   证据：{candidate.evidence}")
        click.echo(f"   建议：{candidate.suggestion}")
        return

    try:
        source = (Path(".") / candidate.file).read_text(encoding="utf-8")
    except OSError:
        click.echo(f"无法读取文件：{candidate.file}", err=True)
        raise SystemExit(1)

    patch, raw = generate_fix(candidate, source, cfg)

    if not patch:
        if raw:
            click.echo(f"\n💡 LLM 建议（未能生成可直接应用的 diff）：\n")
            click.echo(raw)
        else:
            click.echo("LLM 调用失败，请检查网络或 API 配置。", err=True)
        return

    # Show diff preview
    try:
        from rich.syntax import Syntax
        from rich.console import Console
        Console(stderr=True).print(Syntax(patch, "diff", theme="monokai"))
    except Exception:
        click.echo(patch)

    if preview:
        return

    if ask("应用此修改？", default=False):
        if apply_patch(patch, "."):
            click.echo(f"✅ 已写入 {candidate.file}")
        else:
            click.echo("❌ 应用失败，请手动处理。", err=True)
            raise SystemExit(1)


@cli.command("static")
@click.option("--staged", is_flag=True, help="只检查已 git add 的内容")
@click.option("--since", default=None, help="检查相对某个 ref 的改动，如 main")
@click.option("--config", "config_path", default=None, help="配置文件路径")
@click.option("--format", "fmt", default="text", type=click.Choice(["text", "json"]), help="输出格式")
def static_cmd(staged, since, config_path, fmt):
    """静态规则检查（不调用 LLM）：数据库迁移风险 + API 契约破坏性变更。

    适合在 CI 流水线中作为快速卡门，几乎瞬间完成。
    发现 high 风险时退出码为 1。
    """
    from phases.migration_analyzer import analyze as migration_analyze
    from phases.api_change_detector import analyze as api_analyze

    cfg = load_config(str(config_path or DEFAULT_CONFIG))

    try:
        diff = get_diff(staged=staged, since=since)
    except DiffError as e:
        click.echo(f"错误: {e}", err=True)
        raise SystemExit(1)

    if not diff.strip():
        click.echo("无改动，跳过静态检查。")
        return

    diff = redact(diff, cfg.privacy.redact_patterns)

    migration_items = migration_analyze(diff, ".") if cfg.migration.enabled else []
    api_items = api_analyze(diff, ".") if cfg.api_change.enabled else []

    all_items = migration_items + api_items
    has_high = any(i.risk == "high" for i in all_items)

    if fmt == "json":
        import dataclasses as _dc
        click.echo(json.dumps({
            "migration": [_dc.asdict(i) for i in migration_items],
            "api_change": [_dc.asdict(i) for i in api_items],
            "has_high_risk": has_high,
        }, ensure_ascii=False, indent=2))
        if has_high:
            raise SystemExit(1)
        return

    # ── Rich 终端输出 ──────────────────────────────────────────────────────────
    try:
        from rich.console import Console
        from rich.table import Table
        from rich import box as rich_box
        from rich.rule import Rule
        console = Console()
        _rich = True
    except ImportError:
        _rich = False

    _RISK_ICON = {"high": "🚨", "medium": "⚠️", "low": "💡"}
    _RISK_STYLE = {"high": "bold red", "medium": "bold yellow", "low": "bold blue"}

    if not all_items:
        if _rich:
            console.print()
            console.print(Rule("[bold cyan]🌙  Luna Static Check[/bold cyan]", style="cyan"))
            console.print()
            console.print("  [bold green]✓[/bold green]  无数据库迁移风险，无 API 契约破坏性变更")
            console.print()
        else:
            click.echo("✓ 无数据库迁移风险，无 API 契约破坏性变更")
        return

    if _rich:
        console.print()
        console.print(Rule("[bold cyan]🌙  Luna Static Check[/bold cyan]", style="cyan"))
        console.print()

        tbl = Table(
            show_header=True,
            header_style="bold",
            box=rich_box.ROUNDED,
            padding=(0, 1),
            border_style="dim",
            show_lines=True,
            expand=True,
        )
        tbl.add_column("", min_width=2, no_wrap=True, justify="center")
        tbl.add_column("类型", min_width=8, no_wrap=True)
        tbl.add_column("操作", min_width=16, no_wrap=True)
        tbl.add_column("风险说明", min_width=28, ratio=4)
        tbl.add_column("位置", min_width=18, style="dim", no_wrap=True)
        tbl.add_column("建议", min_width=14, ratio=2)

        from rich.text import Text
        for item in sorted(all_items, key=lambda i: {"high": 0, "medium": 1, "low": 2}[i.risk]):
            category = "数据库迁移" if hasattr(item, "operation") else "API 契约"
            op = getattr(item, "operation", None) or getattr(item, "change_type", "")
            tbl.add_row(
                Text(_RISK_ICON.get(item.risk, ""), style=_RISK_STYLE.get(item.risk, "")),
                Text(category, style="dim"),
                Text(op, style=_RISK_STYLE.get(item.risk, "")),
                item.reason,
                f"{item.file}:{item.line}",
                item.suggestion or "-",
            )

        console.print(tbl)
        console.print()

        if has_high:
            console.print("  [bold red]发现 high 风险项，建议修复后再提交。[/bold red]")
        else:
            console.print("  [bold yellow]发现风险项，请评估影响后再提交。[/bold yellow]")
        console.print()
    else:
        for item in sorted(all_items, key=lambda i: {"high": 0, "medium": 1, "low": 2}[i.risk]):
            icon = _RISK_ICON.get(item.risk, "")
            click.echo(f"{icon} [{item.risk}] {item.file}:{item.line}  {item.reason}")
            if item.suggestion:
                click.echo(f"   建议: {item.suggestion}")

    if has_high:
        raise SystemExit(1)


@cli.command("install-hook")
@click.option(
    "--hook", "hook_type",
    default="pre-commit",
    type=click.Choice(["pre-commit", "pre-push"]),
    help="要安装的 hook 类型（默认 pre-commit）",
)
@click.option("--config", "config_path", default=None, help="传给 luna static 的配置文件路径")
def install_hook_cmd(hook_type, config_path):
    """安装 Luna 静态检查作为 git hook（默认关闭，此命令开启）。

    安装后每次 git commit（或 push）前自动运行 luna static，
    发现 high 风险时拦截提交。不调用 LLM，< 1 秒完成。

    兼容 SourceTree / VS Code 等 GUI 工具（使用绝对路径调用 luna）。

    跳过检查：git commit --no-verify
    """
    from hook_installer import install, is_managed

    if is_managed(hook_type=hook_type):
        click.echo(f"✅ {hook_type} hook 已安装（无需重复安装）")
        click.echo("   如需重新安装，先运行：luna uninstall-hook")
        return

    result = install(
        hook_type=hook_type,
        config_path=config_path or "",
        git_root=".",
    )

    if result:
        click.echo(f"✅ 已安装 {hook_type} hook")
        click.echo("   每次提交前自动运行 luna static（纯静态，不调 LLM）")
        click.echo("   发现 high 风险时拦截提交，low/medium 静默放行")
        click.echo("   跳过检查：git commit --no-verify")
        click.echo("   卸载：luna uninstall-hook")
    else:
        click.echo(f"⚠️  {hook_type} hook 已存在（非 Luna 安装），未覆盖", err=True)
        click.echo("   请手动整合，或先备份后删除 .git/hooks/" + hook_type, err=True)
        raise SystemExit(1)


@cli.command("uninstall-hook")
@click.option(
    "--hook", "hook_type",
    default="pre-commit",
    type=click.Choice(["pre-commit", "pre-push"]),
    help="要卸载的 hook 类型（默认 pre-commit）",
)
def uninstall_hook_cmd(hook_type):
    """卸载 Luna 安装的 git hook。只删除由 luna install-hook 创建的文件。"""
    from hook_installer import uninstall

    result = uninstall(hook_type=hook_type, git_root=".")
    if result:
        click.echo(f"✅ 已卸载 {hook_type} hook")
    else:
        click.echo(f"ℹ️  未找到 Luna 管理的 {hook_type} hook，无需卸载")


@cli.command("history")
@click.option("-n", "limit", default=10, help="展示最近 N 次审查（默认 10）")
@click.option("--file", "filter_file", default=None, help="只展示含指定文件的审查记录")
@click.option("--trend", "show_trend", is_flag=True, help="展示风险趋势 sparkline")
@click.option("--hotspots", "show_hotspots", is_flag=True, help="展示高频风险文件 Top 10")
@click.option("--reports-dir", default=None, help="报告目录（默认读配置）")
@click.option("--config", "config_path", default=None, help="配置文件路径，默认 ~/.luna/config.yaml")
def history_cmd(limit, filter_file, show_trend, show_hotspots, reports_dir, config_path):
    """查看历史审查记录、风险趋势和高频问题文件。"""
    cfg = load_config(str(config_path or DEFAULT_CONFIG))
    out_dir = reports_dir or cfg.reports.output_dir

    from phases.history_reader import load_reports, aggregate_hotspots, build_trend
    from history_renderer import render_overview, render_hotspots, render_trend

    reports = load_reports(out_dir, limit=limit)

    if filter_file:
        reports = [
            r for r in reports
            if any(filter_file in item.get("file", "") for item in r.get("items", []))
        ]

    if show_hotspots:
        all_reports = load_reports(out_dir, limit=30)
        render_hotspots(aggregate_hotspots(all_reports))
    elif show_trend:
        all_reports = load_reports(out_dir, limit=30)
        render_trend(build_trend(all_reports))
    else:
        render_overview(reports)


def main():
    cli()


if __name__ == "__main__":
    main()
