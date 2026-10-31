"""USD-only adversarial unresolved-origin overlay under the frozen protocol."""
import csv
from collections import defaultdict
from fractions import Fraction as F
from pathlib import Path
from certificates import f,first_nonpositive,bounds
ROOT=Path(__file__).resolve().parents[1]
def read(p):
 with open(ROOT/'inputs'/p,newline='') as h:return list(csv.DictReader(h))
def main():
 panel=defaultdict(dict);sc=read('scenarios.csv');cells=read('cells.csv')
 for r in read('origins.csv'):
  if r['record_type']=='named_origin' and r['exact_duplicate'].lower()!='true':panel[(r['year'],r['hs6'])][int(r['partnerCode'])]=f(r['primaryValue_decimal'])
 total={k:sum(v.values(),F(0)) for k,v in panel.items()}
 U={(r['year'],r['hs6']):f(r['U_USD']) for r in cells}
 hist=defaultdict(dict)
 for (y,p),vs in panel.items():
  for o,v in vs.items():hist[p,o][y]=v/total[y,p]
 groups=defaultdict(list)
 for r in sc:
  if r['config']=='primary' and r['year']=='2025' and r['family'] in ('A','B') and r['outcome_estimable']=='True':groups[(r['family'],r['scenario_id'],r['severity_d'],r['gamma'],r['eligibility'])].append(r)
 out=[]
 for (fam,event,ds,gs,elig),rows in groups.items():
  d,g=map(f,(ds,gs)); products=[]
  for r in rows:
   p=r['product']; shares={o:v/total['2025',p] for o,v in panel['2025',p].items()}; G={int(x) for x in r['interrupted_origins'].split(',') if x}; gp=d*sum((shares.get(o,F(0)) for o in G),F(0))
   elig_o=[]
   for o,s in shares.items():
    if o in G or s==0:continue
    if elig=='incumbent' or sum(hist[p,o].get(y,F(0))>=F(1,100) for y in ('2022','2023','2024'))>=2:elig_o.append(o)
   a={o:v for o,v in shares.items() if o in elig_o}; A=total['2025',p]; products.append((p,gp,a,A,U['2025',p]))
  # named center top-3 in USD is held fixed per protocol; report only strict cutoffs
  residual=sorted(((p,max(F(0),(gp-g*sum(a.values(),F(0))))*A) for p,gp,a,A,u in products),key=lambda x:(-x[1],x[0]))
  strict=residual[2][1]>residual[3][1]
  if not strict:continue
  top={p for p,v in residual[:3]}
  for model in ('G','O','P'):
   pairrs=[]
   for p,gp,ap,A,up in products:
    if p not in top:continue
    cp=gp*A; a=[ap.get(o,F(0))*A for o in sorted(set(ap)|{o for _,_,aq,_,_ in products for o in aq})]
    origins=sorted(set(ap)|{o for _,_,aq,_,_ in products for o in aq})
    for q,gq,aq,B,uq in products:
     if q in top:continue
     av=[aq.get(o,F(0))*B for o in origins]; cq=gq*B
     if model=='G':bs=[sum(a,F(0))-sum(av,F(0))]
     elif model=='O':bs=[x-y for x,y in zip(a,av)]
     else:bs=[sum(a,F(0)),-sum(av,F(0))]
     rp=first_nonpositive(lambda delta: cp-sum(a,F(0))*bounds(g,delta)[1]-uq,g)
     rq=first_nonpositive(lambda delta: cp-cq-sum((x*bounds(g,delta)[1] if x>0 else x*bounds(g,delta)[0] for x in bs),F(0))-uq,g)
     finite=[x for x in (rp,rq) if x is not None]; rad=min(finite) if finite else None
     pairrs.append((rad,p,q,uq,cp,cq,a,av,bs))
   finite=[x[0] for x in pairrs if x[0] is not None];rad=min(finite) if finite else None
   at0=any(cp-sum(a,F(0))*g<=uq or cp-cq-(sum((b*g for b in bs),F(0)))<=uq for _,p,q,uq,cp,cq,a,av,bs in pairrs)
   out.append(dict(family=fam,event=event,severity=d,gamma=g,eligibility=elig,metric='USD',model=model,certificate_radius='unseparated_at_delta0' if at0 else ('certified_full_domain' if rad is None else str(rad))))
 with open(ROOT/'results'/'unknown_origin_usd_overlay.csv','w',newline='') as h:
  w=csv.DictWriter(h,fieldnames=list(out[0]));w.writeheader();w.writerows(out)
if __name__=='__main__':main()
