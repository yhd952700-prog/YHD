/**
 * 鎏灏 Policy 审批 HTTP 客户端 —— 驾驶舱「审批中心」的真实数据源。
 *
 * ⚠️ 诚实声明（NO-FAKE）
 * ------------------------------------------------------------------
 * 本模块只调用真实端点，不编造任何策略状态：
 *
 *   GET    /v1/policy/enforcement        内核层拦截配置快照（只读）
 *   GET    /v1/policy/approvals          列出审批凭据
 *   POST   /v1/policy/approvals          签发审批凭据
 *   DELETE /v1/policy/approvals/{id}     撤销审批凭据
 *
 * 这四个端点**全部要求 Bearer 令牌**（主体只取自令牌的 sub，请求体无法指定），
 * 因此本模块把令牌作为一等信息对待：没有令牌时不假装「安全运行」，而是如实
 * 报告「未认证」。
 *
 * 令牌只存 sessionStorage —— 关闭标签页即丢弃，不长期驻留在浏览器里。
 */

/** 内核层拦截配置快照（GET /v1/policy/enforcement 的真实形状）。 */
export interface EnforcementSnapshot {
  env_var: string;
  spec: string;
  enabled: boolean;
  enforced_actions: string[];
  count: number;
  exempt_actions: string[];
  config_error: string | null;
}

/** 一条审批凭据（SovereigntyGrant.to_dict()）。 */
export interface ApprovalGrant {
  grant_id: string;
  principal: string;
  actions: string[];
  reason: string;
  issued_by: string;
  granted_at: number;
  expires_at: number;
  revoked_at: number | null;
  revoke_reason: string;
  is_active: boolean;
  remaining_seconds: number;
}

export interface ApprovalList {
  count: number;
  grants: ApprovalGrant[];
  enforcement: EnforcementSnapshot;
}

/** 令牌存储键。sessionStorage：关标签页即失效。 */
const TOKEN_KEY = 'liuhao.approval_token';

/** 读取当前令牌（空串表示未认证）。 */
export function getToken(): string {
  try {
    return sessionStorage.getItem(TOKEN_KEY) || '';
  } catch {
    return '';
  }
}

/** 写入或清除令牌（传空串即清除）。 */
export function setToken(token: string): void {
  const value = (token || '').trim();
  try {
    if (value) sessionStorage.setItem(TOKEN_KEY, value);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    // sessionStorage 不可用（隐私模式）——不抛错，UI 会如实显示未认证
  }
}

/**
 * 令牌的近似有效期展示用。
 * JWT 载荷只用于**本地显示**，不作为任何授权判断依据（后端始终自行校验签名）。
 */
export function describeToken(token: string): { subject: string; expiresAt: number } | null {
  const parts = (token || '').split('.');
  if (parts.length !== 3) return null;
  try {
    const b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const pad = b64.length % 4 ? '='.repeat(4 - (b64.length % 4)) : '';
    const payload = JSON.parse(atob(b64 + pad)) as { sub?: string; exp?: number };
    if (!payload.sub) return null;
    return { subject: String(payload.sub), expiresAt: Number(payload.exp ?? 0) };
  } catch {
    return null;
  }
}

export class PolicyAuthError extends Error {}
export class PolicyApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function authHeaders(token: string): Record<string, string> {
  return {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${token}`,
  };
}

async function readError(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown; error?: unknown };
    if (typeof body.detail === 'string') return body.detail;
    if (body.error) return String(body.error);
  } catch {
    // 落到下面的通用文案
  }
  return `HTTP ${res.status}`;
}

/** 统一的鉴权检查：401 单独成一类，UI 才能区分「没令牌」和「令牌坏了」。 */
async function call<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  if (!token) throw new PolicyAuthError('未认证：缺少审批令牌');
  const res = await fetch(path, { ...init, headers: authHeaders(token), cache: 'no-store' });
  if (res.status === 401) throw new PolicyAuthError(await readError(res));
  if (!res.ok) throw new PolicyApiError(res.status, await readError(res));
  return (await res.json()) as T;
}

/** 内核层拦截配置快照。 */
export function fetchEnforcement(token: string): Promise<EnforcementSnapshot> {
  return call<EnforcementSnapshot>('/v1/policy/enforcement', token);
}

/** 列出审批凭据（默认只看有效的）。响应里同时带拦截配置，省一次往返。 */
export function listApprovals(
  token: string,
  opts: { includeExpired?: boolean; includeRevoked?: boolean } = {},
): Promise<ApprovalList> {
  const q = new URLSearchParams();
  if (opts.includeExpired) q.set('include_expired', 'true');
  if (opts.includeRevoked) q.set('include_revoked', 'true');
  const suffix = q.toString() ? `?${q.toString()}` : '';
  return call<ApprovalList>(`/v1/policy/approvals${suffix}`, token);
}

/**
 * 签发一条审批凭据。
 *
 * 注意：**没有 principal 参数** —— 主体只能来自令牌，这是刻意的安全设计。
 */
export function issueApproval(
  token: string,
  actions: string[],
  reason: string,
  ttlSeconds: number,
): Promise<{ approved: boolean; grant: ApprovalGrant }> {
  return call<{ approved: boolean; grant: ApprovalGrant }>('/v1/policy/approvals', token, {
    method: 'POST',
    body: JSON.stringify({ actions, reason, ttl_seconds: ttlSeconds }),
  });
}

/** 撤销一条审批凭据（幂等）。 */
export function revokeApproval(
  token: string,
  grantId: string,
  reason: string,
): Promise<{ revoked: boolean; grant: ApprovalGrant }> {
  const q = reason ? `?reason=${encodeURIComponent(reason)}` : '';
  return call<{ revoked: boolean; grant: ApprovalGrant }>(
    `/v1/policy/approvals/${encodeURIComponent(grantId)}${q}`,
    token,
    { method: 'DELETE' },
  );
}

/** 人类可读的剩余时间。 */
export function fmtRemaining(seconds: number): string {
  if (seconds <= 0) return '已过期';
  const s = Math.floor(seconds);
  if (s < 60) return `${s} 秒`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} 分 ${s % 60} 秒`;
  return `${Math.floor(m / 60)} 时 ${m % 60} 分`;
}

/**
 * 身份内核自动创建的内置**机器**身份。
 *
 * 只用于**展示提示**：内核的 `_is_verified_human` 是反向排除
 * （`metadata.kind != "service"`），内置 `system` 不带任何 metadata，因此会被
 * 当作「已核验人类」——用它审批时审计只会记到机器身份上（开放项 C-7）。
 *
 * 这份清单必须与 `src/kernels/identity/__init__.py` 保持一致；由
 * `tests/test_policy_approval_http.py` 里的跨语言护栏断言，改一边不改另一边会红。
 */
export const BUILTIN_MACHINE_PRINCIPALS: string[] = ['system', 'liuhao-internal-service'];

/** True 表示该主体是内置机器身份（不是人）。 */
export function isBuiltinMachinePrincipal(subject: string): boolean {
  return BUILTIN_MACHINE_PRINCIPALS.includes((subject || '').trim());
}
