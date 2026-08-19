from __future__ import annotations

import asyncio
import os
import time
import psutil
from oncall.domain.schemas import ToolResult
from .base import CollectResult


class HostIntegration:
    name='host'
    def __init__(self):
        self._prev_disk=None;self._prev_net=None;self._prev_ts=None

    @staticmethod
    def _disk_root() -> str:
        if os.name=='nt':
            return (os.environ.get('SystemDrive') or 'C:')+'\\'
        return '/'

    async def collect(self) -> CollectResult:
        return await asyncio.to_thread(self._collect_sync)

    def _collect_sync(self) -> CollectResult:
        ts=time.time();vm=psutil.virtual_memory();disk=psutil.disk_usage(self._disk_root())
        dio=psutil.disk_io_counters();net=psutil.net_io_counters()
        signals={
            'host.cpu.percent':float(psutil.cpu_percent(interval=0.1)),
            'host.memory.percent':float(vm.percent),
            'host.memory.available_bytes':float(vm.available),
            'host.disk.usage_percent':float(disk.percent),
            'host.disk.free_bytes':float(disk.free),
            'host.disk.read_bytes_per_sec':0.0,'host.disk.write_bytes_per_sec':0.0,
            'host.net.rx_bytes_per_sec':0.0,'host.net.tx_bytes_per_sec':0.0,
        }
        if self._prev_ts and ts>self._prev_ts:
            dt=ts-self._prev_ts
            if dio and self._prev_disk:
                signals['host.disk.read_bytes_per_sec']=max(0.0,(dio.read_bytes-self._prev_disk.read_bytes)/dt)
                signals['host.disk.write_bytes_per_sec']=max(0.0,(dio.write_bytes-self._prev_disk.write_bytes)/dt)
            if net and self._prev_net:
                signals['host.net.rx_bytes_per_sec']=max(0.0,(net.bytes_recv-self._prev_net.bytes_recv)/dt)
                signals['host.net.tx_bytes_per_sec']=max(0.0,(net.bytes_sent-self._prev_net.bytes_sent)/dt)
        self._prev_disk,self._prev_net,self._prev_ts=dio,net,ts
        counters={
            'ts':ts,
            'disk_read_bytes':float(dio.read_bytes) if dio else None,
            'disk_write_bytes':float(dio.write_bytes) if dio else None,
            'net_rx_bytes':float(net.bytes_recv) if net else None,
            'net_tx_bytes':float(net.bytes_sent) if net else None,
        }
        return CollectResult(name=self.name,ok=True,signals=signals,resources={'cpu_count':psutil.cpu_count(),'boot_time':psutil.boot_time(),'counters':counters,'disk_root':self._disk_root()})

    async def query(self) -> ToolResult:
        c=await self.collect()
        return ToolResult(ok=c.ok,summary=f"CPU {c.signals.get('host.cpu.percent',0):.1f}%, memory {c.signals.get('host.memory.percent',0):.1f}%, disk {c.signals.get('host.disk.usage_percent',0):.1f}%",data={'signals':c.signals,'resources':c.resources})
