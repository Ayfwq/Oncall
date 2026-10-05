<script setup lang="ts">
const emit = defineEmits<{ prompt: [text: string] }>()
const suggestions = [
  { title: '排查服务异常', description: '从现象出发，梳理可能的根因', tag: '故障诊断', icon: 'pulse', prompt: '服务响应变慢时，应该如何逐步排查？' },
  { title: '理解监控指标', description: '读懂指标变化，定位性能瓶颈', tag: '指标分析', icon: 'chart', prompt: '如何结合 CPU、内存和请求延迟判断服务的性能瓶颈？' },
  { title: '检索运维知识', description: '查找操作手册与历史处置经验', tag: '知识检索', icon: 'book', prompt: '请检索知识库中与故障排查有关的操作手册，并标注引用来源。' },
]
</script>

<template>
  <div class="welcome-panel">
    <div class="welcome-heading"><div class="welcome-eyebrow">LESS NOISE. MORE SIGNAL.</div><h1>复杂运维，<span>简单对话。</span></h1><p>我是巡脉，你的 AI 运维搭档。一起从问题找到答案。</p></div>
    <section class="mission-card" aria-label="智能运维助手介绍">
      <div class="mission-copy"><span class="mission-label"><i></i> PULSEOPS INTELLIGENCE</span><h2>看见信号背后的<br />每一个关键线索</h2><p>关联监控数据，检索运维知识。<br />让排查有方向，让判断有依据。</p><router-link to="/projects">连接你的项目 <span>↗</span></router-link><span class="mission-footnote">从日志、指标到知识，串联排障上下文</span></div>
      <div class="signal-art" aria-hidden="true">
        <div class="signal-grid"></div><div class="orbit orbit-outer"></div><div class="orbit orbit-inner"></div><div class="orbit-track"><i></i></div>
        <div class="signal-core"><svg viewBox="0 0 64 64" fill="none"><path d="M9 33h13l7-18 9 35 7-23 5 6h7" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg></div>
        <span class="signal-label signal-metrics"><i></i> METRICS</span><span class="signal-label signal-logs"><i></i> LOGS</span><span class="signal-label signal-knowledge"><i></i> KNOWLEDGE</span>
        <span class="signal-coordinate">SIGNAL → CONTEXT → INSIGHT</span>
      </div>
    </section>
    <div class="suggestion-heading"><h3>从这里开始</h3><span>选择一个方向，开启新的探索</span></div>
    <div class="suggestion-grid">
      <button v-for="item in suggestions" :key="item.title" class="suggestion-card" type="button" @click="emit('prompt', item.prompt)">
        <div class="suggestion-top"><span class="suggestion-icon" :class="item.icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path v-if="item.icon === 'pulse'" d="M3 12h5l3-8 4 16 3-8h3"/><path v-else-if="item.icon === 'chart'" d="M4 4v16h16M8 15v-4m5 4V7m5 8V4"/><template v-else><path d="M12 6v15M3 4c4-1 6 0 9 2 3-2 5-3 9-2v15c-4-1-6 0-9 2-3-2-5-3-9-2z"/></template></svg></span><span class="suggestion-arrow">↗</span></div>
        <b>{{ item.title }}</b><p>{{ item.description }}</p><span class="suggestion-tag">{{ item.tag }}</span>
      </button>
    </div>
    <div class="welcome-bottom"><span class="tiny-pulse">⌁</span> 少一些重复排查，多一些专注与从容。<span>PULSEOPS / 巡脉</span></div>
  </div>
</template>

