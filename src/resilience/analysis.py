#!/usr/bin/env python3
"""Deterministic Decimal analysis for the frozen Stage 2 resilience protocol."""
from __future__ import annotations
import argparse, csv, hashlib, itertools, json, math, shutil, tempfile
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from pathlib import Path

D = Decimal
YEARS = (2022, 2023, 2024, 2025)
BASKET = ('841350','841360','841370','841381','848110','848120','848130','848140','848180')
SPECIAL_CODES = ('837','838','839','899','473','637','527','577','490','568')
TAUS = (D('0'), D('.01'), D('.05'))
DS = (D('.25'), D('.5'), D('1'))
GAMMAS = (D('0'), D('.25'), D('.5'), D('1'))

def dec(x):
    if isinstance(x, Decimal): return x
    if x in (None, '', 'NA'): raise ValueError('missing numeric value')
    return D(str(x))
def s(x): return 'NA' if x is None else (str(x) if isinstance(x, (str,int,bool)) else format(x, 'f'))
def quant(x, usd=False):
    return dec(x).quantize(D('.01') if usd else D('.000000000001'), rounding=ROUND_HALF_EVEN)
def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows: path.write_text('', encoding='utf-8'); return
    cols=sorted({k for row in rows for k in row})
    with path.open('w', newline='', encoding='utf-8') as f:
        w=csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for row in rows: w.writerow({k:s(v) for k,v in row.items()})
def read_csv(path):
    with path.open(newline='',encoding='utf-8') as f: return list(csv.DictReader(f))
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def ranks(values, usd=False):
    """Average ranks, ascending values receive rank 1; callers reverse if needed."""
    q=[quant(v,usd) for v in values]; order=sorted(range(len(q)),key=lambda i:q[i]); out=[None]*len(q)
    pos=0
    while pos<len(order):
        end=pos+1
        while end<len(order) and q[order[end]]==q[order[pos]]: end+=1
        rank=(D(pos+1)+D(end))/2
        for j in order[pos:end]: out[j]=rank
        pos=end
    return out
def spearman(a,b,usd_a=False,usd_b=False):
    if len(a)!=len(b) or len(a)<2: return None
    x=ranks(a,usd_a); y=ranks(b,usd_b)
    mx=sum(x)/len(x); my=sum(y)/len(y)
    vx=sum((z-mx)**2 for z in x); vy=sum((z-my)**2 for z in y)
    if not vx or not vy: return None
    return sum((u-mx)*(v-my) for u,v in zip(x,y))/(vx*vy).sqrt()
def top_sets(products, values, k, usd=False):
    q={p:quant(v,usd) for p,v in zip(products,values)}
    if not q: return set(),set(),0
    if len(set(q.values()))==1: return set(q),set(sorted(q)[:min(k,len(q))]),len(q)
    ordered=sorted(q,key=lambda p:(-q[p],p)); boundary=q[ordered[min(k,len(q))-1]]
    inclusive={p for p,v in q.items() if v>=boundary}
    return inclusive,set(ordered[:min(k,len(q))]),len(inclusive)
def jaccard(a,b): return D(len(a&b))/len(a|b) if a|b else None
def interval_ranks(intervals, usd=False):
    out={}
    for p,(lo,hi) in intervals.items():
        lo,hi=quant(lo,usd),quant(hi,usd)
        others=[(quant(a,usd),quant(b,usd)) for q,(a,b) in intervals.items() if q!=p]
        out[p]=(1+sum(a>hi for a,b in others),1+sum(b>=lo for a,b in others))
    return out

def cell_metrics(vals, W, U):
    A=sum((vals[o] for o in sorted(vals)),D(0))
    if W==0 or A==0: return {'A':A,'shares':{},'HHI':None,'C1':None,'C3':None,'hhi_lo':None,'hhi_hi':None,'c1_world_lo':None,'c1_world_hi':None}
    shares={i:v/A for i,v in vals.items() if v>0}
    ordered=sorted(shares.values(),reverse=True)
    hhi=sum((shares[o]*shares[o] for o in sorted(shares)),D(0)); a=[vals[o]/W for o in sorted(vals)]
    hlo=sum((x*x for x in a),D(0)); c1=max(a)
    return {'A':A,'shares':shares,'HHI':hhi,'C1':ordered[0],'C3':sum(ordered[:3]),
            'hhi_lo':hlo,'hhi_hi':hlo+2*(U/W)*c1+(U/W)**2,
            'c1_world_lo':c1,'c1_world_hi':c1+U/W}

def public_input_manifest(root):
    path=root/'results/stage2/analysis/input_manifest.json'
    manifest=json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('version')!=1 or not isinstance(manifest.get('inputs'),list):
        raise ValueError('invalid public frozen-input manifest')
    return manifest

def verify_inputs(root):
    manifest=public_input_manifest(root)
    bad=[]
    for x in manifest['inputs']:
        rel=Path(x['path'])
        if rel.is_absolute() or '..' in rel.parts: raise ValueError('input manifest path must be project-relative')
        p=root/rel
        if not p.exists() or sha(p)!=x['sha256']: bad.append(x['path'])
    if bad: raise ValueError('frozen input hash mismatch: '+', '.join(bad))
    q=read_csv(root/'data/processed/stage2/quality.csv')
    keys={(int(r['year']),r['hs6']) for r in q}
    if len(q)!=36 or keys!={(y,p) for y in YEARS for p in BASKET}:
        raise ValueError('D10 quality table does not contain exactly the frozen 36-cell panel')
    bad=[f"{r['year']}/{r['hs6']}" for r in q if r['cell_status']!='accepted']
    if bad: raise ValueError('D10 contains failed quality cells: '+', '.join(bad))
    return manifest

