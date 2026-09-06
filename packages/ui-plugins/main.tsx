import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import type { Context } from '@cordisjs/core';
import { androidPort, type ProbeCommand, type ProbeSnapshot } from '../client-domain/probe';
import { createKernel } from '../ui-kernel/kernel';
import './probe.css';

declare global { interface Window { ProbeNative?: {request(raw: string): string} } }
const labels = {idle:'等待播放',starting:'正在打开样本',playing:'正在显示',stopping:'正在释放',stopped:'已停止',completed:'播放结束',error:'播放失败'};
type CompositionPhase = 'idle' | 'composing' | 'committed' | 'cancelled';
function Panel({ctx}: {ctx: Context}) {
  const [snapshot, setSnapshot] = useState<ProbeSnapshot>(() => ctx.probe.request({op:'status'}));
  const [problem, setProblem] = useState('');
  const [text, setText] = useState('');
  const [composition, setComposition] = useState<CompositionPhase>('idle');
  const compositionRef = useRef<CompositionPhase>('idle');
  const compositionCancelled = useRef(false);
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const poll = setInterval(() => {
      try { setSnapshot(ctx.probe.request({op:'status'})); } catch (error) { setProblem(String(error)); }
    }, 250);
    return () => clearInterval(poll);
  }, [ctx]);
  function setCompositionPhase(phase: CompositionPhase) {
    compositionRef.current = phase;
    setComposition(phase);
  }
  function cancelComposition() {
    const active = compositionRef.current === 'composing';
    compositionCancelled.current = true;
    if (!active && text.length === 0) return;
    setText('');
    setCompositionPhase('cancelled');
    if (active) inputRef.current?.blur();
  }
  const connected = snapshot.source === 'network' && snapshot.connectionState !== 'stopped' && snapshot.connectionState !== 'error' && snapshot.connectionState !== 'idle';
  useEffect(() => {
    if (connected && snapshot.controlMode === 'control') return;
    cancelComposition();
  }, [connected, snapshot.controlMode]);
  function request(command: ProbeCommand) {
    if (command.op === 'input_text' && (compositionRef.current === 'composing' || compositionRef.current === 'cancelled')) return;
    if (command.op === 'disconnect' || command.op === 'release' || command.op === 'takeover' || command.op === 'stop') cancelComposition();
    try { setProblem(''); setSnapshot(ctx.probe.request(command)); }
    catch (error) { setProblem(String(error)); }
  }
  const busy = !snapshot.released;
  const network = snapshot.source === 'network';
  const canEdit = connected && snapshot.controlMode === 'control';
  const canSubmit = canEdit && Boolean(snapshot.inputReady) && composition !== 'composing' && composition !== 'cancelled' && text.length > 0;
  return <section className={network ? 'remote' : ''} data-state={snapshot.state} data-released={String(snapshot.released)} data-frames={snapshot.renderedFrames}>
    <h1>AgentBrowser</h1><p className="intro">{network ? '远程浏览器 · 页面保留在 Host' : snapshot.source === 'annexb' ? '原生视频验证' : '连接浏览器，或检查本机视频显示'}</p>
    <div className="status" role="status"><strong>{labels[snapshot.state]}</strong><span>{snapshot.renderedFrames} 帧呈现</span></div>
    <div className="actions local-probe"><button id="play" disabled={busy || connected} onClick={() => request({op:'play',sample:'portrait'})}>播放样本</button><button id="stop" className="secondary" disabled={!busy} onClick={() => request({op:'stop'})}>停止并释放</button></div>
    {(snapshot.error || problem) && <p className="error" role="alert">{snapshot.error || problem}。请停止后重新连接或播放。</p>}
    <details><summary>显示检查</summary><p className="detail">{snapshot.codec || '解码器尚未打开'} · {snapshot.released ? '资源已释放' : '资源使用中'}</p><button id="broken" className="secondary" disabled={busy || connected} onClick={() => request({op:'play',sample:'broken'})}>测试坏样本</button></details>
    <section className="network" aria-label="远程浏览器 Host">
      <h2>远程浏览器</h2><p className="detail" role="status">{snapshot.networkConfigured === false ? '请先配置此设备的配对信息' : !connected ? '连接后默认观察，不影响 Agent 操作' : snapshot.connectionState === 'connecting' ? '正在连接…' : snapshot.controlMode === 'control' ? '接管中 · 可点击页面和输入文字' : snapshot.controlMode === 'waiting' ? '等待当前原子操作完成，再交给你控制' : '观察模式 · Agent 可继续操作'}</p>
      <div className="actions"><button id="connect" hidden={connected} onClick={() => request({op:'connect'})}>连接 Host</button><button id="takeover" disabled={!connected || snapshot.epoch === undefined || snapshot.controlMode === 'control' || snapshot.controlMode === 'waiting'} onClick={() => request({op:'takeover',epoch:snapshot.epoch!})}>接管页面</button><button id="release" className="secondary" disabled={!connected || snapshot.controlMode !== 'control'} onClick={() => request({op:'release',epoch:snapshot.epoch!})}>返回观察</button><button id="disconnect" className="secondary" disabled={!connected} onClick={() => request({op:'disconnect'})}>断开</button></div>
      <div className="text-entry" data-composition={composition} data-composition-started={String(composition !== 'idle')} data-composition-committed={String(composition === 'committed')} data-composition-cancelled={String(composition === 'cancelled')}><input ref={inputRef} id="input-text" aria-label="输入到远程页面的文字" value={text} maxLength={4096} onChange={event => { setText(event.target.value); if (compositionRef.current !== 'composing') setCompositionPhase('idle'); }} onCompositionStart={() => { if (!canEdit) return; compositionCancelled.current = false; setCompositionPhase('composing'); }} onCompositionEnd={event => { if (compositionCancelled.current || !canEdit) { setText(''); setCompositionPhase('cancelled'); return; } setText(event.currentTarget.value); setCompositionPhase('committed'); }} onKeyDown={event => { if (event.key === 'Escape' && compositionRef.current === 'composing') { event.preventDefault(); cancelComposition(); } }} onBlur={() => { if (compositionRef.current === 'composing') cancelComposition(); }} placeholder="先点击页面中的输入框" disabled={!canEdit} /><button id="send-text" disabled={!canSubmit} onClick={() => { if (compositionRef.current === 'composing' || compositionRef.current === 'cancelled') return; request({op:'input_text',epoch:snapshot.epoch!,text}); }}>发送</button></div>
    </section>
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
  note.textContent = '断开连接后，远程页面仍保留。';
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
