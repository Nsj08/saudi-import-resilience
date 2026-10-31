"""Complete frozen k=2/3/4, quantization, H1-H3, and limiting-witness checks."""
import csv,json
from collections import defaultdict
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
from fractions import Fraction as F
from itertools import combinations
from pathlib import Path
from certificates import f,clipped,bounds,pair_radius,first_nonpositive,independent_interval_radius,h1_qualifies
ROOT=Path(__file__).resolve().parents[1]
def read(name):
 with open(ROOT/'inputs'/name,newline='') as h:return list(csv.DictReader(h))
def fracdec(x, places):
 with localcontext() as c:
  c.prec=70
  d=Decimal(x.numerator)/Decimal(x.denominator)
  return d.quantize(Decimal(1).scaleb(-places),rounding=ROUND_HALF_EVEN)
def status(vals,k):
 v=sorted(vals.items(),key=lambda z:(-z[1],z[0]));all_tied=len(set(x for _,x in v))==1
 if all_tied:return 'all_tied',[]
 if len(v)<=k:return 'baseline_cutoff_tie',[]
 if v[k-1][1]>v[k][1]:return 'strict', [p for p,_ in v[:k]]
 return 'baseline_cutoff_tie',[]
def stored_status(all_tied,inclusive_members,k):
 if all_tied:return 'all_tied'
 return 'strict' if len(inclusive_members)==k else 'baseline_cutoff_tie'
def same_membership(left,right):
 return set(left)==set(right)
def pair_breaks(model,cp,cq,ap,aq,gamma):
 # Separately return first own-residual and contrast losses.
 b=[sum(ap,F(0))-sum(aq,F(0))] if model=='G' else ([x-y for x,y in zip(ap,aq)] if model=='O' else [sum(ap,F(0)),-sum(aq,F(0))])
 ro=first_nonpositive(lambda d:cp-sum(ap,F(0))*bounds(gamma,d)[1],gamma)
 rc=first_nonpositive(lambda d:cp-cq-sum((x*bounds(gamma,d)[1] if x>0 else x*bounds(gamma,d)[0] for x in b),F(0)),gamma)
 finite=[x for x in (ro,rc) if x is not None]
 return (min(finite) if finite else None,ro,rc,b)
def rate_assignment(model, products, p,q,metric,gamma,delta,binding,b,pair_origins):
 lo,hi=bounds(gamma,delta)
 if model=='G':
  if binding=='own_residual':return {'shared_rate':hi}
  return {'shared_rate':hi if b[0]>0 else lo}
 if model=='O':
  origins=sorted(set().union(*(set(x['a']) for x in products)))
  if binding=='own_residual':return {'origin_rates':{str(o):hi for o in origins}}
  # Coefficients have the origin order common to p and q.
  pair_rates={o:(hi if bb>0 else lo) for o,bb in zip(pair_origins,b)}
  return {'origin_rates':{str(o):pair_rates.get(o,lo) for o in origins}}
 rates={x['product']:gamma for x in products}
 if binding=='own_residual':rates[p]=hi
 else:
  pp=next(x for x in products if x['product']==p);qq=next(x for x in products if x['product']==q)
  if sum(pp['a'].values(),F(0))>0:rates[p]=hi
  if sum(qq['a'].values(),F(0))>0:rates[q]=lo
 return {'product_rates':{str(k):v for k,v in rates.items()}}
def h3_pair_counts(items):
 counts=defaultdict(int); discord=[]; total=0
 for a,b in combinations(sorted(items),2):
  total+=1; e1,g1,r1=a;e2,g2,r2=b
  sg=(g1>g2)-(g1<g2)
  sr=(1 if r1 is None and r2 is not None else -1 if r1 is not None and r2 is None else 0 if r1 is None and r2 is None else (r1>r2)-(r1<r2))
  if sg==0 and sr==0:counts['both_ties']+=1
  elif sg==0:counts['gap_tie_only']+=1
  elif sr==0:counts['radius_tie_only']+=1
  elif sg==sr:counts['concordant']+=1
  else:
   counts['discordant']+=1
   discord.append((e1,e2,g1,g2,r1,r2))
 if sum(counts.values())!=total:raise AssertionError('H3 pair partition failure')
 return counts,discord,total