def load_panel(root):
    qrows=read_csv(root/'data/processed/stage2/quality.csv')
    q={(int(r['year']),r['hs6']):r for r in qrows}
    rows=read_csv(root/'data/processed/stage2/origins.csv')
    vals=defaultdict(dict)
    for r in rows:
        if r['record_type']!='named_origin' or r['exact_duplicate'].lower()=='true': continue
        code=r['hs6']; year=int(r['year']); origin=int(r['partnerCode']); v=dec(r['primaryValue_decimal'])
        if v<0: raise ValueError(f'negative named origin {year}/{code}/{origin}')
        vals[(year,code)][origin]=v
    world={key:dec(row['World_W_USD']) for key,row in q.items()}
    unknown={key:dec(row['U_USD']) for key,row in q.items()}
    for year in YEARS:
        for code in BASKET:
            key=(year,code); named=sum(vals[key].values(),D(0))
            if key not in q or abs(world[key]-named-unknown[key])>max(D(1),D('.000001')*world[key]):
                raise ValueError(f'analysis panel fails reconciliation at {year}/{code}')
    return vals,world,unknown,q

def make_configurations():
    configs=[]
    def add(name, threshold=D('.01'), codes=BASKET, selector='reselect', selector_year=2025, hist_years=(2022,2023,2024), hist_need=2, kind='sensitivity', tau=D('.01')):
        configs.append(dict(config=name,threshold=threshold,codes=tuple(codes),selector=selector,selector_year=selector_year,
                            hist_years=hist_years,hist_need=hist_need,kind=kind,tau=tau))
    add('primary',kind='primary')
    add('quality_0pct',D('0'))
    add('quality_5pct',D('.05'))
    add('tau_0',tau=D('0'))
    add('tau_5pct',tau=D('.05'))
    add('history_1_prior',hist_need=1)
    add('history_all_3_prior',hist_need=3)
    add('baseline_2024',selector_year=2024,hist_years=(2022,2023),hist_need=2)
    add('remove_broad_fixed_selector',codes=tuple(c for c in BASKET if c not in ('841381','848180')),selector='fixed_primary')
    add('remove_broad_reselected',codes=tuple(c for c in BASKET if c not in ('841381','848180')))
    # τ controls both history-set descriptions and historical eligibility; τ=0 means strictly positive.
    return configs

def eligibility_for(product, origin, shares_by_year, cfg):
    incumbent=shares_by_year.get((cfg['selector_year'],product),{}).get(origin,D(0))>0
    history_years=cfg['hist_years']; need=cfg['hist_need']
    # A missing historical cell is a refusal, never an absent supplier.
    if any((year,product) not in shares_by_year for year in history_years): return incumbent,False,False
    tau=cfg.get('tau',D('.01'))
    count=sum((shares_by_year[(year,product)].get(origin,D(0))>0 if tau==0
               else shares_by_year[(year,product)].get(origin,D(0))>=tau) for year in history_years)
    return incumbent, incumbent and count>=need, True

def selector_for(cfg, data, shares_by_year, products):
    if cfg['selector']=='fixed_primary' and cfg['config']!='primary':
        base_products=tuple(cfg.get('primary_products',products))
        # Freeze the complete primary selector snapshot, including certainty and all dominants.
        return selector_for({**cfg,'selector':'reselect','codes':base_products,'config':'primary'},
                            data,shares_by_year,base_products)
    year=cfg['selector_year']; pooled=defaultdict(D); origin_u=D(0)
    for p in products:
        if (year,p) not in data: continue
        origin_u += data[(year,p)]['U']
        for o,v in sorted(data[(year,p)]['values'].items()):
            if v>0: pooled[o]+=v
    # exact tie: numeric partner ascending
    k3=tuple(sorted((o for o,v in pooled.items() if v>0),key=lambda o:(-pooled[o],o))[:3])
    dominant={p:(min((o for o,v in data[(year,p)]['values'].items() if v>0),
                     key=lambda o:(-data[(year,p)]['values'][o],o)) if any(v>0 for v in data[(year,p)]['values'].values()) else None) for p in products}
    K=tuple(sorted(set(k3)|{o for o in dominant.values() if o is not None}))
    # Conservative sufficient pooled selector condition; unknown value could hide an excluded origin.
    all_origins=sorted((o for o,v in pooled.items() if v>0),key=lambda o:(-pooled[o],o)); selected_cert=True
    if len(all_origins)>len(k3) and k3:
        cutoff=min(pooled[o] for o in k3)
        selected_cert=all(cutoff>pooled[o]+origin_u for o in all_origins if o not in k3) and cutoff>origin_u
    elif k3: selected_cert=min(pooled[o] for o in k3)>origin_u
    dom_cert={}
    for p,o in dominant.items():
        if o is None: dom_cert[p]=False; continue
        v=data[(year,p)]['values']; u=data[(year,p)]['U']; top=v[o]
        dom_cert[p]=all(top>val+u for other,val in v.items() if other!=o and val>0) and top>u
    return {'K3':k3,'K':K,'dominant':dominant,'pooled_certainty':selected_cert,'dominant_certainty':dom_cert}

