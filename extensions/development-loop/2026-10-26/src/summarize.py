"""Independently reconcile identities, denominators, and H1/H2 summaries."""
import csv,json
from pathlib import Path
from fractions import Fraction as F
ROOT=Path(__file__).resolve().parents[1]/'results'
cases=list(csv.DictReader(open(ROOT/'case_statuses.csv')))
cert=list(csv.DictReader(open(ROOT/'certificates.csv')))
summary=json.load(open(ROOT/'hypothesis_summary.json'))
keys=('config','year','family','event','severity','gamma','eligibility','metric','k')
case_map={tuple(r[x] for x in keys):r for r in cases}
if len(case_map)!=len(cases):raise AssertionError('duplicate full case identity')
cert_map={(tuple(r[x] for x in keys),r['model']):r for r in cert}
if len(cert_map)!=len(cert):raise AssertionError('duplicate full case/model identity')
checkpoint=list(csv.DictReader(open(ROOT/'checkpoint_witnesses.csv')))
for c in ('primary','baseline_2024','remove_broad_fixed_selector'):
 for k in ('2','3','4'):
  for metric in ('fraction','usd'):
   identities=[i for i in case_map if i[0]==c and i[8]==k and i[7]==metric]
   statuses=[case_map[i]['status'] for i in identities]
   strict=[i for i in identities if case_map[i]['status']=='strict']
   H1=H2=0
   for i in strict:
    rg=cert_map[(i,'G')]['certificate_radius'];ro=cert_map[(i,'O')]['certificate_radius'];rp=cert_map[(i,'P')]['certificate_radius']
    rg=None if rg=='certified_full_domain' else F(rg);ro=None if ro=='certified_full_domain' else F(ro);rp=None if rp=='certified_full_domain' else F(rp)
    found=any(x['config']==i[0] and x['year']==i[1] and x['family']==i[2] and x['event']==i[3] and x['severity']==i[4] and x['gamma']==i[5] and x['eligibility']==i[6] and x['metric']==i[7] and x['k']==i[8] for x in checkpoint)
    if (rg is None or rg>F(1,20)) and rp is not None and rp<=F(1,20) and found:H1+=1
    if (ro is None and rp is not None) or (ro is not None and rp is not None and ro>rp):H2+=1
   rec=next(x for x in summary['by_config_k_metric'] if x['config']==c and x['k']==int(k) and x['metric']==metric)
   partition=(statuses.count('strict'),statuses.count('all_tied'),statuses.count('baseline_cutoff_tie'))
   observed=(rec['strict_records'],rec['all_tied_records'],rec['baseline_cutoff_tie_records'])
   if partition!=observed or len(identities)!=sum(partition):raise AssertionError('summary/status denominator mismatch')
   if (H1,H2)!=(rec['H1_records'],rec['H2_records']):raise AssertionError('H1/H2 summary does not reaggregate from full identities')
# Protocol-pinned primary denominator and H1 counts.
for metric in ('fraction','usd'):
 sub=[r for r in cases if r['config']=='primary' and r['k']=='3' and r['metric']==metric]
 if len(sub)!=216:raise AssertionError('primary k3 case count changed')
 if metric=='fraction':
  total=sum(r['H1_records'] for r in summary['by_config_k_metric'] if r['config']=='primary' and r['k']==3)
 if total!=33:raise AssertionError('primary k3 H1 total across both metrics does not match independent expected result (29 fraction + 4 USD)')
 by_center=summary['H1_by_center_metric_k3']
 if by_center.get('0/fraction')!=23 or by_center.get('0/usd')!=4 or by_center.get('1/4/fraction')!=6:
  raise AssertionError('primary k3 H1 center breakdown differs from frozen result')
print('PASS: complete case identities, disjoint status denominators, and H1/H2 reaggregation across all configurations/k/metrics')
