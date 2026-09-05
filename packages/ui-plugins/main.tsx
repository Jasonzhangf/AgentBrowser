import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import type { Context } from '@cordisjs/core';
import { androidPort, type ProbeSnapshot } from '../client-domain/probe';
import { createKernel } from '../ui-kernel/kernel';
import './probe.css';

declare global { interface Window { ProbeNative?: {request(raw: string): string} } }
const labels = {idle:'等待播放',starting:'正在打开样本',playing:'正在显示',stopping:'正在释放',stopped:'已停止',completed:'播放结束',error:'播放失败'};
function Panel({ctx}: {ctx: Context}) {
  const [snapshot, setSnapshot] = useState<ProbeSnapshot>(() => ctx.probe.request({op:'status'}));
  const [problem, setProblem] = useState('');
  useEffect(() => {
    const poll = setInterval(() => {
      try { setSnapshot(ctx.probe.request({op:'status'})); } catch (error) { setProblem(String(error)); }
    }, 250);
    return () => clearInterval(poll);
  }, [ctx]);
  function request(sample?: 'portrait'|'broken') {
    try { setProblem(''); setSnapshot(ctx.probe.request(sample ? {op:'play',sample} : {op:'stop'})); }
    catch (error) { setProblem(String(error)); }
  }
  const busy = !snapshot.released;
  return <section data-state={snapshot.state} data-released={String(snapshot.released)} data-frames={snapshot.renderedFrames}>
    <h1>AgentBrowser</h1><p className="intro">{snapshot.source === 'annexb' ? '原生 Annex B 解码端口 · 本地输入' : '本地视频探针 · 12 秒 H.264 样本'}</p>
    <div className="status" role="status"><strong>{labels[snapshot.state]}</strong><span>{snapshot.renderedFrames} 帧呈现</span></div>
    <div className="actions"><button id="play" disabled={busy} onClick={() => request('portrait')}>播放样本</button><button id="stop" className="secondary" disabled={!busy} onClick={() => request()}>停止并释放</button></div>
    {(snapshot.error || problem) && <p className="error" role="alert">{snapshot.error || problem}。停止后可重新播放。</p>}
    <details><summary>探针检查</summary><p className="detail">{snapshot.codec || '解码器尚未打开'} · {snapshot.released ? '资源已释放' : '资源使用中'}</p><button id="broken" className="secondary" disabled={busy} onClick={() => request('broken')}>测试坏样本</button></details>
  </section>;
}
async function boot() {
  const host = document.getElementById('root')!;
  const port = androidPort(window.ProbeNative);
  const shell = document.createElement('div');
  const slot = document.createElement('div');
  const footer = document.createElement('footer');
  const toggle = document.createElement('button');
  const note = document.createElement('p');
  toggle.id = 'plugin'; toggle.className = 'text-button';
  note.textContent = '未连接浏览器 Host · 无远程控制';
  footer.append(toggle, note); shell.append(slot, footer); host.append(shell);
  function mount(ctx: Context) { const root = createRoot(slot); root.render(<Panel ctx={ctx}/>); ctx.on('dispose', () => root.unmount()); }
  let kernel = await createKernel(port, mount);
  toggle.textContent = '卸载 UI 插件'; toggle.dataset.mounted = 'true';
  toggle.onclick = async () => {
    toggle.disabled = true;
    try {
      if (kernel.mounted) {
        port.request({op:'stop'});
        const deadline = performance.now() + 4000;
        while (!port.request({op:'status'}).released) {
          if (performance.now() > deadline) throw new Error('NATIVE_RELEASE_TIMEOUT');
          await new Promise(resolve => setTimeout(resolve, 50));
        }
        await kernel.dispose(); slot.textContent = 'UI 插件已卸载，原生资源已释放。';
      } else { slot.textContent = ''; kernel = await createKernel(port, mount); }
      toggle.dataset.mounted = String(kernel.mounted);
      toggle.textContent = kernel.mounted ? '卸载 UI 插件' : '重新加载 UI 插件';
    } catch (error) { note.textContent = String(error); }
    finally { toggle.disabled = false; }
  };
}
boot().catch(error => { document.getElementById('root')!.textContent = `探针启动失败：${String(error)}`; });
