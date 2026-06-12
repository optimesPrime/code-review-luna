import textwrap
from config import load_config


def test_load_config_parses_domains(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(textwrap.dedent("""\
        review:
          max_parallel_domains: 2
        domains:
          - name: "私募基金"
            patterns:
              - "src/private*"
          - name: "公募基金"
            patterns:
              - "src/public*"
    """))
    cfg = load_config(str(cfg_file))
    assert len(cfg.domains) == 2
    assert cfg.domains[0].name == "私募基金"
    assert "src/private*" in cfg.domains[0].patterns
    assert cfg.review.max_parallel_domains == 2


def test_load_config_empty_domains_by_default():
    cfg = load_config("nonexistent.yaml")
    assert cfg.domains == []
    assert cfg.review.max_parallel_domains == 3
