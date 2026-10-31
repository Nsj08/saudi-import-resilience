"""Independent post-result diagnostics over frozen processed origins and scenario outputs.

No production analysis functions are imported. Structural checks and the illustrative
crossover are written as distinct JSON records under results/critical_review/.
"""
from __future__ import annotations
import csv, json
from collections import Counter
from decimal import Decimal, getcontext
from pathlib import Path
getcontext().prec = 42
ROOT = Path(__file__).resolve().parents[2]
D = Decimal

def read_rows(path):
    with path.open(newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))

def history_eligible(baseline_share, prior_shares, threshold=D('0.01')):
    return baseline_share > 0 and sum(v >= threshold for v in prior_shares) >= 2

def crossover(d, VGp, VGq, VEp, VEq):
    numerator=d*(VGp-VGq); denominator=VEp-VEq
    if denominator==0:
        return None, ('identical_pairwise_line' if numerator==0 else 'parallel_no_crossing')
    return numerator/denominator, 'crossing'

def origin_value_map():
    """Reconstruct 2025 named-origin values directly from accepted processed rows."""
    rows=read_rows(ROOT/'data/processed/stage2/origins.csv'); values={}
    for r in rows:
        if r['record_type']=='named_origin' and r['exact_duplicate'].lower()!='true' and int(r['year'])==2025:
            values.setdefault(r['hs6'],{})[r['partnerCode']]=D(r['primaryValue_decimal'])
    totals={p:sum(v.values(),D(0)) for p,v in values.items()}
    return values,totals

def dollar_exposure(d, VG, gamma, VE):
    return max(D(0), d*VG-gamma*VE)

