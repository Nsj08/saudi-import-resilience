"""Independent, disjoint denominator audit from the full case-status table."""
import csv
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]/'results'
rows=list(csv.DictReader(open(ROOT/'case_statuses.csv')))
casekeys=('config','year','family','event','severity','gamma','eligibility','metric','k')
groupkeys=('config','year','family','eligibility','severity','gamma','metric','k')
ids=set()
counts=defaultdict(lambda:{'estimable':0,'strict':0,'all_tied':0,'baseline_cutoff_tie':0})
for r in rows:
 fullkey=tuple(r[x] for x in casekeys)
 if fullkey in ids:raise AssertionError('duplicate immutable case identity: '+repr(fullkey))
 ids.add(fullkey)
 key=tuple(r[x] for x in groupkeys)
 status=r['status']
 if status not in ('strict','all_tied','baseline_cutoff_tie'):raise AssertionError('unknown cutoff status '+status)
 counts[key]['estimable']+=1;counts[key][status]+=1
with open(ROOT/'case_denominators.csv','w',newline='') as h:
 fields=[*groupkeys,'estimable_case_count','strict_count','all_tied_count','baseline_cutoff_tie_count']
 w=csv.DictWriter(h,fieldnames=fields);w.writeheader()
 for key,v in sorted(counts.items()):
  if v['strict']+v['all_tied']+v['baseline_cutoff_tie']!=v['estimable']:raise AssertionError('status partition fails '+repr(key))
  w.writerow(dict(zip(fields,key+(v['estimable'],v['strict'],v['all_tied'],v['baseline_cutoff_tie']))))
print('PASS:',len(rows),'unique case/metric/k identities; all three statuses partition each stratum')
