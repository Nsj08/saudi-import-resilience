import unittest
import csv, hashlib, json, shutil, tempfile
from decimal import Decimal as D
from pathlib import Path

from src.resilience.analysis import (
    cell_metrics, quant, ranks, spearman, top_sets, interval_ranks,
    eligibility_for, selector_for, scenarios_for, GAMMAS, DS,
    pairwise_rank_diagnostics, run, verify_preserved, isolated_replay,
)

ROOT=Path(__file__).resolve().parents[1]


class ArithmeticFixtures(unittest.TestCase):
    def test_hhi_limits_and_shares(self):
        equal=cell_metrics({1:D(5),2:D(5)},D(10),D(0))
        single=cell_metrics({8:D(10)},D(10),D(0))
        self.assertEqual(equal['HHI'],D('.5'))
        self.assertEqual(equal['C1'],D('.5'))
        self.assertEqual(equal['C3'],D(1))
        self.assertEqual(single['HHI'],D(1))
        self.assertEqual(sum(equal['shares'].values()),D(1))
        self.assertEqual(equal['hhi_lo'],equal['HHI'])
        self.assertEqual(equal['hhi_hi'],equal['HHI'])

    def test_unknown_origin_bounds_contain_extreme_allocations(self):
        m=cell_metrics({1:D(70),2:D(20)},D(100),D('10.123456'))
        self.assertEqual(m['hhi_lo'],D('.53'))
        self.assertGreater(m['hhi_hi'],m['hhi_lo'])
        self.assertEqual((m['c1_world_lo'],m['c1_world_hi']),(D('.7'),D('.80123456')))
        self.assertEqual(m['A'],D(90))
        intervals={'a':(D('.2'),D('.5')),'b':(D('.5'),D('.8'))}
        ranks_by_interval=interval_ranks(intervals)
        self.assertEqual(ranks_by_interval['a'],(1,2))
        self.assertEqual(ranks_by_interval['b'],(1,2))

    def test_round_half_even_average_ranks_and_constant_na(self):
        self.assertEqual(quant(D('0.0000000000005')),D('0E-12'))
        self.assertEqual(quant(D('0.0000000000015')),D('0.000000000002'))
        self.assertEqual(ranks([D(1),D(1),D(2)]),[D('1.5'),D('1.5'),D(3)])
        self.assertIsNone(spearman([D(1),D(1)],[D(3),D(3)]))
        self.assertEqual(spearman([D(1),D(2),D(3)],[D(3),D(2),D(1)]),D(-1))

    def test_top_set_includes_boundary_tie_and_exact_display_is_deterministic(self):
        tied,display,size=top_sets(['x','b','a','c'],[D(9),D(7),D(7),D(1)],2)
        self.assertEqual(tied,{'x','a','b'})
        self.assertEqual(display,{'x','a'})
        self.assertEqual(size,3)

    def test_pairwise_reversal_and_tie_changes_exclude_ties(self):
        rev,left_tied,right_tied,changed=pairwise_rank_diagnostics(('a','b','c'),[3,2,1],[1,2,3])
        self.assertEqual((rev,left_tied,right_tied,changed),(3,0,0,0))
        rev,left_tied,right_tied,changed=pairwise_rank_diagnostics(('a','b','c'),[2,2,1],[3,2,1])
        self.assertEqual((rev,left_tied,right_tied,changed),(0,1,0,1))