def structural_diagnostic():
    values,totals=origin_value_map()
    scenarios=read_rows(ROOT/'results/stage2/analysis/scenarios.csv')
    algebra_rows=pair_count=inversions=ordering_ties=0; max_abs_err=D(0); groups={}
    for r in scenarios:
        if r['config']!='primary' or r['family'] not in ('A','B','C') or r['eligibility']!='incumbent' or r['outcome_estimable']!='True': continue
        p=r['product']; G=[x for x in r['interrupted_origins'].split(',') if x]
        S=sum((values[p].get(o,D(0)) for o in G),D(0))/totals[p]
        sev=D(r['severity_d']); gam=D(r['gamma'])
        expected=(sev*S,D(1)-S,max(D(0),(sev+gam)*S-gam))
        actual=(D(r['gross_share']),D(r['eligible_survivor_share']),D(r['residual_share']))
        err=max(abs(a-e) for a,e in zip(actual,expected)); max_abs_err=max(max_abs_err,err)
        if err>D('1e-35'): raise AssertionError(f'fixed-G identity mismatch {p}/{r["scenario_id"]}/{sev}/{gam}: {err}')
        algebra_rows+=1; key=(r['family'],r['scenario_id'],sev,gam)
        groups.setdefault(key,[]).append((S,D(r['residual_share'])))
    for key,pairs in groups.items():
        for i,(s1,e1) in enumerate(pairs):
            for s2,e2 in pairs[i+1:]:
                pair_count+=1
                if (s1>s2 and e1<e2) or (s1<s2 and e1>e2): inversions+=1
                if s1==s2 and e1!=e2: raise AssertionError(f'equal S_G unequal residuals: {key}')
                if s1!=s2 and e1==e2: ordering_ties+=1
    if inversions: raise AssertionError(f'{inversions} strict fixed-G order inversions')
    # Matched outcome invariance; event keys include scenario_id and exact interrupted set.
    cfgs=('quality_5pct','remove_broad_fixed_selector','remove_broad_reselected')
    common=read_rows(ROOT/'results/stage2/analysis/common_sensitivity_comparisons.csv')
    matched=0; details=[]
    for cfg in cfgs:
        rows=[r for r in scenarios if r['config'] in ('primary',cfg) and r['family'] in ('A','B','C') and r['outcome_estimable']=='True']
        primary_products={x['product'] for x in rows if x['config']=='primary'}
        alt_products={x['product'] for x in rows if x['config']==cfg}
        universe=primary_products & alt_products
        maps={}
        for name in ('primary',cfg):
            by={}
            for r in rows:
                if r['config']!=name or r['product'] not in universe: continue
                base=(r['family'],r['scenario_id'],r['severity_d'],r['gamma'],r['eligibility'])
                by.setdefault(base,{})[r['product']]=r
            mp={}
            for base,prd in by.items():
                if set(prd)!=universe: continue
                # scenario_id is explicit: A/B and C labels can otherwise overwrite or alias.
                signature=(base,tuple((p,prd[p]['interrupted_origins']) for p in sorted(universe)))
                mp[signature]=prd
            maps[name]=mp
        local=0
        for signature in set(maps['primary']) & set(maps[cfg]):
            l=maps['primary'][signature]; rr=maps[cfg][signature]
            for product in universe:
                for field in ('identified_fraction','identified_usd'):
                    if D(l[product][field])!=D(rr[product][field]):
                        raise AssertionError(f'matched output differs {cfg}/{signature}/{product}/{field}')
            local+=1
        matched+=local
        for metric in ('fraction','usd'):
            comp=[r for r in common if r['config']==cfg and r['family'] in ('A','B','C') and r['comparison_metric']==metric and r['k']=='3' and r['status']=='matched']
            varied=sum(r['spearman'] not in ('NA','') for r in comp); constant=len(comp)-varied
            rev=sum(int(r['strict_pairwise_order_reversals']) for r in comp)
            tie=sum(int(r['pair_tie_status_changes']) for r in comp)
            if len(comp)!=240 or varied!=170 or constant!=70 or rev or tie:
                raise AssertionError(f'matched common comparison mismatch {cfg}/{metric}: {len(comp)}/{varied}/{constant}/{rev}/{tie}')
            if any(r['spearman'] not in ('NA','') and D(r['spearman'])!=1 for r in comp): raise AssertionError(f'non-unit matched rho {cfg}/{metric}')
            details.append({'config':cfg,'metric':metric,'matched_k3_rows':len(comp),'rho_one_nonconstant':varied,'rho_NA_constant':constant,'strict_order_reversals':rev,'tie_status_changes':tie})
    if matched!=720: raise AssertionError(f'expected 720 matched literal-event vectors including scenario_id, got {matched}')
    # Derive primary/candidate and ALL_APPLICABLE failed/untestable flags from stored verdicts.
    verdicts=read_rows(ROOT/'results/stage2/analysis/priority_verdicts.csv')
    q=read_rows(ROOT/'data/processed/stage2/quality.csv')
    byprod={}
    for r in q: byprod.setdefault(r['hs6'],[]).append(r)
    primary_codes={p for p,rs in byprod.items() if len({r['year'] for r in rs})==4 and all(r['eligible_1pct']=='True' for r in rs)}
    pri=[r for r in verdicts if r['config']=='primary' and r['product'] in primary_codes]
    fail_rows=[r for r in pri if r['condition_failures']]
    passes=[r for r in pri if r['status']=='persistent_high_screening_priority']
    candidate_unusable=[r for r in verdicts if r['config']=='primary' and r['product']=='841381' and r['status']=='undetermined']
    aggregate=[r for r in verdicts if r['config']=='ALL_APPLICABLE']
    q0=[r for r in verdicts if r['config']=='quality_0pct']
    if len(primary_codes)!=8 or len(pri)!=48 or len(fail_rows)!=46 or len(passes)!=2 or len(candidate_unusable)!=6:
        raise AssertionError(f'primary/candidate verdict counts differ: {len(primary_codes)} products, {len(pri)} rows, {len(fail_rows)} failed, {len(passes)} passes, {len(candidate_unusable)} candidate-unusable')
    if not aggregate or any(r['status']!='undetermined' for r in aggregate) or len(aggregate)!=54: raise AssertionError('stored ALL_APPLICABLE verdict is not the expected 54 undetermined rows')
    if len(q0)!=54 or any(r['eligible_in_configuration']!='False' for r in q0): raise AssertionError('quality_0pct is not the stored empty 54-row gate')
    history_rows=[r for r in verdicts if r['config']=='history_all_3_prior' and r['product']=='841350' and r['family']=='C' and r['metric'] in ('fraction','usd')]
    if len(history_rows)!=2 or any('unknown-origin worst rank exceeds top three in 1 informative cases' not in r['condition_failures'] for r in history_rows): raise AssertionError('841350 Family C all-three-year failure rows changed')
    return {'method':'independent Decimal reconstruction from processed named-origin values and frozen output tables; no production analysis functions imported','general_incumbent_common_G':{'rows_checked':algebra_rows,'fixed_family_event_d_gamma_product_pairs_checked':pair_count,'strict_order_inversions':inversions,'strict_SG_orders_tied_by_zero_floor':ordering_ties,'max_abs_formula_error':str(max_abs_err),'tolerance':'1e-35','identity':'gross=d*S_G; eligible survivors=1-S_G; residual=max(0,(d+gamma)*S_G-gamma); monotone in S_G at fixed d,gamma and G'},'matched_literal_event_invariance':{'scenario_vectors_compared':matched,'scenario_id_in_event_key':True,'details':details},'strict_verdict_flags':{'primary_products':len(primary_codes),'primary_eight_rows':len(pri),'primary_eight_known_rank_failures':len(fail_rows),'primary_eight_primary_passes':len(passes),'841350_family_C_pass_rows':[[r['metric'],r['status']] for r in passes],'841350_family_C_history_all_3_prior_fail_rows':[{'metric':r['metric'],'condition_failures':r['condition_failures'],'informative_case_count':r['informative_case_count']} for r in history_rows],'candidate_841381_unusable_rows':len(candidate_unusable),'ALL_APPLICABLE_rows':len(aggregate),'ALL_APPLICABLE_all_undetermined':all(r['status']=='undetermined' for r in aggregate),'quality_0pct_empty_rows':len(q0),'quality_0pct_all_excluded':all(r['eligible_in_configuration']=='False' for r in q0)}}

