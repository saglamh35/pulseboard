from pathlib import Path

import pulseboard

REPO = Path(__file__).parent.parent
CHART = REPO / "deploy" / "helm" / "pulseboard"


class TestChartFilesInSync:
    """The chart bundles copies of the Grafana assets (Helm cannot read files
    outside the chart directory). These asserts stop the copies from drifting
    from the compose-stack originals."""

    def test_dashboard_json_matches_grafana_dir(self):
        assert (CHART / "files" / "pulseboard_dashboard.json").read_text() == (
            REPO / "grafana" / "pulseboard_dashboard.json"
        ).read_text()

    def test_alert_rules_match_grafana_dir(self):
        assert (CHART / "files" / "pulseboard_alerts.yml").read_text() == (
            REPO / "grafana" / "provisioning" / "alerting" / "pulseboard_alerts.yml"
        ).read_text()

    def test_dashboard_provider_matches_grafana_dir(self):
        assert (CHART / "files" / "dashboards.yml").read_text() == (
            REPO / "grafana" / "provisioning" / "dashboards" / "dashboards.yml"
        ).read_text()


class TestChartMetadata:
    def test_app_version_matches_package_version(self):
        chart_yaml = (CHART / "Chart.yaml").read_text()
        assert f'appVersion: "{pulseboard.__version__}"' in chart_yaml
