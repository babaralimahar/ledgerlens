from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from app.adapters.llm import FakeLLMClient, LLMTransient
from app.evals import __main__ as evals_cli
from app.evals.dataset import (
    DatasetError,
    DatasetIntegrityError,
    load_dataset,
    write_sums,
)
from app.evals.heldout import HeldOutAlreadyRun, check_heldout, record_heldout
from app.evals.metrics import REGISTRY
from app.evals.report import write_report
from app.evals.runner import run_suite, select_items
from app.evals.suites import ToyUppercaseSuite, fake_uppercase_responder

REPO_TOY = Path("evals/datasets/toy")


def make_dataset(tmp_path: Path, n: int = 10, name: str = "dev") -> Path:
    d = tmp_path / "datasets" / "toy"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.jsonl"
    p.write_text(
        "".join(
            json.dumps({"id": f"i{i}", "input": f"w{i}", "expected": f"W{i}"}) + "\n"
            for i in range(n)
        ),
        encoding="utf-8",
    )
    return p


def toy(cost: str = "0.0001") -> ToyUppercaseSuite:
    return ToyUppercaseSuite(FakeLLMClient(responder=fake_uppercase_responder(Decimal(cost))))


# ------------------------------------------------------------------ datasets


def test_repo_toy_sets_are_frozen_and_valid() -> None:
    dev = load_dataset(REPO_TOY / "dev.jsonl", require_frozen=True)
    test = load_dataset(REPO_TOY / "test.jsonl", require_frozen=True)
    assert len(dev.items) == 10 and len(test.items) == 5


def test_tampered_frozen_set_is_rejected(tmp_path: Path) -> None:
    p = make_dataset(tmp_path)
    write_sums(p.parent)
    p.write_text(
        p.read_text(encoding="utf-8") + '{"id": "x", "input": "a", "expected": "A"}\n',
        encoding="utf-8",
    )
    with pytest.raises(DatasetIntegrityError, match="changed since it was frozen"):
        load_dataset(p, require_frozen=False)


def test_unfrozen_test_set_is_rejected(tmp_path: Path) -> None:
    p = make_dataset(tmp_path, name="test")
    with pytest.raises(DatasetIntegrityError, match="not listed"):
        load_dataset(p, require_frozen=True)


def test_duplicate_ids_rejected(tmp_path: Path) -> None:
    p = tmp_path / "dup.jsonl"
    p.write_text(
        '{"id":"a","input":1,"expected":1}\n{"id":"a","input":2,"expected":2}\n', encoding="utf-8"
    )
    with pytest.raises(DatasetError, match="duplicate"):
        load_dataset(p, require_frozen=False)


def test_bad_line_reports_line_number(tmp_path: Path) -> None:
    p = tmp_path / "bad.jsonl"
    p.write_text('{"id":"a","input":1,"expected":1}\n{not json}\n', encoding="utf-8")
    with pytest.raises(DatasetError, match=r"bad.jsonl:2"):
        load_dataset(p, require_frozen=False)


# ------------------------------------------------------------------ runner


async def test_runner_scores_all_items(tmp_path: Path) -> None:
    ds = load_dataset(make_dataset(tmp_path), require_frozen=False)
    report = await run_suite(toy(), ds, concurrency=3)
    assert report.n_completed == 10 and report.stopped_reason is None
    assert report.summary(ToyUppercaseSuite.metrics) == {
        "exact_match": 1.0,
        "normalized_match": 1.0,
    }
    assert report.total_cost_usd == Decimal("0.0010")


async def test_max_cost_stops_the_run(tmp_path: Path) -> None:
    ds = load_dataset(make_dataset(tmp_path), require_frozen=False)
    report = await run_suite(toy(cost="0.004"), ds, concurrency=1, max_cost=Decimal("0.01"))
    assert report.stopped_reason == "max_cost"
    assert report.n_completed == 3  # 0.004, 0.008, 0.012 -> no 4th item starts
    assert report.total_cost_usd == Decimal("0.012")


