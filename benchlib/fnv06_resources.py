"""Read raw Linux cgroup-v2 counters without changing limits or processes.

Callers must establish container ownership before supplying its full Docker ID
and host PID. Every read rechecks PID start time, membership and cgroup inode.
References: https://docs.kernel.org/admin-guide/cgroup-v2.html and
https://docs.kernel.org/filesystems/proc.html
"""
from pathlib import Path, PurePosixPath
import re
import time


class CgroupReader:
    def __init__(self,pid,container_id,*,proc=Path('/proc'),root=Path('/sys/fs/cgroup')):
        if type(pid) is not int or pid<=0 or not re.fullmatch('[0-9a-f]{64}',container_id):
            raise ValueError('An owned container ID and host PID are required')
        self.pid=pid;self.container_id=container_id;self.proc=Path(proc);self.root=Path(root).resolve()
        if not (self.root/'cgroup.controllers').is_file():raise ValueError('Unified cgroup v2 is required')
        self.binding=self._binding()

    def _binding(self):
        raw=(self.proc/str(self.pid)/'stat').read_text()
        if int(raw.split(' ',1)[0])!=self.pid:raise ValueError('Process identity differs')
        fields=raw.rsplit(')',1)[1].split()
        if len(fields)<20 or fields[0]=='Z':raise ValueError('Process is absent or exited')
        ticks=int(fields[19]);rows=(self.proc/str(self.pid)/'cgroup').read_text().splitlines()
        unified=[r[3:] for r in rows if r.startswith('0::')]
        if len(unified)!=1:raise ValueError('Missing or ambiguous unified membership')
        name=unified[0];parts=PurePosixPath(name)
        if not name.startswith('/') or name=='/' or '..' in parts.parts or ' (deleted)' in name:
            raise ValueError('Invalid container cgroup')
        folder=(self.root/name.lstrip('/')).resolve()
        if not folder.is_relative_to(self.root) or folder==self.root:raise ValueError('Cgroup escaped the mounted root')
        return {'container_id':self.container_id,'pid':self.pid,'process_start_ticks':ticks,
                'cgroup':name,'cgroup_inode':folder.stat().st_ino,
                'boot_id':(self.proc/'sys/kernel/random/boot_id').read_text().strip()}

    def read(self):
        if self._binding()!=self.binding:raise ValueError('Owned process or cgroup changed')
        folder=self.root/self.binding['cgroup'].lstrip('/')
        files={name:(folder/name).read_text() for name in
               ('cpu.stat','cpu.max','memory.current','memory.max','memory.events')}
        files['memory.peak']=(folder/'memory.peak').read_text() if (folder/'memory.peak').is_file() else None
        ancestors={};parent=folder.parent
        while True:
            name='/'+parent.relative_to(self.root).as_posix() if parent!=self.root else '/'
            ancestors[name]={file:(parent/file).read_text() if (parent/file).is_file() else None
                             for file in ('cpu.stat','cpu.max','memory.max','memory.current','memory.events','memory.peak')}
            if parent==self.root:break
            parent=parent.parent
        if self._binding()!=self.binding:raise ValueError('Process changed during counter collection')
        return {'binding':dict(self.binding),'files':files,'ancestors':ancestors}


def sample(readers,*,proc=Path('/proc')):
    if not readers:raise ValueError('At least one owned container is required')
    started=time.monotonic();wall=time.time()*1000
    result={'started_monotonic_s':started,'started_epoch_ms':wall,
            'containers':{role:reader.read() for role,reader in readers.items()},
            'host':{'stat':Path(proc,'stat').read_text().splitlines()[0],
                    'meminfo':Path(proc,'meminfo').read_text()}}
    result.update(finished_monotonic_s=time.monotonic(),finished_epoch_ms=time.time()*1000)
    return result
