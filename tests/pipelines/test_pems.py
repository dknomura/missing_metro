import math

import numpy as np
import pandas as pd
import pytest

from shared.pipelines.pems import add_segment_length, add_within_year_se


def _base_row(**kwargs):
    defaults = dict(
        station=1000,
        route=101,
        direction="N",
        hour=8,
        year=2022,
        type="ML",
        abs_pm=10.0,
        lanes=4,
        days_observed=60,
        avg_speed=60.0,
        sd_speed=6.0,
        avg_flow=1500.0,
        sd_flow=200.0,
    )
    return {**defaults, **kwargs}


def _df(*overrides):
    rows = overrides if overrides else ({},)
    return pd.DataFrame([_base_row(**r) for r in rows])


class TestWithinYearSE:
    def test_speed_se_formula(self):
        # Arrange
        df = _df(dict(sd_speed=6.0, days_observed=36))

        # Act
        out = add_within_year_se(df)

        # Assert
        assert out["se_speed"].iloc[0] == pytest.approx(1.0)

    def test_flow_se_formula(self):
        # Arrange
        df = _df(dict(sd_flow=200.0, days_observed=100))

        # Act
        out = add_within_year_se(df)

        # Assert
        assert out["se_flow"].iloc[0] == pytest.approx(20.0)

    def test_se_decreases_with_more_days(self):
        # Arrange
        df_few = _df(dict(sd_speed=6.0, days_observed=10))
        df_many = _df(dict(sd_speed=6.0, days_observed=90))

        # Act
        se_few = add_within_year_se(df_few)["se_speed"].iloc[0]
        se_many = add_within_year_se(df_many)["se_speed"].iloc[0]

        # Assert
        assert se_few > se_many

    def test_days_observed_one_gives_se_equal_to_sd(self):
        # Arrange
        df = _df(dict(sd_speed=5.0, days_observed=1))

        # Act
        out = add_within_year_se(df)
        v = out["se_speed"].iloc[0]

        # Assert
        assert math.isfinite(v)
        assert v == pytest.approx(5.0)

    def test_days_observed_zero_clipped_no_crash(self):
        # Arrange
        df = _df(dict(sd_speed=5.0, days_observed=0))

        # Act
        out = add_within_year_se(df)

        # Assert
        assert math.isfinite(out["se_speed"].iloc[0])

    def test_missing_sd_flow_col_skipped(self):
        # Arrange
        df = _df().drop(columns=["sd_flow"])

        # Act
        out = add_within_year_se(df)

        # Assert
        assert "se_flow" not in out.columns

    def test_missing_sd_speed_col_skipped(self):
        # Arrange
        df = _df().drop(columns=["sd_speed"])

        # Act
        out = add_within_year_se(df)

        # Assert
        assert "se_speed" not in out.columns

    def test_multiple_rows_independent(self):
        # Arrange
        df = _df(
            dict(sd_speed=4.0, days_observed=16),  # 4  / sqrt(16) = 1.0
            dict(sd_speed=9.0, days_observed=81),  # 9  / sqrt(81) = 1.0
        )

        # Act
        out = add_within_year_se(df)

        # Assert
        assert out["se_speed"].iloc[0] == pytest.approx(1.0)
        assert out["se_speed"].iloc[1] == pytest.approx(1.0)

    def test_both_output_columns_added(self):
        # Arrange
        df = _df()

        # Act
        out = add_within_year_se(df)

        # Assert
        assert "se_speed" in out.columns
        assert "se_flow" in out.columns

    def test_original_columns_preserved(self):
        # Arrange
        df = _df()
        original_cols = list(df.columns)

        # Act
        out = add_within_year_se(df.copy())

        # Assert
        for col in original_cols:
            assert col in out.columns


