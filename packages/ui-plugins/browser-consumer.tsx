import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';

type HarnessScenario =
  | 'setup'
  | 'rejected'
  | 'configured'
  | 'connected'
  | 'connected-no-frame'
  | 'connected-video'
  | 'presentation-rejected'
  | 'presentation-open-status-error'
  | 'presentation-close-status-error'
  | 'waiting'
  | 'operation-pending'
  | 'control'
  | 'quality'
  | 'quality-fail'
  | 'command-reject'
  | 'status-recover'
  | 'tabs-missing-epoch'
  | 'tabs-valid'
  | 'tabs-context-revoke-on-send'
  | 'tabs-control-revoke-on-send'
  | 'tabs-capability-revoke-on-send'
  | 'tabs-status-error-on-send'
  | 'capability-missing'
  | 'capability-missing-setup'
  | 'capability-false'
  | 'unresolved-legacy-true'
  | 'unresolved-still'
  | 'configured-retry'
  | 'unavailable-repair'
  | 'unavailable-no-repair'
  | 'unavailable-repair-cancel'
  | 'configured-repair'
  | 'connected-unresolved'
  | 'connected-unavailable'
  | 'configured-long-label'
  | 'opening'
  | 'importing'
  | 'error-legacy';

interface ConsumerMessage {
  type: 'command' | 'ready' | 'state';
  command?: Record<string, unknown>;
  scenario?: HarnessScenario;
}

