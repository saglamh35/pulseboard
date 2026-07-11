from fastapi.testclient import TestClient

from pulseboard.app import create_app
from pulseboard.db import Database, MetricRecord
from pulseboard.goals import GOAL_DEFS, goals_met_in_window, sleep_debt_hours, streak_days
from pulseboard.metrics import Goal


def seed(db: Database, *rows: tuple[str, str, float, str]) -> None:
    db.upsert_records(
        [MetricRecord(date, metric, value, "", aggregation, "test") for date, metric, value, aggregation in rows]
    )


class TestGoal:
    def test_at_least(self):
        goal = Goal(8000, "at_least")
        assert goal.met(8000)
        assert goal.met(9001)
        assert not goal.met(7999)

    def test_at_most(self):
        goal = Goal(65, "at_most")
        assert goal.met(60)
        assert goal.met(65)
        assert not goal.met(70)

    def test_registry_declares_goals(self):
        assert {d.name for d in GOAL_DEFS} == {"steps", "sleep_hours", "apple_exercise_time"}


class TestStreakDays:
    def test_no_data_returns_none(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        assert streak_days(db, "steps") is None

    def test_unbroken_run(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed(
            db,
            ("2026-07-07", "steps", 9000, "sum"),
            ("2026-07-08", "steps", 8500, "sum"),
            ("2026-07-09", "steps", 8000, "sum"),
        )
        assert streak_days(db, "steps") == 3

    def test_day_below_goal_ends_streak(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed(
            db,
            ("2026-07-07", "steps", 9000, "sum"),
            ("2026-07-08", "steps", 3000, "sum"),
            ("2026-07-09", "steps", 8000, "sum"),
        )
        assert streak_days(db, "steps") == 1

    def test_missing_calendar_day_breaks_streak(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed(
            db,
            ("2026-07-06", "steps", 9000, "sum"),
            ("2026-07-08", "steps", 8500, "sum"),
            ("2026-07-09", "steps", 8000, "sum"),
        )
        assert streak_days(db, "steps") == 2

    def test_latest_day_missing_goal_zeroes_streak(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed(
            db,
            ("2026-07-08", "steps", 9000, "sum"),
            ("2026-07-09", "steps", 100, "sum"),
        )
        assert streak_days(db, "steps") == 0

    def test_anchored_to_metrics_own_latest_date(self, tmp_path):
        # Steps not synced "today" — the streak still counts up to its own latest day.
        db = Database(str(tmp_path / "test.db"))
        seed(
            db,
            ("2026-07-07", "steps", 8100, "sum"),
            ("2026-07-08", "steps", 8100, "sum"),
            ("2026-07-09", "sleep_hours", 7.5, "sum"),
        )
        assert streak_days(db, "steps") == 2


class TestGoalsMetInWindow:
    def test_five_of_seven(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        values = [9000, 8100, 2000, 8500, 12000, 4000, 8000]
        for i, value in enumerate(values):
            seed(db, (f"2026-07-{6 + i:02d}", "steps", value, "sum"))
        assert goals_met_in_window(db, "steps", "2026-07-06", "2026-07-12") == (5, 7)

    def test_empty_window(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed(db, ("2026-06-01", "steps", 9000, "sum"))
        assert goals_met_in_window(db, "steps", "2026-07-06", "2026-07-12") == (0, 0)


class TestSleepDebtHours:
    def test_no_data_returns_none(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        assert sleep_debt_hours(db) is None

    def test_accumulates_shortfall_only(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        for i in range(14):
            seed(db, (f"2026-07-{1 + i:02d}", "sleep_hours", 6.0, "sum"))
        assert sleep_debt_hours(db) == 14.0

    def test_surplus_night_does_not_repay_debt(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed(
            db,
            ("2026-07-08", "sleep_hours", 6.0, "sum"),
            ("2026-07-09", "sleep_hours", 9.0, "sum"),
        )
        assert sleep_debt_hours(db) == 1.0

    def test_nights_outside_window_excluded(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed(
            db,
            ("2026-06-01", "sleep_hours", 4.0, "sum"),  # far outside the 14-night window
            ("2026-07-09", "sleep_hours", 6.5, "sum"),
        )
        assert sleep_debt_hours(db) == 0.5


class TestGoalsInExporter:
    def test_streak_target_and_debt_gauges(self, tmp_path):
        client = TestClient(create_app(str(tmp_path / "test.db")))
        client.post(
            "/ingest",
            json={
                "date": "2026-07-09",
                "metrics": [
                    {"name": "steps", "value": 8250},
                    {"name": "sleep_hours", "value": 6.0},
                ],
            },
        )
        body = client.get("/metrics").text
        assert 'pulseboard_goal_streak_days{metric="steps"} 1.0' in body
        assert 'pulseboard_goal_streak_days{metric="sleep_hours"} 0.0' in body
        assert 'pulseboard_goal_target{metric="sleep_hours"} 7.0' in body
        assert "pulseboard_sleep_debt_hours 1.0" in body

    def test_no_goal_lines_when_db_empty(self, tmp_path):
        body = TestClient(create_app(str(tmp_path / "test.db"))).get("/metrics").text
        assert "pulseboard_goal_streak_days" not in body
        assert "pulseboard_goal_target" not in body
        assert "pulseboard_sleep_debt_hours" not in body
