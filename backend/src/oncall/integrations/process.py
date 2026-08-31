from __future__ import annotations

import asyncio
import time

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


def _safe_process(pid: int) -> psutil.Process | None:
    try:
        return psutil.Process(pid)
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        return None


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
            time.sleep(0.1)
        rows=[]
        for p,target in matched:
            try:
                with p.oneshot():
                    rows.append({'target':str(target.id or target.name),'pid':p.pid,'name':p.name(),'cpu_percent':float(p.cpu_percent(None)),'rss_bytes':float(p.memory_info().rss),'ppid':p.ppid(),'create_time':p.create_time(),'cmdline':' '.join(p.cmdline())[:2000]})
            except (psutil.AccessDenied,psutil.NoSuchProcess):pass
        return rows

    def _collect_sync(self)->CollectResult:
        rows=self._rows(); pids={r['pid'] for r in rows}; child_count=0
        for pid in list(pids):
            try: child_count += len(psutil.Process(pid).children(recursive=False))
            except (psutil.AccessDenied,psutil.NoSuchProcess): pass
        signals={
            'process.target.alive': 1.0 if rows else 0.0,
            'process.target.count': float(len(rows)),
            'process.target.cpu_percent_sum': float(sum(r['cpu_percent'] for r in rows)),
            'process.target.rss_bytes_sum': float(sum(r['rss_bytes'] for r in rows)),
            'process.target.child_count': float(child_count),
        }
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            grouped.setdefault(str(row.get('target') or 'default'), []).append(row)
        resource_signals = {}
        for key, target_rows in grouped.items():
            resource_signals[key] = {
                'process.target.alive': 1.0,
                'process.target.count': float(len(target_rows)),
                'process.target.cpu_percent_sum': float(sum(r['cpu_percent'] for r in target_rows)),
                'process.target.rss_bytes_sum': float(sum(r['rss_bytes'] for r in target_rows)),
                'process.target.child_count': float(sum(
                    len(p.children(recursive=False))
                    for r in target_rows
                    for p in [_safe_process(r['pid'])]
                    if p is not None
                )),
            }
        # Emit an explicit zero for configured process targets that currently have
        # no match, so ``process.target.alive < 1`` can fire per target.
        for target in self.targets:
            if target.enabled:
                resource_signals.setdefault(str(target.id or target.name), {
                    'process.target.alive': 0.0,
                    'process.target.count': 0.0,
                    'process.target.cpu_percent_sum': 0.0,
                    'process.target.rss_bytes_sum': 0.0,
                    'process.target.child_count': 0.0,
                })
        return CollectResult(name=self.name,ok=True,signals=signals,resource_signals=resource_signals,resources={'top':sorted(rows,key=lambda x:x['cpu_percent'],reverse=True)[:5]})

    async def query(self, limit:int=30)->ToolResult:
        rows=await asyncio.to_thread(self._rows); rows=sorted(rows,key=lambda x:(x['cpu_percent'],x['rss_bytes']),reverse=True)[:max(1,min(limit,100))]
        return ToolResult(ok=True,summary=f'找到 {len(rows)} 个匹配进程',data=rows,truncated=len(rows)>=limit)
