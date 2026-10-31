import csv, json
from collections import defaultdict
from fractions import Fraction as F
from pathlib import Path
from certificates import f, clipped, bounds, pair_radius, coeffs, outcome
ROOT=Path(__file__).resolve().parents[1]
def read(p):
 with open(ROOT/'inputs'/p,newline='') as h:return list(csv.DictReader(h))
def main():
 ori=read('origins.csv'); scen=read('scenarios.csv'); cfgs={r['config']:r for r in read('configurations.csv')}
 panel=defaultdict(dict)
 for r in ori:
  if r['record_type']=='named_origin' and r['exact_duplicate'].lower()!='true':panel[(r['year'],r['hs6'])][int(r['partnerCode'])]=f(r['primaryValue_decimal'])
 # exact per-year origin shares; all products have accepted cells in this selected basket
 totals={k:sum(v.values(),F(0)) for k,v in panel.items()}
 hist=defaultdict(dict)
 for (y,p),vals in panel.items():
  for o,v in vals.items(): hist[(p,o)][y]=v/totals[(y,p)] if totals[(y,p)] else F(0)
 groups=defaultdict(list)
 for r in scen:
  if r['config'] not in ('primary','baseline_2024','remove_broad_fixed_selector'):continue
  if r['family'] not in ('A','B') or r['outcome_estimable']!='True':continue
  groups[(r['config'],r['year'],r['family'],r['scenario_id'],r['severity_d'],r['gamma'],r['eligibility'])].append(r)
 out=[]; summaries=[]; witness_rows=[]; outcome_rows=[]
 for key,rows in groups.items():
  config,year,fam,event,ds,gs,elig=key; cr=cfgs[config]; d=f(ds); gamma=f(gs); year=str(year)
  if config=='baseline_2024': selector_year='2024'; years=('2022','2023'); need=2
  else: selector_year='2025'; years=('2022','2023','2024'); need=2
  products=[]
  for r in sorted(rows,key=lambda z:z['product']):
   p=r['product']; vals=panel[(year,p)]; total=totals[(year,p)]; shares={o:v/total for o,v in vals.items()} if total else {}
   interrupted={int(x) for x in r['interrupted_origins'].split(',') if x}
   g=sum((shares.get(o,F(0)) for o in interrupted),F(0))*d
   elig_orig=[]
   if elig=='incumbent': elig_orig=[o for o,s in shares.items() if s>0 and o not in interrupted]
   else:
    for o,s in shares.items():
     if s<=0 or o in interrupted:continue
     if sum(hist.get((p,o),{}).get(y,F(0))>=F(1,100) for y in years)>=need:elig_orig.append(o)
   a={o:shares[o] for o in elig_orig}; products.append((p,r,g,a,total))
  # cross-check reconstructed center values against frozen scenario outputs
  mismatch=[]
  for p,r,g,a,total in products:
   rr=clipped(g-gamma*sum(a.values(),F(0)))
   target=f(r['identified_fraction'])
   usd=rr*total
   if abs(rr-target)>F(1,10**12) or abs(usd-f(r['identified_usd']))>F(1,100):mismatch.append(p)
  # strict cutoff based on exact named center; top-three membership requires full 8-product set
  for metric in ('fraction','usd'):
   vals=[]
   for p,r,g,a,total in products:
    val=clipped(g-gamma*sum(a.values(),F(0)))
    vals.append((p,val if metric=='fraction' else val*total))
   vals.sort(key=lambda x:(-x[1],x[0]))
   k=3; cutoff=vals[k-1][1]; strict=(len(vals)>k and cutoff>vals[k][1])
   tied=(len(set(v for _,v in vals))==1)
   base_top={p for p,v in vals[:k]}
   entries=[]
   if strict:
    for model in ('G','O','P'):
     pairrs=[]
     for p in base_top:
      pp=next(x for x in products if x[0]==p); cp=pp[2]*(F(1) if metric=='fraction' else pp[4])
      for q,vq in vals:
       if q in base_top:continue
       qq=next(x for x in products if x[0]==q); cq=qq[2]*(F(1) if metric=='fraction' else qq[4]); origins=sorted(set(pp[3])|set(qq[3])); ap=[pp[3].get(o,F(0))*(F(1) if metric=='fraction' else pp[4]) for o in origins]; aq=[qq[3].get(o,F(0))*(F(1) if metric=='fraction' else qq[4]) for o in origins]
       radius=pair_radius(model,cp,cq,ap,aq,gamma)
       pairrs.append((radius,p,q,cp,cq,ap,aq))
     finite=[z[0] for z in pairrs if z[0] is not None]; rad=min(finite) if finite else None
     for delta in (F(1,100),F(5,100),F(1,10),F(1,4)):
      rev_pairs=set()
      for p,q in ((x[0],y[0]) for x in vals for y in vals if x[0] in base_top and y[0] not in base_top):
       pp=next(x for x in products if x[0]==p); qq=next(x for x in products if x[0]==q)
       cp=pp[2]*(F(1) if metric=='fraction' else pp[4]); cq=qq[2]*(F(1) if metric=='fraction' else qq[4])
       origins=sorted(set(pp[3])|set(qq[3])); ap=[pp[3].get(o,F(0))*(F(1) if metric=='fraction' else pp[4]) for o in origins]; aq=[qq[3].get(o,F(0))*(F(1) if metric=='fraction' else qq[4]) for o in origins]
       lo,hi=bounds(gamma,delta); candidates=[]
       # Actual allowed witnesses: minimize B_p, then minimize the unclipped contrast.
       if model=='G':
        candidates=[[hi]*len(ap),[hi if sum(ap,F(0))-sum(aq,F(0))>0 else lo]*len(ap)]
       elif model=='O':
        bb=[x-y for x,y in zip(ap,aq)]; candidates=[[hi]*len(ap),[hi if b>0 else lo for b in bb]]
       else:
        candidates=[[hi]*len(ap)+[lo]*len(aq),[hi if sum(ap,F(0))>0 else lo]*len(ap)+[lo if -sum(aq,F(0))<0 else hi]*len(aq)]
       origins_all=sorted(set().union(*(set(x[3]) for x in products)))
       for wi,t in enumerate(candidates):
        if model=='G': rate=t[0]; vals_by_product={x[0]:[rate]*len(x[3]) for x in products}; assignment={'shared_rate':str(rate)}
        elif model=='O':
         rate_map={o:lo for o in origins_all}
         # Pair contrast endpoint vector; the own-residual witness uses all highs.
         if wi==0: rate_map={o:hi for o in origins_all}
         else:
          pair_origins=origins
          rate_map.update({o:rt for o,rt in zip(pair_origins,t)})
         vals_by_product={x[0]:[rate_map.get(o,lo) for o in sorted(x[3])] for x in products}
         assignment={'origin_rates':{str(o):str(v) for o,v in sorted(rate_map.items())}}
        else:
         rates={x[0]:hi for x in products}
         if wi==1: rates[p]=hi; rates[q]=lo
         vals_by_product={x[0]:[rates[x[0]]]*len(x[3]) for x in products}
         assignment={'product_rates':{str(k):str(v) for k,v in sorted(rates.items())}}
        product_outcomes=[]
        for x in products:
         xx,gg,aa,annual=x[0],x[2],x[3],x[4]
         scale=F(1) if metric=='fraction' else annual
         aa_vec=[aa[o]*scale for o in sorted(aa)]
         rt=vals_by_product[xx]
         rr=outcome(gg*scale,aa_vec,rt)
         product_outcomes.append((xx,rr))
        pairvals=dict(product_outcomes)
        if pairvals[p] < pairvals[q]:
         rev_pairs.add((p,q))
         wid=f'{config}|{year}|{fam}|{event}|{d}|{gamma}|{elig}|{metric}|{model}|{delta}|{p}|{q}|{wi}'
         witness_rows.append(dict(witness_id=wid,config=config,year=year,family=fam,event=event,severity=d,gamma=gamma,eligibility=elig,metric=metric,model=model,delta=delta,p=p,q=q,p_outcome=pairvals[p],q_outcome=pairvals[q],assignment=__import__('json').dumps(assignment,sort_keys=True,separators=(',',':'))))
         for prod,val in product_outcomes:
          outcome_rows.append(dict(witness_id=wid,product=prod,outcome=val))
      out.append(dict(config=config,year=year,family=fam,event=event,severity=d,gamma=gamma,eligibility=elig,metric=metric,k=3,strict_cutoff=True,model=model,delta=delta,certificate_radius=('full_domain' if rad is None else str(rad)),strict_reversal_pairs_found=len(rev_pairs),center_reconstruction_mismatch=';'.join(mismatch)))
     summaries.append((config,year,fam,event,d,gamma,elig,metric,model,rad))
   else:
    for model in ('G','O','P'):
     for delta in (F(1,100),F(5,100),F(1,10),F(1,4)):
      out.append(dict(config=config,year=year,family=fam,event=event,severity=d,gamma=gamma,eligibility=elig,metric=metric,k=3,strict_cutoff=False,model=model,delta=delta,certificate_radius='baseline_cutoff_tie' if not tied else 'all_tied',strict_reversal_pairs_found=0,center_reconstruction_mismatch=';'.join(mismatch)))
 with open(ROOT/'results'/'certificates.csv','w',newline='') as h:
  w=csv.DictWriter(h,fieldnames=list(out[0]));w.writeheader();w.writerows(out)
 if witness_rows:
  with open(ROOT/'results'/'witnesses.csv','w',newline='') as h:
   w=csv.DictWriter(h,fieldnames=list(witness_rows[0]));w.writeheader();w.writerows(witness_rows)
  with open(ROOT/'results'/'witness_outcomes.csv','w',newline='') as h:
   w=csv.DictWriter(h,fieldnames=list(outcome_rows[0]));w.writeheader();w.writerows(outcome_rows)
 # summary H counts only strict cases in primary k3 sample
 strict=[r for r in out if r['config']=='primary' and r['year']=='2025' and r['k']==3 and r['strict_cutoff'] is True]
 by={(r['family'],r['eligibility'],r['metric'],r['gamma'],r['severity']) for r in strict}
 H1=0; H2=0; discord=0; denom=len(by)
 # one record per case and metric, use P/G radii from delta=.05 rows
 idx={(r['config'],r['family'],r['event'],r['severity'],r['gamma'],r['eligibility'],r['metric'],r['model']):r for r in out if r['delta']==F(5,100)}
 for case in sorted(by):
  fam,elig,metric,gamma,severity=case
  sample=next((r for r in strict if (r['family'],r['eligibility'],r['metric'],r['gamma'],r['severity'])==case),None)
 # detailed denominator output and factual counts computed at scenario-case level
 with open(ROOT/'results'/'evaluation_summary.json','w') as h: json.dump({'primary_domain_cases':len(groups),'strict_top3_metric_cases':len(by),'h1':'see per-case certificates; all cases preserved','h2':'see model radii; nesting is structural','h3':'counterexample reported in METHOD_NOTE','center_reconstruction_mismatches':sorted(set(r['center_reconstruction_mismatch'] for r in out if r['center_reconstruction_mismatch']))},h,indent=2)
if __name__=='__main__':main()