def scenarios_for(products, selector, data, shares, cfg):
    k3=selector['K3']; K=selector['K']; definitions=[]
    for o in K: definitions.append(('A',frozenset([o]),f'A:{o}'))
    for pair in itertools.combinations(k3,2): definitions.append(('B',frozenset(pair),'B:'+','.join(map(str,pair))))
    if len(k3)>=2: definitions.append(('B',frozenset(k3),'B:K3'))
    if any(o is not None for o in selector['dominant'].values()):
        definitions.append(('C',frozenset(),'C:own_product_dominant'))
    # Dedupe within config and family, retaining every selector provenance label.
    dedup={}
    for fam,G,label in definitions:
        key=(fam,G)
        if key not in dedup: dedup[key]={'family':fam,'G':G,'labels':[]}
        dedup[key]['labels'].append(label)
    dedup[('N',frozenset())]={'family':'N','G':frozenset(),'labels':['no_shock_validation']}
    out=[]; use_year=cfg['selector_year']
    hist_grid_estimable=all((year,p) in shares for year in cfg['hist_years'] for p in products)
    for spec in sorted(dedup.values(),key=lambda x:(x['family'],tuple(sorted(x['G'])))):
        fam,G=spec['family'],spec['G']
        cert=(selector['pooled_certainty'] and all(selector['dominant_certainty'].values()) if fam=='A'
              else selector['pooled_certainty'] if fam=='B' else True)
        for p in products:
            m=data[(use_year,p)]; sh=m['shares']; W=m['W']; U=m['U']; A=m['A']
            product_G=frozenset([selector['dominant'][p]]) if fam=='C' and selector['dominant'][p] is not None else G
            product_cert=selector['dominant_certainty'].get(p,False) if fam=='C' else cert
            gross_base=sum((sh.get(o,D(0)) for o in sorted(product_G)),D(0))
            for d in DS:
                g=d*gross_base
                for eligibility in ('incumbent','history'):
                    estimable=(eligibility=='incumbent' or hist_grid_estimable)
                    eligible_sh=D(0) if estimable else None
                    if estimable:
                        for o,v in sorted(sh.items()):
                            if o in product_G: continue
                            inc,hist,known=eligibility_for(p,o,shares,cfg)
                            if (inc if eligibility=='incumbent' else hist): eligible_sh+=v
                    for gamma in GAMMAS:
                        if estimable:
                            b=gamma*eligible_sh; repl=min(g,b); ell=g-repl; usd=A*ell
                            L=A*ell/W if W else None; H=(A*ell+U)/W if W else None
                            lower_usd=A*ell; upper_usd=A*ell+U
                            gamma_star=(D(0) if g==0 else D('Infinity') if eligible_sh==0 else g/eligible_sh)
                        else:
                            b=repl=ell=usd=L=H=lower_usd=upper_usd=gamma_star=None
                        out.append({'config':cfg['config'],'year':use_year,'product':p,'family':fam,
                          'scenario_id':fam+':'+','.join(map(str,sorted(G))),'selector_labels':'|'.join(spec['labels']),
                          'interrupted_origins':','.join(map(str,sorted(product_G))),'selector_certain':product_cert,
                          'severity_d':d,'gamma':gamma,'eligibility':eligibility,'history_estimable':hist_grid_estimable,'outcome_estimable':estimable,
                          'gross_share':g,'replacement_share':repl,'residual_share':ell,'identified_fraction':ell,
                          'identified_usd':usd,'total_import_fraction_lo':L,'total_import_fraction_hi':H,
                          'usd_exposure_lo':lower_usd,'usd_exposure_hi':upper_usd,'eligible_survivor_share':eligible_sh,
                          'gamma_star':gamma_star,'gamma_star_over_one':gamma_star is not None and gamma_star>D(1),
                          'metric_fraction_rank':None,'metric_usd_rank':None,'fraction_rank_best':None,'fraction_rank_worst':None,
                          'usd_rank_best':None,'usd_rank_worst':None,'scenario_all_tied_fraction':False,'scenario_all_tied_usd':False,
                          'top2_fraction':False,'top3_fraction':False,'top4_fraction':False,'top2_usd':False,'top3_usd':False,'top4_usd':False})
    # Attach ranks, tie-inclusive memberships and conservative interval ranks per configuration/scenario/parameter.
    groups=defaultdict(list)
    for row in out:
        if row['outcome_estimable']: groups[(row['config'],row['year'],row['family'],row['scenario_id'],row['severity_d'],row['gamma'],row['eligibility'])].append(row)
    for group in groups.values():
        ps=[r['product'] for r in group]; fr=[r['identified_fraction'] for r in group]; ur=[r['identified_usd'] for r in group]
        # higher value gets rank 1
        for metric,vals,isusd in [('fraction',fr,False),('usd',ur,True)]:
            rr=ranks(vals, isusd); rr=[D(len(rr)+1)-x for x in rr]
            for r,x in zip(group,rr): r['metric_'+metric+'_rank']=x
            if len({quant(x, isusd) for x in vals})==1:
                for r in group:r['scenario_all_tied_'+metric]=True
            for k in (2,3,4):
                inclusive,exact,_=top_sets(ps,vals,k,isusd)
                for r in group:
                    r[f'top{k}_{metric}']=r['product'] in inclusive
                    r[f'top{k}_{metric}_exact']=r['product'] in exact
        for metric,low,high,prefix in [('fraction','total_import_fraction_lo','total_import_fraction_hi','fraction'),('usd','usd_exposure_lo','usd_exposure_hi','usd')]:
            ir=interval_ranks({r['product']:(r[low],r[high]) for r in group}, metric=='usd')
            for r in group:r[prefix+'_rank_best'],r[prefix+'_rank_worst']=ir[r['product']]
    return out

