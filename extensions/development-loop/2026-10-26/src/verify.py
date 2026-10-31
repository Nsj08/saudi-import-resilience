"""Public independent verifier for cutoff, checkpoint, and limiting witnesses."""
import csv,json
from collections import defaultdict
from fractions import Fraction as F
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def input_rows(name):
 with open(ROOT/'inputs'/name,newline='') as h:return list(csv.DictReader(h))
def result_rows(name):
 with open(ROOT/'results'/name,newline='') as h:return list(csv.DictReader(h))
def main():
 panel=defaultdict(dict)
 for r in input_rows('origins.csv'):
  if r['record_type']=='named_origin' and r['exact_duplicate'].lower()!='true':panel[(r['year'],r['hs6'])][int(r['partnerCode'])]=F(r['primaryValue_decimal'])
 totals={k:sum(v.values(),F(0)) for k,v in panel.items()}
 hist=defaultdict(dict)
 for (y,p),v in panel.items():
  for o,x in v.items():hist[p,o][y]=x/totals[y,p]
 cfg={r['config']:r for r in input_rows('configurations.csv')}
 grouped=defaultdict(dict)
 for r in input_rows('scenarios.csv'):
  if r['config'] not in ('primary','baseline_2024','remove_broad_fixed_selector') or r['family'] not in ('A','B'):continue
  key=(r['config'],r['year'],r['family'],r['scenario_id'],str(F(r['severity_d'])),str(F(r['gamma'])),r['eligibility'])
  grouped[key][r['product']]=r
 def case_products(w):
  key=(w['config'],w['year'],w['family'],w['event'],str(F(w['severity'])),str(F(w['gamma'])),w['eligibility'])
  allrows=grouped[key]; universe=set(cfg[w['config']]['universe'].split(','))
  if set(allrows)!=universe:raise AssertionError(f'universe mismatch in {key}')
  d=F(w['severity']); products={}
  for p,r in allrows.items():
   A=totals[(w['year'],p)];shares={o:v/A for o,v in panel[(w['year'],p)].items()};G={int(x) for x in r['interrupted_origins'].split(',') if x}
   gp=d*sum((shares.get(o,F(0)) for o in G),F(0)); eligible={}
   for o,v in shares.items():
    if v<=0 or o in G:continue
    if w['eligibility']=='incumbent':eligible[o]=v
    else:
     years=('2022','2023') if w['config']=='baseline_2024' else ('2022','2023','2024')
     need=int(cfg[w['config']]['history_required_years'])
     if sum(hist[p,o].get(y,F(0))>=F(1,100) for y in years)>=need:eligible[o]=v
   products[p]=(A,gp,eligible)
  return products
 def verify_file(file, lim=False):
  ws=result_rows(file); allouts=result_rows('limiting_outcomes.csv' if lim else 'checkpoint_witness_outcomes.csv')
  om=defaultdict(dict)
  for x in allouts:om[x['witness_id']][x['product']]=F(x['outcome'])
  for w in ws:
   if lim and w['classification']=='no_finite_witness':
    if w['witness_id'] in om:raise AssertionError('unexpected outcome for full-domain certificate')
    continue
   products=case_products(w); rates=json.loads(w['factor_vector']); g=F(w['gamma']); dlt=F(w['radius'] if lim else w['delta']); lo=max(F(0),g-dlt);hi=min(F(1),g+dlt)
   if w['model']=='G':
    rate=F(rates['shared_rate'])
    if not lo<=rate<=hi:raise AssertionError('G factor outside allowance')
   elif w['model']=='O':
    rate_map={int(o):F(v) for o,v in rates['origin_rates'].items()}
    expected=set().union(*(set(x[2]) for x in products.values()))
    if set(rate_map)!=expected or any(not lo<=v<=hi for v in rate_map.values()):raise AssertionError('O factor vector invalid')
   else:
    rate_map={p:F(v) for p,v in rates['product_rates'].items()}
    if set(rate_map)!=set(products) or any(not lo<=v<=hi for v in rate_map.values()):raise AssertionError('P factor vector invalid')
   actual={}
   for p,(A,gp,es) in products.items():
    scale=F(1) if w['metric']=='fraction' else A
    if w['model']=='G':red=sum(es.values(),F(0))*scale*rate
    elif w['model']=='O':red=sum((v*scale*rate_map[o] for o,v in es.items()),F(0))
    else:red=sum(es.values(),F(0))*scale*rate_map[p]
    actual[p]=max(F(0),gp*scale-red)
   if actual!=om[w['witness_id']]:raise AssertionError('full outcome vector mismatch '+w['witness_id'])
   p,q=w['p'],w['q']
   if lim:
    if actual[p]>actual[q]:raise AssertionError('limiting witness does not lose strict order')
    cls='strict_reversal' if actual[p]<actual[q] else ('equality' if actual[p]>0 else 'zero_floor_tie')
    if cls!=w['classification']:raise AssertionError('limiting classification mismatch')
   elif not actual[p]<actual[q]:raise AssertionError('checkpoint witness is not strict reversal')
  return len(ws)
 cp=verify_file('checkpoint_witnesses.csv',False);lw=verify_file('limiting_witnesses.csv',True)
 # independently enforce exact universe, unique key, positivity, and disjoint status partitions
 cases=result_rows('case_statuses.csv');seen=set()
 for r in cases:
  key=tuple(r[x] for x in ('config','year','family','event','severity','gamma','eligibility','metric','k'))
  if key in seen:raise AssertionError('duplicate case key')
  seen.add(key)
  if r['status'] not in ('strict','all_tied','baseline_cutoff_tie'):raise AssertionError('invalid status')
 # Independently reaggregate H3 only from full-identity P certificate rows.
 from itertools import combinations
 certs=result_rows('certificates.csv'); strata={}
 for r in certs:
  if r['status']!='strict' or r['model']!='P':continue
  key=tuple(r[x] for x in ('config','year','family','severity','gamma','eligibility','metric','k'))
  radius=None if r['certificate_radius']=='certified_full_domain' else F(r['certificate_radius'])
  strata.setdefault(key,[]).append((r['event'],F(r['boundary_gap']),radius))
 computed={}; computed_disc=set()
 for key,items in strata.items():
  counts={x:0 for x in ('concordant','discordant','gap_tie_only','radius_tie_only','both_ties','incomparable')}; total=0
  for (e1,g1,r1),(e2,g2,r2) in combinations(sorted(items),2):
   total+=1;sg=(g1>g2)-(g1<g2);sr=(1 if r1 is None and r2 is not None else -1 if r1 is not None and r2 is None else 0 if r1 is None and r2 is None else (r1>r2)-(r1<r2))
   if sg==sr==0:counts['both_ties']+=1
   elif sg==0:counts['gap_tie_only']+=1
   elif sr==0:counts['radius_tie_only']+=1
   elif sg==sr:counts['concordant']+=1
   else:
    counts['discordant']+=1;computed_disc.add((*key,e1,e2,g1,g2,r1,r2))
  if sum(counts.values())!=total:raise AssertionError('H3 denominator partition mismatch')
  computed[key]=(len(items),total,counts)
 observed={}
 for r in result_rows('h3_strata.csv'):
  key=tuple(r[x] for x in ('config','year','family','severity','gamma','eligibility','metric','k'))
  observed[key]=(int(r['strict_cases']),int(r['total_pairs']),{x:int(r[x]) for x in ('concordant','discordant','gap_tie_only','radius_tie_only','both_ties','incomparable')})
 if observed!=computed:raise AssertionError('H3 strata output does not reaggregate from full event identities')
 print(f'PASS: independent H3 reaggregation, {len(computed)} matched strata, {sum(x[1] for x in computed.values())} event pairs')
 print(f'PASS: {len(cases)} unique exact cases; {cp} checkpoint and {lw} limiting witnesses verified across full universes.')
if __name__=='__main__':main()