class EligibilityAndSelectorFixtures(unittest.TestCase):
    def test_history_is_incumbent_and_boolean_count_with_missing_year_refusal(self):
        sh={(2025,'P'):{7:D('.2')},(2022,'P'):{7:D('.01')},(2023,'P'):{7:D('.99')},(2024,'P'):{7:D('.009')}}
        cfg={'selector_year':2025,'hist_years':(2022,2023,2024),'hist_need':2}
        inc,hist,known=eligibility_for('P',7,sh,cfg)
        self.assertTrue(inc); self.assertTrue(hist); self.assertTrue(known)
        # A large share in one qualifying year is one year, not an additive score.
        sh[(2022,'P')][7]=D('.009')
        self.assertFalse(eligibility_for('P',7,sh,cfg)[1])
        sh[(2022,'P')][7]=D('.0001');sh[(2023,'P')][7]=D('.009');sh[(2024,'P')][7]=D(0)
        self.assertTrue(eligibility_for('P',7,sh,{**cfg,'tau':D(0)})[1])
        self.assertFalse(eligibility_for('P',7,sh,{**cfg,'tau':D('.05')})[1])
        del sh[(2024,'P')]
        self.assertEqual(eligibility_for('P',7,sh,cfg),(True,False,False))

    def test_selector_short_universe_and_numeric_ties(self):
        data={}
        shares={}
        for p,o in [('A',2),('B',1)]:
            v={1:D(10),2:D(10),3:D(0)}
            sh={1:D('.5'),2:D('.5'),3:D(0)}
            data[(2025,p)]={'values':v,'shares':sh,'U':D(21)};shares[(2025,p)]=sh
        cfg={'selector_year':2025,'selector':'reselect','codes':('A','B'),'config':'fixture'}
        got=selector_for(cfg,data,shares,('A','B'))
        self.assertEqual(got['K3'],(1,2))
        self.assertEqual(got['K'],(1,2))
        self.assertFalse(got['pooled_certainty'])
        # A 5-unit named lead cannot clear unknown value 10; compare values, not partner IDs.
        pdata={(2025,'P'):{'values':{10:D(1000),20:D(995)},'shares':{10:D('.50125'),20:D('.49875')},'U':D(10)}}
        sel=selector_for({'selector_year':2025,'selector':'reselect','config':'certainty'},pdata,{},('P',))
        self.assertFalse(sel['dominant_certainty']['P'])
        empty=selector_for({'selector_year':2025,'selector':'reselect','config':'empty'},
          {(2025,'P'):{'values':{1:D(0),2:D(0)},'shares':{},'U':D(0)}},{},('P',))
        self.assertEqual(empty['K3'],())
        self.assertEqual(empty['dominant']['P'],None)
        self.assertFalse(empty['dominant_certainty']['P'])

    def test_fixed_selector_uses_primary_balanced_eight_not_all_candidates(self):
        products=tuple(f'p{i}' for i in range(9));data={};shares={}
        for i,p in enumerate(products):
            origin=100+i
            values={origin:D(100)}
            data[(2025,p)]={'values':values,'shares':{origin:D(1)},'U':D(0)}
            shares[(2025,p)]={origin:D(1)}
        # Ninth candidate has the largest pooled origin value but is outside primary.
        data[(2025,'p8')]['values']={999:D(10000)};data[(2025,'p8')]['shares']={999:D(1)};shares[(2025,'p8')]={999:D(1)}
        cfg={'selector_year':2025,'selector':'fixed_primary','config':'remove_broad_fixed_selector','primary_products':products[:8]}
        got=selector_for(cfg,data,shares,products[:7])
        self.assertNotIn(999,got['K3'])
        self.assertEqual(got['K3'],tuple(range(100,103)))

    def test_fixed_selector_reuses_complete_primary_snapshot_and_certainty(self):
        products=('P','Q')
        data={(2025,'P'):{'values':{1:D(100),2:D(90),3:D(80),4:D(1)},'shares':{},'U':D(1)},
              (2025,'Q'):{'values':{4:D(1000)},'shares':{},'U':D(0)}}
        primary=selector_for({'selector_year':2025,'selector':'reselect','config':'primary'},data,{},products)
        self.assertEqual(primary['K3'],(4,1,2))
        self.assertTrue(primary['pooled_certainty'])
        self.assertTrue(primary['dominant_certainty']['P'])
        self.assertTrue(primary['dominant_certainty']['Q'])
        fixed=selector_for({'selector_year':2025,'selector':'fixed_primary','config':'remove_broad_fixed_selector',
                            'primary_products':products},data,{},('P',))
        self.assertEqual(fixed,primary)
        self.assertEqual(fixed['K'],primary['K'])
        self.assertTrue(fixed['pooled_certainty'])

    def test_frozen_synthetic_replacement_gamma_star_and_all_shock_cases(self):
        p='P'; product=(p,); values={1:D('.6'),2:D('.3'),3:D('.1')}
        data={(2025,p):{'values':{o:v*D(100) for o,v in values.items()},'shares':values,'A':D(100),'W':D(100),'U':D(0)}}
        shares={(2025,p):values}
        for year in (2022,2023,2024):
            # Only supplier 3 passes the historical one-percent rule.
            shares[(year,p)]={1:D('.009'),2:D('.009'),3:D('.982')}
        selector={'K3':(1,2,3),'K':(1,2,3),'dominant':{p:1},'pooled_certainty':True,'dominant_certainty':{p:True}}
        cfg={'selector_year':2025,'config':'fixed-fixture','hist_years':(2022,2023,2024),'hist_need':2,'tau':D('.01')}
        rows=scenarios_for(product,selector,data,shares,cfg)
        def row(family, origins, d, gamma, eligibility):
            key=family+':'+','.join(map(str,origins))
            return next(r for r in rows if r['scenario_id']==key and r['severity_d']==D(d) and r['gamma']==D(gamma) and r['eligibility']==eligibility)
        inc=row('A',(1,),'.5','.25','incumbent')
        hist=row('A',(1,),'.5','.25','history')
        self.assertEqual((inc['gross_share'],inc['replacement_share'],inc['residual_share'],inc['gamma_star']),(D('.30'),D('.10'),D('.20'),D('.75')))
        self.assertEqual((hist['gross_share'],hist['replacement_share'],hist['residual_share'],hist['gamma_star']),(D('.30'),D('.025'),D('.275'),D(3)))
        pair=row('B',(1,2),'1','1','incumbent')
        self.assertEqual((pair['gross_share'],pair['replacement_share'],pair['residual_share'],pair['gamma_star']),(D('.9'),D('.1'),D('.8'),D(9)))
        allshock=row('B',(1,2,3),'1','1','incumbent')
        self.assertEqual((allshock['gross_share'],allshock['replacement_share'],allshock['residual_share']),(D(1),D(0),D(1)))
        self.assertEqual(str(allshock['gamma_star']),'Infinity')

    def test_scenario_grid_conserves_gross_replacement_and_is_order_invariant(self):
        products=('p1','p2')
        values={('2025','p1'):{1:D('.6'),2:D('.4')},('2025','p2'):{1:D('.2'),2:D('.8')}}
        data={};shares={}
        for (y,p),sh in values.items():
            vals={o:v*D(100) for o,v in sh.items()}
            data[(2025,p)]={'values':vals,'shares':sh,'A':D(100),'W':D(100),'U':D(0)};shares[(2025,p)]=sh
            for year in (2022,2023,2024):shares[(year,p)]=sh
        selector={'K3':(1,2),'K':(1,2),'dominant':{'p1':1,'p2':2},'pooled_certainty':True,'dominant_certainty':{'p1':True,'p2':True}}
        cfg={'selector_year':2025,'config':'fixture','hist_years':(2022,2023,2024),'hist_need':2}
        rows=scenarios_for(products,selector,data,shares,cfg)
        self.assertTrue(any(r['family']=='N' and r['gross_share']==0 for r in rows))
        for r in rows:
            self.assertLessEqual(r['replacement_share'],r['gross_share'])
            self.assertEqual(r['gross_share'],r['replacement_share']+r['residual_share'])
            self.assertGreaterEqual(r['residual_share'],0)
        # Family C is the standardized own-dominant diagnostic: each product receives its own selector.
        crows=[r for r in rows if r['family']=='C' and r['severity_d']==D(1) and r['gamma']==D(0) and r['eligibility']=='incumbent']
        self.assertEqual({r['product']:r['interrupted_origins'] for r in crows},{'p1':'1','p2':'2'})
        c_replaced=[r for r in rows if r['family']=='C' and r['severity_d']==D(1) and r['gamma']==D('.25') and r['eligibility']=='incumbent']
        for r in c_replaced:
            c1=D('.6') if r['product']=='p1' else D('.8')
            self.assertEqual(r['residual_share'],max(D(0),c1-D('.25')*(1-c1)))
        for r in [r for r in rows if r['family']=='C' and r['eligibility']=='incumbent']:
            c1=D('.6') if r['product']=='p1' else D('.8')
            self.assertEqual(r['residual_share'],max(D(0),r['severity_d']*c1-r['gamma']*(1-c1)))
        all_shocked=[r for r in rows if r['family']=='B' and r['scenario_id']=='B:1,2' and r['severity_d']==D(1) and r['gamma']==D(1) and r['eligibility']=='incumbent']
        self.assertEqual(len(all_shocked),2)
        self.assertTrue(all(r['gross_share']==1 and r['replacement_share']==0 and r['residual_share']==1 for r in all_shocked))
        unique=[(r['family'],r['scenario_id']) for r in rows]
        self.assertEqual(len(set(unique)),5)  # the unordered pair and whole-K3 definition are deduplicated
        reordered=cell_metrics({2:D(20),1:D(10)},D(30),D(0))
        reversed_again=cell_metrics({1:D(10),2:D(20)},D(30),D(0))
        self.assertEqual(reordered['HHI'],reversed_again['HHI'])
        # Monotone in d and gamma for a fixed single-origin case.
        fixed=[r for r in rows if r['family']=='A' and r['product']=='p1' and r['scenario_id']=='A:1' and r['eligibility']=='incumbent']
        fixed_d=[r for r in fixed if r['severity_d']==D('.5')]
        by_gamma={g:next(r['residual_share'] for r in fixed_d if r['gamma']==g) for g in GAMMAS}
        self.assertTrue(all(by_gamma[a]>=by_gamma[b] for a,b in zip(GAMMAS,GAMMAS[1:])))
        fixed_gamma=[r for r in fixed if r['gamma']==D('.25')]
        by_d={d:next(r['residual_share'] for r in fixed_gamma if r['severity_d']==d) for d in DS}
        self.assertTrue(all(by_d[a]<=by_d[b] for a,b in zip(DS,DS[1:])))

    def test_missing_history_refuses_numeric_scenario_output(self):
        p='P'; values={1:D('.7'),2:D('.3')};data={(2025,p):{'values':{1:D(70),2:D(30)},'shares':values,'A':D(100),'W':D(100),'U':D(0)}}
        selector={'K3':(1,2),'K':(1,2),'dominant':{p:1},'pooled_certainty':True,'dominant_certainty':{p:True}}
        cfg={'selector_year':2025,'config':'missing','hist_years':(2022,2023,2024),'hist_need':2}
        rows=scenarios_for((p,),selector,data,{(2025,p):values},cfg)
        history=[r for r in rows if r['eligibility']=='history']
        self.assertTrue(history)
        self.assertTrue(all(not r['outcome_estimable'] and r['residual_share'] is None and r['history_estimable'] is False for r in history))

    def test_copy_root_replay_row_order_and_tamper_detection(self):
        with tempfile.TemporaryDirectory(prefix='a20-copy-') as td:
            copied=Path(td)
            input_manifest=json.loads((ROOT/'results/stage2/analysis/input_manifest.json').read_text())
            paths=[Path(x['path']) for x in input_manifest['inputs']]+[Path('results/stage2/analysis/input_manifest.json'),Path('src/resilience/analysis.py')]
            for rel in paths:
                src=ROOT/rel;dst=copied/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
            run(copied)
            with (copied/'results/stage2/analysis/family_summaries.csv').open() as f:
                summaries=list(csv.DictReader(f))
            self.assertEqual(len(summaries),10*9*3*2)
            self.assertTrue({'top2_membership_count','top3_membership_count','top4_membership_count','worst_rank_max','all_tied_case_count'} <= set(summaries[0]))
            with (copied/'results/stage2/analysis/priority_verdicts.csv').open() as f:
                verdicts=list(csv.DictReader(f))
            self.assertEqual(len(verdicts),9*3*2*11)
            with (copied/'results/stage2/analysis/common_sensitivity_comparisons.csv').open() as f:
                comparisons=list(csv.DictReader(f))
            expected={'A':120,'B':96,'C':24}
            for family,count in expected.items():
                for metric in ('fraction','usd'):
                    for k in ('2','3','4'):
                        matched=[r for r in comparisons if r['config']=='tau_0' and r['family']==family
                                 and r['comparison_metric']==metric and r.get('k')==k and r['status']=='matched'
                                 and r.get('scenario_id')]
                        self.assertEqual(len(matched),count,(family,metric,k))
                        self.assertEqual(len({(r['scenario_id'],r['severity_d'],r['gamma'],r['eligibility']) for r in matched}),count)
            q0=[r for r in comparisons if r['config']=='quality_0pct' and r['family'] in ('A','B','C')]
            self.assertEqual(len(q0),18)
            self.assertTrue(all(r['status']=='unestimable_empty_common_universe' and r['common_n']=='0' for r in q0))
            first={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (copied/'results/stage2/analysis').glob('*.csv')}
            verify_preserved(copied)
            # Reordering the same records must leave all analytical tables byte-identical.
            origins=copied/'data/processed/stage2/origins.csv'
            lines=origins.read_text().splitlines();origins.write_text('\n'.join([lines[0],*reversed(lines[1:])])+'\n')
            manifest_path=copied/'results/stage2/analysis/input_manifest.json'
            public=json.loads(manifest_path.read_text())
            newhash=hashlib.sha256(origins.read_bytes()).hexdigest()
            for entry in public['inputs']:
                if entry['path']=='data/processed/stage2/origins.csv':entry['sha256']=newhash
            manifest_path.write_text(json.dumps(public,sort_keys=True,indent=2)+'\n')
            run(copied)
            second={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (copied/'results/stage2/analysis').glob('*.csv')}
            self.assertEqual(first,second)
            self.assertEqual(json.loads((copied/'results/stage2/analysis/output_manifest.json').read_text()),isolated_replay(copied))
            target=copied/'results/stage2/analysis/scenarios.csv';target.write_text(target.read_text()+'tamper\n')
            with self.assertRaisesRegex(ValueError,'corruption'):
                verify_preserved(copied)


if __name__=='__main__':
    unittest.main()