async def test_item_errors_count_as_zero_not_skipped(tmp_path: Path) -> None:
    ds = load_dataset(make_dataset(tmp_path, n=2), require_frozen=False)
    llm = FakeLLMClient(
        script=[LLMTransient("1"), LLMTransient("2")],
        responder=fake_uppercase_responder(Decimal(0)),
    )
    report = await run_suite(ToyUppercaseSuite(llm, max_attempts=2), ds, concurrency=1)
    assert report.n_errors == 1
    assert report.summary(("exact_match",)) == {"exact_match": 0.5}


def test_sampling_is_deterministic_and_ordered(tmp_path: Path) -> None:
    items = load_dataset(make_dataset(tmp_path), require_frozen=False).items
    a = select_items(items, 4, seed=7)
    assert a == select_items(items, 4, seed=7)
    assert [i.id for i in a] == sorted((i.id for i in a), key=lambda s: int(s[1:]))
    assert select_items(items, None, seed=7) == items


def test_metrics() -> None:
    assert REGISTRY["exact_match"]("A", "A") == 1.0
    assert REGISTRY["exact_match"]("A", None) == 0.0
    assert REGISTRY["normalized_match"]("Net 30", "  net   30 ") == 1.0
    assert REGISTRY["contains"]("30", "net 30 days") == 1.0


async def test_report_files_written(tmp_path: Path) -> None:
    ds = load_dataset(make_dataset(tmp_path), require_frozen=False)
    report = await run_suite(toy(), ds)
    json_path, md_path = write_report(report, ToyUppercaseSuite.metrics, tmp_path / "reports")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["dataset_sha256"] == ds.sha256 and data["summary"]["exact_match"] == 1.0
    assert data["prompt_version"] == "toy-uppercase-v1"
    assert "| exact_match | 1.000 |" in md_path.read_text(encoding="utf-8")


# ------------------------------------------------------------------ held-out guard


def test_heldout_guard(tmp_path: Path) -> None:
    log = tmp_path / "HELDOUT_LOG.md"
    check_heldout(log, "toy", "test", "abc", force=False)  # first run allowed
    record_heldout(log, "toy", "test", "abc", "deadbee", forced=False)
    with pytest.raises(HeldOutAlreadyRun):
        check_heldout(log, "toy", "test", "abc", force=False)
    check_heldout(log, "toy", "test", "abc", force=True)  # explicit override
    check_heldout(log, "toy", "test", "other-sha", force=False)  # a new frozen set is new


def test_cli_refuses_second_heldout_run_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    shutil.copytree(REPO_TOY, tmp_path / "datasets" / "toy")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/x")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    evals_cli.get_settings.cache_clear()
    args = ["--suite", "toy", "--split", "test", "--evals-dir", str(tmp_path)]

    assert evals_cli.main(args) == 0
    assert evals_cli.main(args) == 3
    assert "refused" in capsys.readouterr().err
    assert evals_cli.main([*args, "--force"]) == 0

    log = (tmp_path / "HELDOUT_LOG.md").read_text(encoding="utf-8")
    assert log.count("| toy | test |") == 2 and "| yes |" in log
    assert len(list((tmp_path / "reports").glob("*.json"))) == 2
    evals_cli.get_settings.cache_clear()


def test_cli_dev_run_with_cost_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shutil.copytree(REPO_TOY, tmp_path / "datasets" / "toy")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/x")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    evals_cli.get_settings.cache_clear()
    code = evals_cli.main(
        [
            "--suite",
            "toy",
            "--split",
            "dev",
            "--evals-dir",
            str(tmp_path),
            "--max-cost",
            "0.0003",
            "--concurrency",
            "1",
        ]
    )
    assert code == 4  # stopped by the cap
    evals_cli.get_settings.cache_clear()
