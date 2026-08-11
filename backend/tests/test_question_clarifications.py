import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException

from backend.routers import cx_responses


class _Result:
    def __init__(self, row=None):
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return self.row

    def all(self):
        return []


class _Connection:
    def __init__(self):
        self.statements = []
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params=None):
        self.statements.append((str(statement), params or {}))
        return _Result({
            "outcome": "answered",
            "mp": "wb",
            "question_id": "question-1",
            "requested_by": "cs@example.test",
            "answered_at": datetime(2026, 8, 11, 10, 0),
        })

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _Db:
    def __init__(self):
        self.connection = _Connection()
        self.bind = Mock()
        self.bind.connect.return_value = self.connection


class _PermissionDb:
    def __init__(self, granted: bool):
        self.granted = granted
        self.statements = []

    def execute(self, statement, params=None):
        self.statements.append((str(statement), params or {}))
        return _Result((1,) if self.granted else None)


class QuestionClarificationTests(unittest.TestCase):
    def test_quality_queue_access_uses_assigned_module_permission(self):
        denied = _PermissionDb(False)
        with self.assertRaises(HTTPException) as caught:
            cx_responses._qa_questions_access({"role": "qa_manager"}, denied)
        self.assertEqual(caught.exception.status_code, 403)

        # Admin follows the established all-module access policy.
        self.assertEqual(
            cx_responses._qa_questions_access({"role": "admin"}, denied)["role"],
            "admin",
        )

        granted = _PermissionDb(True)
        self.assertEqual(
            cx_responses._qa_questions_access({"role": "quality_reviewer"}, granted)["role"],
            "quality_reviewer",
        )
        sql, params = granted.statements[0]
        self.assertIn("module = 'quality_questions'", sql)
        self.assertEqual(params, {"r": "quality_reviewer"})

    def test_obsolete_pending_clarification_is_cancelled(self):
        connection = _Connection()
        cx_responses._cancel_obsolete_question_clarifications(connection)

        sql = connection.statements[0][0].lower()
        self.assertIn("set status = 'cancelled'", sql)
        self.assertIn("where c.status = 'pending'", sql)
        self.assertIn("not exists", sql)
        self.assertIn(cx_responses.UNIFIED_QUEUE_VIEW, sql)

    def test_status_constraint_migration_is_catalog_guarded(self):
        source = (Path(__file__).resolve().parents[1] / "main.py").read_text()
        self.assertIn("FROM pg_constraint", source)
        self.assertIn("DO $$", source)
        self.assertNotIn(
            "DROP CONSTRAINT IF EXISTS cx_question_clarifications_status_check",
            source,
        )

    def test_only_cs_manager_can_request_clarification(self):
        with self.assertRaises(HTTPException) as caught:
            cx_responses.request_question_clarification(
                "wb", "question-1", cx_responses.ClarificationRequestPayload(),
                _Db(), {"username": "qa@example.test", "role": "qa_manager"},
            )
        self.assertEqual(caught.exception.status_code, 403)

    def test_qa_answer_only_updates_local_clarification(self):
        db = _Db()
        with (
            patch.object(cx_responses.wb_client, "answer_question") as wb_answer,
            patch.object(cx_responses.ym_client, "answer_question") as ym_answer,
        ):
            result = cx_responses.answer_question_clarification(
                7,
                cx_responses.ClarificationAnswerPayload(text="Предложенный текст"),
                db,
                {"username": "quality@example.test", "role": "qa_manager"},
            )

        self.assertEqual(result["status"], "answered")
        self.assertEqual(db.connection.commits, 1)
        sql = "\n".join(statement for statement, _ in db.connection.statements).lower()
        self.assertIn("update cx_question_clarifications", sql)
        self.assertIn(cx_responses.UNIFIED_QUEUE_VIEW, sql)
        self.assertIn("set status = 'cancelled'", sql)
        self.assertNotIn("wb_questions", sql)
        self.assertNotIn("ym_questions", sql)
        wb_answer.assert_not_called()
        ym_answer.assert_not_called()

    def test_qa_answer_rejects_and_commits_cancelled_request(self):
        db = _Db()
        db.connection.execute = Mock(return_value=_Result({"outcome": "cancelled"}))

        with self.assertRaises(HTTPException) as caught:
            cx_responses.answer_question_clarification(
                7,
                cx_responses.ClarificationAnswerPayload(text="Устаревший текст"),
                db,
                {"username": "quality@example.test", "role": "qa_manager"},
            )

        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(db.connection.commits, 1)


if __name__ == "__main__":
    unittest.main()