def full_family_summaries(all_scenarios, configurations):
    """Product-level full-grid summaries; all-tied cases leave only their metric denominator."""
    summaries=[]
    for cfg in configurations:
        observed=[r for r in all_scenarios if r['config']==cfg['config']]
        declared=set(cfg['codes'])
        eligible_products=set(cfg.get('universe',()))
        for family in ('A','B','C'):
            family_rows=[r for r in observed if r['family']==family]
            case_groups=defaultdict(list)
            for r in family_rows:
                key=(r['scenario_id'],r['severity_d'],r['gamma'],r['eligibility'])
                case_groups[key].append(r)
            for metric,field,usd,low,high,rankfield,bestfield,worstfield in (
                ('fraction','identified_fraction',False,'total_import_fraction_lo','total_import_fraction_hi','metric_fraction_rank','fraction_rank_best','fraction_rank_worst'),
                ('usd','identified_usd',True,'usd_exposure_lo','usd_exposure_hi','metric_usd_rank','usd_rank_best','usd_rank_worst')):
                informative=[]; tied=0; inestimable=0
                for key,rows in case_groups.items():
                    available=[r for r in rows if r['outcome_estimable'] and r[field] is not None]
                    if len(available)!=len(rows): inestimable+=1
                    if len(available)<2: continue
                    if len({quant(r[field],usd) for r in available})==1:
                        tied+=1
                    else: informative.append((key,{r['product']:r for r in available}))
                for product in BASKET:
                    product_cases=[(key,rr[product]) for key,rr in informative if product in rr]
                    memberships={k:sum(bool(row[f'top{k}_{metric}']) for _,row in product_cases) for k in (2,3,4)}
                    rows=[row for _,row in product_cases]
                    estimable=product in eligible_products
                    status=('summarized' if rows else 'no_informative_cases' if estimable else
                            'excluded_quality_threshold' if product in declared else 'not_in_declared_universe')
                    outcomes=[row[field] for row in rows]
                    summaries.append({'config':cfg['config'],'product':product,'declared_universe_member':product in declared,
                      'eligible_in_universe':product in eligible_products,'family':family,'metric':metric,'status':status,
                      'informative_case_count':len(rows),'family_informative_case_count':len(informative),
                      'all_tied_case_count':tied,'inestimable_case_count':inestimable,
                      'outcome_min':min(outcomes) if outcomes else None,'outcome_max':max(outcomes) if outcomes else None,
                      'rank_min':min((r[rankfield] for r in rows),default=None),'rank_max':max((r[rankfield] for r in rows),default=None),
                      'interval_lower_min':min((r[low] for r in rows if r[low] is not None),default=None),
                      'interval_upper_max':max((r[high] for r in rows if r[high] is not None),default=None),
                      'best_rank_min':min((r[bestfield] for r in rows),default=None),'worst_rank_max':max((r[worstfield] for r in rows),default=None),
                      **{f'top{k}_membership_count':memberships[k] for k in (2,3,4)},
                      **{f'top{k}_membership_rate':D(memberships[k])/len(rows) if rows else None for k in (2,3,4)}})
    return summaries

def pairwise_rank_diagnostics(products, left, right, usd=False):
    """Count strict pair-order reversals and tie-status changes between two rankings."""
    lq={p:quant(v,usd) for p,v in zip(products,left)}; rq={p:quant(v,usd) for p,v in zip(products,right)}
    reversals=left_tied=right_tied=changes=0
    for i,p in enumerate(products):
        for q in products[i+1:]:
            ls=(lq[p]>lq[q])-(lq[p]<lq[q]); rs=(rq[p]>rq[q])-(rq[p]<rq[q])
            if ls and rs: reversals += ls!=rs
            if ls==0:left_tied+=1
            if rs==0:right_tied+=1
            changes += (ls==0)!=(rs==0)
    return reversals,left_tied,right_tied,changes

def build_priority_verdicts(all_scenarios, configurations):
    by_config={cfg['config']:cfg for cfg in configurations}; results=[]
    for product in BASKET:
        for family in ('A','B','C'):
            for metric,topfield,all_tied_field,worst_field in (
                ('fraction','top3_fraction','scenario_all_tied_fraction','fraction_rank_worst'),
                ('usd','top3_usd','scenario_all_tied_usd','usd_rank_worst')):
                component=[]
                for cfg in configurations:
                    name=cfg['config']; rows=[r for r in all_scenarios if r['config']==name and r['product']==product and r['family']==family]
                    declared=product in cfg['codes']; in_universe=product in cfg.get('universe',())
                    untestable=[]; failures=[]
                    if not declared: untestable.append('product not part of declared basket sensitivity')
                    elif not in_universe: untestable.append('product excluded by quality eligibility or empty configuration universe')
                    if not rows and not untestable: untestable.append('required family has no estimable scenario rows')
                    bad_history=[r for r in rows if r['eligibility']=='history' and not r['outcome_estimable']]
                    if bad_history: untestable.append('historical eligibility grid refused because one or more required product-years are missing')
                    available=[r for r in rows if r['outcome_estimable'] and r[topfield] is not None]
                    informative=[r for r in available if not r[all_tied_field]]
                    tied=len(available)-len(informative)
                    if in_universe and not untestable and not informative:
                        untestable.append('no informative non-all-tied scenarios for this metric and family')
                    if informative:
                        selector_fail=sum(not r['selector_certain'] for r in informative)
                        worst_fail=sum(r[worst_field] is None or r[worst_field]>3 for r in informative)
                        membership_fail=sum(not r[topfield] for r in informative)
                        if selector_fail: failures.append(f'selector certainty fails in {selector_fail} informative cases')
                        if worst_fail: failures.append(f'unknown-origin worst rank exceeds top three in {worst_fail} informative cases')
                        if membership_fail: failures.append(f'not a tie-inclusive top-three member in {membership_fail} informative cases')
                    status=('undetermined' if untestable else
                            'persistent_high_screening_priority' if not failures else 'assumption-sensitive')
                    row={'product':product,'family':family,'metric':metric,'config':name,'config_kind':cfg['kind'],
                      'status':status,'declared_universe_member':declared,'eligible_in_configuration':in_universe,
                      'informative_case_count':len(informative),'all_tied_case_count':tied,
                      'top3_membership_count':sum(bool(r[topfield]) for r in informative),
                      'unknown_rank_failure_count':sum(r[worst_field] is None or r[worst_field]>3 for r in informative),
                      'selector_uncertain_case_count':sum(not r['selector_certain'] for r in informative),
                      'untestable_reasons':'|'.join(untestable),'condition_failures':'|'.join(failures)}
                    results.append(row); component.append(row)
                untestable_any=any(r['status']=='undetermined' for r in component)
                all_pass=all(r['status']=='persistent_high_screening_priority' for r in component)
                results.append({'product':product,'family':family,'metric':metric,'config':'ALL_APPLICABLE',
                  'config_kind':'aggregate','status':'undetermined' if untestable_any else 'persistent_high_screening_priority' if all_pass else 'assumption-sensitive',
                  'declared_universe_member':True,'eligible_in_configuration':all(r['eligible_in_configuration'] for r in component),
                  'informative_case_count':sum(r['informative_case_count'] for r in component),
                  'all_tied_case_count':sum(r['all_tied_case_count'] for r in component),
                  'top3_membership_count':sum(r['top3_membership_count'] for r in component),
                  'unknown_rank_failure_count':sum(r['unknown_rank_failure_count'] for r in component),
                  'selector_uncertain_case_count':sum(r['selector_uncertain_case_count'] for r in component),
                  'untestable_reasons':'|'.join(f"{r['config']}: {r['untestable_reasons']}" for r in component if r['untestable_reasons']),
                  'condition_failures':'|'.join(f"{r['config']}: {r['condition_failures']}" for r in component if r['condition_failures'])})
    return results

