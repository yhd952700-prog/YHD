import { describe, expect, test } from 'vitest'
import { describeFailure } from './errorMessage'

describe('后端错误文案可读化', () => {
  test('429 不把英文限流原文铺给用户', () => {
    // 后端真实返回：`请求过于频繁，请稍后再试（rate limit exceeded）`
    const failure = describeFailure(
      { detail: '请求过于频繁，请稍后再试（rate limit exceeded）' },
      429,
      'Too Many Requests',
      null,
    )
    expect(failure.message).toBe('请求过于频繁，请稍后再试')
  })

  test('429 仍保留追踪 ID，便于排障', () => {
    const failure = describeFailure({ detail: 'rate limit exceeded' }, 429, '', 'trace-123')
    expect(failure.message).toContain('追踪 ID：trace-123')
    expect(failure.message).not.toContain('rate limit exceeded')
  })

  test('非 429 保留服务端真实原因', () => {
    const failure = describeFailure({ detail: '该账号已存在' }, 409, '', null)
    expect(failure.message).toBe('该账号已存在')
  })

  test.each([
    [502, 'Bad Gateway'],
    [503, 'Service Unavailable'],
    [504, 'Gateway Timeout'],
  ])('非 JSON 的 %i 不回显浏览器英文状态文本', (status, statusText) => {
    const failure = describeFailure(null, status, statusText, null)
    expect(failure.message).not.toContain(statusText)
    expect(failure.message).toContain('服务暂时不可用')
    expect(failure.message).toContain('重试')
  })

  test('500 没有可读文案时给中性中文与下一步动作', () => {
    const failure = describeFailure(null, 500, 'Internal Server Error', null)
    expect(failure.message).not.toContain('Internal Server Error')
    expect(failure.message).toContain('没有完成')
    expect(failure.message).toContain('诊断包')
  })

  test('402 没有可读文案时说明额度来源与查看用量出口，绝不直出 Payment Required', () => {
    const failure = describeFailure(null, 402, 'Payment Required', null)
    expect(failure.message).toContain('超出当前额度')
    expect(failure.message).toContain('额度由管理员在启动配置中设定')
    expect(failure.message).toContain('我的 Key')
    expect(failure.message).toContain('重启贾维斯')
    expect(failure.message).not.toContain('权限')
    expect(failure.message).not.toContain('Payment Required')
  })

  test('连不上服务（status 0）明确告诉用户先启动服务', () => {
    const failure = describeFailure(null, 0, '', null)
    expect(failure.message).toContain('贾维斯的本地服务没有运行')
    expect(failure.message).toContain('重试')
  })

  test.each([
    'FileNotFoundError: [Errno 2] No such file or directory',
    'ECONNREFUSED 127.0.0.1:8787',
    'Internal Server Error',
    'unknown_kernel',
  ])('英文异常与机器串不上屏：%s', (detail) => {
    const failure = describeFailure({ detail }, 400, '', null)
    // 400 没有专用兜底，走中性中文；正文里不允许出现英文异常原文。
    expect(failure.message).not.toContain(detail)
    expect(failure.message).toMatch(/[\u4e00-\u9fff]/)
  })

  test('对象形式的 detail 只取中文说明，不拼英文键名', () => {
    const failure = describeFailure(
      { detail: { code: 'agent_unavailable', message: '员工已停用，无法接受指派' } },
      409,
      '',
      null,
    )
    expect(failure.message).toBe('员工已停用，无法接受指派')
    expect(failure.code).toBe('agent_unavailable')
    expect(failure.message).not.toContain('agent_unavailable')
  })

  // ── 结构化错误：后端已把 code 摊平到顶层 ─────────────────────
  test('顶层 code 优先（后端 structured_error_body 的真实形状）', () => {
    const failure = describeFailure(
      {
        code: 'project_has_goals',
        message: '该项目下还有 2 个目标：删除项目只会解除归属，目标与产出都会保留。',
        detail: '该项目下还有 2 个目标：删除项目只会解除归属，目标与产出都会保留。',
      },
      409,
      '',
      null,
    )
    expect(failure.code).toBe('project_has_goals')
    expect(failure.message).toContain('该项目下还有 2 个目标')
  })

  test('兼容旧的 detail.code（未摊平的端点仍能拿到错误码）', () => {
    const failure = describeFailure(
      { detail: { code: 'agent_not_found', message: '指定的员工不存在' } },
      404,
      '',
      null,
    )
    expect(failure.code).toBe('agent_not_found')
  })

  // ── 422：从 [{loc,msg,type}] 数组里取字段名 + 中文规则 ─────────
  test('422 数组给出「哪一项 + 什么规则」，不显示英文原文与字段名', () => {
    const failure = describeFailure(
      {
        detail: [
          {
            loc: ['body', 'password'],
            msg: 'String should have at least 8 characters',
            type: 'string_too_short',
            ctx: { min_length: 8 },
          },
          { loc: ['body', 'name'], msg: 'Field required', type: 'missing' },
        ],
      },
      422,
      '',
      null,
    )
    expect(failure.message).toContain('密码至少 8 位')
    expect(failure.message).toContain('名称不能为空')
    expect(failure.message).not.toContain('String should')
    expect(failure.message).not.toContain('password')
  })

  test('422 敏感字段没有 ctx 时，仍能从英文 msg 取出阈值', () => {
    const failure = describeFailure(
      {
        detail: [
          {
            loc: ['body', 'password'],
            msg: 'String should have at least 12 characters',
            type: 'string_too_short',
          },
        ],
      },
      422,
      '',
      null,
    )
    expect(failure.message).toBe('密码至少 12 位')
  })

  test('422 后端自定义中文规则原样采用', () => {
    const failure = describeFailure(
      {
        detail: [
          { loc: ['body', 'name'], msg: 'Value error, 名称不能包含斜杠', type: 'value_error' },
        ],
      },
      422,
      '',
      null,
    )
    expect(failure.message).toBe('名称不能包含斜杠')
  })

  test('422 完全取不到信息时才用中性兜底', () => {
    const failure = describeFailure({ detail: [] }, 422, '', null)
    expect(failure.message).toContain('不符合要求')
    expect(failure.message).toContain('检查')
  })
})
