import copy
import json
from pathlib import Path
import tempfile
import unittest

from benchlib.fnv06_resources import CgroupReader
from scripts.audit_fnv06_resources import audit_window


def fixture():
    rows=[]
    for i in range(6):
        files={'cpu.stat':f'usage_usec {10000+i*1000}\nuser_usec {8000+i*1000}\nsystem_usec 2000\nnr_periods {10+i}\nnr_throttled 0\nthrottled_usec 0\n',
               'cpu.max':'100000 100000\n','memory.current':'1000000\n','memory.max':'10000000\n',
               'memory.peak':'2000000\n','memory.events':'low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\n'}
        parent=copy.deepcopy(files);parent['cpu.max']='max 100000';parent['memory.max']='max'
        root={k:None for k in files}
        rows.append({'started_monotonic_s':10+i*.1,'finished_monotonic_s':10.001+i*.1,
            'started_epoch_ms':2000000+i*100,'finished_epoch_ms':2000001+i*100,
            'containers':{'generator':{'binding':{'pid':100,'process_start_ticks':12,'container_id':'a'*64,
                'cgroup':'/system.slice/docker.scope','cgroup_inode':555,'boot_id':'fixture-boot'},
                'files':files,'ancestors':{'/system.slice':parent,'/':root}}},
            'host':{'stat':f'cpu {100+i} 0 0 {1000+i*39} 0 0 0 0 0 0','meminfo':'MemTotal: 10000 kB\nMemAvailable: 8000 kB\n'}})
    return rows


class ResourceAuditTests(unittest.TestCase):
    def audit(self,rows):return audit_window(rows,start_epoch_ms=2000050,finish_epoch_ms=2000450)
    def test_idle_bracket_passes_with_declared_coverage(self):
        result=self.audit(fixture());self.assertTrue(result['generator_headroom_passed'])
        self.assertEqual(result['samples_covering_window'],6)
    def test_throttled_container_is_detected_despite_quiet_host(self):
        rows=fixture()
        for row in rows[2:]:row['containers']['generator']['files']['cpu.stat']=row['containers']['generator']['files']['cpu.stat'].replace('nr_throttled 0','nr_throttled 1').replace('throttled_usec 0','throttled_usec 12000')
        result=self.audit(rows);self.assertFalse(result['generator_headroom_passed'])
        self.assertIn('generator_or_ancestor_cpu_throttled:leaf',result['issues'])
        self.assertLess(result['maximum_sampled_host_cpu_busy_fraction'],.1)
    def test_ancestor_throttling_cannot_be_hidden_by_clean_leaf(self):
        rows=fixture()
        for row in rows[2:]:
            entry=row['containers']['generator']['ancestors']['/system.slice']
            entry['cpu.stat']=entry['cpu.stat'].replace('nr_throttled 0','nr_throttled 1')
        self.assertIn('generator_or_ancestor_cpu_throttled:/system.slice',self.audit(rows)['issues'])
    def test_pressure_and_lifetime_peak_are_gated(self):
        for field,value,issue in [('memory.events','low 0\nhigh 1\nmax 0\noom 0\noom_kill 0\n','generator_or_ancestor_memory_pressure:leaf'),
            ('memory.peak','9000000','generator_or_ancestor_memory_headroom_low:leaf'),
            ('memory.peak',None,'lifetime_memory_peak_unavailable:leaf')]:
            with self.subTest(field=field,value=value):
                rows=fixture()
                for row in rows[2:]:row['containers']['generator']['files'][field]=value
                self.assertIn(issue,self.audit(rows)['issues'])
    def test_missing_coverage_gaps_clock_jump_identity_and_limit_drift_rejected(self):
        rows=fixture();mutations=[]
        mutations.extend([rows[1:],rows[:-1],rows[:1]+rows[3:]])
        for kind in ('clock','pid','quota','counter','ancestor','raw_duplicate'):
            case=copy.deepcopy(rows);item=case[3]['containers']['generator']
            if kind=='clock':case[3]['started_epoch_ms']+=100;case[3]['finished_epoch_ms']+=100
            elif kind=='pid':item['binding']['pid']+=1
            elif kind=='quota':item['files']['cpu.max']='200000 100000'
            elif kind=='counter':item['files']['cpu.stat']=item['files']['cpu.stat'].replace('usage_usec 13000','usage_usec 1')
            elif kind=='ancestor':item['ancestors'].pop('/')
            else:item['files']['cpu.stat']+='usage_usec 1\n'
            mutations.append(case)
        for i,case in enumerate(mutations):
            with self.subTest(case=i),self.assertRaises((AssertionError,ValueError,KeyError)):self.audit(case)


class ResourceReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.proc=self.base/'proc';self.root=self.base/'cgroup'
        (self.proc/'100').mkdir(parents=True);(self.proc/'sys/kernel/random').mkdir(parents=True)
        (self.proc/'sys/kernel/random/boot_id').write_text('fixture-boot')
        (self.proc/'100/cgroup').write_text('0::/system.slice/docker.scope\n')
        # The command name includes spaces and a closing parenthesis.
        self.stat='100 (a tricky ) name) '+' '.join(['S']+['0']*18+['123'])+'\n'
        (self.proc/'100/stat').write_text(self.stat)
        leaf=self.root/'system.slice/docker.scope';leaf.mkdir(parents=True)
        (self.root/'cgroup.controllers').write_text('cpu memory')
        for name,value in fixture()[0]['containers']['generator']['files'].items():(leaf/name).write_text(value)
    def test_read_is_bound_to_pid_start_time_membership_and_inode(self):
        reader=CgroupReader(100,'a'*64,proc=self.proc,root=self.root)
        self.assertEqual(reader.read()['binding']['process_start_ticks'],123)
        (self.proc/'100/stat').write_text(self.stat.replace('123','124'))
        with self.assertRaises(ValueError):reader.read()
    def test_unsafe_root_deleted_and_traversing_membership_rejected(self):
        for name in ('/','/../escape','/system.slice/docker.scope (deleted)'):
            with self.subTest(name=name):
                (self.proc/'100/cgroup').write_text('0::'+name+'\n')
                with self.assertRaises(ValueError):CgroupReader(100,'a'*64,proc=self.proc,root=self.root)


if __name__=='__main__':unittest.main()
