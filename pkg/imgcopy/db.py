"""可移植版状态存储：接口与原版 db.py 一致，但底层是一个 JSON 文件（不需要 SQLite）。

状态文件：<IMGCOPY_HOME>/data/state.json（默认当前目录下）。
原版用 SQLite 是为了断点续跑与花费统计；单机小规模跑批用 JSON 足够，也更容易跨平台。
"""

from __future__ import annotations

import json
from pathlib import Path

from . import context as ctx

_EMPTY = {"parent": {}, "child": {}, "verdict": {}, "gen_cache": {}, "run_log": []}


def db_path() -> Path:
    return ctx.ensure_dir(ctx.data_dir()) / "state.json"


def _load() -> dict:
    data = ctx.read_json(db_path(), None)
    if not isinstance(data, dict):
        data = {}
    for k, v in _EMPTY.items():
        data.setdefault(k, json.loads(json.dumps(v)))
    return data


def _save(data: dict) -> None:
    ctx.write_json(db_path(), data)


# ---------------------------------------------------------------- parent

def upsert_parent(rec: dict) -> None:
    d = _load()
    now = ctx.now_iso()
    old = d["parent"].get(rec["parent_id"], {})
    d["parent"][rec["parent_id"]] = {**old, **rec,
                                     "created_at": old.get("created_at") or rec.get("created_at") or now,
                                     "updated_at": now}
    _save(d)


def set_parent_status(parent_id: str, status: str, **fields) -> None:
    order = {n: i for i, n in enumerate(
        ["ingested", "analyzed", "dna", "planned", "generated", "qc", "exported"])}
    d = _load()
    rec = d["parent"].get(parent_id)
    if not rec:
        return
    cur = rec.get("status") or "ingested"
    if status in order and cur in order and order[status] < order[cur]:
        status = cur
    rec.update(fields)
    rec["status"] = status
    rec["updated_at"] = ctx.now_iso()
    _save(d)


def get_parent(parent_id: str) -> dict | None:
    return _load()["parent"].get(parent_id)


def find_parent_by_design(design_name: str) -> dict | None:
    for rec in _load()["parent"].values():
        if rec.get("design_name") == design_name:
            return rec
    return None


def list_parents(status: str | None = None) -> list[dict]:
    rows = list(_load()["parent"].values())
    if status:
        rows = [r for r in rows if r.get("status") == status]
    return sorted(rows, key=lambda r: r.get("created_at") or "")


def next_parent_seq(day: str) -> int:
    n = sum(1 for pid in _load()["parent"] if pid.startswith(f"P_{day}_"))
    return n + 1


# ---------------------------------------------------------------- child

def upsert_child(rec: dict) -> None:
    d = _load()
    now = ctx.now_iso()
    old = d["child"].get(rec["child_id"], {})
    d["child"][rec["child_id"]] = {**old, **rec,
                                   "created_at": old.get("created_at") or rec.get("created_at") or now,
                                   "updated_at": now}
    _save(d)


def set_child_status(child_id: str, status: str, **fields) -> None:
    d = _load()
    rec = d["child"].get(child_id)
    if not rec:
        return
    rec.update(fields)
    rec["status"] = status
    rec["updated_at"] = ctx.now_iso()
    _save(d)


def get_child(child_id: str) -> dict | None:
    return _load()["child"].get(child_id)


def children_of(parent_id: str, status: str | None = None) -> list[dict]:
    rows = [c for c in _load()["child"].values() if c.get("parent_id") == parent_id]
    if status:
        rows = [c for c in rows if c.get("status") == status]
    return sorted(rows, key=lambda c: c.get("child_id") or "")


def upsert_verdict(child_id: str, gate: str, passed: bool, score, reasons, detail,
                   judge_model: str = "cv") -> None:
    d = _load()
    d["verdict"][f"{child_id}|{gate}"] = {
        "child_id": child_id, "gate": gate, "passed": bool(passed), "score": score,
        "reason_codes": reasons or [], "detail": detail or {},
        "judge_model": judge_model, "created_at": ctx.now_iso()}
    _save(d)


def verdicts_of(child_id: str) -> list[dict]:
    return [v for k, v in _load()["verdict"].items() if v.get("child_id") == child_id]


# ---------------------------------------------------------------- cache / log

def cache_get(cache_key: str) -> dict | None:
    return _load()["gen_cache"].get(cache_key)


def cache_put(cache_key: str, image_path: str, job: str = "", cost: dict | None = None) -> None:
    d = _load()
    d["gen_cache"][cache_key] = {"cache_key": cache_key, "image_path": image_path,
                                 "job": job, "cost": cost, "created_at": ctx.now_iso()}
    _save(d)


def log_run(kind: str, started_at: str, ok: bool, detail: str = "") -> None:
    d = _load()
    d["run_log"].append({"kind": kind, "started_at": started_at,
                         "finished_at": ctx.now_iso(), "ok": bool(ok),
                         "detail": detail[:4000]})
    d["run_log"] = d["run_log"][-500:]
    _save(d)


def last_runs(n: int = 10) -> list[dict]:
    return list(reversed(_load()["run_log"][-n:]))


def connect():  # 兼容原版：某些模块只是拿它做一次性查询
    raise NotImplementedError("可移植版没有 SQLite；请用上面的函数")
