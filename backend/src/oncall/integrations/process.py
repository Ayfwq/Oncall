from __future__ import annotations

import asyncio
import psutil
from oncall.application.dtos import ProcessTargetDTO
from oncall.domain.schemas import ToolResult
from .base import CollectResult


def _matches(proc: psutil.Process, target: ProcessTargetDTO) -> bool:
    try:
        info=proc.as_dict(attrs=['name','exe','cmdline','cwd'])
    except (psutil.AccessDenied,psutil.NoSuchProcess):
        return False
    name=(info.get('name') or '').lower(); exe=(info.get('exe') or '').lower()
    if target.executable:
        needle=target.executable.lower()
        if needle not in name and needle not in exe:return False
    cmd=' '.join(info.get('cmdline') or []).lower()
    if any(x.lower() not in cmd for x in target.cmdline_filters):return False
    if target.cwd:
        cwd=(info.get('cwd') or '').lower()
        if target.cwd.lower() not in cwd:return False
    return True


class ProcessIntegration:
    name='process'
    def __init__(self, targets:list[ProcessTargetDTO]): self.targets=targets

    async def collect(self)->CollectResult: return await asyncio.to_thread(self._collect_sync)

    def _rows(self):
        matched=[]
        for p in psutil.process_iter():
            for target in self.targets:
                if not target.enabled or not _matches(p,target):continue
                try:
                    p.cpu_percent(None)  # prime a shared sampling interval
                    matched.append((p,target))
                except (psutil.AccessDenied,psutil.NoSuchProcess):pass
                break
        if matched:
            import time;time.sleep(0.1)
        rows=[]
        for p,target in matched:
            try:
                with p.oneshot():
                    rows.append({'target':target.name,'pid':p.pid,'name':p.name(),'cpu_percent':float(p.cpu_percent(None)),'rss_bytes':float(p.memory_info().rss),'ppid':p.ppid(),'create_time':p.create_time(),'cmdline':' '.join(p.cmdline())[:2000]})
            except (psutil.AccessDenied,psutil.NoSuchProcess):pass
        return rows

    def _collect_sync(self)->CollectResult:
        rows=self._rows(); pids={r['pid'] for r in rows}; child_count=0
        for pid in list(pids):
            try: child_count += len(psutil.Process(pid).children(recursive=False))
            except (psutil.AccessDenied,psutil.NoSuchProcess): pass
        return CollectResult(name=self.name,ok=True,signals={
            'process.target.alive': 1.0 if rows else 0.0,
            'process.target.count': float(len(rows)),
            'process.target.cpu_percent_sum': float(sum(r['cpu_percent'] for r in rows)),
            'process.target.rss_bytes_sum': float(sum(r['rss_bytes'] for r in rows)),
            'process.target.child_count': float(child_count),
        },resources={'top':sorted(rows,key=lambda x:x['cpu_percent'],reverse=True)[:5]})

    async def query(self, limit:int=30)->ToolResult:
        rows=await asyncio.to_thread(self._rows); rows=sorted(rows,key=lambda x:(x['cpu_percent'],x['rss_bytes']),reverse=True)[:max(1,min(limit,100))]
        return ToolResult(ok=True,summary=f'找到 {len(rows)} 个匹配进程',data=rows,truncated=len(rows)>=limit)