<style scoped>
.welcome-panel { width: min(100%, 940px); margin: auto; padding: 34px 40px 18px; }
.welcome-eyebrow { font: 10px/1.5 ui-monospace, Consolas, monospace; letter-spacing: .18em; color: #78918a; margin-bottom: 10px; }
.welcome-heading h1 { font-size: clamp(25px, 2.45vw, 36px); letter-spacing: -.05em; font-weight: 650; margin-bottom: 9px; }
.welcome-heading h1 span { color: #82928c; font-weight: 400; }
.welcome-heading > p { font-size: 13px; color: #77857f; margin-bottom: 26px; }
.mission-card { min-height: 263px; display: grid; grid-template-columns: 1.1fr 1fr; background: linear-gradient(115deg, #f0f9f5, #f7fbf2 60%, #eef7f6); color: #355d4d; border: 1px solid #dfede4; border-radius: 18px; overflow: hidden; position: relative; box-shadow: 0 12px 28px -22px #427d592a; }
.mission-copy { padding: 27px 30px; position: relative; z-index: 1; }
.mission-label { display: flex; align-items: center; gap: 8px; font: 9px ui-monospace, Consolas, monospace; letter-spacing: .13em; color: #709782; }
.mission-label i { width: 5px; height: 5px; background: #91c9a5; border-radius: 50%; box-shadow: 0 0 12px #bef38a80; }
.mission-copy h2 { font-size: clamp(21px, 2vw, 28px); line-height: 1.5; font-weight: 500; letter-spacing: .02em; margin: 16px 0 10px; }
.mission-copy p { color: #7b9386; font-size: 12px; line-height: 1.8; margin-bottom: 20px; }
.mission-copy a { display: inline-flex; align-items: center; gap: 24px; background: #ffffff; color: #4a8063; border: 1px solid #cfe1d3; padding: 8px 14px; border-radius: 7px; font-size: 12px; font-weight: 600; transition: background .2s, transform .2s; }
.mission-copy a:hover { background: #edf7ed; transform: translateY(-2px); }
.mission-footnote { display: block; color: #8fa391; font-size: 9px; margin-top: 13px; }
.signal-art { position: relative; min-width: 0; background: radial-gradient(ellipse at 50% 50%, #b8ddc13a, transparent 65%); }
.signal-grid { position: absolute; inset: 0; background-image: linear-gradient(#82ad9414 1px, transparent 1px), linear-gradient(90deg,#82ad9414 1px,transparent 1px); background-size: 24px 24px; mask-image: radial-gradient(ellipse, black, transparent 75%); }
.orbit { position: absolute; left: 50%; top: 48%; border: 1px solid #93bda840; border-radius: 50%; transform: translate(-50%, -50%); }
.orbit-outer { width: 238px; height: 238px; box-shadow: 0 0 0 25px #a4cbb00a; }
.orbit-inner { width: 162px; height: 162px; border-style: dashed; border-color: #93bda84d; }
.orbit-track { position: absolute; width: 238px; height: 238px; left: calc(50% - 119px); top: calc(48% - 119px); animation: orbit-spin 32s linear infinite; }
.orbit-track i { position: absolute; top: 30px; left: 30px; width: 6px; height: 6px; border-radius: 50%; background: #8cbea0; box-shadow: 0 0 12px #9bc7a280; }
.signal-core { position: absolute; left: 50%; top: 48%; transform: translate(-50%, -50%); width: 88px; height: 88px; display: grid; place-items: center; border-radius: 25px; background: linear-gradient(145deg, #ffffff, #e6f4e9); border: 1px solid #c4dec9; color: #73ac88; box-shadow: 0 12px 35px #8aad8d20, inset 0 1px 10px #ffffff; }
.signal-core svg { width: 62px; height: 62px; }
.signal-label { position: absolute; display: flex; align-items: center; gap: 7px; font: 9px ui-monospace, Consolas, monospace; letter-spacing: .06em; border: 1px solid #d6e6d9; background: #ffffffdd; color: #779982; border-radius: 6px; padding: 7px 10px; }
.signal-label i { width: 4px; height: 4px; background: #9dc5a4; border-radius: 50%; }
.signal-metrics { left: 12%; top: 17%; }.signal-logs { right: 7%; top: 46%; }.signal-knowledge { left: 15%; bottom: 20%; }
.signal-coordinate { position: absolute; bottom: 15px; width: 100%; text-align: center; font: 8px ui-monospace, Consolas, monospace; letter-spacing: .14em; color: #769582; }
.suggestion-heading { display: flex; justify-content: space-between; align-items: center; margin: 26px 0 12px; }.suggestion-heading h3 { font-size: 13px; margin: 0; }.suggestion-heading > span { font-size: 10px; color: #8a9890; }
.suggestion-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.suggestion-card { text-align: left; font: inherit; padding: 17px; color: var(--text); background: #fff; border: 1px solid #e2e9e4; border-radius: 12px; cursor: pointer; transition: transform .2s, box-shadow .2s, border-color .2s; }
.suggestion-card:hover { transform: translateY(-4px); border-color: #a5c6b4; box-shadow: 0 10px 22px -12px #1e604333; }
.suggestion-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }.suggestion-icon { display: grid; place-items: center; width: 32px; height: 32px; border-radius: 9px; background: #eaf4ed; color: #528b66; }.suggestion-icon svg { width: 19px; height: 19px; }.suggestion-icon.chart { background: #edf0fc; color: #7986ba; }.suggestion-icon.book { background: #faf2e6; color: #bc9962; }.suggestion-arrow { color: #9aa89f; font-size: 17px; }
.suggestion-card b { font-size: 13px; font-weight: 600; }.suggestion-card p { color: #859189; font-size: 10px; margin: 6px 0 14px; line-height: 1.6; }.suggestion-tag { font-size: 9px; color: #77897f; background: #f4f7f4; padding: 3px 7px; border-radius: 4px; }
.welcome-bottom { display: flex; align-items: center; gap: 7px; color: #94a098; font-size: 10px; margin-top: 20px; }.welcome-bottom > span:last-child { margin-left: auto; font: 8px ui-monospace, Consolas, monospace; letter-spacing: .08em; }.tiny-pulse { color: #739481; font-size: 20px; }
@keyframes orbit-spin { to { transform: rotate(360deg); } }
@media (min-width: 1600px) { .welcome-panel { padding-top: 55px; }.mission-card { min-height: 290px; }.suggestion-card { padding: 21px; } }
@media (max-width: 1100px) and (min-width: 681px) { .welcome-panel { padding: 26px 24px 18px; }.signal-art { opacity: .75; }.mission-card { grid-template-columns: 1.1fr .8fr; }.mission-copy { padding: 24px; }.suggestion-grid { gap: 8px; }.suggestion-card { padding: 12px; } }
@media (max-width: 680px) { .welcome-panel { padding: 24px 18px 18px; }.welcome-heading > p { font-size: 12px; }.mission-card { min-height: 248px; grid-template-columns: 1fr; }.mission-copy { padding: 23px; }.mission-copy h2 { font-size: 23px; }.signal-art { position: absolute; width: 230px; inset: 0 -65px 0 auto; opacity: .35; pointer-events: none; }.signal-label, .signal-coordinate { display: none; }.suggestion-heading > span { display: none; }.suggestion-grid { grid-template-columns: 1fr; gap: 9px; }.suggestion-card { position: relative; padding: 14px 16px 14px 62px; }.suggestion-top { position: absolute; top: 18px; left: 16px; margin: 0; }.suggestion-arrow, .suggestion-tag { display: none; }.suggestion-card p { margin: 4px 0 0; }.welcome-bottom > span:last-child { display: none; } }
@media (max-height: 800px) and (min-width: 681px) {
  .welcome-panel { padding: 10px 34px 8px; }
  .welcome-eyebrow { margin-bottom: 5px; }
  .welcome-heading h1 { font-size: 27px; margin-bottom: 5px; }
  .welcome-heading > p { margin-bottom: 8px; font-size: 12px; }
  .mission-card { min-height: 150px; grid-template-columns: 1.25fr 1fr; }
  .mission-copy { padding: 16px 24px; }
  .mission-copy h2 { font-size: 20px; margin: 8px 0 5px; }
  .mission-copy h2 br, .mission-copy p br { display: none; }
  .mission-copy p { margin-bottom: 10px; font-size: 11px; }
  .mission-footnote, .welcome-bottom { display: none; }
  .orbit-outer, .orbit-track { width: 174px; height: 174px; }
  .orbit-track { left: calc(50% - 87px); top: calc(48% - 87px); }
  .orbit-inner { width: 120px; height: 120px; }
  .signal-core { width: 68px; height: 68px; border-radius: 20px; }
  .signal-core svg { width: 47px; height: 47px; }
  .signal-coordinate { display: none; }
  .suggestion-heading { margin: 12px 0 9px; }
  .suggestion-card { padding: 12px; }
  .suggestion-top { margin-bottom: 8px; }
  .suggestion-card p { margin: 4px 0 0; }
  .suggestion-tag { display: none; }
}
@media (min-width: 681px) and (max-width: 1000px) {
  .mission-card { grid-template-columns: 1fr; }
  .signal-art { position: absolute; width: 220px; inset: 0 -85px 0 auto; opacity: .4; pointer-events: none; }
  .signal-label, .signal-coordinate { display: none; }
  .suggestion-grid { grid-template-columns: 1fr; }
  .suggestion-card { position: relative; padding: 14px 14px 14px 58px; }
  .suggestion-top { position: absolute; left: 14px; top: 16px; margin: 0; }
  .suggestion-arrow, .suggestion-tag, .suggestion-heading > span, .welcome-bottom { display: none; }
  .suggestion-card p { margin: 4px 0 0; }
}
@media (prefers-reduced-motion: reduce) { .orbit-track { animation: none; }.suggestion-card, .mission-copy a { transition: none; } }
</style>
