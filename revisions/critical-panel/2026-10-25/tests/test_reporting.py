import json
import tempfile
import unittest
from pathlib import Path
from decimal import getcontext, localcontext
import importlib

from src.resilience.report import baseline_series, file_manifest, verify_manifest
from src.resilience import analysis
from src.resilience.report import (build_t3_rows, build_t4_rows, build_t5_rows,
                                   excluded_products, _format_exclusion_reason,
                                   write_t4_markdown)
from src.resilience.reproduce import png_pixels_equal

ROOT=Path(__file__).resolve().parents[1]


class ReportingTests(unittest.TestCase):
    def test_baseline_series_uses_declared_eight_in_hs_order_and_known_denominators(self):
        codes = ("841350", "841360", "841370", "848110", "848120", "848130", "848140", "848180")
        annual = [{"product": code, "year": "2025", "HHI": str(i / 10), "C1": str(i / 20), "A_USD": str(i * 1_000_000)}
                  for i, code in enumerate(reversed(codes), start=1)]
        # An eligible candidate excluded from the balanced primary basket must not leak into the figure.
        annual.append({"product": "841381", "year": "2025", "HHI": "0.99", "C1": "0.99", "A_USD": "999999999"})
        labels = {code: "label-" + code for code in (*codes, "841381")}
        result = baseline_series(annual, labels)
        self.assertEqual([row[0] for row in result], list(codes))
        self.assertEqual([row[1] for row in result], ["label-" + code for code in codes])
        self.assertAlmostEqual(result[0][2], .8)
        self.assertAlmostEqual(result[0][3], .4)
        self.assertAlmostEqual(result[0][4], 8.0)

    def test_baseline_refuses_incomplete_predeclared_universe(self):
        with self.assertRaisesRegex(ValueError, "all eight"):
            baseline_series([], {})

    def test_report_manifest_detects_artifact_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "results/stage2/figures/figure.png"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"original png bytes")
            manifest = file_manifest(root)
            manifest_path = root / "results/stage2/tables/report_manifest.json"
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(len(verify_manifest(root)), 1)
            target.write_bytes(b"tampered png bytes")
            with self.assertRaisesRegex(ValueError, "hash/mode mismatch"):
                verify_manifest(root)

    def test_import_and_analysis_run_restore_caller_decimal_context_and_keep_outputs(self):
        with localcontext() as caller:
            caller.prec=19
            importlib.reload(analysis)
            self.assertEqual(getcontext().prec,19)
            replay=analysis.isolated_replay(ROOT)
            expected=json.loads((ROOT/"results/stage2/analysis/output_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(replay,expected)
            self.assertEqual(getcontext().prec,19)

    def test_png_comparison_checks_rgb_even_when_alpha_is_identical(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            left=Path(tmp)/"left.png";right=Path(tmp)/"right.png"
            Image.new("RGBA",(1,1),(255,0,0,0)).save(left)
            Image.new("RGBA",(1,1),(0,255,0,0)).save(right)
            self.assertFalse(png_pixels_equal(left,right))
            self.assertTrue(png_pixels_equal(left,left))

    def test_table_fixtures_for_exclusions_all_verdict_coverage_and_distinct_cases(self):
        self.assertEqual(excluded_products("a,b,c","a,c"),"b")
        self.assertEqual(excluded_products("a,b","a,b"),"NA")
        summaries=[]
        for config in ("primary","tau_0"):
            for family in ("A","B","C"):
                for metric in ("fraction","usd"):
                    summaries.append({"config":config,"family":family,"metric":metric,"product":"P","status":"summarized",
                        "informative_case_count":"4","all_tied_case_count":"1","inestimable_case_count":"0","outcome_min":"0","outcome_max":"1",
                        "interval_lower_min":"0","interval_upper_max":"1","rank_min":"1","rank_max":"3","best_rank_min":"1","worst_rank_max":"3",
                        "top2_membership_count":"2","top2_membership_rate":"0.5","top3_membership_count":"3","top3_membership_rate":"0.75","top4_membership_count":"4","top4_membership_rate":"1"})
        verdicts=[{"config":"ALL_APPLICABLE","family":f,"metric":m,"product":"P","status":"undetermined","informative_case_count":"0","top3_membership_count":"0","unknown_rank_failure_count":"0","selector_uncertain_case_count":"1","condition_failures":"","untestable_reasons":"empty"} for f in ("A","B","C") for m in ("fraction","usd")]
        t4=build_t4_rows(summaries,verdicts,{"P":"fixture label"})
        self.assertEqual(sum(r["row_type"]=="configuration_summary" for r in t4),12)
        self.assertEqual(sum(r["row_type"]=="all_applicable_global_verdict" for r in t4),6)
        self.assertEqual({r["family"] for r in t4},{"A","B","C"})

        # Broad configurations retain their own declaration while exclusions
        # refer to the original nine-candidate universe.
        configs=[{"config":"broad_without_841381","kind":"broad","declared_codes":"841350,841360,841370,848110,848120,848130,848140","universe":"841350,841360,841370,848110,848120,848130,848140","universe_n":"7","selector_year":"2025","selector":"fixed","status":"estimable","notes":""},
                 {"config":"broad_without_848180","kind":"broad","declared_codes":"841350,841360,841370,841381,848110,848120,848130,848140","universe":"841350,841360,841370,841381,848110,848120,848130,848140","universe_n":"8","selector_year":"2025","selector":"fixed","status":"estimable","notes":""}]
        t3=build_t3_rows(configs,[])
        self.assertEqual([r["excluded_products"] for r in t3],["841381,848180","848180"])
        self.assertEqual(t3[0]["declared_codes"],configs[0]["declared_codes"])
        self.assertIn("U/W=1.8% exceeds 1.0%",_format_exclusion_reason("2022: U/W=0.017669799416224174427 exceeds 0.01",{}))

        with tempfile.TemporaryDirectory() as tmp:
            md=Path(tmp)/"T4.md"
            write_t4_markdown(md,t4)
            compact=md.read_text()
            self.assertEqual(compact.count("## Family "),6)
            self.assertIn("All-applicable global strict-status matrix",compact)
            self.assertIn("T4_family_summaries_verdicts.csv",compact)
            self.assertLess(len(compact.splitlines()),100)

        comparisons=[]
        expected={"A":120,"B":96,"C":24}
        for family,count in expected.items():
            for metric in ("fraction","usd"):
                for i in range(count):
                    base={"config":"tau_0","family":family,"comparison_metric":metric,"status":"matched","scenario_id":f"{family}:{i}",
                          "severity_d":"0.5","gamma":"0.25","eligibility":"incumbent","common_products":"P,Q,R","scenario_signature":"",
                          "reason":"","comparison_years":"","spearman":"0.5","strict_pairwise_order_reversals":"1","left_tied_pair_count":"0",
                          "right_tied_pair_count":"0","pair_tie_status_changes":"0"}
                    comparisons.extend([{**base,"k":k} for k in ("2","3","4")])
                if family=="A" and metric=="fraction":
                    comparisons.extend([{**base,"scenario_id":"","scenario_signature":"","status":"unmatched_primary_definition","reason":"empty","k":k} for k in ("2","3","4")])
        t5=build_t5_rows(comparisons)
        for family,count in expected.items():
            for metric in ("fraction","usd"):
                row=next(r for r in t5 if r["config"]=="tau_0" and r["family"]==family and r["metric"]==metric)
                self.assertEqual(row["matched_case_count"],count,(family,metric))
                unmatched=1 if family=="A" and metric=="fraction" else 0
                self.assertEqual(row["distinct_case_count"],count+unmatched,(family,metric))
                self.assertEqual(row["unmatched_or_unestimable_case_count"],unmatched,(family,metric))

        empty=[{"config":"quality_0pct","family":f,"comparison_metric":m,"status":"unestimable_empty_common_universe","scenario_id":"","scenario_signature":"","reason":"","k":k,"severity_d":"","gamma":"","eligibility":"","common_products":"","spearman":"","strict_pairwise_order_reversals":"","left_tied_pair_count":"","right_tied_pair_count":"","pair_tie_status_changes":""} for f in ("A","B","C","annual_baseline") for m in ("fraction","usd") for k in ("2","3","4")]
        empty_rows=build_t5_rows(empty)
        self.assertTrue(empty_rows)
        for row in empty_rows:
            self.assertEqual(row["configuration_status"],"unestimable_empty_common_universe")
            self.assertEqual(row["distinct_case_count"],0)
            self.assertEqual(row["matched_case_count"],0)
            self.assertEqual(row["unmatched_or_unestimable_case_count"],0)


if __name__ == "__main__":
    unittest.main()