def eval_assignment(model,products,assignment,metric):
 vals={}
 for x in products:
  scale=F(1) if metric=='fraction' else x['A']
  c=x['g']*scale
  if model=='G': a=sum(x['a'].values(),F(0))*scale; red=a*assignment['shared_rate']
  elif model=='O': red=sum((v*scale*assignment['origin_rates'].get(str(o),F(0)) for o,v in x['a'].items()),F(0))
  else:red=sum(x['a'].values(),F(0))*scale*assignment['product_rates'][x['product']]
  vals[x['product']]=clipped(c-red)
 return vals

def main():
 origins=read('origins.csv');scenarios=read('scenarios.csv');configrows={r['config']:r for r in read('configurations.csv')}
 panel=defaultdict(dict)
 for r in origins:
  if r['record_type']=='named_origin' and r['exact_duplicate'].lower()!='true':
   key=(r['year'],r['hs6']); origin=int(r['partnerCode'])
   if origin in panel[key]:raise ValueError(f'duplicate named-origin key {key}/{origin}')
   panel[key][origin]=f(r['primaryValue_decimal'])
 totals={k:sum(v.values(),F(0)) for k,v in panel.items()}
 history=defaultdict(dict)
 for (year,p),v in panel.items():
  for o,x in v.items():history[(p,o)][year]=x/totals[(year,p)]
 groups=defaultdict(dict)
 for r in scenarios:
  if r['config'] not in ('primary','baseline_2024','remove_broad_fixed_selector') or r['family'] not in ('A','B'):continue
  key=(r['config'],r['year'],r['family'],r['scenario_id'],str(f(r['severity_d'])),str(f(r['gamma'])),r['eligibility'])
  
  if r['product'] in groups[key]:raise ValueError(f'duplicate scenario product key {key}/{r["product"]}')
  groups[key][r['product']]=r
 rows=[]; case_status=[]; local=[]; den=defaultdict(lambda:{'estimable':0,'strict':0,'all_tied':0,'baseline_cutoff_tie':0})
 quant=[]; limiting=[]; limiting_out=[]; checkpoint=[]; checkpoint_out=[]; original={} 
 h1records=[]; h2records=[]; common_inv=[]; p_interval_checks=[]; center_vectors={}
 universes={c:set(configrows[c]['universe'].split(',')) for c in configrows if c in ('primary','baseline_2024','remove_broad_fixed_selector')}
 for key,raw in groups.items():
  config,year,fam,event,ds,gs,elig=key; d,g=F(ds),F(gs); cfg=configrows[config]
  products=[]
  for p in sorted(universes[config]):
   if p not in raw:raise ValueError(f'missing product {p} in {key}')
   r=raw[p]
   if r['outcome_estimable']!='True':raise ValueError(f'nonestimable product in retained universe {p}/{key}')
   total=totals[(year,p)]
   if total<=0:raise ValueError(f'nonpositive named total {p}/{year}')
   shares={o:v/total for o,v in panel[(year,p)].items()}
   G={int(x) for x in r['interrupted_origins'].split(',') if x}
   gp=d*sum((shares.get(o,F(0)) for o in G),F(0))
   if elig=='incumbent':es={o:v for o,v in shares.items() if o not in G and v>0}
   else:
    hy=('2022','2023') if config=='baseline_2024' else ('2022','2023','2024')
    need=int(cfg['history_required_years'])
    es={o:v for o,v in shares.items() if o not in G and v>0 and sum(history[(p,o)].get(y,F(0))>=F(1,100) for y in hy)>=need}
   exact=clipped(gp-g*sum(es.values(),F(0)))
   if abs(exact-f(r['identified_fraction']))>F(1,10**12) or abs(exact*total-f(r['identified_usd']))>F(1,100):raise ValueError('frozen-center mismatch')
   products.append({'product':p,'row':r,'g':gp,'a':es,'A':total,'exact':exact})
  if len(products)!=int(cfg['universe_n']) or len({x['product'] for x in products})!=len(products):raise ValueError(f'universe/unique-key mismatch {key}')
  for metric in ('fraction','usd'):
   scale=lambda x:F(1) if metric=='fraction' else x['A']
   vals={x['product']:x['exact']*scale(x) for x in products}
   center_vectors[(config,year,fam,event,d,g,elig,metric)]=vals
   rounded={x['product']:f(fracdec(f(x['row']['identified_fraction'] if metric=='fraction' else x['row']['identified_usd']),12 if metric=='fraction' else 2)) for x in products}
   # Stored original rounded membership flags remain a separate, preserved source check.
   for k in (2,3,4):
    exact_status, exact_members=status(vals,k); round_status,round_members=status(rounded,k)
    source_members=sorted(x['product'] for x in products if x['row'][f'top{k}_{metric}']=='True')
    source_tie=any(x['row'][f'scenario_all_tied_{metric}']=='True' for x in products)
    source_status=stored_status(source_tie,source_members,k)
    source_strict_members=source_members if source_status=='strict' else []
    original_match=(same_membership(source_strict_members,round_members) and source_status==round_status)
    membership_changed=(exact_status!=round_status or (exact_status=='strict' and round_status=='strict' and set(exact_members)!=set(round_members)))
    quant.append(dict(config=config,year=year,family=fam,event=event,severity=d,gamma=g,eligibility=elig,metric=metric,k=k,exact_cutoff_status=exact_status,rounded_cutoff_status=round_status,exact_strict_members=';'.join(exact_members),rounded_strict_members=';'.join(round_members),stored_original_tie_inclusive_members=';'.join(source_members),stored_original_strict_members=';'.join(source_strict_members),stored_original_topk_exact=';'.join(sorted(x['product'] for x in products if x['row'][f'top{k}_{metric}_exact']=='True')),stored_original_all_tied=source_tie,stored_original_cutoff_status=source_status,rounded_matches_stored=original_match,exact_vs_rounded_status_changed=exact_status!=round_status,exact_vs_rounded_membership_changed=membership_changed))
    sk=(config,year,fam,elig,d,g,metric,k); caseid=(config,year,fam,event,d,g,elig,metric,k); den[sk]['estimable']+=1;den[sk][exact_status]+=1
    case_status.append(dict(config=config,year=year,family=fam,event=event,severity=d,gamma=g,eligibility=elig,metric=metric,k=k,status=exact_status,exact_members=';'.join(exact_members),rounded_status=round_status,rounded_members=';'.join(round_members),stored_original_members=';'.join(source_members)))
    if exact_status!='strict':continue
    top=set(exact_members); ordered=sorted(vals.items(),key=lambda z:(-z[1],z[0]));gap=ordered[k-1][1]-ordered[k][1]
    
    pitems=[]
    for p in sorted(top):
     px=next(x for x in products if x['product']==p); cp=px['g']*scale(px); ap={o:v*scale(px) for o,v in px['a'].items()}
     for q,_ in ordered:
      if q in top:continue
      qx=next(x for x in products if x['product']==q); cq=qx['g']*scale(qx); aq={o:v*scale(qx) for o,v in qx['a'].items()}
      origins=sorted(set(ap)|set(aq)); av=[ap.get(o,F(0)) for o in origins];qv=[aq.get(o,F(0)) for o in origins]
      pitems.append((p,q,cp,cq,av,qv,origins))
      pr,own_r,contrast_r,bvec=pair_breaks('P',cp,cq,av,qv,g)
      gap_pair=(cp-g*sum(av,F(0)))-(cq-g*sum(qv,F(0)))
      dP=sum(av,F(0))+sum(qv,F(0))
      linear_region=pr is not None and g>pr and g+pr<1 and contrast_r is not None and (own_r is None or contrast_r<=own_r)
      if linear_region and dP>0:
       local_radius=gap_pair/dP
       lo_r,hi_r=bounds(g,pr)
       boundary_ok=(local_radius==pr and clipped(cp-sum(av,F(0))*hi_r)>0 and clipped(cq-sum(qv,F(0))*lo_r)>0)
       local.append(dict(config=config,year=year,family=fam,event=event,severity=d,gamma=g,eligibility=elig,metric=metric,k=k,p=p,q=q,center_gap=gap_pair,D_P=dP,exact_pair_radius=pr,local_gap_slope_radius=local_radius,status='verified_equal' if boundary_ok else 'inapplicable_boundary_check_failed'))
      else:
       why='zero_slope' if dP==0 else ('no_finite_loss' if pr is None else ('allowance_boundary' if not (g>pr and g+pr<1) else 'own_floor_or_clipping_binds'))
       local.append(dict(config=config,year=year,family=fam,event=event,severity=d,gamma=g,eligibility=elig,metric=metric,k=k,p=p,q=q,center_gap=gap_pair,D_P=dP,exact_pair_radius=('certified_full_domain' if pr is None else pr),local_gap_slope_radius='',status='inapplicable_'+why))
    # Exact product-specific strict-reversal witnesses at frozen δ=.05; all product outcomes share the same declared vector.
    ck_delta=F(1,20); ck_lo,ck_hi=bounds(g,ck_delta)
    for p,q,cp,cq,ap,aq,origins in pitems:
     rates={x['product']:g for x in products};rates[p]=ck_hi;rates[q]=ck_lo
     assignment={'product_rates':{str(pid):rate for pid,rate in rates.items()}}
     allout=eval_assignment('P',products,assignment,metric)
     if allout[p]<allout[q]:
      wid=f'{config}|{year}|{fam}|{event}|{d}|{g}|{elig}|{metric}|{k}|P|{p}|{q}'
      checkpoint.append(dict(witness_id=wid,config=config,year=year,family=fam,event=event,severity=d,gamma=g,eligibility=elig,metric=metric,k=k,model='P',delta=ck_delta,p=p,q=q,p_outcome=allout[p],q_outcome=allout[q],factor_vector=json.dumps(assignment,sort_keys=True,separators=(',',':'),default=str)))
      for prod,val in allout.items():checkpoint_out.append(dict(witness_id=wid,product=prod,outcome=val))
    for model in ('G','O','P'):
     pair_info=[]
     for p,q,cp,cq,ap,aq,origins in pitems:
      rad,ro,rc,b=pair_breaks(model,cp,cq,ap,aq,g)
      pair_info.append((rad,p,q,ro,rc,b,cp,cq,ap,aq,origins))
     finite=[x[0] for x in pair_info if x[0] is not None]; rad=min(finite) if finite else None
     rstatus='certified_full_domain' if rad is None else str(rad)
     rows.append(dict(config=config,year=year,family=fam,event=event,severity=d,gamma=g,eligibility=elig,metric=metric,k=k,status='strict',model=model,certificate_radius=rstatus,boundary_gap=gap,universe_n=len(products)))
     
     # H1/H2 per config/k/metric; H1 is meaningful primary top-three headline, also report sensitivities.
     if model=='G': h1records.append((*sk,rad))
     if model=='O': h2records.append((*sk,rad))
     # exact finite limiting witness: lexicographically first binding pair, every tied condition retained.
     if rad is not None:
      mins=[x for x in pair_info if x[0]==rad]
      chosen=min(mins,key=lambda x:(x[1],x[2]))
      _,p,q,ro,rc,b,cp,cq,ap,aq,origins=chosen
      bindings=[]
      if ro==rad:bindings.append('own_residual')
      if rc==rad:bindings.append('contrast')
      for binding in bindings:
       assign=rate_assignment(model,products,p,q,metric,g,rad,binding,b,origins)
       allout=eval_assignment(model,products,assign,metric)
       rp,rq=allout[p],allout[q]
       if rp>rq:raise AssertionError(f'limiting witness fails strict separation: {key}/{model}/{p}/{q}/{binding}')
       cls='strict_reversal' if rp<rq else ('equality' if rp==rq and rp>0 else 'zero_floor_tie' if rp==rq==0 else 'loss_without_tie')
       wid=f'{config}|{year}|{fam}|{event}|{d}|{g}|{elig}|{metric}|{k}|{model}|{p}|{q}|{binding}'
       limiting.append(dict(witness_id=wid,config=config,year=year,family=fam,event=event,severity=d,gamma=g,eligibility=elig,metric=metric,k=k,model=model,radius=rad,p=p,q=q,binding_condition=binding,p_outcome=rp,q_outcome=rq,classification=cls,factor_vector=json.dumps(assign,sort_keys=True,separators=(',',':'),default=str)))
       for prod,val in allout.items():limiting_out.append(dict(witness_id=wid,product=prod,outcome=val))
     else:
      limiting.append(dict(witness_id=f'{config}|{year}|{fam}|{event}|{d}|{g}|{elig}|{metric}|{k}|{model}',config=config,year=year,family=fam,event=event,severity=d,gamma=g,eligibility=elig,metric=metric,k=k,model=model,radius='certified_full_domain',p='',q='',binding_condition='none',p_outcome='',q_outcome='',classification='no_finite_witness',factor_vector=''))
     # P interval endpoint equivalence for all actual candidate pairs.
     if model=='P':
      for p,q,cp,cq,ap,aq,origins in pitems:
       pi=independent_interval_radius(cp,cq,ap,aq,g)
       pr=pair_breaks('P',cp,cq,ap,aq,g)[0]
       if pi!=pr:raise AssertionError(f'P interval equivalence failed {key}/{p}/{q}/{metric}/{k}')
       p_interval_checks.append((key,p,q,metric,k))
   # G/common incumbent fraction no strict inversion at checkpoints.
   if elig=='incumbent' and metric=='fraction':
    valscenter={x['product']:x['exact'] for x in products}; rank=sorted(valscenter.items(),key=lambda z:(-z[1],z[0]))
    for delta in (F(1,100),F(1,20),F(1,10),F(1,4)):
     lo,hi=bounds(g,delta)
     for p,q in combinations([x[0] for x in rank],2):
      if valscenter[p]==valscenter[q]:continue
      px=next(x for x in products if x['product']==p);qx=next(x for x in products if x['product']==q)
      for rate in (lo,hi):
       rp=clipped(px['g']-sum(px['a'].values(),F(0))*rate)
       rq=clipped(qx['g']-sum(qx['a'].values(),F(0))*rate)
       if (valscenter[p]>valscenter[q] and rp<rq) or (valscenter[p]<valscenter[q] and rp>rq):common_inv.append((key,delta,rate,p,q))
 # Save comprehensive certificates, exact case statuses, and disjoint denominator partitions.
 with open(ROOT/'results'/'certificates.csv','w',newline='') as h:
  w=csv.DictWriter(h,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 with open(ROOT/'results'/'case_statuses.csv','w',newline='') as h:
  w=csv.DictWriter(h,fieldnames=list(case_status[0]));w.writeheader();w.writerows(case_status)
 with open(ROOT/'results'/'case_denominators.csv','w',newline='') as h:
  fields=['config','year','family','eligibility','severity','gamma','metric','k','estimable_case_count','strict_count','all_tied_count','baseline_cutoff_tie_count']
  w=csv.DictWriter(h,fieldnames=fields);w.writeheader()
  for key,v in sorted(den.items()):
   if v['strict']+v['all_tied']+v['baseline_cutoff_tie']!=v['estimable']:raise AssertionError(f'denominator partition failed {key}')
   w.writerow(dict(zip(fields,key+ (v['estimable'],v['strict'],v['all_tied'],v['baseline_cutoff_tie']))))
 for name,data in [('quantization.csv',quant),('limiting_witnesses.csv',limiting),('limiting_outcomes.csv',limiting_out),('local_gap_slope.csv',local)]:
  with open(ROOT/'results'/name,'w',newline='') as h:
   w=csv.DictWriter(h,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
 # H1/H2 denominators use exact full case identities, never collapse event identity or multiply by model.
 def h1_pred(rg,rp,found):return h1_qualifies(rg,rp,found)
 h_summary=[]
 identities={tuple(r[x] for x in ('config','year','family','event','severity','gamma','eligibility','metric','k')) for r in case_status}
 statusmap={tuple(r[x] for x in ('config','year','family','event','severity','gamma','eligibility','metric','k')):r for r in case_status}
 rowmap={(tuple(r[x] for x in ('config','year','family','event','severity','gamma','eligibility','metric','k')),r['model']):r for r in rows}
 for config in ('primary','baseline_2024','remove_broad_fixed_selector'):
  for k in (2,3,4):
   for metric in ('fraction','usd'):
    cases=[i for i in identities if i[0]==config and i[8]==k and i[7]==metric]
    strict=[i for i in cases if statusmap[i]['status']=='strict']
    H1=H2=0
    for i in strict:
     g_row,o_row,p_row=(rowmap[(i,m)] for m in ('G','O','P'))
     rg=None if g_row['certificate_radius']=='certified_full_domain' else F(g_row['certificate_radius'])
     ro=None if o_row['certificate_radius']=='certified_full_domain' else F(o_row['certificate_radius'])
     rp=None if p_row['certificate_radius']=='certified_full_domain' else F(p_row['certificate_radius'])
     found=any(x['config']==i[0] and x['year']==i[1] and x['family']==i[2] and x['event']==i[3] and x['severity']==i[4] and x['gamma']==i[5] and x['eligibility']==i[6] and x['metric']==i[7] and int(x['k'])==i[8] for x in checkpoint)
     if h1_pred(rg,rp,found):H1+=1
     if (ro is None and rp is not None) or (ro is not None and rp is not None and ro>rp):H2+=1
    h_summary.append(dict(config=config,k=k,metric=metric,estimable_records=len(cases),strict_records=len(strict),all_tied_records=sum(statusmap[i]['status']=='all_tied' for i in cases),baseline_cutoff_tie_records=sum(statusmap[i]['status']=='baseline_cutoff_tie' for i in cases),H1_records=H1,H2_records=H2))
 with open(ROOT/'results'/'checkpoint_witnesses.csv','w',newline='') as h:
  w=csv.DictWriter(h,fieldnames=list(checkpoint[0]) if checkpoint else ['witness_id']);w.writeheader();w.writerows(checkpoint)
 with open(ROOT/'results'/'checkpoint_witness_outcomes.csv','w',newline='') as h:
  w=csv.DictWriter(h,fieldnames=list(checkpoint_out[0]) if checkpoint_out else ['witness_id']);w.writeheader();w.writerows(checkpoint_out)
 prim3=[x for x in h_summary if x['config']=='primary' and x['k']==3]
 primary_status={m:{s:sum(1 for i in identities if i[0]=='primary' and i[8]==3 and i[7]==m and statusmap[i]['status']==s) for s in ('strict','all_tied','baseline_cutoff_tie')} for m in ('fraction','usd')}
 h1_by_center={}
 for g0 in ('0','1/4','1/2','1'):
  for metric in ('fraction','usd'):
   h1_by_center[f'{g0}/{metric}']=sum(1 for i in identities if i[0]=='primary' and i[5]==F(g0) and i[7]==metric and i[8]==3 and statusmap[i]['status']=='strict' and h1_pred(None if rowmap[(i,'G')]['certificate_radius']=='certified_full_domain' else F(rowmap[(i,'G')]['certificate_radius']),None if rowmap[(i,'P')]['certificate_radius']=='certified_full_domain' else F(rowmap[(i,'P')]['certificate_radius']),any(x['config']==i[0] and x['year']==i[1] and x['family']==i[2] and x['event']==i[3] and x['severity']==i[4] and x['gamma']==i[5] and x['eligibility']==i[6] and x['metric']==i[7] and int(x['k'])==i[8] for x in checkpoint)))
 full_domain={m:sum(1 for i in identities if i[0]=='primary' and i[7]=='fraction' and i[8]==3 and statusmap[i]['status']=='strict' and rowmap[(i,m)]['certificate_radius']=='certified_full_domain') for m in ('G','O','P')}
 summary={'definitions':{'H1':'G radius is certified full-domain or >.05; P radius is finite and <=.05; a strict P reversal is found at δ=.05. Full-domain P never qualifies.','H2':'O radius strictly exceeds P radius; a full-domain O radius exceeds finite P.'},'by_config_k_metric':h_summary,'primary_k3_headline':{m:next(x for x in h_summary if x['config']=='primary' and x['k']==3 and x['metric']==m) for m in ('fraction','usd')},'H1_by_center_metric_k3':h1_by_center,'primary_k3_status_totals':{m:{s:sum(1 for i in identities if i[0]=='primary' and i[8]==3 and i[7]==m and statusmap[i]['status']==s) for s in ('strict','all_tied','baseline_cutoff_tie')} for m in ('fraction','usd')},'common_incumbent_fraction_strict_inversion_count':len(common_inv),'P_interval_equivalence_real_pair_count':len(p_interval_checks),'full_domain_primary_k3_fraction':full_domain,'checkpoint_witness_records':len(checkpoint)}
 (ROOT/'results'/'hypothesis_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
 (ROOT/'results'/'evaluation_summary.json').write_text(json.dumps({'primary_k3_metric_case_records':sum(x['estimable_records'] for x in prim3),'primary_k3_strict_by_metric':{x['metric']:x['strict_records'] for x in prim3},'primary_k3_status_partition':primary_status,'secondary_configs':{c:[x for x in h_summary if x['config']==c] for c in ('baseline_2024','remove_broad_fixed_selector')}},indent=2)+'\n')
 # Concrete root-selected empirical witness, reconstructed at its full frozen case identity.
 real=next((x for x in checkpoint if x['config']=='primary' and x['year']=='2025' and x['family']=='A' and x['event']=='A:842' and x['severity']==F(1) and x['gamma']==F(1,4) and x['eligibility']=='history' and x['metric']=='fraction' and x['k']==3 and x['model']=='P' and x['delta']==F(1,20) and x['p']=='848120' and x['q']=='848140'),None)
 if real is None:raise AssertionError('root-selected positive-center empirical witness not reproduced')
 realid=('primary','2025','A','A:842',F(1),F(1,4),'history','fraction')
 real_center=center_vectors[realid]; realout={x['product']:x['outcome'] for x in checkpoint_out if x['witness_id']==real['witness_id']}
 realrates=json.loads(real['factor_vector'])['product_rates']
 real_radii={m:next(x['certificate_radius'] for x in rows if x['config']=='primary' and x['year']=='2025' and x['family']=='A' and x['event']=='A:842' and x['severity']==F(1) and x['gamma']==F(1,4) and x['eligibility']=='history' and x['metric']=='fraction' and x['k']==3 and x['model']==m) for m in ('G','O','P')}
 real_example={'case_identity':{'config':'primary','year':2025,'family':'A','event':'A:842','severity_d':'1','gamma0':'1/4','eligibility':'history','metric':'fraction','k':3},'interpretation':'Full-interruption hypothetical accounting case for named origin 842; not a current event, route, forecast, or claim of engineering interchangeability.','selected_pair':{'p':'848120','q':'848140','center_gap':str(real_center['848120']-real_center['848140']),'checkpoint_delta':'1/20','strict_witness_outcomes':{'p':str(real['p_outcome']),'q':str(real['q_outcome'])},'annual_named_import_usd':{x['product']:str(totals[('2025',x['product'])]) for x in products if x['product'] in ('848120','848140')}},'certificate_radii':real_radii,'product_rate_vector':realrates,'center_outcomes':{k:str(v) for k,v in sorted(real_center.items())},'witness_outcomes':{k:str(v) for k,v in sorted(realout.items())},'what_to_verify_to_rule_out_this_reversal':['Model-level and duty-point compatibility for both product classes','Supplier qualification and current contract/quote evidence','Deliverable capacity and allocation at the required time horizon','Procurement lead time and landed cost','Whether the assumed independent product-level replacement rates can be jointly realized'],'scope':'Nominal trade-value accounting under the stated product-specific rate vector; not an optimal procurement decision.'}
 (ROOT/'results'/'real_empirical_example.json').write_text(json.dumps(real_example,indent=2)+'\n')
 with open(ROOT/'results'/'real_empirical_example.csv','w',newline='') as h:
  w=csv.writer(h);w.writerow(['product','annual_named_import_USD','center_residual_fraction','P_witness_rate','P_witness_residual_fraction'])
  for pid in sorted(real_center):w.writerow([pid,totals[('2025',pid)],real_center[pid],realrates[pid],realout[pid]])
 # Quantization summary, retaining original source classifications separately.
 qsummary={'records':len(quant),'rounded_vs_exact_status_changes':sum(x['exact_vs_rounded_status_changed'] for x in quant),'rounded_vs_exact_membership_changes':sum(x['exact_vs_rounded_membership_changed'] for x in quant),'rounded_reconstruction_matches_stored_count':sum(x['rounded_matches_stored'] for x in quant),'source_classification_disagreements':[x for x in quant if not x['rounded_matches_stored']]}
 (ROOT/'results'/'quantization_summary.json').write_text(json.dumps(qsummary,indent=2,default=str)+'\n')
 # H3 fixed four-category feasible counterexample, separate from initial H1 toy.
 d0,g0=F(1,2),F(1,4)
 synpairs={}
 for label,shocks,survivors in [('A',(F(90,100),F(88,100)),(F(10,100),F(12,100))),('B',(F(50,100),F(47,100)),(F(50,100),F(53,100)))]:
  centers=[d0*s-g0*e for s,e in zip(shocks,survivors)]; gap=centers[0]-centers[1]; denom=sum(survivors,F(0)); radius=gap/denom
  if min(centers)<=0 or radius<=0:raise AssertionError('invalid H3 synthetic')
  synpairs[label]={'interrupted_shares':[str(x) for x in shocks],'eligible_shares':[str(x) for x in survivors],'center_residuals':[str(x) for x in centers],'center_gap':str(gap),'D_P':str(denom),'radius':str(radius),'floor_or_cap_binding_at_radius':False}
 syn={'pair_A':synpairs['A'],'pair_B':synpairs['B'],'larger_gap_pair':'B','smaller_radius_pair':'B','radius_order_reverses_gap_order':F(synpairs['B']['center_gap'])>F(synpairs['A']['center_gap']) and F(synpairs['B']['radius'])<F(synpairs['A']['radius']),'all_values_synthetic':True,'d':str(d0),'gamma0':str(g0)}
 (ROOT/'results'/'h3_synthetic_counterexample.json').write_text(json.dumps(syn,indent=2)+'\n')
 # Empirical H3 comparisons: all event pairs within fixed config/year/family/severity/gamma/eligibility/metric/k strata.
 strata=defaultdict(list)
 for r in rows:
  if r['status']!='strict' or r['model']!='P':continue
  key=(r['config'],r['year'],r['family'],r['severity'],r['gamma'],r['eligibility'],r['metric'],r['k'])
  pr=None if r['certificate_radius']=='certified_full_domain' else F(r['certificate_radius'])
  strata[key].append((r['event'],r['boundary_gap'],pr))
 hs=[];discord=[]
 for key,items in strata.items():
  counts,disc,total=h3_pair_counts(items)
  for e1,e2,g1,g2,r1,r2 in disc:
   discord.append(dict(config=key[0],year=key[1],family=key[2],severity=key[3],gamma=key[4],eligibility=key[5],metric=key[6],k=key[7],event_a=e1,event_b=e2,gap_a=g1,gap_b=g2,radius_a=('certified_full_domain' if r1 is None else r1),radius_b=('certified_full_domain' if r2 is None else r2)))
  hs.append(dict(config=key[0],year=key[1],family=key[2],severity=key[3],gamma=key[4],eligibility=key[5],metric=key[6],k=key[7],strict_cases=len(items),total_pairs=total,concordant=counts['concordant'],discordant=counts['discordant'],gap_tie_only=counts['gap_tie_only'],radius_tie_only=counts['radius_tie_only'],both_ties=counts['both_ties'],incomparable=counts['incomparable']))
 for name,data in [('h3_strata.csv',hs),('h3_discordant_pairs.csv',discord)]:
  with open(ROOT/'results'/name,'w',newline='') as h:
   fields=list(data[0]) if data else ['config','year','family','severity','gamma','eligibility','metric','k']
   w=csv.DictWriter(h,fieldnames=fields);w.writeheader();w.writerows(data)
 (ROOT/'results'/'h3_summary.json').write_text(json.dumps({'matched_strata':len(hs),'total_event_pairs':sum(x['total_pairs'] for x in hs),'concordant':sum(x['concordant'] for x in hs),'discordant':sum(x['discordant'] for x in hs),'gap_tie_only':sum(x['gap_tie_only'] for x in hs),'radius_tie_only':sum(x['radius_tie_only'] for x in hs),'both_ties':sum(x['both_ties'] for x in hs),'incomparable':sum(x['incomparable'] for x in hs),'post_initial_operationalization':'Within fixed config/year/metric/k/family/severity/gamma/eligibility; events are the only comparison axis. This diagnostic grouping is disclosed as specified after proposal 1.'},indent=2)+'\n')
 print('cases',len(rows),'strict rows',sum(r['status']=='strict' for r in rows),'limiting',len(limiting),'H1/H2 summary',h_summary[:6])
if __name__=='__main__':main()
