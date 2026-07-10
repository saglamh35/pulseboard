from datetime import date, datetime, timedelta, timezone

from pulseboard.db import Database, MetricRecord, WorkoutRecord
from pulseboard.report import (
    _week_window,
    build_weekly_report,
    main,
    next_run_at,
    notification_summary,
    render_html,
    render_markdown,
)

# 2026-07-06 is a Monday; the "this week" window is 07-06 .. 07-12.
THIS_MONDAY = date(2026, 7, 6)


def seed_two_weeks(db: Database) -> None:
    """Last week: 10000 steps/day, 8 h sleep. This week: 12000 and 6 h."""
    records = []
    for i in range(7):
        last_day = (THIS_MONDAY - timedelta(days=7) + timedelta(days=i)).isoformat()
        this_day = (THIS_MONDAY + timedelta(days=i)).isoformat()
        records += [
            MetricRecord(last_day, "steps", 10000.0, "count", "sum", "canonical"),
            MetricRecord(this_day, "steps", 12000.0, "count", "sum", "canonical"),
            MetricRecord(last_day, "sleep_hours", 8.0, "h", "sum", "canonical"),
            MetricRecord(this_day, "sleep_hours", 6.0, "h", "sum", "canonical"),
        ]
    db.upsert_records(records)


class TestWeekWindow:
    def test_monday_anchors_to_its_own_week(self):
        assert _week_window(date(2026, 7, 6)) == (date(2026, 7, 6), date(2026, 7, 12))

    def test_midweek_and_sunday_share_the_window(self):
        assert _week_window(date(2026, 7, 9)) == (date(2026, 7, 6), date(2026, 7, 12))
        assert _week_window(date(2026, 7, 12)) == (date(2026, 7, 6), date(2026, 7, 12))


class TestBuildWeeklyReport:
    def test_sum_and_mean_deltas(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed_two_weeks(db)
        report = build_weekly_report(db, week_ending=THIS_MONDAY + timedelta(days=3))
        assert report.week_start == "2026-07-06"
        assert report.week_end == "2026-07-12"
        by_label = {c.label: c for c in report.comparisons}

        steps = by_label["Steps"]  # weekly sum
        assert steps.this_week == 84000.0
        assert steps.last_week == 70000.0
        assert steps.delta_pct == 20.0
        assert steps.days_with_data == 7

        sleep = by_label["Sleep (avg/night)"]  # nightly mean
        assert sleep.this_week == 6.0
        assert sleep.last_week == 8.0
        assert sleep.delta_pct == -25.0

    def test_missing_metric_renders_dash_not_crash(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed_two_weeks(db)
        report = build_weekly_report(db, week_ending=THIS_MONDAY)
        by_label = {c.label: c for c in report.comparisons}
        assert by_label["Resting HR (avg)"].this_week is None
        assert by_label["Resting HR (avg)"].delta_pct is None
        assert "—" in render_markdown(report)

    def test_workouts_in_window_are_listed(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed_two_weeks(db)
        db.upsert_workouts(
            [
                WorkoutRecord("2026-07-07 18:00:00 +0200", "2026-07-07", "Running", 31.5, 342.0, 5.2, "canonical"),
                WorkoutRecord("2026-06-01 18:00:00 +0200", "2026-06-01", "Cycling", 60.0, 500.0, 20.0, "canonical"),
            ]
        )
        report = build_weekly_report(db, week_ending=THIS_MONDAY)
        assert [w.activity_type for w in report.workouts] == ["Running"]

    def test_empty_db_still_builds(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        report = build_weekly_report(db, week_ending=THIS_MONDAY)
        assert all(c.this_week is None for c in report.comparisons)
        assert report.freshness_seconds is None


class TestRendering:
    def test_markdown_contains_table_and_disclaimer(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed_two_weeks(db)
        text = render_markdown(build_weekly_report(db, week_ending=THIS_MONDAY))
        assert "| Steps | 84,000 | 70,000 | ▲ +20.0% | 7 |" in text
        assert "not medical advice" in text

    def test_html_contains_table_and_disclaimer(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed_two_weeks(db)
        html = render_html(build_weekly_report(db, week_ending=THIS_MONDAY))
        assert "<table" in html
        assert "84,000" in html
        assert "not medical advice" in html

    def test_notification_summary_is_short(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed_two_weeks(db)
        title, body = notification_summary(build_weekly_report(db, week_ending=THIS_MONDAY))
        assert title == "PulseBoard week 2026-07-06"
        assert "Sleep (avg/night)" in body  # -25% is the biggest move
        assert len(body) < 500


class TestNextRunAt:
    def test_advances_to_next_monday_8am(self):
        wednesday = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
        assert next_run_at(wednesday) == datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc)

    def test_monday_before_8_runs_same_day(self):
        monday_early = datetime(2026, 7, 6, 7, 0, tzinfo=timezone.utc)
        assert next_run_at(monday_early) == datetime(2026, 7, 6, 8, 0, tzinfo=timezone.utc)

    def test_monday_after_8_waits_a_week(self):
        monday_late = datetime(2026, 7, 6, 9, 0, tzinfo=timezone.utc)
        assert next_run_at(monday_late) == datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc)


class TestCli:
    def test_report_to_file(self, tmp_path, capsys):
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        seed_two_weeks(db)
        db.close()
        out = tmp_path / "report.md"
        code = main(["--db", db_path, "--week-ending", "2026-07-06", "--out", str(out)])
        assert code == 0
        assert "84,000" in out.read_text()

    def test_check_freshness_fresh(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.upsert_records([MetricRecord("2026-07-06", "steps", 1.0, "count", "sum", "canonical")])
        db.close()
        assert main(["--db", db_path, "--check-freshness"]) == 0

    def test_check_freshness_empty_db_fails(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        Database(db_path).close()
        assert main(["--db", db_path, "--check-freshness"]) == 1
