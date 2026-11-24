"""Offline segment-view regressions; run with the notebooks/lts_cv uv environment."""

import ast
import contextlib
import io
import json
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import geopandas as gpd
from PIL import Image
from shapely.geometry import LineString, MultiLineString, Point

SOURCE = Path(__file__).resolve().parents[1] / "notebooks/lts_cv/lts_vlm.py"
METRIC_CRS = "EPSG:32611"
ORIGIN = (500_000.0, 3_760_000.0)


def load_segment_functions():
    """Load helpers, not notebook execution, credentials, data, or VLM weights."""
    source = SOURCE.read_text()
    lines = source.splitlines()
    start = next(i for i, line in enumerate(lines, 1) if line == '# %% tags=["mapillary_segments"]')
    end = next((i for i in range(start + 1, len(lines) + 1)
                if lines[i - 1].startswith("# %%")), len(lines) + 1)
    selected = []
    constants = {}
    for node in ast.parse(source, filename=str(SOURCE)).body:
        if isinstance(node, (ast.Import, ast.ImportFrom)) and start < node.lineno < end:
            selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name.startswith("segment_"):
            selected.append(node)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            name = getattr(node.targets[0], "id", None)
            if name in {"SEGMENT_CONFIG", "SEGMENT_VIEW_OFFSETS"}:
                selected.append(node)
            elif name in {"SEGMENT_PALETTE", "SEGMENT_EXTENSIONS", "SEGMENT_PREVIEW_CACHE"}:
                constants[name] = ast.literal_eval(node.value)
    namespace = {"__name__": "segment_test_helpers", **constants}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace


HELPERS = load_segment_functions()


def metric_line(*points):
    return LineString([(ORIGIN[0] + x, ORIGIN[1] + y) for x, y in points])


def image_row(image_id, x, y, **fields):
    point = gpd.GeoSeries([Point(ORIGIN[0] + x, ORIGIN[1] + y)], crs=METRIC_CRS).to_crs(4326).iloc[0]
    return {
        "id": image_id,
        "geometry": {"type": "Point", "coordinates": [point.x, point.y]},
        "compass_angle": 0,
        "thumb_original_url": f"https://images.invalid/{image_id}.jpg",
        **fields,
    }


def supported_image_rows(image_id, x, y, *, dx=0, dy=6, **fields):
    """Give one downloadable image real, timestamped sequence support on either side."""
    sequence = fields.pop("sequence", f"sequence-{image_id}")
    return [
        image_row(image_id, x, y, sequence=sequence, captured_at=2000, **fields),
        image_row(f"{image_id}-before", x - dx, y - dy, sequence=sequence,
                  captured_at=1000, thumb_original_url=None),
        image_row(f"{image_id}-after", x + dx, y + dy, sequence=sequence,
                  captured_at=3000, thumb_original_url=None),
    ]


def sequence_rows(sequence, points, *, times=None, **fields):
    if times is None:
        times = range(1000, 1000 * (len(points) + 1), 1000)
    return [image_row(f"{sequence}-{index}", x, y, sequence=sequence, captured_at=timestamp, **fields)
            for index, ((x, y), timestamp) in enumerate(zip(points, times, strict=True))]


def candidate(image_id, along=17.5, heading=0.0, **fields):
    return {
        "image_id": image_id, "along_m": along, "lateral_m": 3.0,
        "signed_lateral_m": 3.0, "road_side": "left", "bearing_deg": 0.0,
        "heading_deg": heading, "alignment_deg": 0.0, "is_pano": False,
        "travel_direction": None, "captured_at_ms": None, "sequence": None,
        "lon": -117.0, "lat": 34.0,
        "urls": [("original", f"https://images.invalid/{image_id}.jpg")],
        **fields,
    }


def striped_panorama(path):
    """Center, right, seam, left encode relative yaw 0, 90, 180, 270."""
    image = Image.new("RGB", (720, 360), "blue")
    image.paste((255, 255, 0), (90, 0, 270, 360))
    image.paste((255, 0, 0), (270, 0, 450, 360))
    image.paste((0, 255, 0), (450, 0, 630, 360))
    image.save(path)
    image.close()
    return path


def image_response(data, status=200):
    response = mock.MagicMock(status_code=status, headers={"Content-Type": "image/jpeg"})
    response.__enter__.return_value = response
    response.iter_content.return_value = [data]
    return response


class SegmentTestCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("requests.sessions.Session.request", side_effect=AssertionError("Network forbidden"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.enterContext(mock.patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")))

    def call(self, name, *args, **kwargs):
        return HELPERS[name](*args, **kwargs)

    def sample(self, candidates, length=35.0, spacing=25.0):
        return self.call("segment_station_sample", candidates, length, spacing)


class SegmentSelectionTests(SegmentTestCase):
    def test_loader_does_not_initialize_notebook(self):
        for name in ("SEGMENT_ROOT", "SEGMENT_RESULTS", "coverage_state", "model", "torch"):
            self.assertNotIn(name, HELPERS)

    def test_complete_lts4_exact_coverage_ranked_by_geometry_length_and_uid(self):
        cases = [
            ("z-long", 200, 4, "Complete", 100),
            ("b-tie", 80, "4", " complete ", "100"),
            ("a-tie", 80, 4, "COMPLETE", 100),
            ("short", 16.1, 4, "Complete", 100),
            ("too-short", 16, 4, "Complete", 100),
            ("wrong-lts", 300, 1, "Complete", 100),
            ("pending", 400, 4, "Pending", 100),
            ("almost", 500, 4, "Complete", 99.999),
            ("over", 600, 4, "Complete", 100.001),
        ]
        streets = gpd.GeoDataFrame([
            {"uid": uid, "LTS_ov": lts, "status": status, "coverage_pct": coverage,
             "length_m": 9999, "total_m": 9999, "covered_m": 1,
             "geometry": metric_line((0, 0), (0, length))}
            for uid, length, lts, status, coverage in cases
        ], crs=METRIC_CRS)
        original = streets.copy()
        frame, eligible = self.call("segment_candidates", streets)
        self.assertEqual(eligible["uid"].tolist(), ["z-long", "a-tie", "b-tie", "short"])
        self.assertEqual(frame.loc[0, "length_m"], 200)
        self.assertTrue(streets.equals(original), "Selection must not mutate the coverage snapshot")

    def test_missing_status_or_coverage_is_not_complete(self):
        streets = gpd.GeoDataFrame([
            {"uid": "missing", "LTS_ov": 4, "geometry": metric_line((0, 0), (0, 80))}
        ], crs=METRIC_CRS)
        self.assertTrue(self.call("segment_candidates", streets)[1].empty)


class SegmentImageCandidateTests(SegmentTestCase):
    def test_projected_limits_radius_and_malformed_rows(self):
        segment = SimpleNamespace(geometry=metric_line((0, 0), (0, 100)))
        rows = [row for image_id, x, y in [("start", 0, 8), ("end", 0, 92),
                                          ("before", 0, 7.99), ("after", 0, 92.01),
                                          ("near", 24.99, 50), ("edge", 25, 50), ("far", 25.01, 50)]
                for row in supported_image_rows(image_id, x, y)]
        rows.extend([{"id": "invalid", "geometry": {"type": "Point", "coordinates": [999, 999]}},
                     image_row("", 0, 50)])
        candidates, mapped, rejected = self.call("segment_image_candidates", segment, rows)
        self.assertEqual(mapped, 21)
        self.assertGreaterEqual(rejected, 3)
        self.assertEqual({item["image_id"] for item in candidates if item["urls"]},
                         {"start", "end", "near", "edge"})
        positions = {item["image_id"]: item["along_m"] for item in candidates}
        self.assertAlmostEqual(positions["start"], 8, places=6)
        self.assertAlmostEqual(positions["end"], 92, places=6)

    def test_signed_sides_and_heading_fallback(self):
        segment = SimpleNamespace(geometry=metric_line((0, 0), (0, 100)))
        rows = [*supported_image_rows("left", -3, 50, computed_compass_angle=450),
                *supported_image_rows("right", 3, 50, computed_compass_angle="bad", compass_angle=-90),
                *supported_image_rows("center", 0.5, 50, compass_angle=None)]
        candidates, _, _ = self.call("segment_image_candidates", segment, rows)
        by_id = {item["image_id"]: item for item in candidates}
        self.assertEqual([by_id[key]["road_side"] for key in ("left", "right", "center")],
                         ["left", "right", "center"])
        self.assertAlmostEqual(by_id["left"]["signed_lateral_m"], 3, places=6)
        self.assertAlmostEqual(by_id["right"]["signed_lateral_m"], -3, places=6)
        self.assertAlmostEqual(by_id["left"]["bearing_deg"], 0, places=6)
        self.assertEqual(by_id["left"]["heading_deg"], 90)
        self.assertEqual(by_id["right"]["heading_deg"], 270)
        self.assertIsNone(by_id["center"]["heading_deg"])

    def test_reversing_geometry_reverses_sides_bearing_and_travel(self):
        rows = sequence_rows("sequence", [(-3, 20), (-3, 45), (-3, 70)])
        for row in rows:
            row["sequence"] = {"id": "sequence"}
        rows.reverse()
        for points, bearing, side, travel in [
            (((0, 0), (0, 100)), 0, "left", "forward"),
            (((0, 100), (0, 0)), 180, "right", "backward"),
        ]:
            with self.subTest(travel=travel):
                segment = SimpleNamespace(geometry=metric_line(*points))
                candidates, _, _ = self.call("segment_image_candidates", segment, rows)
                self.assertEqual({item["travel_direction"] for item in candidates}, {travel})
                self.assertEqual({item["road_side"] for item in candidates}, {side})
                for item in candidates:
                    self.assertAlmostEqual(item["bearing_deg"], bearing, places=6)

    def test_curved_geometry_uses_local_tangent(self):
        line = metric_line((0, 0), (0, 50), (50, 50))
        for geometry in (line, MultiLineString([line])):
            with self.subTest(geometry=geometry.geom_type):
                segment = SimpleNamespace(geometry=geometry)
                rows = supported_image_rows("bend", 25, 53, dx=6, dy=0)
                candidates, _, _ = self.call("segment_image_candidates", segment, rows)
                image = next(item for item in candidates if item["image_id"] == "bend")
                self.assertAlmostEqual(image["along_m"], 75, places=6)
                self.assertAlmostEqual(image["bearing_deg"], 90, places=6)
                self.assertEqual(image["road_side"], "left")
                self.assertAlmostEqual(image["signed_lateral_m"], 3, places=6)

    def test_unknown_sparse_and_stationary_sequences_are_rejected(self):
        rows = [image_row("a", 0, 20, sequence="a", captured_at=1000),
                image_row("b", 0, 80, sequence="b", captured_at=2000),
                image_row("still1", 0, 40, sequence="still", captured_at=1000),
                image_row("still2", 0, 40.5, sequence="still", captured_at=2000),
                image_row("still3", 0, 40.2, sequence="still", captured_at=3000),
                image_row("no-time", 0, 70, sequence="a"),
                image_row("no-sequence", 0, 50, captured_at=1000)]
        segment = SimpleNamespace(geometry=metric_line((0, 0), (0, 100)))
        issues = []
        candidates, _, rejected = self.call("segment_image_candidates", segment, rows, issues=issues)
        self.assertEqual(candidates, [])
        self.assertEqual(rejected, len(rows))
        self.assertEqual({issue["image_id"] for issue in issues}, {row["id"] for row in rows})
        self.assertTrue(all(issue.get("reason") or issue.get("trajectory_status") for issue in issues))

    def test_matched_trajectory_metrics_and_backward_camera_do_not_confuse_motion(self):
        segment = SimpleNamespace(geometry=metric_line((0, 0), (0, 100)))
        rows = supported_image_rows("rear-camera", -3, 50, compass_angle=180)
        candidates, _, _ = self.call("segment_image_candidates", segment, rows)
        image = next(item for item in candidates if item["image_id"] == "rear-camera")
        self.assertEqual(image["trajectory_status"], "matched")
        self.assertEqual(image["travel_direction"], "forward")
        self.assertEqual(image["heading_deg"], 180)
        self.assertAlmostEqual(image["trajectory_heading_deg"], 0, places=5)
        self.assertAlmostEqual(image["trajectory_alignment_deg"], 0, places=5)
        self.assertAlmostEqual(image["trajectory_baseline_m"], 12, places=5)
        self.assertEqual(image["trajectory_point_count"], 3)
        views, _ = self.sample([image], length=100)
        self.assertEqual([view["view_direction"] for view in views], ["backward"])
        self.assertEqual(views[0]["camera_facing"], "backward")

    def test_trajectory_heading_tolerance_has_an_inclusive_thirty_degree_boundary(self):
        segment = SimpleNamespace(geometry=metric_line((0, 0), (0, 100)))
        for angle, expected in [(29.99, True), (30, True), (30.01, False)]:
            with self.subTest(angle=angle):
                dx, dy = 6 * math.sin(math.radians(angle)), 6 * math.cos(math.radians(angle))
                rows = supported_image_rows("angled", 0, 50, dx=dx, dy=dy)
                candidates, _, _ = self.call("segment_image_candidates", segment, rows)
                self.assertEqual("angled" in {item["image_id"] for item in candidates}, expected)

    def test_crossing_freeway_sequence_cannot_be_rescued_by_north_facing_camera(self):
        segment = SimpleNamespace(geometry=metric_line((0, 0), (0, 100)))
        rows = sequence_rows("westbound-freeway", [(12, 50), (0, 50), (-12, 50)],
                             compass_angle=0, is_pano=True)
        issues = []
        candidates, _, rejected = self.call("segment_image_candidates", segment, rows, issues=issues)
        self.assertEqual(candidates, [])
        self.assertEqual(rejected, 3)
        self.assertEqual({issue["image_id"] for issue in issues}, {row["id"] for row in rows})
        self.assertTrue(all(issue.get("trajectory_status") != "matched" for issue in issues))
        self.assertTrue(any(issue.get("trajectory_alignment_deg", 0) > 30 for issue in issues))

    def test_computed_road_alignment_cannot_hide_original_gps_freeway_trajectory(self):
        segment = SimpleNamespace(geometry=metric_line((0, 0), (0, 100)))
        rows = sequence_rows("gps-freeway", [(6, 44), (0, 50), (-6, 56)], compass_angle=0, is_pano=True)
        for row, y in zip(rows, [44, 50, 56], strict=True):
            row["computed_geometry"] = image_row("computed", 0, y)["geometry"]
        issues = []
        candidates, _, _ = self.call("segment_image_candidates", segment, rows, issues=issues)
        self.assertEqual(candidates, [])
        center = next(issue for issue in issues if issue["image_id"] == "gps-freeway-1")
        self.assertNotEqual(center["trajectory_status"], "matched")
        self.assertGreater(center["trajectory_original_alignment_deg"], 30)

    def test_original_and_computed_trajectories_must_agree_on_travel_direction(self):
        segment = SimpleNamespace(geometry=metric_line((0, 0), (0, 100)))
        rows = sequence_rows("opposite-poses", [(0, 56), (0, 50), (0, 44)])
        for row, y in zip(rows, [44, 50, 56], strict=True):
            row["computed_geometry"] = image_row("computed", 0, y)["geometry"]
        issues = []
        candidates, _, _ = self.call("segment_image_candidates", segment, rows, issues=issues)
        self.assertEqual(candidates, [])
        center = next(issue for issue in issues if issue["image_id"] == "opposite-poses-1")
        self.assertEqual(center["trajectory_original_status"], "matched")
        self.assertNotEqual(center["trajectory_status"], "matched")

    def test_computed_only_sequence_marks_original_trajectory_unavailable(self):
        segment = SimpleNamespace(geometry=metric_line((0, 0), (0, 100)))
        rows = supported_image_rows("computed-only", 0, 50)
        for row in rows:
            row["computed_geometry"] = row.pop("geometry")
        candidates, _, _ = self.call("segment_image_candidates", segment, rows)
        center = next(item for item in candidates if item["image_id"] == "computed-only")
        self.assertEqual(center["trajectory_status"], "matched")
        self.assertTrue(center["trajectory_original_status"])
        self.assertNotEqual(center["trajectory_original_status"], "matched")

    def test_real_freeway_capture_cannot_match_computed_glendale_trajectory(self):
        # Fixed audit coordinates, not a network fixture: SfM and original GPS disagree.
        captures = [
            ("actual-before", 1422408164000, [-118.266329, 34.112723], [-118.26640499457, 34.112695120724]),
            ("977625619674484", 1422408166000, [-118.266739, 34.113053], [-118.26609011439, 34.11310960206]),
            ("actual-after", 1422408168000, [-118.267113, 34.113423], [-118.26557267904, 34.11338409527]),
        ]
        rows = [{"id": image_id, "captured_at": timestamp, "sequence": "v3f2q9zymj3jb8ix1hnyrl",
                 "geometry": {"type": "Point", "coordinates": original},
                 "computed_geometry": {"type": "Point", "coordinates": computed}, "compass_angle": 39.39}
                for image_id, timestamp, original, computed in captures]
        center = gpd.GeoSeries([Point(captures[1][3])], crs=4326).to_crs(METRIC_CRS).iloc[0]
        dx, dy = 100 * math.sin(math.radians(39.39)), 100 * math.cos(math.radians(39.39))
        segment = SimpleNamespace(geometry=LineString([(center.x - dx, center.y - dy),
                                                       (center.x + dx, center.y + dy)]))
        issues = []
        candidates, _, _ = self.call("segment_image_candidates", segment, rows, issues=issues)
        self.assertNotIn("977625619674484", {item["image_id"] for item in candidates})
        issue = next(item for item in issues if item["image_id"] == "977625619674484")
        self.assertLess(issue["trajectory_alignment_deg"], 30)
        self.assertGreater(issue["trajectory_original_alignment_deg"], 30)
        self.assertNotEqual(issue["trajectory_original_status"], "matched")

    def test_turn_and_reversal_are_rejected_even_when_endpoints_progress_along_road(self):
        segment = SimpleNamespace(geometry=metric_line((0, 0), (0, 100)))
        for points in [[(0, 30), (12, 42), (0, 54)], [(0, 30), (0, 50), (0, 40)]]:
            with self.subTest(points=points):
                candidates, _, _ = self.call("segment_image_candidates", segment,
                                             sequence_rows("turn", points))
                self.assertEqual(candidates, [])

    def test_context_outside_radius_is_not_filtered_before_trajectory_validation(self):
        segment = SimpleNamespace(geometry=metric_line((0, 0), (0, 100)))
        rows = sequence_rows("outside-radius", [(0, 44), (0, 50), (0, 56), (35, 56)])
        candidates, _, _ = self.call("segment_image_candidates", segment, rows)
        self.assertNotIn("outside-radius-1", {item["image_id"] for item in candidates})

    def test_context_inside_endpoint_setback_is_not_filtered_before_validation(self):
        segment = SimpleNamespace(geometry=metric_line((0, 0), (0, 100)))
        rows = sequence_rows("outside-setback", [(10, 4), (0, 10), (0, 16), (0, 22)])
        candidates, _, _ = self.call("segment_image_candidates", segment, rows)
        self.assertNotIn("outside-setback-1", {item["image_id"] for item in candidates})

    def test_straight_endpoint_extensions_supply_support_beyond_image_radius(self):
        segment = SimpleNamespace(geometry=metric_line((0, 0), (0, 35)))
        rows = supported_image_rows("short-road", 0, 17.5, dy=45)
        candidates, _, _ = self.call("segment_image_candidates", segment, rows)
        self.assertEqual([item["image_id"] for item in candidates], ["short-road"])
        self.assertEqual(candidates[0]["trajectory_point_count"], 3)
        self.assertAlmostEqual(candidates[0]["trajectory_baseline_m"], 90, places=5)

    def test_duplicate_ids_or_timestamps_do_not_manufacture_three_observations(self):
        segment = SimpleNamespace(geometry=metric_line((0, 0), (0, 100)))
        cases = [sequence_rows("same-time", [(0, 40), (0, 50), (0, 60)], times=[1000, 1000, 1000]),
                 sequence_rows("two-times", [(0, 40), (0, 50), (0, 60)], times=[1000, 1000, 2000])]
        duplicate = image_row("duplicate", 0, 50, sequence="duplicate", captured_at=2000)
        cases.append([image_row("before", 0, 40, sequence="duplicate", captured_at=1000),
                      duplicate, duplicate.copy(), duplicate.copy()])
        for rows in cases:
            with self.subTest(ids=[row["id"] for row in rows]):
                candidates, _, _ = self.call("segment_image_candidates", segment, rows)
                self.assertEqual(candidates, [])

    def test_successful_sequence_and_candidate_ids_are_deduplicated(self):
        segment = SimpleNamespace(geometry=metric_line((0, 0), (0, 100)))
        rows = supported_image_rows("dedup", 0, 50)
        candidates, _, _ = self.call("segment_image_candidates", segment, rows + rows)
        self.assertEqual(len(candidates), 3)
        self.assertEqual(len({item["image_id"] for item in candidates}), 3)
        self.assertEqual({item["trajectory_point_count"] for item in candidates}, {3})

    def test_conflicting_same_timestamp_positions_cannot_be_hidden_by_input_order(self):
        segment = SimpleNamespace(geometry=metric_line((0, 0), (0, 100)))
        rows = sequence_rows("conflict", [(0, 0), (0, 20), (40, 20), (0, 40)],
                             times=[1000, 2000, 2000, 3000])
        for ordered in (rows, list(reversed(rows))):
            with self.subTest(ids=[row["id"] for row in ordered]):
                candidates, _, _ = self.call("segment_image_candidates", segment, ordered)
                self.assertNotIn("conflict-1", {item["image_id"] for item in candidates})

    def test_short_baseline_and_long_time_gap_cannot_supply_support(self):
        segment = SimpleNamespace(geometry=metric_line((0, 0), (0, 100)))
        cases = [sequence_rows("short", [(0, 40), (0, 44), (0, 48)]),
                 sequence_rows("gap", [(0, 40), (0, 50), (0, 60)], times=[1000, 122000, 123000])]
        for rows in cases:
            with self.subTest(sequence=rows[0]["sequence"]):
                candidates, _, _ = self.call("segment_image_candidates", segment, rows)
                self.assertEqual(candidates, [])

    def test_ten_metre_baseline_and_sixty_metre_context_boundaries_are_inclusive(self):
        segment = SimpleNamespace(geometry=metric_line((0, 0), (0, 100)))
        for step, expected in [(4.99, False), (5, True), (60, True), (60.01, False)]:
            with self.subTest(step=step):
                rows = supported_image_rows("boundary", 0, 50, dy=step)
                candidates, _, _ = self.call("segment_image_candidates", segment, rows)
                self.assertEqual("boundary" in {item["image_id"] for item in candidates}, expected)

    def test_time_context_is_measured_from_anchor_not_just_between_neighbors(self):
        segment = SimpleNamespace(geometry=metric_line((0, 0), (0, 100)))
        for step_ms, expected in [(100000, ["time-context-1"]), (120000, ["time-context-1"]), (120001, [])]:
            with self.subTest(step_ms=step_ms):
                rows = sequence_rows("time-context", [(0, 40), (0, 50), (0, 60)],
                                     times=[1000, 1000 + step_ms, 1000 + 2 * step_ms])
                candidates, _, _ = self.call("segment_image_candidates", segment, rows)
                self.assertEqual([item["image_id"] for item in candidates], expected)

    def test_missing_or_invalid_capture_time_never_supplies_trajectory_support(self):
        segment = SimpleNamespace(geometry=metric_line((0, 0), (0, 100)))
        for timestamp in (None, "invalid", float("nan"), float("inf")):
            with self.subTest(timestamp=timestamp):
                rows = supported_image_rows("invalid-time", 0, 50)
                rows[0]["captured_at"] = timestamp
                candidates, _, _ = self.call("segment_image_candidates", segment, rows)
                self.assertEqual(candidates, [])

    def test_support_cannot_be_borrowed_from_another_sequence(self):
        segment = SimpleNamespace(geometry=metric_line((0, 0), (0, 100)))
        rows = [image_row(str(index), 0, y, sequence=str(index), captured_at=1000 * (index + 1))
                for index, y in enumerate([40, 50, 60])]
        candidates, _, _ = self.call("segment_image_candidates", segment, rows)
        self.assertEqual(candidates, [])

    def test_turn_beyond_local_distance_or_time_context_does_not_poison_anchor(self):
        segment = SimpleNamespace(geometry=metric_line((0, 0), (0, 200)))
        cases = [sequence_rows("far", [(0, 44), (0, 50), (0, 56), (0, 120), (10, 56)]),
                 sequence_rows("late", [(0, 44), (0, 50), (0, 56), (10, 56)],
                               times=[1000, 2000, 3000, 130000])]
        for rows in cases:
            with self.subTest(sequence=rows[0]["sequence"]):
                candidates, _, _ = self.call("segment_image_candidates", segment, rows)
                by_id = {item["image_id"]: item for item in candidates}
                anchor = by_id[rows[1]["id"]]
                self.assertEqual(anchor["trajectory_point_count"], 3)
                self.assertEqual(anchor["trajectory_status"], "matched")

    def test_trajectory_follows_changing_local_road_tangents(self):
        points = [(0, 0), (0, 20), (2, 30), (7, 40), (15, 48), (25, 53), (35, 55)]
        segment = SimpleNamespace(geometry=metric_line(*points))
        rows = sequence_rows("curve", points)
        candidates, _, _ = self.call("segment_image_candidates", segment, rows)
        by_id = {item["image_id"]: item for item in candidates}
        self.assertIn("curve-3", by_id)
        self.assertEqual(by_id["curve-3"]["trajectory_status"], "matched")
        self.assertEqual(by_id["curve-3"]["travel_direction"], "forward")

    def test_straight_sequence_shortcut_across_road_detours_is_rejected(self):
        segment = SimpleNamespace(geometry=metric_line((0, 0), (0, 40), (20, 40), (20, 0),
                                                       (20, -40), (40, -40), (40, 0)))
        rows = sequence_rows("shortcut", [(0, 0), (20, 0), (40, 0)], compass_angle=90)
        candidates, _, _ = self.call("segment_image_candidates", segment, rows)
        self.assertNotIn("shortcut-1", {item["image_id"] for item in candidates})


class SegmentStationTests(SegmentTestCase):
    def test_pano_supplies_only_two_distinct_view_copies_without_mutating_source(self):
        source = candidate("pano", heading=90, is_pano=True)
        before = source.copy()
        views, gaps = self.sample([source])
        self.assertFalse(gaps)
        self.assertEqual(HELPERS["SEGMENT_VIEW_OFFSETS"], {"forward": 0, "backward": 180})
        self.assertEqual({view["view_direction"]: view["view_heading_deg"] for view in views},
                         {"forward": 0, "backward": 180})
        self.assertEqual({view["camera_facing"] for view in views}, {"unknown"})
        self.assertTrue(all(view is not source for view in views))
        self.assertEqual(source, before)

    def test_nonpano_heading_tolerance_is_inclusive_and_directional(self):
        for heading, expected in [(30, {"forward"}), (330, {"forward"}),
                                  (30.01, set()), (180, {"backward"}),
                                  (270, set()), (90, set())]:
            with self.subTest(heading=heading):
                views, _ = self.sample([candidate("image", heading=heading)])
                self.assertEqual({view["view_direction"] for view in views}, expected)
                for view in views:
                    self.assertEqual(view["view_heading_deg"], heading)

    def test_unavailable_urls_or_heading_cannot_supply_even_panorama_views(self):
        sources = [candidate("no-url", is_pano=True, urls=[]),
                   candidate("no-heading", heading=None, is_pano=True)]
        views, gaps = self.sample(sources)
        self.assertEqual(views, [])
        self.assertEqual({gap["direction"] for gap in gaps}, set(HELPERS["SEGMENT_VIEW_OFFSETS"]))

    def test_opposite_side_pair_preferred_over_closer_same_side(self):
        views, _ = self.sample([candidate("forward", road_side="left"),
                                candidate("same", heading=180, road_side="left"),
                                candidate("opposite", along=11, heading=180, road_side="right")])
        self.assertEqual({view["image_id"] for view in views}, {"forward", "opposite"})

    def test_same_side_pair_is_valid_fallback(self):
        views, gaps = self.sample([candidate("forward"), candidate("backward", heading=180)])
        self.assertEqual({view["view_direction"] for view in views}, {"forward", "backward"})
        self.assertFalse(gaps)

    def test_panorama_preferred_when_road_side_and_camera_facing_are_comparable(self):
        sources = [candidate("ordinary-forward"), candidate("ordinary-backward", heading=180),
                   candidate("pano", along=20, is_pano=True)]
        views, gaps = self.sample(sources)
        self.assertEqual({view["image_id"] for view in views}, {"pano"})
        self.assertEqual({view["view_direction"] for view in views}, {"forward", "backward"})
        self.assertFalse(gaps)

    def test_panorama_preference_does_not_override_opposite_sides_or_vehicle_forward_facing(self):
        for sources, direction, expected in [
            ([candidate("pano", is_pano=True), candidate("opposite", heading=180, road_side="right")],
             "backward", "opposite"),
            ([candidate("pano", is_pano=True, travel_direction="backward"),
              candidate("front", travel_direction="forward")], "forward", "front"),
        ]:
            with self.subTest(direction=direction, expected=expected):
                views, _ = self.sample(sources)
                by_direction = {view["view_direction"]: view for view in views}
                self.assertEqual(by_direction[direction]["image_id"], expected)

    def test_vehicle_forward_facing_preferred_and_inferred_for_both_directions(self):
        sources = [candidate("rear", travel_direction="backward"),
                   candidate("front", along=11, travel_direction="forward"),
                   candidate("backward-front", heading=180, road_side="right", travel_direction="backward")]
        views, _ = self.sample(sources)
        self.assertEqual({view["image_id"] for view in views}, {"front", "backward-front"})
        self.assertEqual({view["camera_facing"] for view in views}, {"forward"})

    def test_inclusive_eight_metre_endpoint_setbacks(self):
        sources = [candidate("before", along=7.99, is_pano=True),
                   candidate("start", along=8, is_pano=True),
                   candidate("end", along=53, is_pano=True),
                   candidate("after", along=53.01, is_pano=True)]
        views, gaps = self.sample(sources, length=61)
        self.assertEqual({view["image_id"] for view in views}, {"start", "end"})
        self.assertFalse(gaps)
        self.assertTrue(all(8 <= view["along_m"] <= 53 for view in views))
        self.assertEqual(self.call("segment_direction_gaps", views, 61, 25), gaps)

    def test_dense_sources_make_one_view_per_direction_at_least_25_m_apart(self):
        sources = [candidate(str(along), along=along, is_pano=True) for along in range(8, 93)]
        views, gaps = self.sample(sources, length=100)
        self.assertFalse(gaps)
        self.assertEqual(sorted({view["sample_station_m"] for view in views}), [8, 33, 58, 83])
        self.assertEqual(len(views), 8)
        for direction in HELPERS["SEGMENT_VIEW_OFFSETS"]:
            positions = [view["along_m"] for view in views if view["view_direction"] == direction]
            self.assertTrue(all(b - a >= 25 for a, b in zip(positions[:-1], positions[1:], strict=True)))

    def test_sparse_sets_allow_gaps_over_25_m_without_extra_fill_images(self):
        sources = [candidate("first", along=8, is_pano=True), candidate("last", along=90, is_pano=True)]
        views, gaps = self.sample(sources, length=100)
        self.assertEqual(len(views), 4)
        self.assertFalse(gaps)

    def test_entire_sets_are_separated_when_opposite_side_cameras_are_offset(self):
        sources = [candidate("forward", along=8, travel_direction="forward"),
                   candidate("backward", along=15, heading=180, road_side="right"),
                   candidate("near", along=33, is_pano=True), candidate("next", along=60, is_pano=True)]
        views, _ = self.sample(sources, length=100)
        stations = sorted({view["sample_station_m"] for view in views})
        self.assertEqual(len(stations), 2)
        for first, second in zip(stations[:-1], stations[1:], strict=True):
            previous = max(view["along_m"] for view in views if view["sample_station_m"] == first)
            following = min(view["along_m"] for view in views if view["sample_station_m"] == second)
            self.assertGreaterEqual(following - previous, 25)

    def test_passed_spacing_controls_sampling_and_gap_detection(self):
        sources = [candidate(str(along), along=along, is_pano=True) for along in (8, 18, 28, 38)]
        views, gaps = self.sample(sources, length=50, spacing=10)
        self.assertFalse(gaps)
        for direction in HELPERS["SEGMENT_VIEW_OFFSETS"]:
            positions = sorted(view["along_m"] for view in views if view["view_direction"] == direction)
            self.assertGreaterEqual(min(b - a for a, b in zip(positions[:-1], positions[1:], strict=True)), 10)

    def test_missing_delivery_retains_expected_stations_and_flags_close_photos(self):
        views = [{**candidate("a", along=8), "view_direction": "forward", "sample_station_m": 8},
                 {**candidate("b", along=20), "view_direction": "forward", "sample_station_m": 20}]
        gaps = self.call("segment_direction_gaps", views, 100, 25, stations=[8, 20, 60])
        self.assertTrue(any(gap["reason"] == "directional spacing below 25 m" for gap in gaps))
        self.assertEqual({gap["direction"] for gap in gaps if gap["start_m"] == 60},
                         set(HELPERS["SEGMENT_VIEW_OFFSETS"]))

    def test_invalid_sampling_dimensions(self):
        for length, spacing in [(16, 25), (15, 25), (100, 0), (100, -1)]:
            with self.subTest(length=length, spacing=spacing), self.assertRaises(ValueError):
                self.sample([], length=length, spacing=spacing)


class SegmentPhotoTableTests(SegmentTestCase):
    def test_two_ordered_photo_columns_group_sets_and_sort_stations(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "photo.png"
            Image.new("RGB", (8, 8), "red").save(path)
            records = [{**candidate(f"{station}-{direction}", along=station),
                        "segment_uid": "road<script>", "sample_station_m": station,
                        "view_direction": direction, "view_heading_deg": heading, "path": str(path)}
                       for station in (33, 8)
                       for direction, heading in reversed(HELPERS["SEGMENT_VIEW_OFFSETS"].items())]
            table = self.call("segment_photo_table", records).data
        headings = [table.index(f"<th>{direction.title()}</th>")
                    for direction in HELPERS["SEGMENT_VIEW_OFFSETS"]]
        self.assertEqual(headings, sorted(headings))
        self.assertEqual(table.count("<img "), 4)
        self.assertNotIn("<th>Left</th>", table)
        self.assertNotIn("<th>Right</th>", table)
        self.assertEqual(table.count("<tr>"), 3)
        self.assertLess(table.index("<td>8.0</td>"), table.index("<td>33.0</td>"))
        self.assertIn("road&lt;script&gt;", table)
        self.assertNotIn("road<script>", table)
        self.assertIn("Road forward 0.0 deg", table)

    def test_missing_and_failed_views_are_explicit(self):
        record = {**candidate("failed"), "segment_uid": "road", "sample_station_m": 8,
                  "view_direction": "forward", "path": None, "error": "bad <download>"}
        table = self.call("segment_photo_table", [record]).data
        self.assertEqual(table.count("<b>Missing</b>"), 1)
        self.assertIn("Unavailable", table)
        self.assertIn("bad &lt;download&gt;", table)
        self.assertIn("No images downloaded", self.call("segment_photo_table", []).data)


class SegmentPerspectiveTests(SegmentTestCase):
    def test_default_views_are_1600_pixel_wide_widescreen_with_120_degree_horizontal_fov(self):
        self.assertEqual(HELPERS["SEGMENT_CONFIG"]["view_size_px"], 1600)
        self.assertEqual(HELPERS["SEGMENT_CONFIG"]["view_aspect_ratio"], 16 / 9)
        self.assertEqual(HELPERS["SEGMENT_CONFIG"]["view_fov_deg"], 120)

    def test_cardinal_crop_centers_and_rotated_panorama_heading(self):
        colors = {0: (255, 0, 0), 90: (0, 255, 0), 180: (0, 0, 255), 270: (255, 255, 0)}
        headings = {"forward": 0, "right": 90, "backward": 180, "left": 270}
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(HELPERS["SEGMENT_CONFIG"], view_size_px=65):
            directory = Path(temporary)
            source = striped_panorama(directory / "panorama.png")
            for source_heading in (0, 90):
                paths = []
                for direction, target_heading in headings.items():
                    with self.subTest(source_heading=source_heading, direction=direction):
                        view = candidate("pano", heading=source_heading, is_pano=True,
                                         view_direction=direction, view_heading_deg=target_heading)
                        path = self.call("segment_perspective_view", source, view, directory / str(source_heading))
                        paths.append(path)
                        self.assertIsInstance(path, Path)
                        self.assertEqual(path.name, f"pano_{direction}.jpg")
                        with Image.open(path) as crop:
                            self.assertEqual(crop.size, (65, 37))
                            center = crop.getpixel((32, 18))
                        expected = colors[(target_heading - source_heading) % 360]
                        self.assertTrue(all(abs(a - b) <= 10 for a, b in zip(center, expected, strict=True)),
                                        f"Center {center} differs from {expected}")
                self.assertEqual(len(set(paths)), 4)

    def test_wider_horizontal_fov_includes_more_roadside_context(self):
        view = candidate("wide", is_pano=True, view_direction="forward", view_heading_deg=0)
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(HELPERS["SEGMENT_CONFIG"], view_size_px=65):
            directory = Path(temporary)
            source = striped_panorama(directory / "panorama.png")
            wide = self.call("segment_perspective_view", source, view, directory / "wide")
            with mock.patch.dict(HELPERS["SEGMENT_CONFIG"], view_fov_deg=90):
                narrow = self.call("segment_perspective_view", source, view, directory / "narrow")
            with Image.open(wide) as crop:
                wide_edges = [crop.getpixel((x, 18)) for x in (0, 64)]
            with Image.open(narrow) as crop:
                narrow_edges = [crop.getpixel((x, 18)) for x in (0, 64)]
        for actual, expected in zip(wide_edges, [(255, 255, 0), (0, 255, 0)], strict=True):
            self.assertTrue(all(abs(a - b) <= 15 for a, b in zip(actual, expected, strict=True)))
        self.assertTrue(all(red > 230 and green < 25 for red, green, _ in narrow_edges))

    def test_widescreen_aspect_reduces_vertical_fov_without_stretching_projection(self):
        view = candidate("latitude", is_pano=True, view_direction="forward", view_heading_deg=0)
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(HELPERS["SEGMENT_CONFIG"], view_size_px=65):
            directory = Path(temporary)
            source = directory / "latitude.png"
            with Image.linear_gradient("L").resize((720, 360)).convert("RGB") as image:
                image.save(source)
            wide = self.call("segment_perspective_view", source, view, directory / "wide")
            with mock.patch.dict(HELPERS["SEGMENT_CONFIG"], view_aspect_ratio=1):
                square = self.call("segment_perspective_view", source, view, directory / "square")
            with Image.open(wide) as crop:
                wide_top = crop.getpixel((32, 0))[0]
            with Image.open(square) as crop:
                self.assertEqual(crop.size, (65, 65))
                square_top = crop.getpixel((32, 0))[0]
        self.assertGreater(wide_top - square_top, 15)

    def test_nonpanorama_returns_source_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "ordinary.jpg"
            Image.new("RGB", (80, 45), "red").save(source)
            original = source.read_bytes()
            output = Path(temporary) / "views"
            result = self.call("segment_perspective_view", source, candidate("ordinary"), output)
            self.assertEqual(result, source)
            self.assertEqual(result.read_bytes(), original)
            with Image.open(result) as image:
                self.assertEqual(image.size, (80, 45))
            self.assertFalse(output.exists())

    def test_invalid_panorama_aspect_ratio_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "not-a-panorama.jpg"
            Image.new("RGB", (100, 100), "red").save(source)
            view = candidate("invalid", is_pano=True, view_direction="forward", view_heading_deg=0)
            with self.assertRaises(ValueError):
                self.call("segment_perspective_view", source, view, Path(temporary) / "views")


class SegmentDownloadTests(SegmentTestCase):
    def setUp(self):
        super().setUp()
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.sleep = self.enterContext(mock.patch.object(HELPERS["time"], "sleep"))
        self.enterContext(mock.patch.dict(HELPERS["SEGMENT_CONFIG"], download_attempts=2))
        with io.BytesIO() as buffer, Image.new("RGB", (64, 32), "red") as image:
            image.save(buffer, format="JPEG")
            self.valid = buffer.getvalue()
        # Dropping the end marker preserves headers but prevents pixel decoding.
        self.truncated = self.valid[:-2]
        with Image.open(io.BytesIO(self.truncated)) as image:
            image.verify()
        with Image.open(io.BytesIO(self.truncated)) as image, self.assertRaises(OSError):
            image.load()

    def test_cached_truncated_jpeg_is_removed_and_downloaded_again(self):
        cached = self.directory / "image.jpg"
        cached.write_bytes(self.truncated)
        session = mock.Mock()

        def replacement(*args, **kwargs):
            self.assertFalse(cached.exists(), "Undecodable cache must be removed before retry")
            return image_response(self.valid)

        session.get.side_effect = replacement
        path = self.call("segment_download_url", session, "https://images.invalid/image.jpg", self.directory, "image")
        self.assertEqual(path, cached)
        self.assertEqual(path.read_bytes(), self.valid)
        session.get.assert_called_once()
        self.sleep.assert_not_called()

    def test_new_truncated_jpeg_is_retried_before_returning_a_decodable_file(self):
        session = mock.Mock()
        session.get.side_effect = [image_response(self.truncated), image_response(self.valid)]
        path = self.call("segment_download_url", session, "https://images.invalid/image.jpg", self.directory, "image")
        self.assertEqual(path.read_bytes(), self.valid)
        with Image.open(path) as image:
            image.load()
        self.assertEqual(session.get.call_count, 2)
        self.sleep.assert_called_once_with(1)
        self.assertFalse(list(self.directory.glob("*.part")))

    def test_repeated_truncated_downloads_raise_without_poisoning_cache(self):
        session = mock.Mock()
        session.get.side_effect = [image_response(self.truncated), image_response(self.truncated)]
        with self.assertRaises(RuntimeError):
            self.call("segment_download_url", session, "https://images.invalid/image.jpg", self.directory, "image")
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(list(self.directory.iterdir()), [])
        self.sleep.assert_called_once_with(1)


class SegmentDeliveryTests(SegmentTestCase):
    def setUp(self):
        super().setUp()
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.directory = Path(temporary)
        self.source = striped_panorama(self.directory / "source.png")
        self.enterContext(mock.patch.dict(HELPERS["SEGMENT_CONFIG"], cache_dir=self.directory / "cache",
                                          view_size_px=65, request_pause_s=0))
        self.segments = gpd.GeoDataFrame([
            {"uid": "road", "geometry": metric_line((0, 0), (0, 35))}
        ], crs=METRIC_CRS)
        self.rows = supported_image_rows("pano", -3, 17.5, is_pano=True,
                                         thumb_2048_url="https://images.invalid/fallback.jpg")

    def grab(self, download, crop=None):
        overrides = {
            "segment_token": mock.Mock(return_value="offline-test-token"),
            "segment_graph_images": mock.Mock(return_value=(self.rows, 1)),
            "segment_download_url": download,
        }
        if crop is not None:
            overrides["segment_perspective_view"] = crop
        with mock.patch.dict(HELPERS, overrides), contextlib.redirect_stdout(io.StringIO()):
            records, images, issues = self.call("segment_grab_images", self.segments)
        for item in images:
            self.addCleanup(item["image"].close)
        metadata = json.loads((self.directory / "cache/road/metadata.json").read_text())
        self.assertEqual((self.directory / "cache/selected_images.csv").exists(), bool(records))
        return records, images, issues, metadata

    def test_crossing_sequence_is_rejected_before_any_image_download(self):
        self.rows = sequence_rows("freeway", [(12, 17.5), (0, 17.5), (-12, 17.5)],
                                  is_pano=True, compass_angle=0)
        download = mock.Mock(side_effect=AssertionError("Rejected images must never download"))
        records, images, issues, metadata = self.grab(download)
        download.assert_not_called()
        self.assertEqual(records, [])
        self.assertEqual(images, [])
        self.assertEqual(metadata["images"], [])
        self.assertFalse(metadata["directional_complete"])
        self.assertEqual(metadata["selected_count"], 0)
        rejections = metadata["trajectory_rejections"]
        self.assertEqual({issue["image_id"] for issue in rejections}, {row["id"] for row in self.rows})
        for issue in rejections:
            self.assertNotEqual(issue["trajectory_status"], "matched")
            self.assertEqual(issue["trajectory_point_count"], 3)
            self.assertAlmostEqual(issue["trajectory_baseline_m"], 24, places=5)
            self.assertAlmostEqual(issue["trajectory_alignment_deg"], 90, places=5)
        self.assertEqual(len([issue for issue in issues if issue["stage"] == "sequence trajectory"]), 3)
        self.assertEqual({issue["direction"] for issue in issues if issue["stage"] == "directional coverage"},
                         {"forward", "backward"})

    def test_missing_sequence_cannot_download_an_otherwise_usable_panorama(self):
        self.rows[0]["sequence"] = None
        download = mock.Mock(side_effect=AssertionError("Unvalidated images must never download"))
        records, images, issues, metadata = self.grab(download)
        download.assert_not_called()
        self.assertEqual(records, [])
        self.assertEqual(images, [])
        self.assertFalse(metadata["directional_complete"])
        self.assertTrue(any(issue.get("image_id") == "pano" for issue in issues))

    def test_original_fallback_downloaded_once_for_two_real_crops_and_trajectory_metadata(self):
        download = mock.Mock(side_effect=[RuntimeError("original unavailable"), self.source])
        records, images, issues, metadata = self.grab(download)
        self.assertEqual(download.call_count, 2)
        self.assertEqual([call.args[1] for call in download.call_args_list],
                         [self.rows[0]["thumb_original_url"], self.rows[0]["thumb_2048_url"]])
        self.assertEqual([call.args[2] for call in download.call_args_list],
                         [self.directory / "cache/road/originals" / kind for kind in ("original", "2048")])
        self.assertEqual(len(records), 2)
        self.assertEqual(len(images), 2)
        self.assertFalse(issues)
        self.assertTrue(metadata["directional_complete"])
        self.assertEqual(metadata["selection_gaps"], [])
        self.assertEqual(metadata["delivery_gaps"], [])
        self.assertEqual({record["resolution"] for record in records}, {"2048"})
        self.assertEqual({record["status"] for record in records}, {"downloaded"})
        self.assertEqual({record["original_path"] for record in records}, {str(self.source)})
        self.assertEqual(len({record["path"] for record in records}), 2)
        self.assertEqual({record["view_direction"] for record in records}, {"forward", "backward"})
        for record in records:
            self.assertTrue(Path(record["path"]).is_file())
            self.assertEqual(Path(record["path"]).name, f"pano_{record['view_direction']}.jpg")
            self.assertEqual(record["trajectory_status"], "matched")
            self.assertEqual(record["trajectory_point_count"], 3)
            self.assertAlmostEqual(record["trajectory_baseline_m"], 12, places=5)
            self.assertAlmostEqual(record["trajectory_heading_deg"], 0, places=5)
            self.assertAlmostEqual(record["trajectory_alignment_deg"], 0, places=5)
            with Image.open(record["path"]) as image:
                self.assertEqual(image.size, (65, 37))
        persisted = metadata["images"]
        self.assertEqual(len(persisted), 2)
        metrics = ["trajectory_status", "trajectory_point_count", "trajectory_baseline_m",
                   "trajectory_heading_deg", "trajectory_alignment_deg", "trajectory_original_status",
                   "trajectory_original_point_count", "trajectory_original_baseline_m",
                   "trajectory_original_heading_deg", "trajectory_original_alignment_deg"]
        columns = (self.directory / "cache/selected_images.csv").read_text().splitlines()[0].split(",")
        for saved, record in zip(persisted, records, strict=True):
            for metric in metrics:
                self.assertEqual(saved[metric], record[metric])
                self.assertIn(metric, columns)

    def test_crop_failure_is_unavailable_and_reported_as_delivery_gap(self):
        perspective = HELPERS["segment_perspective_view"]

        def crop(source, view, directory):
            if view["view_direction"] == "backward":
                raise ValueError("bad directional crop")
            return perspective(source, view, directory)

        download = mock.Mock(return_value=self.source)
        records, images, issues, metadata = self.grab(download, crop)
        self.assertEqual(download.call_count, 1)
        self.assertEqual(len(images), 1)
        failed = [record for record in records if record["status"] == "unavailable"]
        self.assertEqual([record["view_direction"] for record in failed], ["backward"])
        self.assertIsNone(failed[0]["path"])
        self.assertIn("ValueError", failed[0]["error"])
        self.assertEqual(metadata["selection_gaps"], [])
        self.assertFalse(metadata["directional_complete"])
        self.assertEqual({gap["direction"] for gap in metadata["delivery_gaps"]}, {"backward"})
        self.assertEqual({issue["stage"] for issue in issues}, {"directional coverage"})

    def test_failed_download_never_counts_original_as_delivered_views(self):
        download = mock.Mock(side_effect=RuntimeError("download unavailable"))
        records, images, issues, metadata = self.grab(download)
        self.assertEqual(download.call_count, 2)
        self.assertEqual(len(records), 2)
        self.assertEqual(images, [])
        self.assertTrue(all(record["path"] is None and record["status"] == "unavailable"
                            for record in records))
        self.assertFalse(metadata["directional_complete"])
        self.assertEqual({issue["direction"] for issue in issues}, set(HELPERS["SEGMENT_VIEW_OFFSETS"]))

    def test_cached_fallback_cannot_be_reused_or_mislabeled_as_original(self):
        self.rows[0]["thumb_1024_url"] = "https://images.invalid/smallest.jpg"
        with Image.open(self.source) as image, io.BytesIO() as buffer:
            image.save(buffer, format="JPEG")
            data = buffer.getvalue()
        session = mock.MagicMock(headers={})
        session.__enter__.return_value = session
        session.get.side_effect = [image_response(b"", status=503), image_response(b"", status=503),
                                   image_response(data), image_response(data)]
        requests = SimpleNamespace(Session=mock.Mock(return_value=session))
        with (
            mock.patch.dict(HELPERS, requests=requests),
            mock.patch.dict(HELPERS["SEGMENT_CONFIG"], download_attempts=1),
            mock.patch.object(HELPERS["time"], "sleep"),
        ):
            records, images, issues, _ = self.grab(HELPERS["segment_download_url"])
            self.assertFalse(issues)
            fallback = self.directory / "cache/road/originals/1024/pano.jpg"
            self.assertEqual({record["resolution"] for record in records}, {"1024"})
            self.assertEqual({record["original_path"] for record in records}, {str(fallback)})
            self.assertTrue(fallback.is_file())
            for item in images:
                item["image"].close()
            records, _, issues, metadata = self.grab(HELPERS["segment_download_url"])
        original = self.directory / "cache/road/originals/original/pano.jpg"
        self.assertFalse(issues)
        self.assertTrue(metadata["directional_complete"])
        self.assertEqual({record["resolution"] for record in records}, {"original"})
        self.assertEqual({record["original_path"] for record in records}, {str(original)})
        self.assertTrue(original.is_file())
        self.assertTrue(fallback.is_file())
        self.assertEqual([call.args[0] for call in session.get.call_args_list],
                         [self.rows[0][key] for key in ("thumb_original_url", "thumb_2048_url",
                                                       "thumb_1024_url", "thumb_original_url")])


if __name__ == "__main__":
    unittest.main()
