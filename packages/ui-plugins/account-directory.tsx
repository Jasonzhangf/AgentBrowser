import React, { useEffect, useState } from 'react';
import type { AccountCommand, AccountPort, AccountSnapshot } from '../client-domain/account-directory';

const labels: Record<AccountSnapshot['accountState'], string> = {
  signed_out: '未登录', signing_in: '正在登录', authenticated: '已登录',
  registering_device: '正在注册设备', refreshing_directory: '正在刷新目录',
  signing_out: '正在登出', expired: '登录已过期', error: '账号操作失败',
};
const hostLabels = {online: '在线', offline: '离线', expired: '目录已过期'};

export function AccountDirectory({port}: {port: AccountPort}) {
  const [snapshot, setSnapshot] = useState<AccountSnapshot>(() => port.request({op: 'account_status'}));
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [deviceName, setDeviceName] = useState('');
  const [problem, setProblem] = useState('');

  useEffect(() => {
    const timer = setInterval(() => {
      try { setSnapshot(port.request({op: 'account_status'})); } catch (error) { setProblem(String(error)); }
    }, 250);
    return () => clearInterval(timer);
  }, [port]);

  function request(command: AccountCommand) {
    try { setProblem(''); setSnapshot(port.request(command)); }
    catch (error) { setProblem(String(error)); }
  }

  const busy = snapshot.pending;
  const hasSession = snapshot.expiresAtMs > Date.now() && snapshot.accountState !== 'signed_out';
  const canRegister = hasSession && !busy && !snapshot.deviceId;
  const needsLogin = !hasSession && !busy;
  return <section className="account-directory" aria-label="Relay 账号与设备" data-account-state={snapshot.accountState}>
    <div className="account-heading"><div><h2>Relay 账号</h2><p className="detail" role="status">{labels[snapshot.accountState]}</p></div>{hasSession && <span className="account-expiry">授权至 {new Date(snapshot.expiresAtMs).toLocaleTimeString()}</span>}</div>
    {(snapshot.error || problem) && <p className="error" role="alert" aria-live="assertive">{snapshot.error || problem}。请检查 Relay 配置或重试。</p>}
    {needsLogin && <form className="account-login" onSubmit={event => { event.preventDefault(); if (username && password) { request({op: 'account_login', username, password}); setPassword(''); } }}>
      <label htmlFor="account-username">账号</label><input id="account-username" value={username} autoComplete="username" onChange={event => setUsername(event.currentTarget.value)} disabled={busy} />
      <label htmlFor="account-password">密码</label><input id="account-password" type="password" value={password} autoComplete="current-password" onChange={event => setPassword(event.currentTarget.value)} disabled={busy} />
      <button id="account-login" type="submit" disabled={busy || !username || !password}>登录</button>
    </form>}
    {hasSession && <>
      <div className="account-actions"><span className="detail">设备：{snapshot.deviceId || '尚未注册'}</span><button id="account-refresh" className="secondary" disabled={busy} onClick={() => request({op: 'account_refresh'})}>刷新目录</button><button id="account-logout" className="secondary" disabled={busy} onClick={() => request({op: 'account_logout'})}>登出</button></div>
      {canRegister && <form className="account-register" onSubmit={event => { event.preventDefault(); if (deviceName) request({op: 'account_register_device', name: deviceName}); }}>
        <label htmlFor="account-device-name">此设备名称</label><input id="account-device-name" value={deviceName} maxLength={64} onChange={event => setDeviceName(event.currentTarget.value)} placeholder="例如：我的手机" disabled={busy} /><button id="account-register" type="submit" disabled={busy || !deviceName}>注册此设备</button>
      </form>}
      <div className="directory" aria-live="polite"><div className="directory-heading"><strong>浏览器目录</strong><span className="detail">{snapshot.directoryState === 'expired' ? '目录已过期' : snapshot.directoryState === 'empty' ? '暂无在线 Host' : '已同步'}</span></div>
        {snapshot.hosts.length === 0 ? <p className="detail">刷新后显示同账号的 Host。账号登录不代表浏览器已连接。</p> : <ul>{snapshot.hosts.map(host => <li key={host.hostId}><span>{host.deviceName}</span><span className={`host-status ${host.status}`}>{hostLabels[host.status]}</span><small>{host.snapshot.sessions.length} 个会话</small></li>)}</ul>}
      </div>
    </>}
  </section>;
}
