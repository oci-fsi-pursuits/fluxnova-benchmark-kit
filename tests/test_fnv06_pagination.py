import copy
from urllib.parse import parse_qs,urlparse
import unittest

from benchlib.fnv06_pagination import PaginatedRecorder
from scripts.audit_fnv06_functional import paged_inventory


class InventoryServer(PaginatedRecorder):
    def __init__(self,size,mode=None):
        self.rows=[{'id':'row-'+str(i),'value':i} for i in range(size)]
        self.requests=[];self.mode=mode;self.count_calls=0

    def get(self,path):
        parsed=urlparse(path);params=parse_qs(parsed.query)
        if parsed.path.endswith('/count'):
            self.count_calls+=1;n=len(self.rows)
            if self.mode=='count_changed' and self.count_calls>1:n+=1
            response={'count':n}
        else:
            start=int(params['firstResult'][0]);limit=int(params['maxResults'][0])
            if self.mode=='ignored_offset':start=0
            response=copy.deepcopy(self.rows[start:start+limit])
            if self.mode=='short_page' and start==500:response.pop()
            if self.mode=='duplicate' and start==500:response[0]=copy.deepcopy(self.rows[0])
        row={'index':len(self.requests),'method':'GET','path':path,'status':200,'response':response}
        self.requests.append(row);return row


class CountedPaginationTests(unittest.TestCase):
    def test_empty_boundary_and_larger_inventories_are_complete(self):
        for size in (0,1,499,500,501,1000,1001,13338):
            with self.subTest(size=size):
                server=InventoryServer(size);item=server.collection('/history/variable-instance')
                self.assertEqual(paged_inventory(item,server.requests,'/history/variable-instance'),server.rows)

    def test_job_log_filter_is_bound_to_every_page_and_count(self):
        server=InventoryServer(2200);params={'jobDefinitionId':'owned:id'}
        item=server.collection('/history/job-log',params)
        self.assertEqual(len(paged_inventory(item,server.requests,'/history/job-log',params)),2200)
        for row in server.requests:
            self.assertEqual(parse_qs(urlparse(row['path']).query)['jobDefinitionId'],['owned:id'])
        with self.assertRaises(AssertionError):paged_inventory(item,server.requests,'/history/job-log',{'jobDefinitionId':'another'})

    def test_collector_refuses_drift_short_pages_duplicates_and_ignored_offset(self):
        for mode in ('count_changed','short_page','duplicate','ignored_offset'):
            with self.subTest(mode=mode),self.assertRaises(RuntimeError):
                InventoryServer(1001,mode).collection('/history/variable-instance')

    def test_independent_auditor_rejects_missing_forged_or_misbound_pages(self):
        for change in ('missing','duplicate','count','offset','terminal','ordering','extra_filter','invented_row'):
            server=InventoryServer(1001);item=server.collection('/history/variable-instance');rows=server.requests
            if change=='missing':item['list_requests'].pop(1)
            if change=='duplicate':rows[item['list_requests'][1]]['response'][0]=copy.deepcopy(rows[1]['response'][0]);item['rows'][500]=copy.deepcopy(item['rows'][0])
            if change=='count':rows[item['count_request']]['response']['count']-=1
            if change=='offset':rows[item['list_requests'][1]]['path']=rows[item['list_requests'][0]]['path']
            if change=='terminal':rows[item['terminal_request']]['response']=[{'id':'hidden'}]
            if change=='ordering':item['list_requests'].reverse()
            if change=='extra_filter':rows[item['count_request']]['path']+='?processInstanceId=hide-others'
            if change=='invented_row':item['rows'][1]={'id':'invented'}
            with self.subTest(change=change),self.assertRaises(AssertionError):
                paged_inventory(item,rows,'/history/variable-instance')


if __name__=='__main__':unittest.main()
