import itertools,sys,unittest
from fractions import Fraction as F
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from certificates import bounds, clipped, coeffs, f, first_nonpositive, independent_interval_radius, pair_radius, outcome, h1_qualifies

class ExactCertificateTests(unittest.TestCase):
 def test_small_corner_oracle_truth_and_nesting(self):
  cases=[(F(2,5),F(7,20),[F(1,5),F(1,10)],[F(1,8),F(1,4)]),(F(1,3),F(1,4),[F(1,2),F(0)],[F(0),F(1,2)])]
  for cp,cq,ap,aq in cases:
   for gamma in map(F,['0','.25','.5','1']):
    for delta in map(F,['0','.1','.3','1']):
     lo,hi=bounds(gamma,delta)
     # Independent brute-force corner oracle for strict ordering.
     for model in ('G','O','P'):
      if model=='G': pairs=[(outcome(cp,ap,[t]*len(ap)),outcome(cq,aq,[t]*len(aq))) for t in (lo,hi)]
      elif model=='O':
       pairs=[(outcome(cp,ap,t),outcome(cq,aq,t)) for t in itertools.product((lo,hi),repeat=len(ap))]
      else:
       pairs=[(outcome(cp,ap,[x]*len(ap)),outcome(cq,aq,[y]*len(aq))) for x,y in itertools.product((lo,hi),repeat=2)]
      truth=all(x>y for x,y in pairs)
      # Certificate truth is characterized by exact minima.
      b=coeffs(model,ap,aq,None)
      contrast=cp-cq-sum((x*hi if x>0 else x*lo for x in b),F(0))
      bp=cp-sum(ap,F(0))*hi
      self.assertEqual(truth,bp>0 and contrast>0)
     rp,ro,rg=(pair_radius(m,cp,cq,ap,aq,gamma) for m in ('P','O','G'))
     key=lambda x:F(2) if x is None else x
     self.assertLessEqual(key(rp),key(ro)); self.assertLessEqual(key(ro),key(rg))

 def test_first_root_against_bisection_and_edges(self):
  for gamma in map(F,['0','.25','.5','1']):
   for intercept,slope in [(F(1,7),F(3,2)),(F(1),F(0)),(F(0),F(1))]:
    root=first_nonpositive(lambda d:intercept-slope*d,gamma)
    expected=F(0) if intercept<=0 else (intercept/slope if slope and intercept/slope<=1 else None)
    self.assertEqual(root,expected)

 def test_exact_root_against_independent_bisection_predicate(self):
  gamma=F(1,4); fn=lambda d:F(17,100)-F(3,5)*min(F(1),gamma+d)
  root=first_nonpositive(fn,gamma)
  lo,hi=F(0),F(1)
  for _ in range(90):
   mid=(lo+hi)/2
   if fn(mid)>0:lo=mid
   else:hi=mid
  self.assertLessEqual(root-lo,F(1,2**88));self.assertLessEqual(hi-root,F(1,2**88))

 def test_floor_tie_zero_slope_and_empty_weights(self):
  self.assertEqual(outcome(F(1,10),[],[]),F(1,10))
  self.assertEqual(outcome(F(0),[F(1,2)],[F(1)]),F(0))
  self.assertIsNone(pair_radius('G',F(1),F(0),[F(0)],[F(0)],F(1,4)))
  self.assertEqual(pair_radius('G',F(1,3),F(1,3),[F(0)],[F(0)],F(1,2)),F(0))

 def test_stored_tie_inclusive_membership_status(self):
  from completion import stored_status, same_membership
  self.assertTrue(same_membership(['841370','848180'],['848180','841370']))
  self.assertFalse(same_membership(['841370','848180'],['841370']))
  self.assertEqual(stored_status(False,['a','b'],2),'strict')
  self.assertEqual(stored_status(False,['a','b','c'],2),'baseline_cutoff_tie')
  self.assertEqual(stored_status(True,['a','b','c'],2),'all_tied')

 def test_two_event_h3_discordance_keeps_event_specific_radii(self):
  from completion import h3_pair_counts
  cases=[('A:156',F(2,100),F(1,20)),('A:842',F(1,100),F(1,10))]
  counts,pairs,total=h3_pair_counts(cases)
  self.assertEqual(total,1);self.assertEqual(counts['discordant'],1)
  self.assertEqual(pairs[0][0:2],('A:156','A:842'))

 def test_local_origin_overlap_identity(self):
  a=[F(3,5),F(1,5)];b=[F(3,10),F(1,2)]
  lhs=sum((abs(x-y) for x,y in zip(a,b)),F(0))
  rhs=sum(a,F(0))+sum(b,F(0))-2*sum((min(x,y) for x,y in zip(a,b)),F(0))
  self.assertEqual(lhs,rhs)
  self.assertEqual(sum(abs(x-y) for x,y in zip([F(3,5)],[F(61,100)])),F(1,100))
  self.assertEqual(sum(abs(x-y) for x,y in zip([F(3,5),F(0)],[F(0),F(61,100)])),sum([F(3,5),F(61,100)]))

 def test_h1_edge_predicate(self):
  self.assertTrue(h1_qualifies(None,F(1,100),True))
  self.assertTrue(h1_qualifies(F(1,20)+F(1,100),F(1,20),True))
  self.assertFalse(h1_qualifies(F(1,20),F(1,100),True))
  self.assertFalse(h1_qualifies(None,None,True))
  self.assertFalse(h1_qualifies(F(1,4),F(1,100),False))

 def test_interval_baseline_is_p_certificate(self):
  for gamma in (F(0),F(1,4),F(1,2),F(1)):
   args=(F(1,5),F(1,8),[F(1,7),F(1,9)],[F(1,8),F(1,10)],gamma)
   self.assertEqual(independent_interval_radius(*args),pair_radius('P',*args))

 def test_fixed_synthetic_reversal_and_common_rate(self):
  # Root-frozen worked example: equal scales, common interrupted origin.
  c1,c2=F(1,5),F(39,200); a1,a2=F(3,5),F(61,100); gamma=F(1,4)
  self.assertEqual(c1-gamma*a1,F(1,20))
  self.assertGreater(c1-gamma*a1,c2-gamma*a2) # p leads at center
  # Rates .26/.24 reverse under P; common rate preserves difference sign.
  self.assertLess(c1-a1*F(26,100),c2-a2*F(24,100))
  self.assertGreater(c1-a1*F(26,100),c2-a2*F(26,100))
  self.assertEqual(pair_radius('P',c1,c2,[a1],[a2],gamma),F(3,484))

 def test_outcome_rejects_truncated_rate_vectors(self):
  with self.assertRaises(ValueError): outcome(F(1),[F(1,3),F(1,4)],[F(1,2)])

 def test_origin_shared_edge_cases_equal_G_and_P(self):
  gamma=F(1,4);cp,cq=F(1,5),F(39,200)
  # Same surviving origin: O has one contrast coefficient, identical to G.
  same={m:pair_radius(m,cp,cq,[F(3,5)],[F(61,100)],gamma) for m in ('G','O')}
  self.assertEqual(same['G'],same['O'])
  # Disjoint surviving origins: O's contrast coordinates are exactly P's two coordinates.
  disjoint={m:pair_radius(m,cp,cq,[F(3,5),F(0)],[F(0),F(61,100)],gamma) for m in ('O','P')}
  self.assertEqual(disjoint['O'],disjoint['P'])

 def test_noninteger_usd_currency_invariance(self):
  args=(F('120.25'),F('111.1'),[F('18.3'),F('7.8')],[F('4.7'),F('22.6')],F(1,4))
  r=pair_radius('O',*args); scale=F('1000.01')
  scaled=(args[0]*scale,args[1]*scale,[x*scale for x in args[2]],[x*scale for x in args[3]],args[4])
  self.assertEqual(r,pair_radius('O',*scaled))

if __name__=='__main__':unittest.main()