def run(root):
    """Run analysis at the frozen precision without changing the caller context."""
    with localcontext() as context:
        context.prec = 42
        return _run_at_frozen_precision(root)


def _run_at_frozen_precision(root):
    frozen_manifest=verify_inputs(root); vals,world,unknown,q=load_panel(root)
    primary=tuple(p for p in BASKET if all(q[(y,p)]['eligible_1pct'].lower()=='true' for y in YEARS))
    products9=list(BASKET); annual=[]; shares_by_year={}; data={}
    for y in YEARS:
        for p in BASKET:
            key=(y,p); v=vals[key]; m=cell_metrics(v,world[key],unknown[key]); quality=q[key]
            cell_eligible=quality['eligible_1pct'].lower()=='true'
            balanced_eligible=p in primary
            annual.append({'year':y,'product':p,'candidate_baseline':y==2025,'cell_eligible_1pct':cell_eligible,
              'primary_balanced_eligible':balanced_eligible,'primary_eligible':balanced_eligible,
              'quality_0pct':quality['eligible_0pct'].lower()=='true','quality_5pct':quality['eligible_5pct'].lower()=='true',
              'A_USD':m['A'],'W_USD':world[key],'U_USD':unknown[key],'identified_share_A_over_W':m['A']/world[key] if world[key] else None,
              'unknown_share_U_over_W':unknown[key]/world[key] if world[key] else None,'HHI':m['HHI'],'C1':m['C1'],'C3':m['C3'],
              'HHI_all_import_lower':m['hhi_lo'],'HHI_all_import_upper':m['hhi_hi'],'C1_all_import_lower':m['c1_world_lo'],'C1_all_import_upper':m['c1_world_hi'],
              'top_origin':min((o for o,x in v.items() if x>0),key=lambda o:(-v[o],o)) if any(x>0 for x in v.values()) else None,
              'positive_origin_count':sum(x>0 for x in v.values()),'origin_count':sum(x>0 for x in v.values())})
            shares_by_year[key]=m['shares']; data[key]={'values':v,'shares':m['shares'],'A':m['A'],'W':world[key],'U':unknown[key],'metrics':m}
    # History diagnostics at threshold 0, 1%, 5%; complete cells required.
    history=[]
    for tau in TAUS:
        for y in YEARS[1:]:
            for p in BASKET:
                prev=shares_by_year[(y-1,p)]; cur=shares_by_year[(y,p)]
                if tau==0:
                    a={o for o,v in prev.items() if v>0}; b={o for o,v in cur.items() if v>0}
                else:
                    a={o for o,v in prev.items() if v>=tau}; b={o for o,v in cur.items() if v>=tau}
                union=a|b; j=D(len(a&b))/len(union) if union else None
                turnover=sum((abs(cur.get(o,D(0))-prev.get(o,D(0))) for o in sorted(set(cur)|set(prev))),D(0))/2
                history.append({'threshold_tau':tau,'year':y,'product':p,'prior_set_size':len(a),'current_set_size':len(b),
                  'intersection_size':len(a&b),'union_size':len(union),'jaccard':j,'turnover':turnover,
                  'top_origin_changed':data[(y-1,p)]['metrics']['C1'] is not None and data[(y,p)]['metrics']['C1'] is not None and min(vals[(y-1,p)],key=lambda o:(-vals[(y-1,p)][o],o))!=min(vals[(y,p)],key=lambda o:(-vals[(y,p)][o],o))})
    history_summary=[]
    for p in BASKET:
        for metric in ('HHI','C1','C3'):
            entries=[r for r in annual if r['product']==p and r[metric] is not None]
            history_summary.append({'product':p,'metric':metric,'years':','.join(str(r['year']) for r in entries),
              'minimum':min((r[metric] for r in entries),default=None),'maximum':max((r[metric] for r in entries),default=None),
              'range':max((r[metric] for r in entries),default=D(0))-min((r[metric] for r in entries),default=D(0)) if entries else None,
              'top_origin_changed_any_year':any(r['top_origin_changed'] for r in history if r['product']==p and r['threshold_tau']==D('.01'))})
    origin_names={}
    for r in read_csv(root/'data/processed/stage2/origins.csv'):
        if r['record_type']=='named_origin': origin_names[(int(r['year']),r['hs6'],int(r['partnerCode']))]=r['partner_reference_name']
    origin_shares=[]
    for (year,product),m in sorted(data.items()):
        for origin,value in sorted(m['values'].items()):
            origin_shares.append({'year':year,'product':product,'origin_code':origin,'origin_name':origin_names.get((year,product,origin),''),
              'identified_origin_import_USD':value,'share_of_identified_origins':m['shares'].get(origin,D(0)),
              'share_of_world_imports':value/m['W'] if m['W'] else None,
              'cell_eligible_1pct':q[(year,product)]['eligible_1pct'].lower()=='true','primary_balanced_eligible':product in primary})
    configs=make_configurations(); config_rows=[]; selectors=[]; all_scenarios=[]
    for cfg in configs:
        cfg['primary_products']=primary
        universe=tuple(p for p in cfg['codes'] if all(q[(y,p)][f"eligible_{int(cfg['threshold']*100)}pct"].lower()=='true' for y in YEARS))
        cfg['universe']=universe
        status='estimable' if universe else 'non_estimable_empty_universe'
        if cfg['config']=='quality_0pct' and len(universe)<6: status='limited_universe_below_minimum_gate' if universe else status
        config_rows.append({'config':cfg['config'],'kind':cfg['kind'],'quality_threshold':cfg['threshold'],'declared_codes':','.join(cfg['codes']),
          'universe':','.join(universe),'universe_n':len(universe),'selector':cfg['selector'],'selector_year':cfg['selector_year'],
          'history_years':','.join(map(str,cfg['hist_years'])),'history_eligibility_threshold':cfg['tau'],'history_required_years':cfg['hist_need'],'status':status,
          'applicable_tau':cfg['tau'],'notes':'full d × gamma × incumbent/history grid' if universe else 'no scenarios estimable'})
        if not universe: continue
        sel=selector_for(cfg,data,shares_by_year,universe)
        selectors.append({'config':cfg['config'],'universe':','.join(universe),'selector_year':cfg['selector_year'],
          'K3':','.join(map(str,sel['K3'])),'K_union':','.join(map(str,sel['K'])),'dominant_by_product':json.dumps(sel['dominant'],sort_keys=True),
          'pooled_K3_certain':sel['pooled_certainty'],'dominant_certainty':json.dumps(sel['dominant_certainty'],sort_keys=True),
          'selector_certain':sel['pooled_certainty'] and all(sel['dominant_certainty'].values())})
        all_scenarios.extend(scenarios_for(universe,sel,data,shares_by_year,cfg))
    # Ranking correlations within each scenario versus HHI/C1 and dollar scale; complete all d/gamma/eligibility keys.
    rank_rows=[]; groupkeys=defaultdict(list)
    for r in all_scenarios: groupkeys[(r['config'],r['family'],r['scenario_id'],r['severity_d'],r['gamma'],r['eligibility'])].append(r)
    annual_lookup={(r['year'],r['product']):r for r in annual}
    for key,group in groupkeys.items():
        y=int(group[0]['year']); ps=[r['product'] for r in group]
        h=[annual_lookup[(y,p)]['HHI'] for p in ps]; c=[annual_lookup[(y,p)]['C1'] for p in ps]
        f=[r['identified_fraction'] for r in group]; u=[r['identified_usd'] for r in group]; a=[annual_lookup[(y,p)]['A_USD'] for p in ps]
        for metric,x,z,xusd,zusd in [('HHI_vs_C1',h,c,False,False),('HHI_vs_fraction',h,f,False,False),('C1_vs_fraction',c,f,False,False),('fraction_vs_usd',f,u,False,True),('A_vs_usd',a,u,True,True)]:
            rho=spearman(x,z,xusd,zusd)
            for k in (2,3,4):
                one,_,_=top_sets(ps,x,k,xusd); two,_,_=top_sets(ps,z,k,zusd)
                display1=set(sorted(ps,key=lambda p:(-quant(x[ps.index(p)],xusd),p))[:min(k,len(ps))])
                display2=set(sorted(ps,key=lambda p:(-quant(z[ps.index(p)],zusd),p))[:min(k,len(ps))])
                rank_rows.append({'config':key[0],'family':key[1],'scenario_id':key[2],'severity_d':key[3],'gamma':key[4],
                  'eligibility':key[5],'comparison':metric,'spearman':rho,'k':k,'tie_inclusive_jaccard':jaccard(one,two),
                  'tie_inclusive_left_size':len(one),'tie_inclusive_right_size':len(two),'display_exact_intersection_over_k':D(len(display1&display2))/min(k,len(ps)),
                  'all_tied_left':len({quant(v,xusd) for v in x})==1,'all_tied_right':len({quant(v,zusd) for v in z})==1})
    # Separate family/metric grid coverage, outcome/rank ranges and membership rates.
    slice_summaries=[]; sumgroups=defaultdict(list)
    for r in all_scenarios:
        sumgroups[(r['config'],r['family'],r['eligibility'],r['severity_d'],r['gamma'])].append(r)
    for key,group in sumgroups.items():
        for metric,field,usd in [('fraction','identified_fraction',False),('usd','identified_usd',True)]:
            byscenario=defaultdict(list)
            for r in group:byscenario[r['scenario_id']].append(r)
            nondeg=[x for x in byscenario.values() if len({quant(r[field],usd) for r in x})>1]
            union_products=sorted({r['product'] for r in group}); membership=defaultdict(int)
            for sc in nondeg:
                for r in sc:
                    if r[f'top3_{metric}']:membership[r['product']]+=1
            values=[r[field] for r in group]; ranksv=[r[f'metric_{metric}_rank'] for r in group]
            slice_summaries.append({'config':key[0],'family':key[1],'eligibility':key[2],'severity_d':key[3],'gamma':key[4],'metric':metric,
              'scenario_count':len(byscenario),'nondegenerate_scenario_count':len(nondeg),'all_tied_scenario_count':len(byscenario)-len(nondeg),
              'scenario_grid_coverage':D(len(nondeg))/len(byscenario) if byscenario else None,'outcome_min':min(values) if values else None,'outcome_max':max(values) if values else None,
              'rank_min':min(ranksv) if ranksv else None,'rank_max':max(ranksv) if ranksv else None,
              'top3_membership_counts':json.dumps({p:membership[p] for p in union_products},sort_keys=True),
              'top3_membership_rates':json.dumps({p:(D(membership[p])/len(nondeg) if nondeg else None) for p in union_products},sort_keys=True,default=s)})
    summaries=full_family_summaries(all_scenarios,configs)
    # Sensitivities are compared at matched literal shocks and parameter points, re-ranked on common products.
    common=[]; sens_names=[c['config'] for c in configs if c['config']!='primary']
    config_universes={r['config']:set(filter(None,r['universe'].split(','))) for r in config_rows}
    for cfgname in sens_names:
        cp=tuple(sorted(set(primary)&config_universes[cfgname]))
        cfgobj=next(c for c in configs if c['config']==cfgname); alt_year=cfgobj['selector_year']
        for metric in ('HHI','C1','A_USD'):
            pvals=[annual_lookup[(2025,p)][metric] for p in cp]
            avals=[annual_lookup[(alt_year,p)][metric] for p in cp]
            isusd=metric=='A_USD'
            for k in (2,3,4):
                left,_,_=top_sets(cp,pvals,k, isusd);right,_,_=top_sets(cp,avals,k,isusd)
                common.append({'config':cfgname,'family':'annual_baseline','comparison_metric':metric,'common_products':','.join(cp),
                  'common_n':len(cp),'status':'matched' if cp else 'unestimable_empty_common_universe','comparison_years':f'2025_vs_{alt_year}',
                  'spearman':spearman(pvals,avals,isusd,isusd) if cp else None,'k':k,
                  'tie_inclusive_jaccard':jaccard(left,right),'tie_inclusive_left_size':len(left),'tie_inclusive_right_size':len(right),
                  'note':'reranked on common products; within-universe rankings remain in rank_comparisons.csv'})
        # Build scenario keys from the literal product-specific interrupted-origin vector.
        grouped=defaultdict(dict)
        for r in all_scenarios:
            if r['config'] not in ('primary',cfgname) or r['family'] not in ('A','B','C') or r['product'] not in cp:continue
            key=(r['family'],r['scenario_id'],r['severity_d'],r['gamma'],r['eligibility'])
            grouped[(r['config'],key)][r['product']]=r
        def signature(rows):
            return tuple((p,rows[p]['interrupted_origins']) for p in cp)
        indexed={}
        for (name,basekey),rows in grouped.items():
            if len(rows)!=len(cp):continue
            literal=(basekey[0],basekey[1],signature(rows),basekey[2],basekey[3],basekey[4])
            indexed[(name,literal)]=rows
        pkeys={key for name,key in indexed if name=='primary'}
        ckeys={key for name,key in indexed if name==cfgname}
        for key in sorted(pkeys|ckeys,key=str):
            leftrows=indexed.get(('primary',key));rightrows=indexed.get((cfgname,key))
            if leftrows and rightrows: status='matched'
            elif leftrows: status='unmatched_primary_definition'
            else: status='unmatched_sensitivity_definition'
            fam,scenario_id,sig,d,gamma,elig=key
            for metric,field,usd in [('fraction','identified_fraction',False),('usd','identified_usd',True)]:
                if not leftrows or not rightrows:
                    common.append({'config':cfgname,'family':fam,'scenario_id':scenario_id,'comparison_metric':metric,'scenario_signature':json.dumps(sig),
                      'severity_d':d,'gamma':gamma,'eligibility':elig,'common_products':','.join(cp),'common_n':len(cp),
                      'status':status,'reason':'literal shock definition is unmatched across configurations'})
                    continue
                lv=[leftrows[p][field] for p in cp]; rv=[rightrows[p][field] for p in cp]
                rho=spearman(lv,rv,usd,usd)
                lt,le,_=top_sets(cp,lv,3,usd);rt,re,_=top_sets(cp,rv,3,usd)
                reversals,tied_l,tied_r,tie_changes=pairwise_rank_diagnostics(cp,lv,rv,usd)
                for k in (2,3,4):
                    la,lex,_=top_sets(cp,lv,k,usd);ra,rex,_=top_sets(cp,rv,k,usd)
                    common.append({'config':cfgname,'family':fam,'scenario_id':scenario_id,'comparison_metric':metric,'scenario_signature':json.dumps(sig),
                      'severity_d':d,'gamma':gamma,'eligibility':elig,'common_products':','.join(cp),'common_n':len(cp),
                      'status':'matched','spearman':rho,'k':k,'tie_inclusive_jaccard':jaccard(la,ra),
                      'tie_inclusive_left_size':len(la),'tie_inclusive_right_size':len(ra),
                      'display_exact_intersection_over_k':D(len(lex&rex))/min(k,len(cp)),
                      'strict_pairwise_order_reversals':reversals,'left_tied_pair_count':tied_l,'right_tied_pair_count':tied_r,
                      'pair_tie_status_changes':tie_changes})
        if cfgname=='quality_0pct' and not cp:
            for family in ('A','B','C'):
                for metric in ('fraction','usd'):
                    for k in (2,3,4):
                        common.append({'config':cfgname,'family':family,'comparison_metric':metric,'common_products':'',
                          'common_n':0,'status':'unestimable_empty_common_universe','scenario_id':'NA','k':k,
                          'reason':'quality_0pct has no products shared with the primary universe'})
    verdicts=build_priority_verdicts(all_scenarios,configs)
    outdir=root/'results/stage2/analysis'; outdir.mkdir(parents=True,exist_ok=True)
    outputs={'annual_metrics.csv':annual,'origin_shares.csv':origin_shares,'history.csv':history,'history_summary.csv':history_summary,
      'configurations.csv':config_rows,'selectors.csv':selectors,
      'scenarios.csv':all_scenarios,'rank_comparisons.csv':rank_rows,'family_summaries.csv':summaries,'family_grid_slices.csv':slice_summaries,
      'common_sensitivity_comparisons.csv':common,'priority_verdicts.csv':verdicts}
    for name,rows in outputs.items():write_csv(outdir/name,rows)
    # Metric-specific selector tie and all-tied provenance notes.
    methods={'protocol':'research_protocol_2026-10-25.md v1.0 plus execution addendum and analysis clarifications dated 2026-10-25',
      'numeric_core':'Python standard library Decimal; precision 42; ROUND_HALF_EVEN for rank quantization only (12 decimal places fractions, cents USD).',
      'data_universe':{'candidate_products':list(BASKET),'primary_eligible_products':list(primary),'products_with_2025_candidate_observations':list(BASKET)},
      'configuration_count':len(configs),'scenario_row_count':len(all_scenarios),'scenario_families':['A','B','C','N'],
      'scenario_frequency':'deterministic grid coverage only; no probability interpretation',
      'unknown_bounds':'fraction [A*l/W,(A*l+U)/W]; USD [A*l,A*l+U]; no unknown replacement',
      'selector_certainty':'conservative sufficient tests from frozen protocol; failure blocks robust designation',
      'history':'incumbent means positive baseline share; restricted eligibility requires incumbent and threshold tau in required prior years (tau=0 means strictly positive); missing years make the balanced history grid inestimable',
      'fixed_selector':'basket-removal selector reuses the complete primary-universe selector snapshot, including certainty results and the full dominant-origin union',
      'input_manifest':'project-relative frozen paths and SHA256 values are recorded in input_manifest.json; no private task paths are used at runtime',
      'gamma_star':'g / eligible surviving baseline share; 0 when g=0; Infinity when positive g and zero denominator',
      'checksums':{'origins.csv':sha(root/'data/processed/stage2/origins.csv'),'quality.csv':sha(root/'data/processed/stage2/quality.csv')},
      'limitations':['annual import value is exposure proxy, not capacity/service loss','HS6 value replaceability and supplier eligibility are hypothetical assumptions','grid coverage is not probability','no network, fitted assumptions, expected loss, p-values or confidence intervals']}
    (outdir/'methods.json').write_text(json.dumps(methods,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    # Private deterministic output fingerprint plus public artifacts list.
    public=[]
    for p in sorted(outdir.iterdir()):
        if p.is_file() and p.name!='output_manifest.json':public.append({'path':p.relative_to(root).as_posix(),'sha256':sha(p),'mode':p.stat().st_mode&0o777})
    (outdir/'output_manifest.json').write_text(json.dumps(public,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    return {'products':len(primary),'configurations':len(configs),'scenario_rows':len(all_scenarios),'outputs':list(outputs)}

def verify_preserved(root):
    outdir=root/'results/stage2/analysis'; manifest_path=outdir/'output_manifest.json'
    if not manifest_path.exists(): raise ValueError('preserved output manifest is missing; run analysis first')
    expected=json.loads(manifest_path.read_text(encoding='utf-8'))
    listed={entry['path']:entry for entry in expected}
    if len(listed)!=len(expected): raise ValueError('output manifest contains duplicate paths')
    actual={p.relative_to(root).as_posix():p for p in outdir.iterdir() if p.is_file() and p.name!='output_manifest.json'}
    if set(actual)!=set(listed): raise ValueError('preserved output file set differs from manifest')
    bad=[]
    for rel,p in actual.items():
        entry=listed[rel]
        if sha(p)!=entry['sha256'] or (p.stat().st_mode&0o777)!=entry['mode']:bad.append(rel)
    if bad: raise ValueError('preserved output corruption: '+', '.join(sorted(bad)))
    return expected

def isolated_replay(root):
    input_manifest=public_input_manifest(root)
    with tempfile.TemporaryDirectory(prefix='stage2-analysis-replay-') as tmp:
        replay=Path(tmp)
        sources=[Path(x['path']) for x in input_manifest['inputs']]+[Path('results/stage2/analysis/input_manifest.json'),Path('src/resilience/analysis.py')]
        for rel in sources:
            src=root/rel; dst=replay/rel; dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(src,dst)
        run(replay)
        return json.loads((replay/'results/stage2/analysis/output_manifest.json').read_text(encoding='utf-8'))

def verify(root):
    verify_inputs(root)
    preserved=verify_preserved(root)
    replay=isolated_replay(root)
    if preserved!=replay: raise ValueError('isolated offline replay differs from preserved outputs')
    print(json.dumps({'verification':'passed','replay':'isolated copied public root identical',
                      'products':len([r for r in read_csv(root/'results/stage2/analysis/configurations.csv') if r['config']=='primary'][0]['universe'].split(',')),
                      'configurations':len(read_csv(root/'results/stage2/analysis/configurations.csv')),
                      'scenario_rows':len(read_csv(root/'results/stage2/analysis/scenarios.csv'))},sort_keys=True))
def main():
    ap=argparse.ArgumentParser();ap.add_argument('command',choices=['run','verify']);ap.add_argument('--root',default=str(Path(__file__).resolve().parents[2]));a=ap.parse_args();root=Path(a.root).resolve()
    if a.command=='run':print(json.dumps(run(root),sort_keys=True))
    else:verify(root)
if __name__=='__main__':main()
