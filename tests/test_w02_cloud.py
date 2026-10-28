import copy
import unittest

from benchlib.w02_cloud import assess_telemetry, measurement_plan, require_owned_database, require_matched_controls


class W02CloudTests(unittest.TestCase):
    def target(self):
        nonce='a'*32
        return {'id':'target','display_name':'fluxnova-w02-'+nonce[:12],'instance_count':1,'source':{'source_type':'NONE'},
                'freeform_tags':{'managed_by':'fluxnova-benchmarks','w02_run_id':nonce,'temporary_comparison':'true','comparison_lane':'w02'}}

    def test_ownership_rejects_source_wrong_lane_or_target(self):
        target=self.target();kwargs=dict(source_id='original',target_id='target',run_id='a'*32)
        require_owned_database(target,**kwargs)
        for mutated in ({**target,'id':'original'}, {**target,'instance_count':2},
                        {**target,'source':{'source_type':'BACKUP'}}, {**target,'freeform_tags':{}}):
            with self.assertRaises(ValueError):require_owned_database(mutated,**kwargs)
        with self.assertRaises(ValueError):require_owned_database(target,**{**kwargs,'source_id':'target'})

    def test_pair_order_reproducible_and_balanced(self):
        plan=measurement_plan('b'*32,1)
        self.assertEqual(plan,measurement_plan('b'*32,1))
        self.assertEqual(len(plan['pairs']),5)
        self.assertEqual(sum(p[0]=='sequential' for p in plan['pairs']),3)
        self.assertTrue(all(set(p)=={'sequential','parallel'} for p in plan['pairs']))
        with self.assertRaises(ValueError):measurement_plan('b'*32,10)
        with self.assertRaises(ValueError):require_matched_controls({'slots':2},{'slots':3})

    def test_docker_mount_order_is_ignored_but_permissions_and_command_order_are_not(self):
        before={'containers':{'backend':{'arguments':['python','-m','services.workflow_checks'],
            'mounts':[{'Type':'bind','Source':'source','Destination':'/source','RW':False},
                      {'Type':'bind','Source':'backend','Destination':'/data','RW':True}]}}}
        after=copy.deepcopy(before);after['containers']['backend']['mounts'].reverse()
        require_matched_controls(before,after)
        altered=copy.deepcopy(after);altered['containers']['backend']['mounts'][1]['RW']=True
        with self.assertRaises(ValueError):require_matched_controls(before,altered)
        altered=copy.deepcopy(after);altered['containers']['backend']['arguments'].reverse()
        with self.assertRaises(ValueError):require_matched_controls(before,altered)

    def telemetry(self):
        rows=[]
        for i in range(5):
            rows.append({'monotonic_s':i*5,'host':{'cpu_total_ticks':1000+i*100,'cpu_idle_ticks':800+i*90,
                'memory_total_bytes':1000,'memory_available_bytes':700},
                'containers':{role:{'running':True,'oom_killed':False,'restart_count':0,'cpu_percent':2.,'memory_usage':'10 MiB / 256 MiB'}
                              for role in ('engine','backend','worker')},
                'database':{'commits':100+i,'deadlocks':0,'connections':5,'active':1,'lock_waiters':0,'server_version':'17.6','tls':True},
                'engine':{'active_instances':1,'external_tasks':1,'incidents':0,'jobs':0}})
        return rows,{'started_monotonic_s':1,'finished_monotonic_s':19}

    def test_telemetry_coverage_and_headroom(self):
        rows,execution=self.telemetry();result=assess_telemetry(rows,execution)
        self.assertTrue(result['complete']);self.assertTrue(result['headroom'])
        self.assertAlmostEqual(result['host_cpu_p95_percent'],10)
        self.assertFalse(assess_telemetry(rows[1:],execution)['complete'])
        self.assertFalse(assess_telemetry(rows[:-1],execution)['complete'])

    def test_missing_role_restart_tls_or_invalid_clock_rejected(self):
        rows,execution=self.telemetry()
        mutations=[lambda x:x[1]['containers'].pop('backend'),
                   lambda x:x[1]['containers']['engine'].update(restart_count=1),
                   lambda x:x[1]['database'].update(tls=False),
                   lambda x:x[1]['database'].update(commits=float('nan')),
                   lambda x:x[1].update(monotonic_s=0),
                   lambda x:x[1]['engine'].update(incidents=1)]
        for change in mutations:
            altered=copy.deepcopy(rows);change(altered)
            self.assertFalse(assess_telemetry(altered,execution)['complete'])

    def test_busy_host_and_memory_pressure_fail_headroom(self):
        rows,execution=self.telemetry()
        for i,row in enumerate(rows):row['host']['cpu_idle_ticks']=800+i*10
        result=assess_telemetry(rows,execution)
        self.assertTrue(result['complete']);self.assertFalse(result['headroom'])
        rows,execution=self.telemetry();rows[1]['host']['memory_available_bytes']=100
        self.assertFalse(assess_telemetry(rows,execution)['headroom'])


if __name__=='__main__':unittest.main()
