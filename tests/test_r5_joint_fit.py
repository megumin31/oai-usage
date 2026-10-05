"""Invented traces: joint original endpoints, indivisible budgets, offset union."""
import asyncio, contextlib, copy, io, json, unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch
from test_historical_segments import BASE, CATALOG, M, G
from test_r4_frontier import trace
from test_robust_segments import RobustFixtures, PRICES, oracle_trace

def raw(deltas,costs=None,step=60):
    blocks=trace(deltas,costs)
    for i,b in enumerate(blocks):
        b['before']['observed_at']=(BASE+timedelta(seconds=i*step)).isoformat()
        b['after']['observed_at']=(BASE+timedelta(seconds=(i+1)*step)).isoformat()
        b.update(span_seconds=step,quota_epoch_id='epoch',profile={'model':'fiction','mode':'default','effort':'high'},policy_id='raw_edges',alignment_offset_seconds=0)
    return blocks

class JointFitTests(unittest.TestCase):
    def test_clean_joint_constraints(self):
        r=M['joint_fit'](raw([1]*30,[10]*30))
        self.assertEqual(r['status'],'conditional_joint_estimate');self.assertAlmostEqual(r['estimated_total_api_cost_usd'],1000)
        self.assertEqual(r['evidence_counts']['one_sided_constraints'],30);self.assertEqual(r['evidence_counts']['unique_raw_endpoints'],31)
        self.assertGreaterEqual(r['support']['group_count'],2)
        self.assertLessEqual(r['conditional_range_usd']['lower'],1000);self.assertGreaterEqual(r['conditional_range_usd']['upper'],1000)
        self.assertFalse(r['unconditional_identification']['finite_upper_identified'])
    def test_one_budget_never_split_into_multiple_support_units(self):
        r=M['joint_fit'](raw([3]*8,[3]*8,step=30))
        self.assertIsNone(r['estimated_total_api_cost_usd']);self.assertTrue(all(h['support_group_count']<=1 for h in r['hypotheses']))
    def test_duplicate_reading_alias_not_extra_support(self):
        b=raw([1]*30,[10]*30);want=M['joint_fit'](b)
        alias=copy.deepcopy(b[5]);alias['id']='alias'
        got=M['joint_fit'](b+[alias,copy.deepcopy(b[4])])
        for k in ('estimated_total_api_cost_usd','conditional_range_usd','evidence_counts','support'):self.assertEqual(got[k],want[k])
    def test_repeated_discovery_scheme_not_extra_evidence(self):
        b=raw([1]*30,[10]*30);a=M['joint_fit'](b,discovery_spans=(300,));z=M['joint_fit'](b,discovery_spans=(300,300))
        self.assertEqual(a['support'],z['support']);self.assertEqual(a['evidence_counts'],z['evidence_counts'])
    def test_overlap_rejected(self):
        b=raw([1]*30,[10]*30);x=copy.deepcopy(b[2]);x['id']='overlap';x['before']['observed_at']=b[1]['before']['observed_at']
        with self.assertRaisesRegex(ValueError,'Overlapping'):M['joint_fit'](b+[x])
    def test_zero_cost_negative_raw_path_not_silently_removed(self):
        r=M['joint_fit'](raw([1]*10+[-3]+[1]*10,[10]*10+[0]+[10]*10))
        self.assertEqual(r['status'],'cannot_estimate');self.assertIn('all_raw_one_sided_constraints_infeasible',r['reasons'])
        self.assertEqual(r['evidence_counts']['raw_edges'],21)
    def test_offsets_not_mixed(self):
        b=raw([1]*10,[10]*10);b[-1]['alignment_offset_seconds']=60
        with self.assertRaisesRegex(ValueError,'epoch/profile/offset'):M['joint_fit'](b)
    def test_offset_union_not_intersection(self):
        fn=M['joint_union_capacity_ranges']
        self.assertEqual(fn([{'lower':100,'upper':200},{'lower':150,'upper':250}]),[{'lower':100,'upper':250}])
        self.assertEqual(fn([{'lower':100,'upper':120},{'lower':180,'upper':200}]),[{'lower':100,'upper':120},{'lower':180,'upper':200}])
        self.assertEqual(fn([{'lower':100,'upper':None},{'lower':100,'upper':150}]),[{'lower':100,'upper':None}])
    def test_measurement_sensitivity_fixed_budgets(self):
        r=M['joint_fit'](raw([1]*30,[10]*30));q,b,s=r['quantization_only']['capacity_range_usd'],r['conditional_range_usd'],r['endpoint_error_stress']['capacity_range_usd']
        self.assertGreaterEqual(q['lower'],b['lower']);self.assertLessEqual(q['upper'],b['upper']);self.assertGreaterEqual(b['lower'],s['lower']);self.assertLessEqual(b['upper'],s['upper'])
        self.assertTrue(r['quantization_only']['frozen_clean_portion_budgets'])
    def test_all_polluted_world_still_unidentified(self):
        r=M['joint_fit'](raw([1]*30,[10]*30));self.assertEqual(r['estimated_total_api_cost_usd'],1000)
        self.assertIsNone(r['unconditional_identification']['upper_capacity_usd']);self.assertLess(r['estimated_total_api_cost_usd'],2000)
    def test_frozen_later_coefficient(self):
        t=M['joint_fit'](raw([1]*30,[10]*30));v=M['joint_validate_frozen'](t,raw([1]*30,[10]*30))
        self.assertEqual(v['frozen_a_percent_per_usd'],.1);self.assertFalse(v['candidate_refitted_on_holdout']);self.assertEqual(v['status'],'later_recurrence_supported')
        v=M['joint_validate_frozen'](t,raw([0]*30,[10]*30));self.assertEqual(v['status'],'contradicted_under_endpoint_error_assumption')
    def test_portion_granularity_not_raw_union_dedup(self):
        ts=M['joint_discovery_tilings'](raw([4]*20,[4]*20));keys=[tuple(tuple(p['atomic_block_ids']) for p in t['portions']) for t in ts]
        self.assertEqual(len(keys),len(set(keys)));self.assertGreater(len(keys),1)
        unions=[frozenset(bid for p in t['portions'] for bid in p['atomic_block_ids']) for t in ts];self.assertTrue(all(u==unions[0] for u in unions))

