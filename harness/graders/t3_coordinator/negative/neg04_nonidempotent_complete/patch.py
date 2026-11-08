"""NEGATIVE 4: completing an already-DONE job overwrites the result and
appends another COMPLETED receipt.

Expected failure: idempotent_completion gate (and no_lost_completion for a
late duplicate after a reclaim).
"""
from _patchlib import main_wrapper, replace_once


def patch(path, workspace):
    replace_once(
        path,
        """            if row["state"] == JobState.DONE:
                self._event(
                    conn,
                    EVENT_COMPLETION_REPEATED,
                    job_id,
                    worker_id,
                    {"stored_worker": row["lease_worker"], "accepted": False},
                    now,
                )
                return self._job_from_row(self._get_job_row(conn, job_id))""",
        """            if row["state"] == JobState.DONE:
                conn.execute(
                    "UPDATE jobs SET result = ?, updated_at = ? WHERE job_id = ?",
                    (encode_json(result), now, job_id),
                )
                conn.execute(
                    "INSERT INTO results (job_id, worker_id, result, completed_at) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(job_id) DO UPDATE SET "
                    "result = excluded.result, worker_id = excluded.worker_id, "
                    "completed_at = excluded.completed_at",
                    (job_id, worker_id, encode_json(result), now),
                )
                self._event(conn, EVENT_COMPLETED, job_id, worker_id,
                            {"duplicate": True, "result": result}, now)
                return self._job_from_row(self._get_job_row(conn, job_id))""",
        "neg04-nonidempotent",
    )


if __name__ == "__main__":
    main_wrapper("neg04_nonidempotent_complete", patch)
