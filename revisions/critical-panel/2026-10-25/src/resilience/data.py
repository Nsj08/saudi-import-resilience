"""Fetch, clean and verify the frozen Stage 2 Comtrade panel."""
from __future__ import annotations

import csv
import hashlib
import json
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASKET = ["841350", "841360", "841370", "841381", "848110", "848120", "848130", "848140", "848180"]
YEARS = range(2022, 2026)
SPECIAL = {837, 838, 839, 899, 473, 637, 527, 577, 490, 568}
DIMENSIONS = {"typeCode": "C", "freqCode": "A", "reporterCode": 682, "flowCode": "M", "partner2Code": 0, "customsCode": "C00", "motCode": 0}
KEY_FIELDS = ["typeCode", "freqCode", "period", "reporterCode", "flowCode", "partnerCode", "partner2Code", "classificationCode", "cmdCode", "customsCode", "motCode", "mosCode"]
BASE = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
RAW_MANIFEST_SHA256 = "bf0fde4603eb952116f3783d7d6d1d03a095ba9fb73a1cf6d1c904ef01d9f33e"
CACHE = {
    (2022, "841370"): "data/raw/comtrade/comtrade_sample_2022_841370_partners.json",
    (2023, "841370"): "data/raw/comtrade/comtrade_sample_2023_841370_partners.json",
    (2023, "848180"): "data/raw/comtrade/comtrade_sample_2023_848180_partners.json",
    (2025, "848180"): "data/raw/comtrade/comtrade_sample_2025_848180_partners.json",
}
REVISION_KEYS = ("sourceRevision", "sourceRevisionId", "revision", "revisionId", "downloadId", "releaseId", "updateId")


class DataError(ValueError):
    """Raised for invalid, incomplete or untraceable source data."""


