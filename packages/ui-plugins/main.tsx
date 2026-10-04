import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import type { Context } from '@cordisjs/core';
import {
  androidPort,
  isPresentationResult,
  type PresentationResult,
  type PresentationState,
  type ProbeCommand,
  type ProbeSnapshot,
  type SnapshotCommand,
  type VideoQualityProfile,
} from '../client-domain/probe';
import { createKernel } from '../ui-kernel/kernel';
import './probe.css';

declare global {
  interface Window {
    ProbeNative?: { request(raw: string): string };
  }
}

const qualityLabels: Record<VideoQualityProfile, string> = {low: '低', balanced: '均衡', high: '高'};
const qualityDescriptions: Record<VideoQualityProfile, string> = {low: '省流量', balanced: '清晰与流畅', high: '更清晰'};

export function normalizeAddress(value: string): string {
  const address = value.trim();
  const scheme = /^([a-z][a-z0-9+.-]*):(.*)$/i.exec(address);
  if (!scheme) return `https://${address}`;

  const schemeName = scheme[1].toLowerCase();
  const opaquePayload = scheme[2];
  const standardSchemes = new Set([
    'http', 'https', 'about', 'blob', 'data', 'file', 'javascript', 'mailto',
    'tel', 'sms', 'geo', 'urn', 'ftp', 'ftps', 'ws', 'wss',
  ]);
  if (standardSchemes.has(schemeName)) return address;
  if (opaquePayload.startsWith('//') || scheme[1].includes('+')) return address;
  if (!/^\d+(?:[/?#]|$)/.test(opaquePayload)) return address;
  return `https://${address}`;
}

function connectionKey(snapshot: ProbeSnapshot): string {
  if (snapshot.source !== 'network') return 'local';
  if (snapshot.connectionState === 'connecting') return 'connecting';
  if (snapshot.connectionState === 'stopping') return 'stopping';
  if (snapshot.connectionState === 'stopped') return 'disconnected';
  if (snapshot.connectionState === 'error' || snapshot.error) return 'error';
  if (snapshot.connectionState === 'connected') return snapshot.controlMode ?? 'observe';
  return snapshot.connectionState ?? 'idle';
}

function chromeModeLabel(snapshot: ProbeSnapshot): string {
  if (snapshot.source !== 'network') return '未连接';
  if (snapshot.connectionState === 'connecting') return '连接中';
  if (snapshot.connectionState === 'stopping') return '断开中';
  if (snapshot.connectionState === 'error' || snapshot.error) return '连接失败';
  if (snapshot.connectionState !== 'connected') return '未连接';
  if (snapshot.controlMode === 'control') return '接管';
  if (snapshot.controlMode === 'waiting') return '等待';
  if (snapshot.controlMode === 'observe') return '观察';
  return '状态未知';
}

function connectionSetupProjection(snapshot: ProbeSnapshot) {
  if (snapshot.connectionSetup) return snapshot.connectionSetup;
  if (snapshot.networkConfigured === false) return {state: 'required' as const, error: null};
  if (snapshot.networkConfigured === true) return {state: 'configured' as const, error: null};
  return {state: 'required' as const, error: null};
}

function statusSummary(snapshot: ProbeSnapshot): string {
  if (snapshot.source !== 'network') return '未连接 Host';
  const setup = connectionSetupProjection(snapshot);
  if (setup.state === 'required') return '需要配置连接';
  if (setup.state === 'opening') return '正在打开连接设置';
  if (setup.state === 'importing') return '正在导入连接信息';
  if (snapshot.connectionState === 'connecting') return '正在连接 Host';
  if (snapshot.connectionState === 'stopping') return '正在断开';
  if (snapshot.connectionState === 'stopped') return '已断开，Host 页面保留';
  if (snapshot.connectionState === 'error' || snapshot.error) return '连接失败';
  if (snapshot.connectionState === 'connected') {
    if (snapshot.controlMode === 'control') return snapshot.inputReady ? '接管中，可操作远端页面' : '接管中，等待最新画面';
    if (snapshot.controlMode === 'waiting') return snapshot.pending ? `等待接管：${snapshot.pending}` : '等待当前远端操作结束';
    return '观察中，Agent 可继续操作';
  }
  return '未连接 Host';
}

function statusMeta(snapshot: ProbeSnapshot): string {
  if (snapshot.source !== 'network') return '本地浏览器 chrome';
  const setup = connectionSetupProjection(snapshot);
  if (setup.state === 'required') return '请先配置或导入连接';
  if (setup.state === 'opening') return '等待原生配置入口';
  if (setup.state === 'importing') return '等待原生导入入口';
  if (snapshot.connectionState === 'connecting') return '正在建立连接';
  if (snapshot.connectionState === 'stopping') return '正在释放本地连接';
  if (snapshot.connectionState === 'stopped') return 'Host 页面保留';
  if (snapshot.connectionState === 'error' || snapshot.error) return '可修正连接后重试';
  if (snapshot.controlMode === 'control') return '输入路径由原生视频与系统 IME 负责';
  if (snapshot.controlMode === 'waiting') return '不会取消当前远端操作';
  if (snapshot.connectionState === 'connected') return '只读观察，不影响 Agent';
  return '等待连接';
}

function metricLabel(value: number | null, unit: string): string {
  return value === null ? '不可用' : `${value}${unit}`;
}

function canNavigateSnapshot(snapshot: ProbeSnapshot): boolean {
  const navigationAvailable = snapshot.capabilities?.navigation === true;
  return snapshot.source === 'network'
    && snapshot.connectionState === 'connected'
    && snapshot.controlMode === 'control'
    && Boolean(snapshot.inputReady)
    && snapshot.epoch !== undefined
    && navigationAvailable;
}

function canManageTabsSnapshot(snapshot: ProbeSnapshot): boolean {
  return snapshot.source === 'network'
    && snapshot.connectionState === 'connected'
    && snapshot.controlMode === 'control'
    && Boolean(snapshot.inputReady)
    && snapshot.epoch !== undefined
    && snapshot.capabilities?.tabs === true;
}

function canRequestQualitySnapshot(snapshot: ProbeSnapshot): boolean {
  return snapshot.source === 'network'
    && snapshot.connectionState === 'connected'
    && snapshot.capabilities?.quality === true
    && Boolean(snapshot.sessionId)
    && Boolean(snapshot.videoQuality);
}

function Panel({ctx, onDrawerFooterTarget}: {ctx: Context; onDrawerFooterTarget: (target: HTMLDivElement | null) => void}) {
  const [snapshot, setSnapshot] = useState<ProbeSnapshot>(() => ctx.probe.request({op: 'status'}));
  const [commandProblem, setCommandProblem] = useState('');
  const [statusProblem, setStatusProblem] = useState('');
  const [qualityProblem, setQualityProblem] = useState('');
  const [presentationProblem, setPresentationProblem] = useState('');
  const [qualityBusy, setQualityBusy] = useState(false);
  const [url, setUrl] = useState(() => snapshot.navigation?.url ?? '');
  const [addressDirty, setAddressDirty] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const drawerToggleRef = useRef<HTMLButtonElement>(null);
  const drawerCloseRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLElement>(null);
  const drawerCloseSourceRef = useRef<'none' | 'escape' | 'drawer' | 'outside'>('none');
  const drawerFocusBeforeCloseRef = useRef(false);
  const presentationSequenceRef = useRef(0);
  const presentationPendingRef = useRef(false);
  const appliedPresentationRevisionRef = useRef<number | null>(null);
  const addressComposingRef = useRef(false);
  const addressCompositionJustEndedRef = useRef(false);
  const addressCompositionTimerRef = useRef<number | null>(null);

  useEffect(() => {
    const poll = setInterval(() => {
      try {
        setSnapshot(ctx.probe.request({op: 'status'}));
        setStatusProblem('');
      } catch (error) {
        setStatusProblem(String(error));
      }
    }, 250);
    return () => clearInterval(poll);
  }, [ctx]);

  useEffect(() => () => {
    if (addressCompositionTimerRef.current !== null) window.clearTimeout(addressCompositionTimerRef.current);
  }, []);

  useEffect(() => {
    const next = snapshot.navigation?.url;
    if (next !== undefined && !addressDirty) setUrl(next);
  }, [snapshot.navigation?.url, addressDirty]);

  const network = snapshot.source === 'network';
  const connected = network && snapshot.connectionState === 'connected';
  const connectionActive = network && (snapshot.connectionState === 'connected' || snapshot.connectionState === 'connecting' || snapshot.connectionState === 'stopping');
  const mode = connectionKey(snapshot);
  const setup = connectionSetupProjection(snapshot);
  const navigation = snapshot.navigation ?? {url: '', title: null, canGoBack: false, canGoForward: false, loading: false};
  const tabs = snapshot.tabs ?? {available: false, activeTabId: null, items: []};
  const profiles = snapshot.profiles ?? {available: false, selectedProfileId: null, items: []};
  const quality = snapshot.videoQuality;
  const layout = snapshot.layout;
  const nativeVideoVisible = layout?.visibleContent === 'native-video';
  const navigationAvailable = snapshot.capabilities?.navigation === true;
  const tabsAvailable = snapshot.capabilities?.tabs === true;
  const profilesAvailable = snapshot.capabilities?.profiles === true;
  const connectionSetupAvailable = snapshot.capabilities?.connectionSetup === true;
  const qualityAvailable = connected && snapshot.capabilities?.quality === true && Boolean(quality);
  const canNavigate = connected && snapshot.controlMode === 'control' && Boolean(snapshot.inputReady) && snapshot.epoch !== undefined && navigationAvailable;
  const canManageTabs = canManageTabsSnapshot(snapshot);
  const canSelectProfile = profilesAvailable && !connectionActive && !snapshot.pending;
  const setupBusy = setup.state === 'opening' || setup.state === 'importing';
  const connectionConfigured = setup.state === 'configured' || setup.state === 'error' || snapshot.networkConfigured === true;
  const canConnect = !connectionActive && !setupBusy && connectionConfigured;
  const canDisconnect = connectionActive;
  const canTakeover = connected && snapshot.epoch !== undefined && snapshot.controlMode !== 'control' && snapshot.controlMode !== 'waiting';
  const canRelease = connected && snapshot.controlMode === 'control';
  const canSetQuality = qualityAvailable && Boolean(snapshot.sessionId) && !qualityBusy;
  const hasError = Boolean(snapshot.error || commandProblem || statusProblem || presentationProblem || setup.error);
  const errorText = snapshot.error || commandProblem || statusProblem || presentationProblem || setup.error;
  const selectedProfileId = profiles.selectedProfileId ?? profiles.items.find(profile => profile.active)?.id ?? profiles.items[0]?.id ?? '';
  const safeInsets = layout
    ? {
      top: Math.max(0, layout.safeContentRect.y - layout.webRect.y),
      right: Math.max(0, layout.webRect.x + layout.webRect.width - layout.safeContentRect.x - layout.safeContentRect.width),
      bottom: Math.max(0, layout.webRect.y + layout.webRect.height - layout.safeContentRect.y - layout.safeContentRect.height),
      left: Math.max(0, layout.safeContentRect.x - layout.webRect.x),
    }
    : {top: 0, right: 0, bottom: 0, left: 0};
  const shellStyle = {
    '--safe-top': `${safeInsets.top}px`,
    '--safe-right': `${safeInsets.right}px`,
    '--safe-bottom': `${safeInsets.bottom}px`,
    '--safe-left': `${safeInsets.left}px`,
  } as React.CSSProperties;

  function request(command: SnapshotCommand) {
    try {
      setCommandProblem('');
      setSnapshot(ctx.probe.request(command));
    } catch (error) {
      setCommandProblem(String(error));
    }
  }

  function requestPresentation(state: PresentationState): PresentationResult {
    const id = `presentation-${++presentationSequenceRef.current}`;
    const result = ctx.probe.request({id, op: 'set_presentation', state});
    if (!isPresentationResult(result)) throw new Error('INVALID_PRESENTATION_RESULT');
    return result;
  }

  function requestTabCommand(build: (epoch: number) => Exclude<ProbeCommand, {op: 'set_presentation'} | {op: 'set_video_quality'}>) {
    try {
      const current = ctx.probe.request({op: 'status'});
      if (!canManageTabsSnapshot(current) || current.epoch === undefined) {
        setSnapshot(current);
        return;
      }
      request(build(current.epoch));
    } catch (error) {
      setStatusProblem(String(error));
    }
  }

  function requestQuality(profile: VideoQualityProfile | null, lowLatency: boolean | 'toggle' | null) {
    if (!canSetQuality) return;
    setQualityBusy(true);
    setQualityProblem('');
    try {
      const current = ctx.probe.request({op: 'status'});
      const currentQuality = current.videoQuality;
      if (!canRequestQualitySnapshot(current) || !current.sessionId || !currentQuality) {
        setSnapshot(current);
        throw new Error('QUALITY_UNAVAILABLE');
      }
      const selectedProfile = profile ?? currentQuality.requested.profile;
      const selectedLowLatency = lowLatency === 'toggle' ? !currentQuality.requested.lowLatency : lowLatency ?? currentQuality.requested.lowLatency;
      setSnapshot(ctx.probe.request({
        op: 'set_video_quality',
        sessionId: current.sessionId,
        generation: currentQuality.requested.generation,
        profile: selectedProfile,
        lowLatency: selectedLowLatency,
      }));
    } catch (error) {
      setQualityProblem(String(error));
    } finally {
      setQualityBusy(false);
    }
  }

  function submitAddress(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (addressComposingRef.current || addressCompositionJustEndedRef.current) return;
    const submitted = event.currentTarget.querySelector<HTMLInputElement>('input')?.value ?? url;
    if (!submitted.trim()) return;
    try {
      const current = ctx.probe.request({op: 'status'});
      if (!canNavigateSnapshot(current)) {
        setSnapshot(current);
        return;
      }
      const target = normalizeAddress(submitted);
      setUrl(target);
      setAddressDirty(false);
      request({op: 'navigate', epoch: current.epoch!, url: target});
    } catch (error) {
      setStatusProblem(String(error));
    }
  }

  function selectAddress(event: React.FocusEvent<HTMLInputElement>) {
    event.currentTarget.select();
  }

  function submitAddressOnEnter(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key !== 'Enter') return;
    if (addressComposingRef.current
        || event.nativeEvent.isComposing
        || event.nativeEvent.keyCode === 229
        || addressCompositionJustEndedRef.current) return;
    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  }

  function startAddressComposition() {
    if (addressCompositionTimerRef.current !== null) {
      window.clearTimeout(addressCompositionTimerRef.current);
      addressCompositionTimerRef.current = null;
    }
    addressComposingRef.current = true;
    addressCompositionJustEndedRef.current = false;
  }

  function endAddressComposition() {
    addressComposingRef.current = false;
    addressCompositionJustEndedRef.current = true;
    if (addressCompositionTimerRef.current !== null) window.clearTimeout(addressCompositionTimerRef.current);
    addressCompositionTimerRef.current = window.setTimeout(() => {
      addressCompositionJustEndedRef.current = false;
      addressCompositionTimerRef.current = null;
    }, 0);
  }

  function drawerContainsFocus(): boolean {
    return Boolean(drawerRef.current?.contains(document.activeElement));
  }

  function openDrawer() {
    if (drawerOpen || presentationPendingRef.current) return;
    presentationPendingRef.current = true;
    setPresentationProblem('');
    try {
      const result = requestPresentation('overlay');
      if (result.status === 'rejected') {
        setPresentationProblem(`打开抽屉被拒绝：${result.reason}`);
        return;
      }
      appliedPresentationRevisionRef.current = result.geometry.revision;
      setDrawerOpen(true);
      try {
        setSnapshot(ctx.probe.request({op: 'status'}));
        setStatusProblem('');
      } catch (error) {
        setStatusProblem(String(error));
      }
    } catch (error) {
      setPresentationProblem(`打开抽屉失败：${String(error)}`);
    } finally {
      presentationPendingRef.current = false;
    }
  }

  function closeDrawer(source: 'escape' | 'drawer' | 'outside') {
    if (!drawerOpen || presentationPendingRef.current) return;
    const focusWasInside = drawerContainsFocus();
    presentationPendingRef.current = true;
    setPresentationProblem('');
    try {
      const result = requestPresentation('browsing');
      if (result.status === 'rejected') {
        setPresentationProblem(`关闭抽屉被拒绝：${result.reason}`);
        return;
      }
      appliedPresentationRevisionRef.current = result.geometry.revision;
      if (focusWasInside && document.activeElement instanceof HTMLElement) document.activeElement.blur();
      drawerFocusBeforeCloseRef.current = focusWasInside;
      drawerCloseSourceRef.current = source;
      setDrawerOpen(false);
      try {
        setSnapshot(ctx.probe.request({op: 'status'}));
        setStatusProblem('');
      } catch (error) {
        setStatusProblem(String(error));
      }
    } catch (error) {
      setPresentationProblem(`关闭抽屉失败：${String(error)}`);
    } finally {
      presentationPendingRef.current = false;
    }
  }

  function handleDrawerBackdropClick(event: React.MouseEvent<HTMLButtonElement>) {
    event.currentTarget.blur();
    closeDrawer('outside');
  }

  function handleDrawerKeyDown(event: React.KeyboardEvent<HTMLElement>) {
    if (event.key !== 'Tab') return;
    const focusable = Array.from(drawerRef.current?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
    ) ?? []);
    if (focusable.length === 0) {
      event.preventDefault();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  useEffect(() => {
    if (!drawerOpen) return;
    drawerCloseRef.current?.focus();
    function handleEscape(event: KeyboardEvent) {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      closeDrawer('escape');
    }
    document.addEventListener('keydown', handleEscape);
    return () => {
      document.removeEventListener('keydown', handleEscape);
      const restore = drawerFocusBeforeCloseRef.current
        && (drawerCloseSourceRef.current === 'escape'
          || drawerCloseSourceRef.current === 'drawer');
      if (restore) drawerToggleRef.current?.focus();
      drawerCloseSourceRef.current = 'none';
      drawerFocusBeforeCloseRef.current = false;
    };
  }, [drawerOpen]);

  useEffect(() => {
    const presentation = snapshot.layout?.presentation;
    const revision = snapshot.layout?.revision;
    const appliedRevision = appliedPresentationRevisionRef.current;
    if (appliedRevision !== null && revision !== undefined && revision < appliedRevision) return;
    if (revision !== undefined) appliedPresentationRevisionRef.current = null;
    if (presentation === 'browsing' && drawerOpen) setDrawerOpen(false);
    if (presentation === 'overlay' && !drawerOpen) setDrawerOpen(true);
  }, [snapshot.layout?.presentation, snapshot.layout?.revision, drawerOpen]);

  useLayoutEffect(() => {
    document.documentElement.dataset.remote = String(network);
    document.body.dataset.remote = String(network);
    return () => {
      delete document.documentElement.dataset.remote;
      delete document.body.dataset.remote;
    };
  }, [network]);

  return <>
    <main className={network ? 'remote-shell' : 'local-shell'} style={shellStyle} data-remote={String(network)} data-connection={mode} data-error={String(hasError)} data-drawer-open={String(drawerOpen)} data-presentation={layout?.presentation ?? 'unknown'} data-visible-content={layout?.visibleContent ?? 'unknown'}>
      <header className="browser-chrome" aria-label="浏览器工具栏">
        <div className="chrome-nav" role="group" aria-label="页面导航">
          <button id="back" type="button" aria-label="后退" title="后退" disabled={!canNavigate || !navigation.canGoBack} onClick={() => request({op: 'go_back', epoch: snapshot.epoch!})}>‹</button>
          <button id="forward" type="button" aria-label="前进" title="前进" disabled={!canNavigate || !navigation.canGoForward} onClick={() => request({op: 'go_forward', epoch: snapshot.epoch!})}>›</button>
          <button id="reload" type="button" aria-label="刷新" title="刷新" disabled={!canNavigate} onClick={() => request({op: 'reload', epoch: snapshot.epoch!})}>↻</button>
        </div>
        <form className="browser-address" onSubmit={submitAddress} noValidate>
          <label className="sr-only" htmlFor="address">页面地址</label>
          <input
            id="address"
            type="url"
            autoCorrect="off"
            spellCheck={false}
            value={url}
            onChange={event => { setAddressDirty(true); setUrl(event.currentTarget.value); }}
            onFocus={selectAddress}
            onCompositionStart={startAddressComposition}
            onCompositionEnd={endAddressComposition}
            onKeyDown={submitAddressOnEnter}
            placeholder="输入页面地址"
            disabled={!canNavigate}
          />
          <span className={`chrome-mode mode-${mode}`} role="status" aria-live="polite">{chromeModeLabel(snapshot)}</span>
        </form>
        <button id="tabs-toggle" className="icon-button" type="button" aria-label="标签页" title="标签页" disabled={!tabsAvailable} onClick={openDrawer}>
          <span aria-hidden="true">▤</span>
          {tabsAvailable && tabs.items.length > 0 && <span className="tab-count">{tabs.items.length}</span>}
        </button>
        <button ref={drawerToggleRef} id="drawer-toggle" className="icon-button" type="button" aria-controls="browser-drawer" aria-expanded={drawerOpen} aria-label={drawerOpen ? '关闭菜单' : '打开菜单'} title="菜单" onClick={() => { if (drawerOpen) closeDrawer('outside'); else openDrawer(); }}>
          <span aria-hidden="true">☰</span>
        </button>
      </header>
      {hasError && <div className="chrome-error" role="alert" aria-live="assertive">{errorText}</div>}
      <section className="viewer" aria-label="原生视频区域" data-ready={String(nativeVideoVisible)} data-visible-content={layout?.visibleContent ?? 'unknown'} data-input-path="native-video-surface">
        <div className="viewer-surface">
          <div className="viewer-empty">
            <strong>{nativeVideoVisible ? '原生画面已呈现' : connected ? (navigation.loading ? '正在加载页面' : '等待原生画面') : '未连接 Host'}</strong>
            <small>{nativeVideoVisible ? '输入由原生视频区域承载' : connected ? '远端页面由 Host 持有' : '打开菜单配置连接'}</small>
          </div>
        </div>
      </section>
      <button className="drawer-backdrop" type="button" aria-label="关闭菜单" aria-hidden={!drawerOpen} tabIndex={-1} onClick={handleDrawerBackdropClick} />
      <aside ref={drawerRef} id="browser-drawer" className="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title" aria-hidden={!drawerOpen} inert={!drawerOpen} data-open={String(drawerOpen)} onKeyDown={handleDrawerKeyDown}>
        <div className="drawer-header">
          <div><span className="eyebrow">AgentBrowser</span><h2 id="drawer-title">浏览器菜单</h2></div>
          <button ref={drawerCloseRef} id="drawer-close" className="icon-button" type="button" aria-label="关闭菜单" onClick={() => closeDrawer('drawer')}>×</button>
        </div>
        <section className="drawer-section" aria-labelledby="connection-title">
          <div className="section-heading"><h3 id="connection-title">连接</h3><span className={`badge badge-${mode}`}>{chromeModeLabel(snapshot)}</span></div>
          <div className="status-card" role="status" data-control-mode={connected ? (snapshot.controlMode ?? 'observe') : 'disconnected'} data-operation-pending={String(Boolean(snapshot.pending))}>
            <strong className="status-title">{statusSummary(snapshot)}</strong>
            <span className="status-meta">{statusMeta(snapshot)}</span>
          </div>
          {(setup.state === 'required' || setup.state === 'error') && <div className="setup-actions" role="group" aria-label="连接配置">
            <button id="connection-setup" type="button" disabled={!connectionSetupAvailable || setupBusy} onClick={() => request({op: 'open_connection_setup'})}>配置连接</button>
            <button id="connection-import" className="secondary" type="button" disabled={!connectionSetupAvailable || setupBusy} onClick={() => request({op: 'import_connection'})}>导入</button>
          </div>}
          {setup.error && <p className="error" role="alert" aria-live="assertive">{setup.error}</p>}
          <div className="remote-actions" role="group" aria-label="连接操作">
            <button id="connect" type="button" disabled={!canConnect} onClick={() => request({op: 'connect'})}>{setup.state === 'error' ? '重试连接' : '连接'}</button>
            <button id="disconnect" className="secondary" type="button" disabled={!canDisconnect} onClick={() => request({op: 'disconnect'})}>断开</button>
            <button id="takeover" type="button" disabled={!canTakeover} onClick={() => request({op: 'takeover', epoch: snapshot.epoch!})}>接管</button>
            <button id="release" className="secondary" type="button" disabled={!canRelease} onClick={() => request({op: 'release', epoch: snapshot.epoch!})}>返回观察</button>
          </div>
        </section>
        <section className="drawer-section" aria-labelledby="tabs-title">
          <div className="section-heading"><h3 id="tabs-title">标签页</h3><button id="new-tab" className="compact-button" type="button" disabled={!canManageTabs} onClick={() => requestTabCommand(epoch => ({op: 'new_tab', epoch}))}>新建</button></div>
          {!tabsAvailable
            ? <p className="capability-unavailable">标签页能力不可用</p>
            : tabs.items.length === 0
              ? <p className="detail">暂无标签页</p>
              : <ul className="tab-list">{tabs.items.map(tab => <li key={tab.id} className={tab.active ? 'active' : ''}>
                <button id={`tab-${tab.id}`} className="tab-select" type="button" disabled={!canManageTabs || tab.active} onClick={() => requestTabCommand(epoch => ({op: 'select_tab', epoch, tabId: tab.id}))}>
                  <strong>{tab.title || tab.url || tab.id}</strong><small>{tab.url}</small>
                </button>
                <button className="tab-close" type="button" aria-label={`关闭 ${tab.title || tab.id}`} disabled={!canManageTabs} onClick={() => requestTabCommand(epoch => ({op: 'close_tab', epoch, tabId: tab.id}))}>×</button>
              </li>)}</ul>}
        </section>
        <section className="drawer-section" aria-labelledby="profiles-title">
          <div className="section-heading"><h3 id="profiles-title">Profile</h3><span className="detail">{profiles.selectedProfileId ?? '未选择'}</span></div>
          {!profilesAvailable
            ? <p className="capability-unavailable">Profile 能力不可用</p>
            : <><label className="field-label" htmlFor="profile-select">当前 Profile</label><select id="profile-select" value={selectedProfileId} disabled={!canSelectProfile} onChange={event => request({op: 'select_profile', profileId: event.currentTarget.value})}>
              {profiles.items.length === 0 && <option value="">暂无 Profile</option>}
              {profiles.items.map(profile => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
            </select></>}
        </section>
        <section className="drawer-section quality-controls" aria-labelledby="quality-title" aria-busy={qualityBusy} data-operation-pending={String(Boolean(snapshot.pending))}>
          <div className="quality-heading"><div><h3 id="quality-title">视频质量</h3><p>只调整远端视频传输。</p></div><span className="quality-capability" data-supported={String(qualityAvailable)}>{qualityAvailable ? '已协商' : '未协商'}</span></div>
          {qualityAvailable && quality ? <>
            <div className="quality-profile" role="group" aria-label="视频质量档位">
              {(['low', 'balanced', 'high'] as VideoQualityProfile[]).map(profile => <button
                key={profile}
                id={`quality-${profile}`}
                type="button"
                className={quality.requested.profile === profile ? 'quality-choice selected' : 'quality-choice'}
                aria-pressed={quality.requested.profile === profile}
                disabled={!canSetQuality}
                onClick={() => requestQuality(profile, null)}
              ><span>{qualityLabels[profile]}</span><small>{qualityDescriptions[profile]}</small></button>)}
            </div>
            <button id="quality-low-latency" className="quality-switch" type="button" role="switch" aria-checked={quality.requested.lowLatency} disabled={!canSetQuality} onClick={() => requestQuality(null, 'toggle')}>低延迟 <strong>{quality.requested.lowLatency ? '开' : '关'}</strong></button>
            <div className="quality-status" role="status" aria-live="polite">
              <div className="quality-profiles"><span>请求</span><strong>{qualityLabels[quality.requested.profile]} · 低延迟{quality.requested.lowLatency ? '开' : '关'} · g{quality.requested.generation}</strong><span>生效</span><strong>{qualityLabels[quality.effective.profile]} · 低延迟{quality.effective.lowLatency ? '开' : '关'} · {quality.effective.generation === null ? '未确认' : `g${quality.effective.generation}`}</strong></div>
              <div className="quality-metrics"><span>RTT {metricLabel(quality.metrics.rttMs, ' ms')}</span><span>抖动 {metricLabel(quality.metrics.jitterMs, ' ms')}</span><span>丢包 {metricLabel(quality.metrics.lossPercent, '%')}</span><span>队列 {metricLabel(quality.metrics.queueMs, ' ms')}</span></div>
            </div>
          </> : <p className="capability-unavailable">当前连接未提供画质能力</p>}
          {qualityProblem && <p className="error quality-error" role="alert" aria-live="assertive">画质请求失败：{qualityProblem}。生效状态未在本地修改。</p>}
        </section>
        <section className="drawer-section" aria-labelledby="diagnostics-title">
          <h3 id="diagnostics-title">诊断</h3>
          <dl className="diagnostics">
            <div><dt>状态</dt><dd>{snapshot.connectionState ?? 'unknown'}</dd></div>
            <div><dt>控制</dt><dd>{snapshot.controlMode ?? 'unknown'}</dd></div>
            <div><dt>Session</dt><dd>{snapshot.sessionId ?? 'unknown'}</dd></div>
            <div><dt>Transport</dt><dd>{snapshot.transport ?? 'unknown'}</dd></div>
            <div><dt>Path</dt><dd>{snapshot.path ?? 'unknown'}</dd></div>
            <div><dt>Epoch</dt><dd>{snapshot.epoch ?? 'unknown'}</dd></div>
            <div><dt>Document</dt><dd>{snapshot.documentRevision ?? 'unknown'}</dd></div>
            <div><dt>Viewport</dt><dd>{snapshot.viewportRevision ?? 'unknown'}</dd></div>
            <div><dt>Displayed</dt><dd>{snapshot.displayedDocumentRevision ?? 'unknown'} / {snapshot.displayedViewportRevision ?? 'unknown'}</dd></div>
            <div><dt>Codec</dt><dd>{snapshot.codec || 'unknown'}</dd></div>
          </dl>
          <div className="drawer-footer-slot" ref={onDrawerFooterTarget}/>
        </section>
      </aside>
    </main>
  </>;
}

async function boot() {
  const host = document.getElementById('root')!;
  const port = androidPort(window.ProbeNative);
  const shell = document.createElement('div');
  const slot = document.createElement('div');
  const footer = document.createElement('footer');
  const toggle = document.createElement('button');
  const note = document.createElement('p');
  shell.className = 'plugin-shell';
  footer.className = 'plugin-footer';
  toggle.id = 'plugin';
  toggle.type = 'button';
  toggle.className = 'text-button';
  note.textContent = '卸载 UI 插件不会停止远端操作。';
  footer.append(toggle, note);
  shell.append(slot, footer);
  host.append(shell);

  function moveFooter(target: HTMLDivElement | null) {
    (target ?? shell).append(footer);
  }

  function mount(ctx: Context) {
    const root = createRoot(slot);
    root.render(<Panel ctx={ctx} onDrawerFooterTarget={moveFooter}/>);
    ctx.on('dispose', () => {
      root.unmount();
      shell.append(footer);
    });
  }

  let kernel = await createKernel(port, mount);
  toggle.textContent = '卸载 UI 插件';
  toggle.dataset.mounted = 'true';
  toggle.onclick = async () => {
    toggle.disabled = true;
    try {
      if (kernel.mounted) {
        await kernel.dispose();
        slot.textContent = 'UI 插件已卸载。';
      } else {
        slot.textContent = '';
        kernel = await createKernel(port, mount);
      }
      toggle.dataset.mounted = String(kernel.mounted);
      toggle.textContent = kernel.mounted ? '卸载 UI 插件' : '重新加载 UI 插件';
    } catch (error) {
      note.textContent = String(error);
    } finally {
      toggle.disabled = false;
    }
  };
}

boot().catch(error => {
  document.getElementById('root')!.textContent = `浏览器启动失败：${String(error)}`;
});