class TestSegmentLength:
    def test_northbound_segment_lengths(self):
        # Arrange
        df = _df(
            dict(station=1, abs_pm=10.0, direction="N"),
            dict(station=2, abs_pm=12.5, direction="N"),
            dict(station=3, abs_pm=15.0, direction="N"),
        )

        # Act
        out = add_segment_length(df).sort_values("abs_pm").reset_index(drop=True)

        # Assert
        assert out["segment_length"].iloc[0] == pytest.approx(2.5)
        assert out["segment_length"].iloc[1] == pytest.approx(2.5)
        assert pd.isna(out["segment_length"].iloc[2])

    def test_southbound_segment_lengths(self):
        # Arrange
        df = _df(
            dict(station=1, abs_pm=15.0, direction="S"),
            dict(station=2, abs_pm=12.5, direction="S"),
            dict(station=3, abs_pm=10.0, direction="S"),
        )

        # Act
        out = add_segment_length(df).sort_values("abs_pm", ascending=False).reset_index(drop=True)

        # Assert
        assert out["segment_length"].iloc[0] == pytest.approx(2.5)
        assert out["segment_length"].iloc[1] == pytest.approx(2.5)
        assert pd.isna(out["segment_length"].iloc[2])

    def test_eastbound_treated_like_northbound(self):
        # Arrange
        df = _df(
            dict(station=1, abs_pm=5.0, direction="E", route=580),
            dict(station=2, abs_pm=8.0, direction="E", route=580),
        )

        # Act
        out = add_segment_length(df).sort_values("abs_pm").reset_index(drop=True)

        # Assert
        assert out["segment_length"].iloc[0] == pytest.approx(3.0)

    def test_westbound_treated_like_southbound(self):
        # Arrange
        df = _df(
            dict(station=1, abs_pm=8.0, direction="W", route=580),
            dict(station=2, abs_pm=5.0, direction="W", route=580),
        )

        # Act
        out = add_segment_length(df).sort_values("abs_pm", ascending=False).reset_index(drop=True)

        # Assert
        assert out["segment_length"].iloc[0] == pytest.approx(3.0)

    def test_segment_length_always_non_negative(self):
        # Arrange
        df = _df(
            dict(station=1, abs_pm=15.0, direction="S"),
            dict(station=2, abs_pm=12.5, direction="S"),
            dict(station=3, abs_pm=10.0, direction="S"),
        )

        # Act
        out = add_segment_length(df)

        # Assert
        assert (out["segment_length"].dropna() >= 0).all()

    def test_segment_computed_within_year(self):
        # Arrange
        df = _df(
            dict(station=1, abs_pm=10.0, year=2020),
            dict(station=2, abs_pm=12.5, year=2020),
            dict(station=3, abs_pm=15.0, year=2020),
            dict(station=1, abs_pm=10.0, year=2021),
            dict(station=3, abs_pm=15.0, year=2021),
        )

        # Act
        out = add_segment_length(df)

        # Assert
        seg_2020 = out[(out.station == 1) & (out.year == 2020)]["segment_length"].iloc[0]
        seg_2021 = out[(out.station == 1) & (out.year == 2021)]["segment_length"].iloc[0]
        assert seg_2020 == pytest.approx(2.5)
        assert seg_2021 == pytest.approx(5.0)

    def test_routes_do_not_bleed(self):
        # Arrange
        df = _df(
            dict(station=1, route=101, abs_pm=10.0),
            dict(station=2, route=280, abs_pm=11.0),
        )

        # Act
        out = add_segment_length(df)

        # Assert
        assert out["segment_length"].isna().all()

    def test_opposite_directions_do_not_bleed(self):
        # Arrange
        df = _df(
            dict(station=1, direction="N", abs_pm=10.0),
            dict(station=2, direction="S", abs_pm=12.5),
        )

        # Act
        out = add_segment_length(df)

        # Assert
        assert out["segment_length"].isna().all()

    def test_different_hours_do_not_bleed(self):
        # Arrange
        df = _df(
            dict(station=1, hour=8, abs_pm=10.0),
            dict(station=2, hour=17, abs_pm=12.5),
        )

        # Act
        out = add_segment_length(df)

        # Assert
        assert out["segment_length"].isna().all()

    def test_single_station_nan_segment(self):
        # Arrange
        df = _df()

        # Act
        out = add_segment_length(df)

        # Assert
        assert "segment_length" in out.columns
        assert pd.isna(out["segment_length"].iloc[0])

    def test_missing_abs_pm_column_skipped_gracefully(self):
        # Arrange
        df = pd.DataFrame([{k: v for k, v in _base_row().items() if k != "abs_pm"}])

        # Act
        out = add_segment_length(df)

        # Assert
        assert "segment_length" not in out.columns

    def test_abs_pm_as_string_coerced(self):
        # Arrange
        df = _df(
            dict(station=1, abs_pm="10.0"),
            dict(station=2, abs_pm="12.5"),
        )

        # Act
        out = add_segment_length(df).sort_values("abs_pm").reset_index(drop=True)

        # Assert
        assert out["segment_length"].iloc[0] == pytest.approx(2.5)

    def test_output_columns_present(self):
        # Arrange
        df = _df(
            dict(station=1, abs_pm=10.0),
            dict(station=2, abs_pm=12.5),
        )

        # Act
        out = add_segment_length(df)

        # Assert
        for col in ("travel_order_pm", "next_pm", "segment_length"):
            assert col in out.columns

    def test_original_columns_preserved(self):
        # Arrange
        df = _df(dict(station=1, abs_pm=10.0), dict(station=2, abs_pm=12.5))
        original_cols = list(df.columns)

        # Act
        out = add_segment_length(df.copy())

        # Assert
        for col in original_cols:
            assert col in out.columns
