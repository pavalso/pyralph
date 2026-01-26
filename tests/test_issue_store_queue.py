from pyralph.fetch_ready_issues import IssueStore, ProcessingQueue

from .helpers import IssueWatcherTestCase


class TestIssueStore(IssueWatcherTestCase):
    def test_save_and_get(self):
        store = IssueStore(self.store_dir)
        issue = self.create_sample_issue()
        stored = store.save(issue)
        assert stored.number == 1
        assert stored.title == "Test Issue"
        assert stored.status == "pending"
        retrieved = store.get(1)
        assert retrieved is not None
        assert retrieved.number == 1

    def test_exists(self):
        store = IssueStore(self.store_dir)
        assert not store.exists(1)
        store.save(self.create_sample_issue())
        assert store.exists(1)

    def test_delete(self):
        store = IssueStore(self.store_dir)
        store.save(self.create_sample_issue())
        assert store.delete(1)
        assert not store.exists(1)
        assert not store.delete(999)

    def test_list_issues(self):
        store = IssueStore(self.store_dir)
        store.save(self.create_sample_issue(1))
        store.save(self.create_sample_issue(2))
        issues = store.list_issues()
        assert len(issues) == 2
        assert issues[0].number == 1
        assert issues[1].number == 2

    def test_list_issues_by_status(self):
        store = IssueStore(self.store_dir)
        store.save(self.create_sample_issue(1), status="pending")
        store.save(self.create_sample_issue(2), status="completed")
        pending = store.list_issues(status="pending")
        assert len(pending) == 1
        assert pending[0].number == 1

    def test_update_status(self):
        store = IssueStore(self.store_dir)
        store.save(self.create_sample_issue())
        updated = store.update_status(1, "completed")
        assert updated.status == "completed"
        retrieved = store.get(1)
        assert retrieved.status == "completed"

    def test_count(self):
        store = IssueStore(self.store_dir)
        assert store.count() == 0
        store.save(self.create_sample_issue(1))
        store.save(self.create_sample_issue(2))
        assert store.count() == 2

    def test_clear(self):
        store = IssueStore(self.store_dir)
        store.save(self.create_sample_issue(1))
        store.save(self.create_sample_issue(2))
        deleted = store.clear()
        assert deleted == 2
        assert store.count() == 0


class TestProcessingQueue(IssueWatcherTestCase):
    def test_enqueue_and_dequeue(self):
        queue = ProcessingQueue(self.queue_dir)
        item = queue.enqueue(42)
        assert item.issue_number == 42
        assert item.status == "pending"
        dequeued = queue.dequeue()
        assert dequeued.issue_number == 42
        assert dequeued.status == "processing"

    def test_enqueue_idempotent(self):
        queue = ProcessingQueue(self.queue_dir)
        item1 = queue.enqueue(42)
        item2 = queue.enqueue(42)
        assert item1.issue_number == item2.issue_number
        assert queue.count() == 1

    def test_priority_ordering(self):
        queue = ProcessingQueue(self.queue_dir)
        queue.enqueue(1, priority=10)
        queue.enqueue(2, priority=1)
        queue.enqueue(3, priority=5)
        item = queue.dequeue()
        assert item.issue_number == 2

    def test_mark_completed(self):
        queue = ProcessingQueue(self.queue_dir)
        queue.enqueue(42)
        queue.dequeue()
        completed = queue.mark_completed(42)
        assert completed.status == "completed"
        assert completed.completed_at is not None

    def test_mark_failed(self):
        queue = ProcessingQueue(self.queue_dir)
        queue.enqueue(42)
        queue.dequeue()
        failed = queue.mark_failed(42, error="Test error")
        assert failed.status == "failed"
        assert failed.error == "Test error"

    def test_retry(self):
        queue = ProcessingQueue(self.queue_dir)
        queue.enqueue(42)
        queue.dequeue()
        queue.mark_failed(42)
        retried = queue.retry(42)
        assert retried.status == "pending"
        assert retried.retry_count == 1

    def test_list_items(self):
        queue = ProcessingQueue(self.queue_dir)
        queue.enqueue(1)
        queue.enqueue(2)
        queue.dequeue()
        pending = queue.list_items(status="pending")
        processing = queue.list_items(status="processing")
        assert len(pending) == 1
        assert len(processing) == 1

    def test_reset_processing(self):
        queue = ProcessingQueue(self.queue_dir)
        queue.enqueue(1)
        queue.enqueue(2)
        queue.dequeue()
        queue.dequeue()
        reset_count = queue.reset_processing()
        assert reset_count == 2
        assert queue.count(status="pending") == 2
