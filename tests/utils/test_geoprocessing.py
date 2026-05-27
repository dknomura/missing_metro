import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point, Polygon, box

from shared.utils.constants import SCAG_FT_CRS
from shared.utils.geoprocessing import clip_to_buffer_rings

# EPSG:2229 coordinates for a point in central LA
LA_X, LA_Y = 6_490_000, 2_040_000


def _source(x: float = LA_X, y: float = LA_Y) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"source_id": ["S1"]},
        geometry=[Point(x, y)],
        crs=SCAG_FT_CRS,
    )


def _point_feature(x: float, y: float) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"feature_id": ["F1"]},
        geometry=[Point(x, y)],
        crs=SCAG_FT_CRS,
    )


def _line_feature(x0: float, y0: float, x1: float, y1: float) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"feature_id": ["F1"]},
        geometry=[LineString([(x0, y0), (x1, y1)])],
        crs=SCAG_FT_CRS,
    )


def _polygon_feature(cx: float, cy: float, half: float = 50) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"feature_id": ["F1"]},
        geometry=[box(cx - half, cy - half, cx + half, cy + half)],
        crs=SCAG_FT_CRS,
    )


class TestClipToBufferRingsInclusion:
    """Features inside / outside buffer rings are included / excluded correctly."""

    def test_point_inside_inner_ring_is_included(self):
        # Arrange
        features = _point_feature(LA_X + 100, LA_Y)

        # Act
        inner, *_ = clip_to_buffer_rings(features, _source(), [200, 1320])

        # Asser
        assert len(inner) == 1

    def test_point_outside_outer_ring_is_excluded(self):
        # Arrange
        features = _point_feature(LA_X + 3000, LA_Y)

        # Act
        inner, outer = clip_to_buffer_rings(features, _source(), [200, 2640])

        # Asser
        assert len(inner) == 0
        assert len(outer) == 0

    def test_point_in_donut_excluded_from_inner_ring(self):
        # Arrange
        features = _point_feature(LA_X + 500, LA_Y)

        # Act
        inner, donut = clip_to_buffer_rings(features, _source(), [200, 1320])

        # Asser
        assert len(inner) == 0
        assert len(donut) == 1

    def test_line_crossing_buffer_boundary_is_clipped(self):
        # Arrange
        features = _line_feature(LA_X, LA_Y, LA_X + 1000, LA_Y)

        # Act
        inner, *_ = clip_to_buffer_rings(features, _source(), [200, 1320])

        # Assert
        original_length = features.geometry.length.iloc[0]
        assert len(inner) == 1
        assert inner.geometry.length.iloc[0] < original_length

    def test_line_fully_outside_all_rings_excluded(self):
        # Arrange
        features = _line_feature(LA_X + 3000, LA_Y, LA_X + 4000, LA_Y)

        # Act
        inner, outer = clip_to_buffer_rings(features, _source(), [200, 2640])

        # Asser
        assert len(inner) == 0
        assert len(outer) == 0

    def test_polygon_inside_inner_ring_fully_included(self):
        # Arrange
        features = _polygon_feature(LA_X + 50, LA_Y, half=10)

        # Act
        inner, *_ = clip_to_buffer_rings(features, _source(), [200, 1320])

        # Asser
        assert len(inner) == 1
        assert inner.geometry.area.iloc[0] == pytest.approx(features.geometry.area.iloc[0], rel=0.01)

    def test_polygon_straddling_inner_boundary_is_clipped(self):
        # Arrange
        features = _polygon_feature(LA_X, LA_Y, half=500)

        # Act
        inner, *_ = clip_to_buffer_rings(features, _source(), [200, 1320])

        # Assert
        assert inner.geometry.area.iloc[0] < features.geometry.area.iloc[0]

    def test_polygon_outside_all_rings_excluded(self):
        # Arrange
        features = _polygon_feature(LA_X + 5000, LA_Y, half=100)

        # Act
        inner, outer = clip_to_buffer_rings(features, _source(), [200, 2640])

        # Asser
        assert len(inner) == 0
        assert len(outer) == 0


class TestClipToBufferRingsDonutShape:
    """Donut rings have boundaries at the expected distances."""

    def test_inner_ring_radius_matches_buffer_distance(self):
        # Arrange
        features = _polygon_feature(LA_X, LA_Y, half=3000)

        # Act
        inner, *_ = clip_to_buffer_rings(features, _source(), [200, 1320, 2640])

        # Assert
        expected_area = 3.14159 * 200**2
        assert inner.geometry.area.iloc[0] == pytest.approx(expected_area, rel=0.01)

    def test_donut_excludes_inner_buffer_area(self):
        # Arrange
        features = _polygon_feature(LA_X, LA_Y, half=3000)

        # Act
        inner, donut, *_ = clip_to_buffer_rings(features, _source(), [200, 1320, 2640])

        # Assert
        donut_union = donut.geometry.unary_union
        inner_union = inner.geometry.unary_union
        assert donut_union.intersection(inner_union).area == pytest.approx(0, abs=1)

    def test_donut_outer_boundary_matches_buffer_distance(self):
        # Arrange
        features = _polygon_feature(LA_X, LA_Y, half=3000)

        # Act
        inner, donut, *_ = clip_to_buffer_rings(features, _source(), [200, 1320, 2640])

        # Assert
        expected_area = 3.14159 * 1320**2
        combined_area = inner.geometry.area.sum() + donut.geometry.area.sum()
        assert combined_area == pytest.approx(expected_area, rel=0.01)

    def test_three_rings_are_non_overlapping_and_contiguous(self):
        # Arrange
        features = _polygon_feature(LA_X, LA_Y, half=3000)

        # Act
        inner, qtr, half = clip_to_buffer_rings(features, _source(), [200, 1320, 2640])

        # Assert
        inner_u = inner.geometry.unary_union
        qtr_u = qtr.geometry.unary_union
        half_u = half.geometry.unary_union
        assert inner_u.intersection(qtr_u).area == pytest.approx(0, abs=1)
        assert inner_u.intersection(half_u).area == pytest.approx(0, abs=1)
        assert qtr_u.intersection(half_u).area == pytest.approx(0, abs=1)

        # Assert
        expected_area = 3.14159 * 2640**2
        total_area = inner_u.area + qtr_u.area + half_u.area
        assert total_area == pytest.approx(expected_area, rel=0.01)