function Consumer() {
  const [scenario, setScenario] = useState<HarnessScenario>('setup');
  const [commands, setCommands] = useState<Array<Record<string, unknown>>>([]);
  const [ready, setReady] = useState(false);
  const frame = useRef<HTMLIFrameElement>(null);

  function setScenarioAndReload(next: HarnessScenario) {
    setScenario(next);
    setCommands([]);
    setReady(false);
    frame.current?.contentWindow?.postMessage({type: 'scenario', scenario: next}, window.location.origin);
  }

  function revokeCapabilities() {
    setCommands([]);
    frame.current?.contentWindow?.postMessage({type: 'capabilities', mode: 'false'}, window.location.origin);
  }

  function recoverPresentationStatus() {
    frame.current?.contentWindow?.postMessage({type: 'presentation-status-recover'}, window.location.origin);
  }

  useEffect(() => {
    function receive(event: MessageEvent<ConsumerMessage>) {
      if (event.origin !== window.location.origin) return;
      if (event.data.type === 'ready') {
        setReady(true);
        return;
      }
      if (event.data.type === 'command' && event.data.command) {
        setCommands(current => [...current, event.data.command!]);
        return;
      }
      if (event.data.type === 'state' && event.data.scenario) {
        setScenario(event.data.scenario);
      }
    }
    window.addEventListener('message', receive);
    return () => window.removeEventListener('message', receive);
  }, []);

  return <div className="consumer">
    <header className="consumer-header">
      <strong>W4 public chrome consumer</strong>
      <span>scenario={scenario} {ready ? 'ready' : 'loading'}</span>
      <button id="scenario-setup" onClick={() => setScenarioAndReload('setup')}>首次配置</button>
      <button id="scenario-rejected" onClick={() => setScenarioAndReload('rejected')}>连接拒绝</button>
      <button id="scenario-configured" onClick={() => setScenarioAndReload('configured')}>已配置未连接</button>
      <button id="scenario-connected" onClick={() => setScenarioAndReload('connected')}>已连接</button>
      <button id="scenario-connected-no-frame" onClick={() => setScenarioAndReload('connected-no-frame')}>连接无画面</button>
      <button id="scenario-connected-video" onClick={() => setScenarioAndReload('connected-video')}>有效画面</button>
      <button id="scenario-presentation-rejected" onClick={() => setScenarioAndReload('presentation-rejected')}>呈现拒绝</button>
      <button id="scenario-presentation-open-status-error" onClick={() => setScenarioAndReload('presentation-open-status-error')}>打开后状态错误</button>
      <button id="scenario-presentation-close-status-error" onClick={() => setScenarioAndReload('presentation-close-status-error')}>关闭后状态错误</button>
      <button id="scenario-waiting" onClick={() => setScenarioAndReload('waiting')}>等待接管</button>
      <button id="scenario-operation-pending" onClick={() => setScenarioAndReload('operation-pending')}>操作等待</button>
      <button id="scenario-control" onClick={() => setScenarioAndReload('control')}>已接管</button>
      <button id="scenario-quality" onClick={() => setScenarioAndReload('quality')}>画质请求</button>
      <button id="scenario-quality-fail" onClick={() => setScenarioAndReload('quality-fail')}>画质失败</button>
      <button id="scenario-command-reject" onClick={() => setScenarioAndReload('command-reject')}>命令拒绝</button>
      <button id="scenario-status-recover" onClick={() => setScenarioAndReload('status-recover')}>状态失败恢复</button>
      <button id="scenario-tabs-missing-epoch" onClick={() => setScenarioAndReload('tabs-missing-epoch')}>标签缺 epoch</button>
      <button id="scenario-tabs-valid" onClick={() => setScenarioAndReload('tabs-valid')}>标签有效 epoch</button>
      <button id="scenario-tabs-context-revoke-on-send" onClick={() => setScenarioAndReload('tabs-context-revoke-on-send')}>标签发送前撤销</button>
      <button id="scenario-tabs-control-revoke-on-send" onClick={() => setScenarioAndReload('tabs-control-revoke-on-send')}>标签发送前撤控</button>
      <button id="scenario-tabs-capability-revoke-on-send" onClick={() => setScenarioAndReload('tabs-capability-revoke-on-send')}>标签发送前撤能力</button>
      <button id="scenario-tabs-status-error-on-send" onClick={() => setScenarioAndReload('tabs-status-error-on-send')}>标签发送前状态错误</button>
      <button id="scenario-capability-missing" onClick={() => setScenarioAndReload('capability-missing')}>能力字段缺失</button>
      <button id="scenario-capability-missing-setup" onClick={() => setScenarioAndReload('capability-missing-setup')}>配置能力字段缺失</button>
      <button id="scenario-capability-false" onClick={() => setScenarioAndReload('capability-false')}>能力字段 false</button>
      <button id="scenario-unresolved-legacy-true" onClick={() => setScenarioAndReload('unresolved-legacy-true')}>未决+旧配置</button>
      <button id="scenario-unresolved-still" onClick={() => setScenarioAndReload('unresolved-still')}>未决保持</button>
      <button id="scenario-configured-retry" onClick={() => setScenarioAndReload('configured-retry')}>重试已恢复</button>
      <button id="scenario-unavailable-repair" onClick={() => setScenarioAndReload('unavailable-repair')}>不可用可修复</button>
      <button id="scenario-unavailable-no-repair" onClick={() => setScenarioAndReload('unavailable-no-repair')}>不可用禁修复</button>
      <button id="scenario-unavailable-repair-cancel" onClick={() => setScenarioAndReload('unavailable-repair-cancel')}>修复取消</button>
      <button id="scenario-configured-repair" onClick={() => setScenarioAndReload('configured-repair')}>已配置禁修复</button>
      <button id="scenario-connected-unresolved" onClick={() => setScenarioAndReload('connected-unresolved')}>连接中未决</button>
      <button id="scenario-connected-unavailable" onClick={() => setScenarioAndReload('connected-unavailable')}>连接中不可用</button>
      <button id="scenario-configured-long-label" onClick={() => setScenarioAndReload('configured-long-label')}>长标签</button>
      <button id="scenario-opening" onClick={() => setScenarioAndReload('opening')}>正在打开</button>
      <button id="scenario-importing" onClick={() => setScenarioAndReload('importing')}>正在导入</button>
      <button id="scenario-error-legacy" onClick={() => setScenarioAndReload('error-legacy')}>兼容错误</button>
      <button id="revoke-capabilities" onClick={revokeCapabilities}>撤销能力</button>
      <button id="presentation-status-recover" onClick={recoverPresentationStatus}>恢复状态刷新</button>
    </header>
    <iframe ref={frame} id="consumer-frame" title="实际构建 UI" src="/ui-frame.html"/>
    <pre id="commands" aria-live="polite">{JSON.stringify(commands, null, 2)}</pre>
  </div>;
}

createRoot(document.getElementById('root')!).render(<Consumer/>);