def sha(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_json(path: Path):
    """Load numbers as Decimal so JSON parsing never passes through binary float."""
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    except (OSError, json.JSONDecodeError) as exc:
        raise DataError(f"cannot read JSON {path}: {exc}") from exc


def dec(value, field="value") -> Decimal:
    if value is None or isinstance(value, bool):
        raise DataError(f"missing/non-numeric {field}")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise DataError(f"invalid {field}: {value!r}") from exc
    if not result.is_finite():
        raise DataError(f"non-finite {field}")
    return result


def strict_int(value, field):
    if isinstance(value, bool):
        raise DataError(f"invalid boolean identifier for {field}")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise DataError(f"invalid non-integral identifier for {field}: {value!r}")


def safe_path(root: Path, value: str, *, allow_internal_absolute=True) -> Path:
    """Resolve a manifest path and reject traversal or symlinks outside the project."""
    root_real = root.resolve()
    candidate = Path(value)
    if candidate.is_absolute():
        if not allow_internal_absolute:
            raise DataError(f"absolute manifest path is not portable: {value}")
        resolved = candidate.resolve()
    else:
        resolved = (root_real / candidate).resolve()
    try:
        resolved.relative_to(root_real)
    except ValueError as exc:
        raise DataError(f"manifest path escapes project root: {value}") from exc
    return resolved


def source_manifest(root: Path):
    manifest_path = root / "sources/raw_manifest.json"
    if sha(manifest_path) != RAW_MANIFEST_SHA256:
        raise DataError("sources/raw_manifest.json does not match the frozen contract hash")
    data = load_json(manifest_path)
    mapping = {item["path"]: item["sha256"] for item in data}
    return mapping


def verify_source_files(root: Path):
    """Verify original samples, partner reference and all World coverage files used."""
    mapping = source_manifest(root)
    required = {"data/raw/comtrade/partner_reference.json", "data/raw/comtrade/H6_reference.json"}
    required.update(CACHE.values())
    for year in YEARS:
        for code in BASKET:
            normal = f"data/raw/comtrade/coverage/world_{year}_{code}.json"
            retry = normal.replace(".json", "_retry1.json")
            required.add(normal if (root / normal).exists() else retry)
    expanded = set(required)
    for rel in tuple(required):
        prov_rel = rel.replace(".json", ".provenance.json")
        if prov_rel in mapping:
            expanded.add(prov_rel)
    for rel in sorted(expanded):
        expected = mapping.get(rel)
        if not expected:
            raise DataError(f"source manifest has no hash for required source: {rel}")
        path = safe_path(root, rel, allow_internal_absolute=False)
        if not path.is_file() or sha(path) != expected:
            raise DataError(f"frozen source hash mismatch or missing source: {rel}")
        if rel.endswith(".provenance.json"):
            meta = load_json(path)
            raw_rel = rel.replace(".provenance.json", ".json")
            if meta.get("sha256") and meta["sha256"] != mapping.get(raw_rel):
                raise DataError(f"source provenance does not bind raw source hash: {raw_rel}")
    return mapping


def validate_response(payload, year, code):
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise DataError("malformed response")
    if payload.get("error"):
        raise DataError(f"API error: {payload['error']}")
    count = payload.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count != len(payload["data"]):
        raise DataError("count/data mismatch")
    if count >= 500 or payload.get("more") is True:
        raise DataError("preview incomplete or at 500-record ceiling")
    for row in payload["data"]:
        for key, expected in DIMENSIONS.items():
            value = row.get(key)
            if isinstance(expected, int):
                value = strict_int(value, key)
            if value != expected:
                raise DataError(f"dimension mismatch {key}={value!r}")
        if str(row.get("period")) != str(year) or str(row.get("cmdCode")) != code:
            raise DataError("period/product mismatch")
        if row.get("classificationCode") != "H6" or row.get("isOriginalClassification") is not True:
            raise DataError("not original H6")
        if str(row.get("mosCode")) != "0":
            raise DataError("mosCode dimension mismatch")
        strict_int(row.get("partnerCode"), "partnerCode")
        if dec(row.get("primaryValue"), "primaryValue") < 0:
            raise DataError("negative primaryValue")
    return payload["data"]


def canonical_value(value):
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    if isinstance(value, dict):
        return {key: canonical_value(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [canonical_value(item) for item in value]
    return value


def canonical_record(row):
    return json.dumps(canonical_value(row), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def row_key(row):
    return tuple(str(row.get(key)) for key in KEY_FIELDS)


def collapse_rows(rows):
    """Only identical complete records collapse for aggregation; conflicting records fail."""
    grouped = {}
    for row in rows:
        key = row_key(row)
        grouped.setdefault(key, []).append(row)
    unique = []
    duplicate_count = 0
    for key in sorted(grouped):
        group = sorted(grouped[key], key=canonical_record)
        fingerprints = {canonical_record(row) for row in group}
        if len(fingerprints) != 1:
            raise DataError(f"conflicting duplicate record for dimension key {key}")
        unique.append(group[0])
        duplicate_count += len(group) - 1
    return unique, duplicate_count


def annotate_duplicates(rows):
    """Preserve all source rows while marking exact repeats deterministically."""
    seen = set()
    output = []
    for row in sorted(rows, key=lambda item: (row_key(item), canonical_record(item))):
        fingerprint = canonical_record(row)
        duplicate = fingerprint in seen
        seen.add(fingerprint)
        output.append((row, duplicate))
    return output


def reconcile(world, named, unresolved):
    diff = world - named - unresolved
    tolerance = max(Decimal(1), Decimal("0.000001") * world)
    return diff, tolerance, abs(diff) <= tolerance


def eligibility(reconciles, world, unresolved, threshold):
    """Zero World totals have undefined origin shares and never pass a threshold."""
    return bool(reconciles and world > 0 and unresolved >= 0 and unresolved / world <= Decimal(str(threshold)))


def strict_partner_map(root: Path):
    source = load_json(root / "data/raw/comtrade/partner_reference.json")
    mapping = {}
    for item in source.get("results", []):
        code = strict_int(item.get("PartnerCode", item.get("id")), "reference PartnerCode")
        if not isinstance(item.get("isGroup"), bool):
            raise DataError(f"reference isGroup must be boolean for partner {code}")
        mapping[code] = item
    return mapping


def require_partner(code, mapping):
    code = strict_int(code, "partnerCode")
    try:
        return mapping[code]
    except KeyError as exc:
        raise DataError(f"unknown partner code {code}; no invented label") from exc


def classify_partner(code, reference):
    code = strict_int(code, "partnerCode")
    if code == 0:
        return "world"
    if code in SPECIAL:
        return "unresolved_special"
    if reference["isGroup"]:
        return "overlapping_group_excluded"
    return "named_origin"


def classification_totals(classified):
    """Return named, unresolved-special and excluded-group amounts after exact dedupe."""
    named = unresolved = groups = Decimal(0)
    for row, category, _label, duplicate in classified:
        if duplicate:
            continue
        value = dec(row.get("primaryValue"), "primaryValue")
        if category == "named_origin":
            named += value
        elif category == "unresolved_special":
            unresolved += value
        elif category == "overlapping_group_excluded":
            groups += value
    return named, unresolved, groups


def stage_manifest(root: Path, verify_files=True):
    path = root / "data/raw/stage2/manifest.json"
    data = load_json(path)
    if not isinstance(data, list):
        raise DataError("Stage 2 manifest must be a list")
    mapping = {}
    migrated = False
    for entry in data:
        key = (strict_int(entry.get("year"), "manifest year"), str(entry.get("cmdCode")))
        if key in mapping:
            raise DataError(f"duplicate Stage 2 manifest cell {key}")
        for path_field, hash_field in (("raw_path", "raw_sha256"), ("provenance_path", "provenance_sha256")):
            if not isinstance(entry.get(hash_field), str) or len(entry[hash_field]) != 64:
                raise DataError(f"missing/invalid {hash_field} for {key}")
            resolved = safe_path(root, entry.get(path_field, ""))
            if verify_files and (not resolved.is_file() or sha(resolved) != entry[hash_field]):
                raise DataError(f"Stage 2 {path_field} hash mismatch for {key}")
            if Path(entry[path_field]).is_absolute():
                migrated = True
                entry[path_field] = resolved.relative_to(root.resolve()).as_posix()
        if "source_revision_ids" not in entry or "source_revision_ids_absent" not in entry:
            migrated = True
        prov_path = safe_path(root, entry["provenance_path"], allow_internal_absolute=False)
        if verify_files:
            prov = load_json(prov_path)
            if prov.get("sha256") != entry["raw_sha256"]:
                raise DataError(f"provenance raw sha256 mismatch for {key}")
        else:
            try:
                prov = load_json(prov_path) if prov_path.is_file() else {}
            except DataError:
                prov = {}
        revision = prov.get("source_revision_ids") or {name: prov[name] for name in REVISION_KEYS if name in prov}
        entry["source_revision_ids"] = revision
        entry["source_revision_ids_absent"] = not bool(revision)
        mapping[key] = entry
    return path, data, mapping, migrated


def validate_stage_cell(root: Path, entry, year, code):
    expected_raw = f"data/raw/stage2/partners_{year}_{code}.json"
    expected_prov = f"data/raw/stage2/partners_{year}_{code}.provenance.json"
    if entry.get("raw_path") != expected_raw or entry.get("provenance_path") != expected_prov:
        raise DataError(f"manifest paths do not match canonical cell paths for {year}/{code}")
    raw_path = safe_path(root, entry["raw_path"], allow_internal_absolute=False)
    prov_path = safe_path(root, entry["provenance_path"], allow_internal_absolute=False)
    if sha(raw_path) != entry["raw_sha256"] or sha(prov_path) != entry["provenance_sha256"]:
        raise DataError("cached raw/provenance hash mismatch")
    provenance = load_json(prov_path)
    if provenance.get("sha256") != entry["raw_sha256"]:
        raise DataError("provenance embedded sha256 mismatch")
    provenance_ids = provenance.get("source_revision_ids") or {name: provenance[name] for name in REVISION_KEYS if name in provenance}
    if "source_revision_ids" in entry and entry["source_revision_ids"] != provenance_ids:
        raise DataError("manifest source revision IDs do not match provenance")
    if "source_revision_ids_absent" in entry and entry["source_revision_ids_absent"] != (not bool(provenance_ids)):
        raise DataError("manifest source revision absence flag does not match provenance")
    payload = load_json(raw_path)
    return validate_response(payload, year, code), raw_path, provenance


def migration_write(path, manifest):
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def request_cell(root: Path, year, code, delay=7, opener=None, sleeper=None):
    """Request one missing cell; every unsuccessful response/attempt is recorded."""
    opener = opener or urlopen
    sleeper = sleeper or time.sleep
    raw_dir = root / "data/raw/stage2"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"partners_{year}_{code}.json"
    prov_path = raw_dir / f"partners_{year}_{code}.provenance.json"
    params = {"reporterCode": 682, "period": year, "cmdCode": code, "flowCode": "M", "partner2Code": 0, "customsCode": "C00", "motCode": 0}
    url = BASE + "?" + urlencode(params)
    failures = []
    for attempt in range(1, 4):
        body = None
        status = None
        ctype = ""
        timestamp = datetime.now(timezone.utc).isoformat()
        try:
            req = Request(url, headers={"User-Agent": "Stage2-reproducible-research/1.0"})
            with opener(req, timeout=45) as response:
                body = response.read()
                status = response.status
                ctype = response.headers.get("Content-Type", "")
            if status != 200:
                raise DataError(f"HTTP {status}")
            payload = json.loads(body, parse_float=Decimal)
            validate_response(payload, year, code)
            raw_path.write_bytes(body)
            revision = {key: payload[key] for key in REVISION_KEYS if key in payload}
            prov = {"url": url, "retrieved_utc": timestamp, "status": status, "content_type": ctype,
                    "sha256": hashlib.sha256(body).hexdigest(), "attempt": attempt,
                    "source_revision_ids": revision, "source_revision_ids_absent": not bool(revision),
                    "source_metadata": {key: str(payload[key]) for key in ("count", "elapsedTime", "error", "more") if key in payload}}
            prov_path.write_text(json.dumps(prov, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            return {"year": year, "cmdCode": code, "raw_path": raw_path.relative_to(root).as_posix(),
                    "raw_sha256": sha(raw_path), "provenance_path": prov_path.relative_to(root).as_posix(),
                    "provenance_sha256": sha(prov_path), "source": "retrieved",
                    "source_revision_ids": revision, "source_revision_ids_absent": not bool(revision)}
        except HTTPError as exc:
            status = exc.code
            try:
                body = exc.read()
            except Exception:
                body = None
            error = f"HTTPError {exc.code}: {exc.reason}"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        failure_record = {"attempt": attempt, "retrieved_utc": timestamp, "url": url, "status": status,
                          "error": error, "response_body_path": None, "response_body_sha256": None}
        if body is not None:
            response_path = raw_dir / f"partners_{year}_{code}.attempt{attempt}.response.json"
            response_path.write_bytes(body)
            failure_record["response_body_path"] = response_path.relative_to(root).as_posix()
            failure_record["response_body_sha256"] = sha(response_path)
        failure_path = raw_dir / f"partners_{year}_{code}.attempt{attempt}.failure.json"
        failure_path.write_text(json.dumps(failure_record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        failures.append(failure_record)
        if attempt < 3:
            sleeper(max(delay, 7))
    final_path = raw_dir / f"partners_{year}_{code}.failure.json"
    final_path.write_text(json.dumps(failures, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    raise DataError(f"failed {year}/{code} after two retries; failures recorded in {final_path}")


def fetch(root, delay=7, requester=None, sleeper=None, monotonic=None):
    root = Path(root).resolve()
    requester = requester or request_cell
    sleeper = sleeper or time.sleep
    monotonic = monotonic or time.monotonic
    verify_source_files(root)
    raw_dir = root / "data/raw/stage2"
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = raw_dir / "manifest.json"
    if manifest_path.exists():
        _, manifest, entries, migrated = stage_manifest(root)
    else:
        manifest, entries, migrated = [], {}, False
    # Preserve each verified cached source and leave the manifest byte-identical on repeat runs.
    if len(entries) == 36 and all((year, code) in entries for year in YEARS for code in BASKET):
        for year in YEARS:
            for code in BASKET:
                validate_stage_cell(root, entries[(year, code)], year, code)
        if migrated:
            migration_write(manifest_path, manifest)
        return
    # Fail closed before any writes if a manifest-omitted cell already has raw bytes.
    for year in YEARS:
        for code in BASKET:
            if (year, code) in entries:
                continue
            raw_path = raw_dir / f"partners_{year}_{code}.json"
            prov_path = raw_dir / f"partners_{year}_{code}.provenance.json"
            if raw_path.exists() or prov_path.exists():
                raise DataError(f"orphan raw/provenance exists for missing manifest cell {year}/{code}; refusing overwrite")
    last_request_at = None
    for year in YEARS:
        for code in BASKET:
            if (year, code) in entries:
                validate_stage_cell(root, entries[(year, code)], year, code)
                continue
            key = (year, code)
            if key in CACHE:
                src = safe_path(root, CACHE[key], allow_internal_absolute=False)
                src_prov = Path(str(src).replace(".json", ".provenance.json"))
                raw_sha = sha(src)
                meta = load_json(src_prov)
                if meta.get("sha256") != raw_sha:
                    raise DataError(f"Stage 1 cached sample provenance mismatch for {key}")
                dst = raw_dir / f"partners_{year}_{code}.json"
                prov = raw_dir / f"partners_{year}_{code}.provenance.json"
                dst.write_bytes(src.read_bytes())
                prov.write_bytes(src_prov.read_bytes())
                source_revision_ids = meta.get("source_revision_ids") or {name: meta[name] for name in REVISION_KEYS if name in meta}
                entry = {"year": year, "cmdCode": code, "raw_path": dst.relative_to(root).as_posix(),
                         "raw_sha256": sha(dst), "provenance_path": prov.relative_to(root).as_posix(),
                         "provenance_sha256": sha(prov), "source": "stage1-cache",
                         "source_revision_ids": source_revision_ids, "source_revision_ids_absent": not bool(source_revision_ids)}
            else:
                interval = max(delay, 7)
                if last_request_at is not None:
                    remaining = interval - (monotonic() - last_request_at)
                    if remaining > 0:
                        sleeper(remaining)
                if requester is request_cell:
                    entry = requester(root, year, code, delay=delay, sleeper=sleeper)
                else:
                    entry = requester(root, year, code, delay=delay)
                last_request_at = monotonic()
            if "source_revision_ids" not in entry:
                entry["source_revision_ids"] = {}
                entry["source_revision_ids_absent"] = True
            entries[key] = entry
            manifest.append(entry)
            manifest.sort(key=lambda item: (int(item["year"]), str(item["cmdCode"])))
            migration_write(manifest_path, manifest)
    # Validate the final manifest and all cached pairs before reporting success.
    _, _, final_entries, _ = stage_manifest(root)
    for year in YEARS:
        for code in BASKET:
            validate_stage_cell(root, final_entries[(year, code)], year, code)


def require_world_payload(root, source_hashes, year, code):
    normal = f"data/raw/comtrade/coverage/world_{year}_{code}.json"
    rel = normal if (root / normal).exists() else normal.replace(".json", "_retry1.json")
    expected = source_hashes.get(rel)
    if not expected:
        raise DataError(f"source manifest has no hash for World coverage {rel}")
    path = safe_path(root, rel, allow_internal_absolute=False)
    if sha(path) != expected:
        raise DataError(f"cached World coverage hash mismatch: {rel}")
    payload = load_json(path)
    rows = validate_response(payload, year, code)
    world = [row for row in rows if strict_int(row.get("partnerCode"), "partnerCode") == 0]
    if len(rows) != 1 or len(world) != 1:
        raise DataError(f"cached coverage must contain exactly one World row for {year}/{code}")
    return world[0], path


def api_scalar(value):
    if isinstance(value, Decimal):
        return str(value)
    if value is None:
        return ""
    return value


def record_for_csv(row, year, code, classification, label, source_path, source_hash, duplicate):
    result = {key: api_scalar(value) for key, value in row.items()}
    result.update({"year": year, "hs6": code, "record_type": classification, "partner_reference_name": label,
                   "source_path": source_path, "source_sha256": source_hash, "exact_duplicate": duplicate,
                   "primaryValue_decimal": str(dec(row.get("primaryValue"), "primaryValue"))})
    return result


def clean(root):
    root = Path(root).resolve()
    source_hashes = verify_source_files(root)
    out = root / "data/processed/stage2"
    out.mkdir(parents=True, exist_ok=True)
    manifest_path, manifest, entries, migrated = stage_manifest(root, verify_files=False)
    partner_lookup = strict_partner_map(root)
    all_records, origins, cells, quality = [], [], [], []
    for year in YEARS:
        for code in BASKET:
            entry = entries.get((year, code))
            try:
                if entry is None:
                    raise DataError("missing Stage 2 manifest entry")
                rows, raw_path, provenance = validate_stage_cell(root, entry, year, code)
                world_reference, _ = require_world_payload(root, source_hashes, year, code)
                unique_rows, duplicate_count = collapse_rows(rows)
                world_values = [row for row in unique_rows if strict_int(row["partnerCode"], "partnerCode") == 0]
                if len(world_values) != 1:
                    raise DataError(f"expected one World row, found {len(world_values)}")
                W = dec(world_values[0]["primaryValue"], "World primaryValue")
                if dec(world_reference["primaryValue"], "coverage World value") != W:
                    raise DataError("partner-panel World value differs from cached World coverage")
                classified_rows = []
                for row, duplicate in annotate_duplicates(rows):
                    partner = strict_int(row.get("partnerCode"), "partnerCode")
                    if partner == 0:
                        classification, label = "world", "World"
                    else:
                        reference = require_partner(partner, partner_lookup)
                        classification = classify_partner(partner, reference)
                        label = str(reference.get("PartnerDesc") or reference.get("text") or "")
                    classified_rows.append((row, classification, label, duplicate))
                A, Z, groups = classification_totals(classified_rows)
                for row, classification, label, duplicate in classified_rows:
                    record = record_for_csv(row, year, code, classification, label, entry["raw_path"], entry["raw_sha256"], duplicate)
                    all_records.append(record)
                    if classification == "named_origin" and not duplicate:
                        origins.append(record)
                diff, tolerance, reconciles = reconcile(W, A, Z)
                ratio = (Z / W) if W > 0 else None
                named_records = [r for r in unique_rows if strict_int(r.get("partnerCode"), "partnerCode") != 0 and classify_partner(strict_int(r["partnerCode"], "partnerCode"), require_partner(r["partnerCode"], partner_lookup)) == "named_origin"]
                qty_units = sorted({str(r.get("qtyUnitCode")) for r in unique_rows if r.get("qtyUnitCode") is not None})
                alt_units = sorted({str(r.get("altQtyUnitCode")) for r in unique_rows if r.get("altQtyUnitCode") is not None})
                qty_est = sum(bool(r.get("isQtyEstimated")) for r in unique_rows)
                net_est = sum(bool(r.get("isNetWgtEstimated")) for r in unique_rows)
                fob_values = [dec(r["fobvalue"], "fobvalue") for r in unique_rows if r.get("fobvalue") is not None]
                cif_values = [dec(r["cifvalue"], "cifvalue") for r in unique_rows if r.get("cifvalue") is not None]
                value_matches_cif = sum(dec(r["primaryValue"]) == dec(r["cifvalue"]) for r in unique_rows if r.get("cifvalue") is not None)
                world_cif = dec(world_values[0]["cifvalue"], "World cifvalue") if world_values[0].get("cifvalue") is not None else None
                world_fob = dec(world_values[0]["fobvalue"], "World fobvalue") if world_values[0].get("fobvalue") is not None else None
                quality_row = {"year": year, "hs6": code, "cell_status": "accepted" if reconciles else "failed",
                    "failure_reason": "" if reconciles else "World minus named minus unresolved special exceeds tolerance",
                    "World_W_USD": str(W), "named_A_USD": str(A), "unresolved_special_Z_USD": str(Z),
                    "overlapping_groups_excluded_USD": str(groups), "reconciliation_difference_USD": str(diff),
                    "tolerance_USD": str(tolerance), "reconciles": reconciles, "U_USD": str(Z),
                    "U_over_W": str(ratio) if ratio is not None else "NA",
                    "eligible_0pct": eligibility(reconciles, W, Z, "0"),
                    "eligible_1pct": eligibility(reconciles, W, Z, "0.01"),
                    "eligible_5pct": eligibility(reconciles, W, Z, "0.05"),
                    "duplicate_records_counted_once": duplicate_count, "input_records": len(rows),
                    "named_origin_records": len(named_records), "quantity_unit_codes": ";".join(qty_units),
                    "alternative_quantity_unit_codes": ";".join(alt_units), "estimated_quantity_records": qty_est,
                    "estimated_net_weight_records": net_est, "cifvalue_records": len(cif_values),
                    "fobvalue_records": len(fob_values), "primary_equals_cifvalue_records": value_matches_cif,
                    "World_CIF_USD": str(world_cif) if world_cif is not None else "NA",
                    "World_FOB_USD": str(world_fob) if world_fob is not None else "NA",
                    "World_primary_equals_CIF": world_cif == W if world_cif is not None else "NA",
                    "source_path": entry["raw_path"], "source_sha256": entry["raw_sha256"]}
                quality.append(quality_row)
                cells.append({"status": quality_row["cell_status"], "reason": quality_row["failure_reason"], **quality_row})
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"
                cells.append({"year": year, "hs6": code, "status": "failed", "reason": reason,
                              "World_W_USD": "NA", "named_A_USD": "NA", "unresolved_special_Z_USD": "NA",
                              "overlapping_groups_excluded_USD": "NA", "U_USD": "NA"})
                failed_quality = {"year": year, "hs6": code, "cell_status": "failed", "failure_reason": reason,
                                "World_W_USD": "NA", "named_A_USD": "NA", "unresolved_special_Z_USD": "NA",
                                "overlapping_groups_excluded_USD": "NA", "reconciliation_difference_USD": "NA",
                                "tolerance_USD": "NA", "reconciles": False, "U_USD": "NA", "U_over_W": "NA",
                                "eligible_0pct": False, "eligible_1pct": False, "eligible_5pct": False,
                                "duplicate_records_counted_once": "NA", "input_records": "NA",
                                "quantity_unit_codes": "NA", "alternative_quantity_unit_codes": "NA",
                                "estimated_quantity_records": "NA", "estimated_net_weight_records": "NA",
                                "cifvalue_records": "NA", "fobvalue_records": "NA", "primary_equals_cifvalue_records": "NA",
                                "World_CIF_USD": "NA", "World_FOB_USD": "NA", "World_primary_equals_CIF": "NA"}
                quality.append(failed_quality)
                cells.append({"status": "failed", "reason": reason, **failed_quality})
    write_csv(out / "origins.csv", origins)
    write_csv(out / "records.csv", all_records)
    write_csv(out / "cells.csv", cells)
    write_csv(out / "quality.csv", quality)
    return cells, quality


def write_csv(path, rows):
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: api_scalar(value) for key, value in row.items()})


def verify(root):
    root = Path(root).resolve()
    cells, quality = clean(root)
    failures = [f"{row['year']}/{row['hs6']}: {row['reason']}" for row in cells if row["status"] != "accepted"]
    by = {code: {int(row["year"]): row for row in quality if row["hs6"] == code} for code in BASKET}
    primary = [code for code in BASKET if all(by[code].get(year, {}).get("eligible_1pct") is True for year in YEARS)]
    pumps = [code for code in primary if code.startswith("841")]
    valves = [code for code in primary if code.startswith("848")]
    if len(primary) < 6 or not pumps or not valves:
        failures.append(f"primary basket stop gate fails: {len(primary)} products, pumps={len(pumps)}, valves={len(valves)}")
    stamps, revisions = [], set()
    _, _, entries, _ = stage_manifest(root, verify_files=False)
    for entry in entries.values():
        try:
            _, _, provenance = validate_stage_cell(root, entry, int(entry["year"]), str(entry["cmdCode"]))
        except Exception:
            continue
        if provenance.get("retrieved_utc"):
            stamps.append(provenance["retrieved_utc"])
        revisions.update((provenance.get("source_revision_ids") or {}).keys())
    if stamps:
        stamp_note = f"Retrieval timestamps span {min(stamps)} to {max(stamps)}; four valid Stage 1 partner slices retain their earlier timestamps and are reused. "
    else:
        stamp_note = "Retrieval timestamps are unavailable. "
    revision_note = "Source revision/download identifiers were absent in all response metadata and this absence is recorded per request." if not revisions else "Source revision fields retained: " + ", ".join(sorted(revisions)) + "."
    report = ["# Stage 2 data quality", "", "Extraction and cleaning only; this report contains no concentration, ranking or scenario results.", "",
              f"Product-year cells: {len(cells)}; accepted: {sum(row['status']=='accepted' for row in cells)}; failed: {sum(row['status']!='accepted' for row in cells)}.",
              f"Balanced primary universe passing U/W ≤ 1% in every year: {len(primary)} products ({', '.join(primary)}); pumps: {len(pumps)}; valves: {len(valves)}.", stamp_note + revision_note, "",
              "Values remain nominal USD decimals. Quantity and weight values are diagnostics only, and no quantities are aggregated; their unit codes and source estimation flags are preserved.",
              "World rows and overlapping groups are retained in records.csv but excluded from the named-origin denominator. Only explicit protocol special codes enter unresolved-origin U. Unmapped partners fail the cell.", "",
              "| HS6 | Year | W USD | World CIF USD | World FOB USD | Primary=CIF | Named A USD | Special U USD | Groups excluded USD | Difference USD | U/W | Cell | 1% eligible |",
              "|---|---:|---:|---:|:---:|:---:|---:|---:|---:|---:|---:|:---:|:---:|"]
    for row in quality:
        report.append("| " + " | ".join(str(row.get(key, "NA")) for key in ["hs6", "year", "World_W_USD", "World_CIF_USD", "World_FOB_USD", "World_primary_equals_CIF", "named_A_USD", "unresolved_special_Z_USD", "overlapping_groups_excluded_USD", "reconciliation_difference_USD", "U_over_W", "cell_status", "eligible_1pct"]) + " |")
    accepted_quality = [row for row in quality if row.get("cell_status") == "accepted"]
    qty_units = sorted({unit for row in accepted_quality for unit in str(row.get("quantity_unit_codes", "")).split(";") if unit})
    alt_units = sorted({unit for row in accepted_quality for unit in str(row.get("alternative_quantity_unit_codes", "")).split(";") if unit})
    sum_field = lambda key: sum(int(row.get(key, 0) or 0) for row in accepted_quality)
    report.extend(["", f"Record diagnostics: quantity unit codes={', '.join(qty_units) or 'none'}; alternative quantity unit codes={', '.join(alt_units) or 'none'}; quantity-estimated records={sum_field('estimated_quantity_records')}; net-weight-estimated records={sum_field('estimated_net_weight_records')}.",
                   f"Valuation fields present: CIF={sum_field('cifvalue_records')} partner/World records, FOB={sum_field('fobvalue_records')} records; primaryValue equals CIF on {sum_field('primary_equals_cifvalue_records')} records. The table reports CIF and FOB values for each World row so the Saudi import valuation can be inspected cell by cell. CIF expands to cost, insurance and freight. These are nominal USD fields, and no valuation conversion is applied."])
    failures_from_quality = [row for row in quality if row.get("cell_status") == "failed"]
    if failures_from_quality:
        report.extend(["", "Failed cells are preserved explicitly in cells.csv and quality.csv:"])
        report.extend([f"- {row['year']}/{row['hs6']}: {row['failure_reason']}" for row in failures_from_quality])
    exclusions = [code for code in BASKET if any(by[code].get(year, {}).get("eligible_1pct") is not True for year in YEARS)]
    report.extend(["", f"Primary-basket exclusions generated from quality data: {', '.join(exclusions) if exclusions else 'none'}."])
    zero_cells = [row for row in quality if row.get("World_W_USD") not in (None, "NA", "") and dec(row["World_W_USD"]) == 0]
    if zero_cells:
        report.append(f"Documented zero-World cells: {len(zero_cells)}; their origin shares are undefined and they do not pass eligibility.")
    report.extend(["", "Reconciliation uses |W − A − U| ≤ max(USD 1, 10⁻⁶W). Residuals remain visible as discrepancies and are never reassigned to U. A zero World total has undefined shares and cannot pass any eligibility threshold.",
                   "A separate official Saudi HS6 numerical corroboration attempt did not retrieve a verifiable comparable table; World-to-partner checks are internal consistency checks, not independent validation."])
    report_path = root / "results/stage2/data_quality.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
    if failures:
        raise DataError("; ".join(failures))


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["fetch", "clean", "verify"])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--delay", type=int, default=7)
    args = parser.parse_args()
    if args.command == "fetch":
        fetch(args.root, args.delay)
    elif args.command == "clean":
        clean(args.root)
    else:
        verify(args.root)


if __name__ == "__main__":
    main()
