"""Unit tests for the execution metadata store."""

from genxai.core.execution import ExecutionStore


def test_execution_store_create_update():
    store = ExecutionStore()
    run_id = store.generate_run_id()
    record = store.create(run_id, workflow="wf", status="running")

    assert record.run_id == run_id
    assert record.status == "running"

    store.update(run_id, status="success", result={"ok": True}, completed=True)
    updated = store.get(run_id)

    assert updated is not None
    assert updated.status == "success"
    assert updated.result == {"ok": True}


def test_execution_store_sqlite_persistence(tmp_path):
    db_path = tmp_path / "exec.db"
    store = ExecutionStore(sql_url=f"sqlite:///{db_path}")
    run_id = store.generate_run_id()
    store.create(run_id, workflow="wf", status="running")
    store.update(run_id, status="success", result={"ok": True}, completed=True)

    fetched = store.get(run_id)
    assert fetched is not None
    assert fetched.status == "success"
    assert fetched.result == {"ok": True}


def test_persisted_runs_reload_on_init(tmp_path):
    store = ExecutionStore(persistence_path=tmp_path)
    store.create("run-1", workflow="wf", status="running")
    store.update("run-1", status="success", result={"ok": True}, completed=True)

    reloaded = ExecutionStore(persistence_path=tmp_path)
    record = reloaded.get("run-1")
    assert record is not None
    assert record.status == "success"
    assert record.result == {"ok": True}
    assert record.completed_at is not None


def test_corrupt_persisted_file_is_skipped(tmp_path):
    (tmp_path / "execution_bad.json").write_text("{not json")
    store = ExecutionStore(persistence_path=tmp_path)
    assert store.get("bad") is None
