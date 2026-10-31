import importlib.util, unittest
from decimal import Decimal as D
from pathlib import Path
P=Path(__file__).resolve().parents[2]/'src/critical_review/diagnostics.py'
spec=importlib.util.spec_from_file_location('diag',P); diag=importlib.util.module_from_spec(spec); spec.loader.exec_module(diag)
class DiagnosticTests(unittest.TestCase):
    def test_fixed_event_identity_and_monotonicity(self):
        # Formula independently exercised at boundary and tie conditions.
        def ell(S,d,g): return max(D(0),(d+g)*S-g)
        for S in (D(0),D('.2'),D('.8'),D(1)):
            for d in (D('.25'),D('.5'),D(1)):
                for g in (D(0),D('.25'),D(1)):
                    self.assertEqual(ell(S,d,g),max(D(0),d*S-g*(1-S)))
                    self.assertGreaterEqual(ell(S,d,g),D(0))
        self.assertEqual(ell(D('.2'),D('.5'),D('.5')),D(0)) # zero-floor boundary
        self.assertEqual(ell(D('.2'),D('.5'),D('.4')),ell(D('.2'),D('.5'),D('.3'))) # both floor at zero
        # Actual USD zero-floor boundary: equality and just above it both clip to zero.
        g0=D('.5')*D(100)/D(20)
        self.assertEqual(diag.dollar_exposure(D('.5'),D(100),g0,D(20)),D(0))
        self.assertEqual(diag.dollar_exposure(D('.5'),D(100),g0+D('.000001'),D(20)),D(0))
        self.assertGreater(diag.dollar_exposure(D('.5'),D(100),g0-D('.000001'),D(20)),D(0))
        self.assertLess(ell(D('.7'),D('.5'),D('.2')),ell(D('.8'),D('.5'),D('.2')))
    def test_threshold_denominator_and_equality_edges(self):
        self.assertTrue(diag.history_eligible(D('.02'),[D('.01'),D('.01'),D('.009999')]))
        self.assertFalse(diag.history_eligible(D('.02'),[D('.01'),D('.009999'),D(0)]))
        self.assertFalse(diag.history_eligible(D(0),[D('.5'),D('.5'),D('.5')]))
        self.assertEqual(diag.crossover(D('.5'),D(10),D(10),D(20),D(20)),(None,'identical_pairwise_line'))
        self.assertEqual(diag.crossover(D('.5'),D(11),D(10),D(20),D(20)),(None,'parallel_no_crossing'))
        g,status=diag.crossover(D('.5'),D(20),D(10),D(10),D(0))
        self.assertEqual(status,'crossing'); self.assertEqual(g,D('.5'))
    def test_actual_crossings_and_gamma_quarter(self):
        result=diag.reconstruct_crossover()['results']
        self.assertEqual(result['incumbent']['crossing_status'],'interior_positive')
        self.assertEqual(result['history']['crossing_status'],'interior_positive')
        for key in ('incumbent','history'):
            checks=result[key]['checks']
            self.assertLess(D(checks['just_below']['difference_350_minus_370']),0)
            self.assertGreater(D(checks['just_above']['difference_350_minus_370']),0)
            self.assertNotEqual(D(checks['gamma_0.25']['difference_350_minus_370']),0)
            self.assertTrue(result[key]['both_unclipped_positive_at_crossing'])
        self.assertLess(D(result['incumbent']['crossing_gamma']),D('.25'))
        self.assertGreater(D(result['history']['crossing_gamma']),D('.25'))
    def test_structural_diagnostic_reconstructs_portable_counts(self):
        x=diag.structural_diagnostic()
        self.assertEqual(x['general_incumbent_common_G']['rows_checked'],960)
        self.assertEqual(x['general_incumbent_common_G']['fixed_family_event_d_gamma_product_pairs_checked'],3360)
        self.assertEqual(x['matched_literal_event_invariance']['scenario_vectors_compared'],720)
        self.assertTrue(x['matched_literal_event_invariance']['scenario_id_in_event_key'])
        flags=x['strict_verdict_flags']
        self.assertEqual((flags['primary_eight_rows'],flags['primary_eight_known_rank_failures'],flags['primary_eight_primary_passes']),(48,46,2))
        self.assertEqual(flags['candidate_841381_unusable_rows'],6)
if __name__=='__main__': unittest.main()