def reconstruct_crossover():
    raw=read_rows(ROOT/'data/processed/stage2/origins.csv')
    raw_by={}
    for r in raw:
        if r['record_type']=='named_origin' and r['exact_duplicate'].lower()!='true':
            raw_by.setdefault((r['hs6'],r['year']),{})[r['partnerCode']]=D(r['primaryValue_decimal'])
    share_rows=read_rows(ROOT/'results/stage2/analysis/origin_shares.csv')
    # Verify the table inputs against an independent reconstruction from processed named-origin values.
    for r in share_rows:
        if r['product'] not in ('841350','841370'): continue
        amounts=raw_by.get((r['product'],r['year']),{}); total=sum(amounts.values(),D(0))
        amount=amounts.get(r['origin_code'],D(0)); expected=amount/total if total else D(0)
        if abs(expected-D(r['share_of_identified_origins']))>D('1e-35'): raise AssertionError('processed origin share does not match stored table input')
    scenarios=[r for r in read_rows(ROOT/'results/stage2/analysis/scenarios.csv') if r['config']=='primary' and r['family']=='B' and r['scenario_id']=='B:156,380,842' and r['severity_d']=='0.5' and r['gamma']=='0.25']
    if not scenarios: raise ValueError('stored target event missing')
    out={}
    for eligibility in ('incumbent','history'):
        vals={}
        for product in ('841350','841370'):
            base_amounts=raw_by[(product,'2025')]
            A=sum(base_amounts.values(),D(0))
            S=sum((base_amounts.get(c,D(0)) for c in ('156','380','842')),D(0))/A
            E=D(0)
            for code,amount in base_amounts.items():
                if code in ('156','380','842') or amount<=0: continue
                if eligibility=='incumbent': eligible=True
                else:
                    prior=[]
                    for year in ('2022','2023','2024'):
                        total_y=sum(raw_by.get((product,year),{}).values(),D(0))
                        origin_y=raw_by.get((product,year),{}).get(code,D(0))
                        prior.append(origin_y/total_y if total_y else D(0))
                    eligible=history_eligible(amount/A,prior)
                if eligible: E+=amount
            vals[product]={'V_G':A*S,'V_E':E,'A':A,'S_G':S}
        p,q=vals['841350'],vals['841370']; cross,rawstatus=crossover(D('.5'),p['V_G'],q['V_G'],p['V_E'],q['V_E'])
        status=rawstatus; positive=False
        if cross is not None:
            up=D('.5')*p['V_G']-cross*p['V_E']; uq=D('.5')*q['V_G']-cross*q['V_E']; positive=up>0 and uq>0
            if cross<0: status='crossing_below_zero'
            elif cross>D(1): status='crossing_above_examined_grid'
            elif not positive: status='crossing_not_both_positive'
            else: status='interior_positive'
        def exposure(v,g): return dollar_exposure(D('.5'),v['V_G'],g,v['V_E'])
        eps=D('0.000001'); checks={}
        for label,g in [('gamma_0.25',D('.25'))]: checks[label]={'gamma':str(g),'usd_841350':str(exposure(p,g)),'usd_841370':str(exposure(q,g)),'difference_350_minus_370':str(exposure(p,g)-exposure(q,g))}
        if cross is not None and status=='interior_positive':
            for label,g in [('just_below',cross-eps),('at_crossing',cross),('just_above',cross+eps)]: checks[label]={'gamma':str(g),'usd_841350':str(exposure(p,g)),'usd_841370':str(exposure(q,g)),'difference_350_minus_370':str(exposure(p,g)-exposure(q,g))}
        out[eligibility]={'inputs':{k:{f:str(v) for f,v in item.items()} for k,item in vals.items()},'formula':'gamma_cross=d*(V_Gp−V_Gq)/(V_Ep−V_Eq); interpreted only if both unclipped outcomes are positive','crossing_gamma':str(cross) if cross is not None else None,'crossing_status':status,'both_unclipped_positive_at_crossing':positive,'checks':checks}
    return {'label':'posthoc exploratory accounting diagnostic; introduced 2026-10-25 after viewing the existing reversal; not prespecified, probabilistic, a calibration, or an optimization','event':'B:156,380,842; joint-K3 alias; d=0.5; primary; products 841350 and 841370','source':'processed named-origin values independently reconstruct A and yearly shares; reconstructed shares verified against existing origin_shares.csv; eligibility independently recomputed under frozen rules','results':out}

if __name__=='__main__':
    structural=structural_diagnostic(); crossover_result=reconstruct_crossover()
    outdir=ROOT/'results/critical_review';outdir.mkdir(parents=True,exist_ok=True)
    (outdir/'structural_diagnostics.json').write_text(json.dumps(structural,indent=2)+'\n',encoding='utf-8')
    (outdir/'crossover_diagnostic.json').write_text(json.dumps(crossover_result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'structural':{'rows':structural['general_incumbent_common_G']['rows_checked'],'pairs':structural['general_incumbent_common_G']['fixed_family_event_d_gamma_product_pairs_checked'],'matched':structural['matched_literal_event_invariance']['scenario_vectors_compared'],'primary_fail':structural['strict_verdict_flags']['primary_eight_known_rank_failures']},'crossovers':{k:v['crossing_gamma'] for k,v in crossover_result['results'].items()}},sort_keys=True))