class JointReviewRegressions(unittest.TestCase):
    def test_shared_original_interiors_shrink_same_aggregate_budget_range(self):
        edges=raw([6,4],[4,6],step=150);p=M['joint_portion'](edges)
        fine=M['robust_rate_interval'](edges,{b['id'] for b in edges},1,clean_portions=[p])
        coarse=M['robust_rate_interval']([p],{p['id']},1)
        self.assertAlmostEqual(fine['lower'],8/9);self.assertAlmostEqual(fine['upper'],1)
        self.assertGreater(fine['lower'],coarse['lower']);self.assertLess(fine['upper'],coarse['upper'])
        self.assertEqual(M['frontier_capacity_range'](fine)['lower'],100)
        self.assertAlmostEqual(M['frontier_capacity_range'](fine)['upper'],112.5)
    def test_jointly_feasible_separated_subset_not_hidden_by_full_overlap(self):
        edges=raw([7,4,3,6,3,1,5,4],[10,10,5,7,15,5,15,3],step=300)
        fit=M['joint_fit'](edges)
        self.assertEqual(fit['status'],'conditional_joint_estimate')
        self.assertGreaterEqual(fit['support']['group_count'],2)
        self.assertIn('not exhaustive',fit['discovery_completeness'])
        validation=M['joint_validate_frozen']({'a_percent_per_usd':.28,'estimated_total_api_cost_usd':100/.28},edges)
        self.assertEqual(validation['status'],'later_recurrence_supported')
    def test_any_passing_recurrence_beats_larger_failing_group(self):
        edges=raw([6,6,6,3,0,6,6,3],[7,7,10,5,3,7,15,15],step=300)
        v=M['joint_validate_frozen']({'a_percent_per_usd':.25,'estimated_total_api_cost_usd':400},edges)
        self.assertEqual(v['status'],'later_recurrence_supported')
        self.assertGreaterEqual(v['support']['independent_support_count'],2)
    def test_cost_lower_bound_bridge_is_physical_but_never_clean(self):
        edges=raw([1]*30,[10]*30)
        edges[7]['known_api_cost_usd']=0;edges[7]['clean_budget_eligible']=False
        fit=M['joint_fit'](edges)
        self.assertEqual(fit['evidence_counts']['raw_edges'],30)
        self.assertNotIn(edges[7]['id'],fit['selected_raw_edge_ids'])
        self.assertTrue(all(edges[7]['id'] not in p['atomic_block_ids'] for p in fit['selected_clean_portions']))

class JointIntegrationTests(RobustFixtures):
    def test_raw_graph_and_accounting(self):
        records,_=oracle_trace([10]*30,step=60);self.write('trace.jsonl',records)
        d,_=M['joint_segments']([self.root],PRICES,offsets=(0,),now=BASE+timedelta(hours=12));r,=d['model_epoch_estimates']
        self.assertEqual(r['estimated_total_api_cost_usd'],1000);self.assertFalse(r['offsets_intersected']);self.assertEqual(r['evidence_counts']['raw_edges'],30)
        self.assertIsNone(r['estimated_remaining_api_cost_usd']);self.assertEqual(d['counts']['local_events'],30);self.assertIn('original edges',M['render_joint_segments'](d))
    def test_joint_cli_offline(self):
        records,_=oracle_trace([10]*30,step=60);self.write('trace.jsonl',records);forbidden=AsyncMock(side_effect=AssertionError('Network forbidden'))
        with patch.dict(G,{'fetch_live':forbidden,'load_price_catalog_async':forbidden}):
            with contextlib.redirect_stdout(io.StringIO()) as o:code=asyncio.run(M['async_main'](['segments','joint','--root',str(self.root),'--price-catalog',str(CATALOG),'--json']))
        self.assertEqual(code,0);self.assertEqual(json.loads(o.getvalue())['kind'],'joint_raw_edge_quota_scenarios');forbidden.assert_not_called()
