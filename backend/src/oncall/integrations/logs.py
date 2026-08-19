from __future__ import annotations

import asyncio
import re
from collections import Counter
from pathlib import Path

from oncall.application.dtos import LogSourceDTO
from oncall.domain.schemas import ToolResult

from .base import CollectResult

ERROR_RE=re.compile(r'\b(error|exception|critical|fatal)\b',re.I)
WARN_RE=re.compile(r'\b(warn|warning)\b',re.I)
NUMBER_RE=re.compile(r'\b\d+\b')
HEX_RE=re.compile(r'0x[0-9a-f]+',re.I)


def signature(line:str)->str:
    text=NUMBER_RE.sub('<n>',line.strip())
    text=HEX_RE.sub('<hex>',text)
    return text[:500]


class LogIntegration:
    name='logs'
    def __init__(self,sources:list[LogSourceDTO],window_lines:int=3000):
        self.sources=sources;self.window_lines=window_lines

    @staticmethod
    def summarize(lines_by_source:dict[str,list[str]],window_seconds:int=300)->CollectResult:
        errors=[];warnings=[];statuses={}
        for source,lines in lines_by_source.items():
            statuses[source]={'ok':True,'lines':len(lines)}
            errors.extend(l.strip() for l in lines if ERROR_RE.search(l))
            warnings.extend(l.strip() for l in lines if WARN_RE.search(l))
        counts=Counter(signature(x) for x in errors)
        top_sig,top_count=(counts.most_common(1)[0] if counts else ('',0))
        minutes=max(window_seconds/60.0,1/60)
        return CollectResult(
            name='logs',ok=True,
            signals={
                'log.error.count_window':float(len(errors)),
                'log.warning.count_window':float(len(warnings)),
                'log.error.rate_per_min':float(len(errors))/minutes,
                'log.top_signature.count':float(top_count),
            },
            resources={'sources':statuses,'top_signature':top_sig,'recent_errors':errors[-10:]},
        )

    async def collect(self)->CollectResult:
        # Fallback summary used outside MonitoringEngine. The engine itself uses a
        # persistent DB cursor, so it only counts newly appended log lines.
        return await asyncio.to_thread(self._collect_tail_sync)

    def _tail(self,path:Path,encoding:str,max_lines:int)->list[str]:
        if not path.exists() or not path.is_file():return []
        # Tool path is intentionally bounded. Monitoring uses byte cursors instead.
        with path.open('r',encoding=encoding,errors='replace') as f:
            from collections import deque
            return list(deque(f,maxlen=max_lines))

    def _collect_tail_sync(self)->CollectResult:
        lines_by_source={}
        errors={}
        for src in self.sources:
            p=Path(src.path)
            try:
                if not p.exists() or not p.is_file():
                    # A missing source is an explicit error, not a healthy empty log.
                    errors[src.path]='file not found'
                    continue
                lines_by_source[src.path]=self._tail(p,src.encoding,self.window_lines)
            except Exception as exc:errors[src.path]=str(exc)
        result=self.summarize(lines_by_source,300)
        for source,error in errors.items():result.resources.setdefault('sources',{})[source]={'ok':False,'error':error}
        result.ok=bool(lines_by_source) or not self.sources
        return result

    async def query(self,keyword:str='',level:str='',limit:int=100)->ToolResult:
        def run():
            result=[]
            for src in self.sources:
                p=Path(src.path)
                for line in self._tail(p,src.encoding,10000):
                    if keyword and keyword.lower() not in line.lower():continue
                    if level.lower()=='error' and not ERROR_RE.search(line):continue
                    if level.lower() in ('warn','warning') and not WARN_RE.search(line):continue
                    result.append({'source':src.path,'line':line.rstrip()[:4000]})
            return result[-max(1,min(limit,300)):]
        rows=await asyncio.to_thread(run)
        return ToolResult(ok=True,summary=f'返回 {len(rows)} 条日志',data=rows,truncated=len(rows)>=limit)
