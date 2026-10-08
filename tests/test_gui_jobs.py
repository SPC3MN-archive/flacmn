import tempfile
import threading
import time
import unittest
from pathlib import Path

from iaflac.events import Cancelled, check
from iaflac.gui.jobs import DownloadQueue, JobManager


def wait_for(cond, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    raise AssertionError("condition not reached")


class SlowRunner:
    """Pretends to download 50 chunks, honouring cancel between chunks; remembers where it got to (like .part files)."""

    def __init__(self):
        self.progress_at = {}
        self.gate = threading.Event()
        self.gate.set()

    def __call__(self, item, progress, cancelled):
        start = self.progress_at.get(item.id, 0)
        for i in range(start, 50):
            self.gate.wait()
            check(cancelled)
            self.progress_at[item.id] = i + 1
            progress({"stage": "download", "done_bytes": i + 1, "total_bytes": 50, "file": "x", "index": 1, "total": 1})
            time.sleep(0.002)
        return {"ok": True, "folder": "/x", "resumed_from": start}


def add(q, name):
    return q.add(identifier=name, media="flac", artist="A", album=name, folder=f"/lib/A/{name}", meta={}, options={})


class Queue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Path(self.tmp.name) / "queue.json"
        self.events = []
        self.runner = SlowRunner()

    def tearDown(self):
        self.tmp.cleanup()

    def queue(self):
        return DownloadQueue(self.runner, self.events.append, store=self.store)

    def test_items_run_in_order_and_finish(self):
        q = self.queue()
        a, b = add(q, "one"), add(q, "two")
        wait_for(lambda: q.items[b.id].state == "done")
        self.assertEqual(q.items[a.id].state, "done")
        self.assertEqual(q.items[a.id].done_bytes, 50)
        self.assertTrue(any(e["type"] == "queue" and e["item"]["state"] == "downloading" for e in self.events))
        q.stop()

    def test_the_same_folder_cannot_be_queued_twice(self):
        q = self.queue()
        self.runner.gate.clear()
        add(q, "one")
        with self.assertRaises(ValueError):
            add(q, "one")
        self.runner.gate.set()
        q.stop()

    def test_pause_then_resume_continues_where_it_stopped(self):
        q = self.queue()
        item = add(q, "one")
        wait_for(lambda: self.runner.progress_at.get(item.id, 0) >= 5)
        q.pause(item.id)
        wait_for(lambda: q.items[item.id].state == "paused")
        stopped_at = self.runner.progress_at[item.id]
        self.assertLess(stopped_at, 50)
        q.resume(item.id)
        wait_for(lambda: q.items[item.id].state == "done")
        self.assertEqual(q.items[item.id].result["resumed_from"], stopped_at)
        q.stop()

    def test_cancel_and_retry(self):
        q = self.queue()
        item = add(q, "one")
        wait_for(lambda: self.runner.progress_at.get(item.id, 0) >= 2)
        q.cancel(item.id)
        wait_for(lambda: q.items[item.id].state == "cancelled")
        q.retry(item.id)
        wait_for(lambda: q.items[item.id].state == "done")
        q.stop()

    def test_a_failure_is_reported_and_can_be_retried(self):
        calls = []

        def flaky(item, progress, cancelled):
            calls.append(1)
            if len(calls) == 1:
                return {"ok": False, "failures": ["01.flac: download failed (timeout); rerun to resume"]}
            return {"ok": True}

        q = DownloadQueue(flaky, self.events.append, store=self.store)
        item = add(q, "one")
        wait_for(lambda: q.items[item.id].state == "failed")
        self.assertIn("timeout", q.items[item.id].error)
        q.retry(item.id)
        wait_for(lambda: q.items[item.id].state == "done")
        q.stop()

    def test_the_queue_survives_a_restart_and_unfinished_items_start_again(self):
        q = self.queue()
        self.runner.gate.clear()
        first = add(q, "one")
        second = add(q, "two")
        wait_for(lambda: q.items[first.id].state == "downloading")
        q.stop()
        self.runner.gate.set()
        wait_for(lambda: q.items[first.id].state == "queued")
        again = self.queue()
        self.assertEqual([i.id for i in again.list()], [first.id, second.id])
        wait_for(lambda: all(i.state == "done" for i in again.list()))
        again.stop()

    def test_remove_and_clear_finished(self):
        q = self.queue()
        a = add(q, "one")
        wait_for(lambda: q.items[a.id].state == "done")
        self.assertEqual(q.clear_finished(), 1)
        self.assertEqual(q.list(), [])
        q.stop()


class Jobs(unittest.TestCase):
    def test_a_job_reports_its_result_or_its_error(self):
        events = []
        jm = JobManager(events.append, workers=2)
        ok = jm.submit("t", "ok", lambda job: {"answer": 42})
        bad = jm.submit("t", "bad", lambda job: 1 / 0)
        wait_for(lambda: ok.state == "done" and bad.state == "failed")
        self.assertEqual(ok.result, {"answer": 42})
        self.assertIn("ZeroDivisionError", bad.error)
        self.assertTrue(any(e["type"] == "job" and e["job"]["state"] == "done" for e in events))

    def test_cancel_a_running_job(self):
        jm = JobManager(lambda e: None, workers=1)

        def long(job):
            while True:
                check(job.cancelled)
                time.sleep(0.01)

        job = jm.submit("t", "long", long)
        wait_for(lambda: job.state == "running")
        self.assertTrue(jm.cancel(job.id))
        wait_for(lambda: job.state == "cancelled")
        self.assertFalse(jm.cancel(job.id), "already finished")


if __name__ == "__main__":
    unittest.main()
