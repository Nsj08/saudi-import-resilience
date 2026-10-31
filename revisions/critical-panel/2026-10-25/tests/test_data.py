import copy
import csv
import io
import json
import shutil
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from resilience import data

ROOT = Path(__file__).resolve().parents[1]


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.row = {
            "typeCode": "C", "freqCode": "A", "period": "2022", "reporterCode": 682,
            "flowCode": "M", "partnerCode": 4, "partner2Code": 0, "classificationCode": "H6",
            "cmdCode": "841350", "customsCode": "C00", "motCode": 0, "mosCode": "0",
            "isOriginalClassification": True, "primaryValue": Decimal("1.00000000000000001"),
            "qty": Decimal("12.3400"), "qtyUnitCode": 8, "isQtyEstimated": False,
            "netWgt": Decimal("9.25"), "isNetWgtEstimated": True,
        }
        self.payload = {"count": 1, "data": [self.row], "error": ""}

    def test_valid_payload_and_exact_decimal_json(self):
        self.assertEqual(data.validate_response(self.payload, 2022, "841350"), [self.row])
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "precision.json"
            p.write_text('{"x":0.123456789012345678901234567890123456789}')
            self.assertEqual(str(data.load_json(p)["x"]), "0.123456789012345678901234567890123456789")

    def test_dimensions_period_product_and_h6(self):
        for field, value, expected in [("motCode", 1, "dimension mismatch"),
                                       ("mosCode", "1", "mosCode dimension mismatch"),
                                       ("period", "2023", "period/product mismatch"),
                                       ("cmdCode", "841360", "period/product mismatch"),
                                       ("classificationCode", "H5", "not original H6"),
                                       ("isOriginalClassification", False, "not original H6")]:
            payload = copy.deepcopy(self.payload)
            payload["data"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(data.DataError, expected):
                data.validate_response(payload, 2022, "841350")

    def test_boolean_numeric_dimensions_are_rejected(self):
        for field in ["reporterCode", "partner2Code", "motCode"]:
            payload = copy.deepcopy(self.payload)
            payload["data"][0][field] = False if field != "reporterCode" else True
            with self.subTest(field=field), self.assertRaises(data.DataError):
                data.validate_response(payload, 2022, "841350")

    def test_count_more_ceiling_and_api_failure(self):
        for change, message in [(lambda p: p.update(count=2), "count/data mismatch"),
                                (lambda p: p.update(more=True), "incomplete"),
                                (lambda p: p.update(error="bad request"), "API error")]:
            payload = copy.deepcopy(self.payload)
            change(payload)
            with self.subTest(message=message), self.assertRaisesRegex(data.DataError, message):
                data.validate_response(payload, 2022, "841350")
        payload = copy.deepcopy(self.payload)
        payload["count"] = 500
        payload["data"] = [self.row] * 500
        with self.assertRaisesRegex(data.DataError, "ceiling"):
            data.validate_response(payload, 2022, "841350")

    def test_missing_and_negative_values_fail(self):
        for value, message in [(None, "missing/non-numeric"), (-1, "negative")]:
            payload = copy.deepcopy(self.payload)
            payload["data"][0]["primaryValue"] = value
            with self.subTest(value=value), self.assertRaisesRegex(data.DataError, message):
                data.validate_response(payload, 2022, "841350")

    def test_fractional_and_boolean_identifiers_fail_closed(self):
        for value in [True, Decimal("4.5"), "4.5"]:
            with self.subTest(value=value), self.assertRaises(data.DataError):
                data.strict_int(value, "partnerCode")
        self.assertEqual(data.strict_int("4", "partnerCode"), 4)

    def test_special_code_unresolved_even_if_not_group_and_ordinary_group_excluded(self):
        self.assertEqual(data.classify_partner(899, {"isGroup": False}), "unresolved_special")
        self.assertEqual(data.classify_partner(156, {"isGroup": True}), "overlapping_group_excluded")
        self.assertEqual(data.classify_partner(4, {"isGroup": False}), "named_origin")
        fixture = [
            ({"primaryValue": Decimal("5")}, "named_origin", "named", False),
            ({"primaryValue": Decimal("2")}, "unresolved_special", "NES", False),
            ({"primaryValue": Decimal("100")}, "overlapping_group_excluded", "region", False),
        ]
        self.assertEqual(data.classification_totals(fixture), (Decimal("5"), Decimal("2"), Decimal("100")))

    def test_unknown_partner_rejected(self):
        with self.assertRaisesRegex(data.DataError, "unknown partner code"):
            data.require_partner(999, {4: {"isGroup": False}})

    def test_exact_duplicate_requires_every_field_to_match(self):
        duplicate = copy.deepcopy(self.row)
        unique, count = data.collapse_rows([self.row, duplicate])
        self.assertEqual(len(unique), 1)
        self.assertEqual(count, 1)
        for field, value in [("qtyUnitCode", 9), ("qty", Decimal("12.35")), ("isQtyEstimated", True), ("netWgt", Decimal("9.26"))]:
            conflict = copy.deepcopy(self.row)
            conflict[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(data.DataError, "conflicting duplicate"):
                data.collapse_rows([self.row, conflict])

    def test_partner_order_and_exact_duplicate_do_not_change_canonical_output(self):
        rows = [self.row, copy.deepcopy(self.row)]
        left = [(data.row_key(row), data.canonical_record(row), duplicate) for row, duplicate in data.annotate_duplicates(rows)]
        right = [(data.row_key(row), data.canonical_record(row), duplicate) for row, duplicate in data.annotate_duplicates(list(reversed(rows)))]
        self.assertEqual(left, right)

    def test_world_mismatch_residual_is_not_accepted_or_added_to_u(self):
        difference, tolerance, accepted = data.reconcile(Decimal("100000"), Decimal("80000"), Decimal("0"))
        self.assertEqual(difference, Decimal("20000"))
        self.assertLess(tolerance, difference)
        self.assertFalse(accepted)

    def test_zero_world_is_not_eligible(self):
        diff, tol, accepted = data.reconcile(Decimal(0), Decimal(0), Decimal(0))
        self.assertTrue(accepted)
        self.assertEqual((diff, tol), (Decimal(0), Decimal(1)))
        for threshold in ["0", "0.01", "0.05"]:
            self.assertFalse(data.eligibility(accepted, Decimal(0), Decimal(0), threshold))

    def test_manifest_paths_cannot_escape_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(data.DataError, "escapes project root"):
                data.safe_path(root, "../outside.json", allow_internal_absolute=False)

    def test_failed_attempt_http_body_is_preserved_even_if_retry_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample = copy.deepcopy(self.payload)
            sample["data"][0]["partnerCode"] = 0
            sample["revisionId"] = "release-2026-10"
            raw = json.dumps(sample, default=lambda x: str(x) if isinstance(x, Decimal) else x).encode()

            class Response:
                status = 200
                headers = {"Content-Type": "application/json"}
                def __enter__(self): return self
                def __exit__(self, *_): return False
                def read(self): return raw

            calls = {"n": 0}
            def opener(request, timeout):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise HTTPError(request.full_url, 429, "rate limited", {}, io.BytesIO(b'{"error":"429"}'))
                return Response()

            entry = data.request_cell(root, 2022, "841350", opener=opener, sleeper=lambda _delay: None)
            self.assertEqual(calls["n"], 2)
            failure = json.loads((root / "data/raw/stage2/partners_2022_841350.attempt1.failure.json").read_text())
            self.assertEqual(failure["status"], 429)
            self.assertEqual((root / failure["response_body_path"]).read_bytes(), b'{"error":"429"}')
            provenance = json.loads((root / entry["provenance_path"]).read_text())
            self.assertEqual(entry["source_revision_ids"], {"revisionId": "release-2026-10"})
            self.assertFalse(entry["source_revision_ids_absent"])
            self.assertEqual(provenance["source_revision_ids"], {"revisionId": "release-2026-10"})
            self.assertFalse(provenance["source_revision_ids_absent"])


class OfflineIntegrityTests(unittest.TestCase):
    def copy_inputs(self, root):
        root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(ROOT / "data/raw/comtrade", root / "data/raw/comtrade")
        shutil.copytree(ROOT / "data/raw/stage2", root / "data/raw/stage2")
        (root / "sources").mkdir(parents=True)
        shutil.copy2(ROOT / "sources/raw_manifest.json", root / "sources/raw_manifest.json")

    def test_temp_project_offline_clean_and_repeat_fetch_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "copy"
            self.copy_inputs(root)
            data.clean(root)
            for name in ["origins.csv", "records.csv", "cells.csv", "quality.csv"]:
                self.assertEqual((root / "data/processed/stage2" / name).read_bytes(),
                                 (ROOT / "data/processed/stage2" / name).read_bytes())
            manifest = root / "data/raw/stage2/manifest.json"
            before = manifest.read_bytes()
            hashes = {p.name: data.sha(p) for p in (root / "data/raw/stage2").glob("partners_*.json")}
            data.fetch(root)
            self.assertEqual(manifest.read_bytes(), before)
            self.assertEqual(hashes, {p.name: data.sha(p) for p in (root / "data/raw/stage2").glob("partners_*.json")})

    def test_tampered_raw_or_provenance_becomes_explicit_failed_cell(self):
        for target in ["raw", "provenance"]:
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "copy"
                self.copy_inputs(root)
                path = root / ("data/raw/stage2/partners_2022_841350.json" if target == "raw" else "data/raw/stage2/partners_2022_841350.provenance.json")
                path.write_bytes(path.read_bytes() + b" ")
                with self.assertRaises(data.DataError):
                    data.verify(root)
                with (root / "data/processed/stage2/cells.csv").open() as stream:
                    cells = list(csv.DictReader(stream))
                failed = [row for row in cells if row["year"] == "2022" and row["hs6"] == "841350"]
                self.assertEqual(failed[0]["status"], "failed")
                with (root / "data/processed/stage2/quality.csv").open() as stream:
                    saved = list(csv.DictReader(stream))
                self.assertEqual(next(row for row in saved if row["hs6"] == "841350" and row["year"] == "2022")["cell_status"], "failed")

    def test_partner_row_order_and_exact_duplicate_do_not_change_cleaned_values(self):
        def rewrite(root, reverse):
            raw_path = root / "data/raw/stage2/partners_2022_841350.json"
            payload = data.load_json(raw_path)
            chosen = next(row for row in payload["data"] if row["partnerCode"] not in (0, 490, 568))
            augmented = list(payload["data"]) + [copy.deepcopy(chosen)]
            augmented = list(reversed(augmented)) if reverse else augmented[1:] + augmented[:1]
            payload["data"] = augmented
            payload["count"] = len(augmented)
            raw_path.write_text(json.dumps(payload, default=lambda value: str(value) if isinstance(value, Decimal) else value))
            manifest_path = root / "data/raw/stage2/manifest.json"
            manifest = json.loads(manifest_path.read_text())
            entry = next(row for row in manifest if row["year"] == 2022 and row["cmdCode"] == "841350")
            entry["raw_sha256"] = data.sha(raw_path)
            prov_path = root / entry["provenance_path"]
            provenance = json.loads(prov_path.read_text())
            provenance["sha256"] = entry["raw_sha256"]
            prov_path.write_text(json.dumps(provenance, indent=2) + "\n")
            entry["provenance_sha256"] = data.sha(prov_path)
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

        with tempfile.TemporaryDirectory() as left_tmp, tempfile.TemporaryDirectory() as right_tmp:
            left, right = Path(left_tmp) / "copy", Path(right_tmp) / "copy"
            self.copy_inputs(left); self.copy_inputs(right)
            rewrite(left, False); rewrite(right, True)
            data.clean(left); data.clean(right)
            for filename in ["origins.csv", "records.csv", "cells.csv", "quality.csv"]:
                with (left / "data/processed/stage2" / filename).open() as a, (right / "data/processed/stage2" / filename).open() as b:
                    left_rows, right_rows = list(csv.DictReader(a)), list(csv.DictReader(b))
                for rows in (left_rows, right_rows):
                    for row in rows:
                        row.pop("source_sha256", None)
                self.assertEqual(left_rows, right_rows, filename)
            with (left / "data/processed/stage2/quality.csv").open() as stream:
                quality = list(csv.DictReader(stream))
            cell = next(row for row in quality if row["year"] == "2022" and row["hs6"] == "841350")
            self.assertEqual(cell["duplicate_records_counted_once"], "1")

    def test_original_source_hash_mismatch_fails_before_cleaning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "copy"
            self.copy_inputs(root)
            reference = root / "data/raw/comtrade/partner_reference.json"
            reference.write_bytes(reference.read_bytes() + b" ")
            with self.assertRaisesRegex(data.DataError, "frozen source hash mismatch"):
                data.clean(root)

    def test_stage_manifest_path_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "copy"
            self.copy_inputs(root)
            manifest_path = root / "data/raw/stage2/manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest[0]["raw_path"] = "../../outside.json"
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(data.DataError, "escapes project root"):
                data.fetch(root)

    def test_missing_manifest_cell_is_explicit_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "copy"
            self.copy_inputs(root)
            manifest_path = root / "data/raw/stage2/manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest = [row for row in manifest if not (row["year"] == 2022 and row["cmdCode"] == "841350")]
            manifest_path.write_text(json.dumps(manifest))
            cells, _ = data.clean(root)
            cell = next(row for row in cells if row["year"] == 2022 and row["hs6"] == "841350")
            self.assertEqual(cell["status"], "failed")
            self.assertEqual(cell["World_W_USD"], "NA")

    def test_missing_manifest_entry_with_orphan_bytes_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "copy"
            self.copy_inputs(root)
            manifest_path = root / "data/raw/stage2/manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest = [row for row in manifest if not (row["year"] == 2022 and row["cmdCode"] == "841350")]
            manifest_path.write_text(json.dumps(manifest))
            raw = root / "data/raw/stage2/partners_2022_841350.json"
            prov = root / "data/raw/stage2/partners_2022_841350.provenance.json"
            before = (raw.read_bytes(), prov.read_bytes())
            with self.assertRaisesRegex(data.DataError, "orphan raw/provenance"):
                data.fetch(root)
            self.assertEqual((raw.read_bytes(), prov.read_bytes()), before)

    def test_fetch_spaces_mocked_successes_across_cached_cells_and_keeps_revision_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "copy"
            self.copy_inputs(root)
            shutil.rmtree(root / "data/raw/stage2")
            now = [0.0]
            request_times = []

            def fake_sleep(seconds):
                now[0] += seconds

            def fake_requester(project, year, code, delay=7):
                request_times.append(now[0])
                payload = {"count": 1, "data": [{
                    "typeCode": "C", "freqCode": "A", "period": str(year), "reporterCode": 682,
                    "flowCode": "M", "partnerCode": 0, "partner2Code": 0, "classificationCode": "H6",
                    "cmdCode": code, "customsCode": "C00", "motCode": 0, "mosCode": "0",
                    "isOriginalClassification": True, "primaryValue": 1,
                }]}
                raw_path = project / "data/raw/stage2" / f"partners_{year}_{code}.json"
                prov_path = project / "data/raw/stage2" / f"partners_{year}_{code}.provenance.json"
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                raw_bytes = json.dumps(payload, separators=(",", ":")).encode()
                raw_path.write_bytes(raw_bytes)
                revision = {"revisionId": "fixture-release"} if not request_times[:-1] else {}
                provenance = {"sha256": data.sha(raw_path), "source_revision_ids": revision,
                              "source_revision_ids_absent": not bool(revision)}
                prov_path.write_text(json.dumps(provenance))
                return {"year": year, "cmdCode": code,
                        "raw_path": raw_path.relative_to(project).as_posix(), "raw_sha256": data.sha(raw_path),
                        "provenance_path": prov_path.relative_to(project).as_posix(),
                        "provenance_sha256": data.sha(prov_path), "source": "mock",
                        "source_revision_ids": revision, "source_revision_ids_absent": not bool(revision)}

            data.fetch(root, requester=fake_requester, sleeper=fake_sleep, monotonic=lambda: now[0])
            self.assertGreaterEqual(len(request_times), 2)
            self.assertTrue(all(later - earlier >= 7 for earlier, later in zip(request_times, request_times[1:])))
            manifest = json.loads((root / "data/raw/stage2/manifest.json").read_text())
            revision_entry = next(row for row in manifest if row["source_revision_ids"])
            self.assertEqual(revision_entry["source_revision_ids"], {"revisionId": "fixture-release"})
            self.assertFalse(revision_entry["source_revision_ids_absent"])


if __name__ == "__main__":
    unittest.main()
